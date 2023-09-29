# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
Component for handling package updates.
"""
# pylint: disable=too-many-lines

import datetime
import os
import os.path
import re
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Generator, BinaryIO
from urllib.parse import quote, urlparse

from OpenSSL.crypto import FILETYPE_PEM, load_certificate  # type: ignore[import]
from opsicommon.client.opsiservice import ServiceClient
from opsicommon.config.opsi import OpsiConfig
from opsicommon.logging import get_logger, secret_filter
from opsicommon.objects import NetbootProduct, ProductOnClient, ProductOnDepot
from opsicommon.package import OpsiPackage
from opsicommon.server.rights import set_rights
from opsicommon.ssl import install_ca
from opsicommon.types import forceProductId
from opsicommon.utils import prepare_proxy_environment
from opsicommon.package.repo_meta import RepoMetaPackageCollection
from requests import Response, Session  # type: ignore[import]
from requests.packages import urllib3  # type: ignore[import,attr-defined]

from pyzsync import (
	create_zsync_file,
	read_zsync_file,
	get_patch_instructions,
	patch_file,
	SOURCE_REMOTE,
	HTTPPatcher,
	PatchInstruction,
	ProgressListener,
	CaseInsensitiveDict,
)
from OPSI.Util import compareVersions, formatFileSize, md5sum  # type: ignore[import]
from OPSI.Util.File.Opsi import parseFilename  # type: ignore[import]

from opsiutils import get_service_client
from opsiutils.update_packages.Config import DEFAULT_USER_AGENT, ConfigurationParser
from opsiutils.update_packages.Notifier import (
	BaseNotifier,
	DummyNotifier,
	EmailNotifier,
)
from opsiutils.update_packages.Repository import LinksExtractor

from .Repository import ProductRepositoryInfo

urllib3.disable_warnings()

__all__ = ("OpsiPackageUpdater",)

logger = get_logger("opsi.general")


class HashsumMissmatchError(ValueError):
	pass


class RequestsHTTPPatcher(HTTPPatcher):
	def __init__(
		self,
		session: Session,
		url: str,
		instructions: list[PatchInstruction],
		target_file: BinaryIO,
		headers: dict[str, str],
		max_ranges_per_request: int = 100,
		read_timeout: int = 8 * 3600,
	) -> None:
		super().__init__(
			instructions=instructions,
			target_file=target_file,
			url=url,
			headers=headers,
			max_ranges_per_request=max_ranges_per_request,
			read_timeout=read_timeout,
		)

		self._session: Session = session
		self._response: Response | None = None

	def _send_request(self) -> tuple[int, CaseInsensitiveDict]:
		self._response = self._session.get(self.url, headers=self._headers, stream=True, timeout=self._read_timeout)
		return self._response.status_code, CaseInsensitiveDict(dict(self._response.headers))

	def _read_response_data(self, size: int | None = None) -> bytes:
		assert isinstance(self._response, Response)
		return self._response.raw.read(size)


class OpsiPackageUpdater:  # pylint: disable=too-many-public-methods,too-many-instance-attributes
	def __init__(self, config: dict[str, str | None | list[ProductRepositoryInfo]]) -> None:
		self.config = config
		self.httpHeaders = {"User-Agent": self.config.get("userAgent", DEFAULT_USER_AGENT)}
		self.configBackend: ServiceClient | None = None
		self.depotBackend: ServiceClient | None = None
		self.depotId = OpsiConfig().get("host", "id")
		self.depotServiceUrl = ""
		self.isConfigServer = OpsiConfig().get("host", "server-role") == "configserver"
		self.errors: list[Exception] = []
		self.metafile_cache: dict[str, bytes] = {}

		# Proxy is needed for getConfigBackend which is needed for ConfigurationParser.parse
		self.config["proxy"] = ConfigurationParser.get_proxy(self.config["configFile"])

		depots = self.getConfigBackend().host_getObjects(type="OpsiDepotserver", id=self.depotId)  # pylint: disable=no-member
		if not self.isConfigServer:
			url = urlparse(depots[0].repositoryRemoteUrl)
			self.depotServiceUrl = f"https://localhost:{url.port or 4447}"
		try:
			self.depotKey = depots[0].opsiHostKey
		except IndexError as err:
			raise ValueError(f"Depot '{self.depotId}' not found in backend") from err

		if not self.depotKey:
			raise ValueError(f"Opsi host key for depot '{self.depotId}' not found in backend")
		secret_filter.add_secrets(self.depotKey)

		self.readConfigFile()

	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc_val, exc_tb):
		try:
			self.configBackend.backend_exit()  # pylint: disable=no-member
		except Exception:  # pylint: disable=broad-except
			pass

	def getActiveRepositories(self) -> Generator[ProductRepositoryInfo, None, None]:
		"""
		Iterates over the found repositories and yields the active ones
		one by one.
		If a repository name filter is given only repositories matching
		the name will be returned.

		:rtype: ProductRepositoryInfo
		"""
		for repo in self.getRepositories():
			if not repo.active:
				continue

			yield repo

	def getRepositories(self) -> Generator[ProductRepositoryInfo, None, None]:
		"""
		Iterates over all found repositories and yields them.
		If a repository name filter is given only repositories matching
		the name will be returned.

		:rtype: ProductRepositoryInfo
		"""
		name = self.config.get("repositoryName", None)

		for repo in self.config.get("repositories", []):
			if name and repo.name.strip().lower() != name.strip().lower():
				continue

			yield repo

	def readConfigFile(self) -> None:
		parser = ConfigurationParser(
			configFile=self.config["configFile"], backend=self.getConfigBackend(), depotId=self.depotId, depotKey=self.depotKey
		)
		self.config = parser.parse(self.config)

	def getConfigBackend(self) -> ServiceClient:
		if not self.configBackend:
			self.configBackend = get_service_client(proxy_url=self.config["proxy"])
			try:
				ca_crt = load_certificate(FILETYPE_PEM, self.configBackend.getOpsiCACert())  # pylint: disable=no-member
				install_ca(ca_crt)
			except Exception as err:  # pylint: disable=broad-except
				logger.info("Failed to update opsi CA: %s", err)
		return self.configBackend

	def getDepotBackend(self) -> ServiceClient:
		if self.isConfigServer:
			return self.getConfigBackend()

		if not self.depotBackend:
			self.depotBackend = get_service_client(address=self.depotServiceUrl)
		return self.depotBackend

	def get_new_packages_per_repository(self) -> dict[ProductRepositoryInfo, list[dict[str, str | ProductRepositoryInfo]]]:
		downloadablePackages = self.getDownloadablePackages()
		downloadablePackages = self.onlyNewestPackages(downloadablePackages)
		downloadablePackages = self._filterProducts(downloadablePackages)
		result: dict[str, list[dict[str, ProductRepositoryInfo]]] = {}
		for package in downloadablePackages:
			if result.get(package["repository"]):
				result[package["repository"]].append(package)
			else:
				result[package["repository"]] = [package]
		return result

	def _useZsync(  # pylint: disable=too-many-return-statements
		self, session: Session, availablePackage: dict[str, str | ProductRepositoryInfo], localPackage: dict[str, str] | None
	) -> bool:
		if not self.config["useZsync"]:
			return False
		if not localPackage:
			logger.info("Cannot use zsync, no local package found")
			return False
		if not availablePackage["zsyncFile"]:
			logger.info("Cannot use zsync, no zsync file on server found")
			return False

		response = session.head(availablePackage["packageFile"])
		if response.headers.get("Accept-Ranges") != "bytes":
			logger.info("Cannot use zsync, server or proxy does not accept byte ranges")
			return False

		return True

	def check_dependency_sequence(self, sequence: list[str], productId: str, dependency: str) -> None:
		try:
			ppos = sequence.index(productId)
			try:
				dpos = sequence.index(dependency)
				logger.debug("Dependency %s has index %s", dependency, dpos)
			except ValueError:
				logger.error("Dependency %s of package %s not in sequence. Adding it.", productId, dependency)
				sequence.insert(ppos, dependency)
			if ppos < dpos:
				sequence.remove(dependency)
				sequence.insert(ppos, dependency)
		except Exception as err:  # pylint: disable=broad-except
			logger.debug("While processing package '%s', product_dependency '%s': %s", productId, dependency, err)

	def processUpdates(self) -> None:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
		if not any(self.getActiveRepositories()):
			logger.warning("No repositories configured, nothing to do")
			return

		notifier = self._getNotifier()
		try:  # pylint: disable=too-many-nested-blocks
			newPackages = self.get_packages(notifier)
			if not newPackages:
				logger.notice("No new packages available")
				return

			logger.info("New packages available: %s", ", ".join(sorted([np["productId"] for np in newPackages])))

			def in_installation_window(start_str, end_str):
				now = datetime.datetime.now().time()
				start = datetime.time(int(start_str.split(":")[0]), int(start_str.split(":")[1]))
				end = datetime.time(int(end_str.split(":")[0]), int(end_str.split(":")[1]))

				logger.debug("Installation window configuration: start=%s, end=%s, now=%s", start, end, now)

				in_window = False
				if start <= end:
					in_window = start <= now <= end
				else:
					# Crosses midnight
					in_window = now >= start or now <= end

				if in_window:
					logger.info("Current time %s is within the configured installation window (%s-%s)", now, start, end)
					return True

				logger.info("Current time %s is outside the configured installation window (%s-%s)", now, start, end)
				return False

			insideInstallWindow = True
			# Times have to be specified in the form HH:MM, i.e. 06:30
			if not self.config["installationWindowStartTime"] or not self.config["installationWindowEndTime"]:
				logger.info("Installation time window is not defined, installing products and setting actions")
			elif in_installation_window(self.config["installationWindowStartTime"], self.config["installationWindowEndTime"]):
				logger.notice("Running inside installation time window, installing products and setting actions")
			else:
				logger.notice(
					"Running outside installation time window, not installing products except product ids %s",
					self.config["installationWindowExceptions"],
				)
				insideInstallWindow = False

			sequence = []
			for package in newPackages:
				if not insideInstallWindow and package["productId"] not in self.config["installationWindowExceptions"]:
					continue
				sequence.append(package["productId"])

			for package in newPackages:
				if package["productId"] not in sequence:
					continue
				packageFile = os.path.join(self.config["packageDir"], package["filename"])
				productId = package["productId"]
				opsi_package = OpsiPackage(
					Path(packageFile), temp_dir=Path(self.config.get("tempdir")) if self.config.get("tempdir") else None
				)
				for dependency in opsi_package.package_dependencies:
					self.check_dependency_sequence(sequence, productId, dependency.package)
				for prod_dependency in opsi_package.product_dependencies:
					self.check_dependency_sequence(sequence, productId, prod_dependency.requiredProductId)

			sortedPackages: list[dict[str, str | ProductRepositoryInfo]] = []
			for productId in sequence:
				for package in newPackages:
					if productId == package["productId"]:
						sortedPackages.append(package)
						break
			newPackages = sortedPackages

			backend = self.getConfigBackend()
			depotBackend = self.getDepotBackend()
			installedPackages = []
			for package in newPackages:
				packageFile = os.path.join(self.config["packageDir"], package["filename"])

				if package["repository"].onlyDownload:
					logger.debug("Download only is set for repository, not installing package '%s'", packageFile)
					continue

				try:
					propertyDefaultValues = {}
					try:
						if package["repository"].inheritProductProperties and package["repository"].opsiDepotId:
							logger.info("Trying to get product property defaults from repository")
							productPropertyStates = backend.productPropertyState_getObjects(  # pylint: disable=no-member
								productId=package["productId"], objectId=package["repository"].opsiDepotId
							)
						else:
							productPropertyStates = backend.productPropertyState_getObjects(  # pylint: disable=no-member
								productId=package["productId"], objectId=self.depotId
							)
						if productPropertyStates:
							for pps in productPropertyStates:
								propertyDefaultValues[pps.propertyId] = pps.values
						logger.notice("Using product property defaults: %s", propertyDefaultValues)
					except Exception as err:  # pylint: disable=broad-except
						logger.warning("Failed to get product property defaults: %s", err)

					logger.info("Installing package '%s'", packageFile)
					depotBackend.depot_installPackage(  # pylint: disable=no-member
						filename=packageFile, propertyDefaultValues=propertyDefaultValues, tempDir=self.config.get("tempdir", "/tmp")
					)
					productOnDepots = backend.productOnDepot_getObjects(  # pylint: disable=no-member
						depotId=self.depotId, productId=package["productId"]
					)
					if not productOnDepots:
						raise ValueError(f"Product {package['productId']!r} not found on depot '{self.depotId}' after installation")
					package["product"] = backend.product_getObjects(  # pylint: disable=no-member
						id=productOnDepots[0].productId,
						productVersion=productOnDepots[0].productVersion,
						packageVersion=productOnDepots[0].packageVersion,
					)[0]

					message = f"Package '{packageFile}' successfully installed"
					notifier.appendLine(message, pre="\n")
					logger.notice(message)
					installedPackages.append(package)

				except Exception as err:  # pylint: disable=broad-except
					if not self.config.get("ignoreErrors"):
						raise
					logger.error("Ignoring error for package %s: %s", package["productId"], err, exc_info=True)
					notifier.appendLine(f"Ignoring error for package {package['productId']}: {err}")

			if not installedPackages:
				logger.notice("No new packages installed")
				return

			logger.debug("Mark redis product cache as dirty for depot: %s", self.depotId)
			config_id = f"opsiconfd.{self.depotId}.product.cache.outdated"
			backend.config_createBool(id=config_id, description="", defaultValues=[True])  # pylint: disable=no-member

			shutdownProduct = None
			if self.config["wolAction"] and self.config["wolShutdownWanted"]:
				try:
					shutdownProduct = backend.productOnDepot_getObjects(  # pylint: disable=no-member
						depotId=self.depotId, productId="shutdownwanted"
					)[
						0
					]
					logger.info("Found 'shutdownwanted' product on depot '%s': %s", self.depotId, shutdownProduct)
				except IndexError:
					logger.error("Product 'shutdownwanted' not avaliable on depot '%s'", self.depotId)

			wakeOnLanClients = set()
			for package in installedPackages:
				if not package["product"].setupScript:
					continue
				if package["repository"].autoSetup:
					if isinstance(package["product"], NetbootProduct):
						logger.info(
							"Not setting action 'setup' for product '%s' where installation status 'installed' "
							"because auto setup is not allowed for netboot products",
							package["productId"],
						)
						continue
					if package["productId"].startswith(
						("opsi-local-image-", "opsi-uefi-", "opsi-vhd-", "opsi-wim-", "windows10-upgrade", "opsi-auto-update", "windomain")
					):
						logger.info(
							"Not setting action 'setup' for product '%s' where installation status 'installed' "
							"because auto setup is not allowed for opsi module products",
							package["productId"],
						)
						continue

					if any(exclude.search(package["productId"]) for exclude in package["repository"].autoSetupExcludes):
						logger.info(
							"Not setting action 'setup' for product '%s' because it's excluded by regular expression", package["productId"]
						)
						continue

					logger.notice(
						"Setting action 'setup' for product '%s' where installation status 'installed' "
						"because auto setup is set for repository '%s'",
						package["productId"],
						package["repository"].name,
					)
				else:
					logger.info(
						"Not setting action 'setup' for product '%s' where installation status 'installed' "
						"because auto setup is not set for repository '%s'",
						package["productId"],
						package["repository"].name,
					)
					continue

				clientToDepotserver = backend.configState_getClientToDepotserver(depotIds=[self.depotId])  # pylint: disable=no-member
				clientIds = set(ctd["clientId"] for ctd in clientToDepotserver if ctd["clientId"])

				if clientIds:
					productOnClients = backend.productOnClient_getObjects(  # pylint: disable=no-member
						attributes=["installationStatus"],
						productId=package["productId"],
						productType="LocalbootProduct",
						clientId=clientIds,
						installationStatus=["installed"],
					)
					if productOnClients:
						wolEnabled = self.config["wolAction"]
						excludedWolProducts = set(self.config["wolActionExcludeProductIds"])

						for poc in productOnClients:
							poc.setActionRequest("setup")
							if wolEnabled and package["productId"] not in excludedWolProducts:
								wakeOnLanClients.add(poc.clientId)

						backend.productOnClient_updateObjects(productOnClients)  # pylint: disable=no-member
						notifier.appendLine(
							(
								f"Product {package['productId']} set to 'setup' on clients: "
								", ".join(sorted(poc.clientId for poc in productOnClients))
							)
						)

			if wakeOnLanClients:
				logger.notice("Powering on clients %s", wakeOnLanClients)
				notifier.appendLine(f"Powering on clients: {', '.join(sorted(wakeOnLanClients))}")

				for clientId in wakeOnLanClients:
					try:
						logger.info("Powering on client '%s'", clientId)
						if self.config["wolShutdownWanted"] and shutdownProduct:
							logger.info("Setting shutdownwanted to 'setup' for client '%s'", clientId)

							backend.productOnClient_updateObjects(  # pylint: disable=no-member
								[
									ProductOnClient(
										productId=shutdownProduct.productId,
										productType=shutdownProduct.productType,
										productVersion=shutdownProduct.productVersion,
										packageVersion=shutdownProduct.packageVersion,
										clientId=clientId,
										actionRequest="setup",
									)
								]
							)
						backend.hostControl_start([clientId])  # pylint: disable=no-member
						time.sleep(self.config["wolStartGap"])
					except Exception as err:  # pylint: disable=broad-except
						logger.error("Failed to power on client '%s': %s", clientId, err)
		except Exception as err:  # pylint: disable=broad-except
			notifier.appendLine(f"Error occurred: {err}")
			notifier.setSubject(f"ERROR {self.config['subject']}")
			raise
		finally:
			if notifier and notifier.hasMessage():
				notifier.notify()

	def _getNotifier(self) -> BaseNotifier:
		if not self.config["notification"]:
			return DummyNotifier()

		logger.info("E-Mail notification is activated")
		notifier = EmailNotifier(
			smtphost=self.config["smtphost"],
			smtpport=self.config["smtpport"],
			sender=self.config["sender"],
			receivers=self.config["receivers"],
			subject=self.config["subject"],
		)

		if self.config["use_starttls"]:
			notifier.useStarttls = self.config["use_starttls"]

		if self.config["smtpuser"] and self.config["smtppassword"] is not None:
			notifier.username = self.config["smtpuser"]
			notifier.password = self.config["smtppassword"]

		return notifier

	def _filterProducts(self, products: list[dict[str, str | ProductRepositoryInfo]]) -> list[dict[str, str | ProductRepositoryInfo]]:
		if self.config["processProductIds"]:
			# Checking if given productIds are available and
			# process only these products
			newProductList = []
			for product in self.config["processProductIds"]:
				for pac in products:
					if product == pac["productId"]:
						newProductList.append(pac)
						break
				else:
					logger.error("Product '%s' not found in repository!", product)
					possibleProductIDs = sorted(set(pac["productId"] for pac in products))
					logger.notice("Possible products are: %s", ", ".join(possibleProductIDs))
					raise ValueError(f"You have searched for a product, which was not found in configured repository: '{product}'")

			if newProductList:
				return newProductList

		return products

	def _verifyDownloadedPackage(self, packageFile: str, availablePackage: dict[str, str | ProductRepositoryInfo]) -> bool:
		"""
		Verify the downloaded package.

		This checks the hashsums of the downloaded package.

		:param packageFile: The path to the package that is checked.
		:type packageFile: str
		:param availablePackage: Information about the package.
		:type availablePackage: dict
		"""

		logger.info("Verifying download of package '%s'", packageFile)
		if not availablePackage["md5sum"]:
			logger.warning("%s: Cannot verify download of package: missing md5sum file", availablePackage["productId"])
			return True

		md5 = md5sum(packageFile)
		if md5 != availablePackage["md5sum"]:
			logger.info("%s: md5sum mismatch, package download failed", availablePackage["productId"])
			return False

		logger.info("%s: md5sum match, package download verified", availablePackage["productId"])
		return True

	def get_installed_package(
		self, availablePackage: dict[str, str | ProductRepositoryInfo], installedProducts: list[ProductOnDepot]
	) -> ProductOnDepot | None:
		logger.info("Testing if download/installation of package '%s' is needed", availablePackage["filename"])
		for product in installedProducts:
			if product.productId == availablePackage["productId"]:
				logger.debug("Product '%s' is installed", availablePackage["productId"])
				logger.debug(
					"Available product version is '%s', installed product version is '%s-%s'",
					availablePackage["version"],
					product.productVersion,
					product.packageVersion,
				)
				return product
		return None

	def get_local_package(
		self, availablePackage: dict[str, str | ProductRepositoryInfo], localPackages: list[dict[str, str]]
	) -> dict[str, str] | None:
		for localPackage in localPackages:
			if localPackage["productId"] == availablePackage["productId"]:
				logger.debug("Found local package file '%s'", localPackage["filename"])
				return localPackage
		return None

	def is_download_needed(
		self,
		localPackageFound: dict[str, str],
		availablePackage: dict[str, str | ProductRepositoryInfo],
		notifier: BaseNotifier | None = None,
	) -> bool:
		if (
			localPackageFound
			and localPackageFound["filename"] == availablePackage["filename"]
			and localPackageFound["md5sum"] == availablePackage["md5sum"]
		):
			# Recalculate md5sum
			localPackageFound["md5sum"] = md5sum(localPackageFound["packageFile"])
			if localPackageFound["md5sum"] == availablePackage["md5sum"]:
				logger.info(
					"%s - download of package is not required: found local package %s with matching md5sum",
					availablePackage["filename"],
					localPackageFound["filename"],
				)
				# No notifier message as nothing to do
				return False

		if self.config["forceDownload"]:
			message = f"{availablePackage['filename']} - download of package is forced."
		elif localPackageFound:
			message = (
				f"{availablePackage['filename']} - download of package is required: "
				f"found local package {localPackageFound['filename']} which differs from available"
			)
		else:
			message = f"{availablePackage['filename']} - download of package is required: local package not found"

		logger.notice(message)
		if notifier is not None:
			notifier.appendLine(message)
		return True

	def is_install_needed(self, availablePackage: dict[str, str | ProductRepositoryInfo], product: ProductOnDepot) -> bool:
		if not product:
			if availablePackage["repository"].autoInstall:
				logger.notice(
					"%s - installation required: product '%s' is not installed and auto install is set for repository '%s'",
					availablePackage["filename"],
					availablePackage["productId"],
					availablePackage["repository"].name,
				)
				return True
			logger.info(
				"%s - installation not required: product '%s' is not installed but auto install is not set for repository '%s'",
				availablePackage["filename"],
				availablePackage["productId"],
				availablePackage["repository"].name,
			)
			return False

		if compareVersions(availablePackage["version"], ">", f"{product.productVersion}-{product.packageVersion}"):
			if availablePackage["repository"].autoUpdate:
				logger.notice(
					"%s - installation required: a more recent version of product '%s' was found"
					" (installed: %s-%s, available: %s) and auto update is set for repository '%s'",
					availablePackage["filename"],
					availablePackage["productId"],
					product.productVersion,
					product.packageVersion,
					availablePackage["version"],
					availablePackage["repository"].name,
				)
				return True
			logger.info(
				"%s - installation not required: a more recent version of product '%s' was found"
				" (installed: %s-%s, available: %s) but auto update is not set for repository '%s'",
				availablePackage["filename"],
				availablePackage["productId"],
				product.productVersion,
				product.packageVersion,
				availablePackage["version"],
				availablePackage["repository"].name,
			)
			return False
		logger.info(
			"%s - installation not required: installed version '%s-%s' of product '%s' is up to date",
			availablePackage["filename"],
			product.productVersion,
			product.packageVersion,
			availablePackage["productId"],
		)
		return False

	def get_packages(  # pylint: disable=too-many-locals
		self, notifier: BaseNotifier, all_packages: bool = False
	) -> list[dict[str, str | ProductRepositoryInfo]]:
		installedProducts = self.getInstalledProducts()
		localPackages = self.getLocalPackages()
		pack_per_repo: dict[ProductRepositoryInfo, list[dict[str, str | ProductRepositoryInfo]]] = self.get_new_packages_per_repository()
		newPackages: list[dict[str, str | ProductRepositoryInfo]] = []
		if not any(pack_per_repo.values()):
			logger.warning("No downloadable packages found")
			return newPackages

		for repository, downloadablePackages in pack_per_repo.items():
			logger.debug("Processing downloadable packages on repository %s", repository)
			with self.makeSession(repository) as session:
				for availablePackage in downloadablePackages:
					logger.debug("Processing available package %s", availablePackage)
					try:
						# This ís called to keep the logs consistent
						product = self.get_installed_package(availablePackage, installedProducts)
						if not all_packages and not self.is_install_needed(availablePackage, product):
							continue

						localPackageFound = self.get_local_package(availablePackage, localPackages)
						zsync = self._useZsync(session, availablePackage, localPackageFound)
						if self.is_download_needed(localPackageFound, availablePackage, notifier=notifier):
							self.get_package(availablePackage, localPackageFound, session, zsync=zsync, notifier=notifier)
						packageFile = os.path.join(self.config["packageDir"], availablePackage["filename"])
						verified = self._verifyDownloadedPackage(packageFile, availablePackage)
						if not verified and zsync:
							logger.info("%s: zsync download has failed, trying full download", availablePackage["productId"])
							self.get_package(availablePackage, localPackageFound, session, zsync=False, notifier=notifier)
							verified = self._verifyDownloadedPackage(packageFile, availablePackage)
						if not verified:
							raise HashsumMissmatchError(f"{availablePackage['productId']}: md5sum mismatch")
						self.cleanupPackages(availablePackage)
						newPackages.append(availablePackage)
					except Exception as exc:  # pylint: disable=broad-except
						if self.config.get("ignoreErrors"):
							logger.error("Ignoring Error for package %s: %s", availablePackage["productId"], exc, exc_info=True)
							notifier.appendLine(f"Ignoring Error for package {availablePackage['productId']}: {exc}")
						else:
							raise exc
		return newPackages

	def get_package(  # pylint: disable=too-many-arguments
		self,
		availablePackage: dict[str, str | ProductRepositoryInfo],
		localPackageFound: dict[str, str],
		session: Session,
		notifier: BaseNotifier | None = None,
		zsync: bool = True,
	) -> None:
		packageFile = os.path.join(self.config["packageDir"], availablePackage["filename"])
		if zsync:
			if localPackageFound["filename"] != availablePackage["filename"]:
				os.rename(os.path.join(self.config["packageDir"], localPackageFound["filename"]), packageFile)
				localPackageFound["filename"] = availablePackage["filename"]

			message = None
			try:
				self.zsyncPackage(availablePackage, packageFile, session)
				message = f"Zsync of {availablePackage['packageFile']!r} completed"
				logger.info(message)
			except Exception as err:  # pylint: disable=broad-except
				if str(err) == "Aborted by progress callback":
					logger.info("Zsync aborted")
				else:
					logger.error("Zsync of %r failed: %s", availablePackage["packageFile"], err, exc_info=True)

			if notifier and message:
				notifier.appendLine(message)
		else:
			self.downloadPackage(availablePackage, session, notifier=notifier)

	def downloadPackages(self) -> None:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
		if not any(self.getActiveRepositories()):
			logger.warning("No repositories configured, nothing to do")
			return

		notifier = self._getNotifier()
		try:
			newPackages = self.get_packages(notifier, all_packages=True)
			if not newPackages:
				logger.notice("No new packages downloaded")
				return
		except Exception as err:  # pylint: disable=broad-except
			notifier.appendLine(f"Error occurred: {err}")
			if isinstance(notifier, EmailNotifier):
				notifier.setSubject(f"ERROR {self.config['subject']}")
			raise
		finally:
			if notifier and notifier.hasMessage():
				notifier.notify()

	def zsyncPackage(  # pylint: disable=invalid-name
		self, availablePackage: dict[str, str | ProductRepositoryInfo], packageFile: str, session: Session
	) -> None:
		package_file = Path(packageFile)
		# raise Exception("Not implemented")
		logger.info("Zsyncing %s to %s", availablePackage["packageFile"], package_file)

		if not package_file.exists():
			raise FileNotFoundError(f"Package file {package_file} not found")

		url = availablePackage["zsyncFile"]
		logger.info("Fetching zsync file %s", url)
		response = session.get(url, headers=self.httpHeaders, stream=True, timeout=1800)  # 30 minutes timeout
		if response.status_code < 200 or response.status_code > 299:
			logger.error("Failed to fetch zsync file from %s: %s - %s", url, response.status_code, response.text)
			raise ConnectionError(f"Failed to fetch zsync file from {url}: {response.status_code} - {response.text}")

		zsync_file = package_file.with_name(f"{package_file.name}.zsync-download")
		with zsync_file.open("wb") as file:
			for chunk in response.iter_content(chunk_size=32768):
				file.write(chunk)
		logger.debug("Zsync file '%s' downloaded", zsync_file)

		zsync_file_info = read_zsync_file(zsync_file)
		zsync_file.unlink()

		files = [package_file] + list(package_file.parent.glob(f"{package_file.name}.zsync-tmp*"))
		logger.info("Analyzing local files %r", files)

		ap_last_time = time.time()
		ap_last_position = 0
		ap_per_second = 0

		def progress_callback(pos: int, total: int) -> bool:  # pylint: disable=unused-argument
			nonlocal ap_last_time, ap_last_position, ap_per_second
			now = time.time()
			elapsed = now - ap_last_time
			per_second = (pos - ap_last_position) / elapsed if elapsed else 0.0
			ap_per_second = int(ap_per_second * 0.7 + per_second * 0.3)
			ap_last_time = now
			ap_last_position = pos
			logger.debug("Local file analyze speed: %0.3f MB/s", ap_per_second / 1_000_000)
			# Check after 5MB if analyze speed is >= 1 MB/s (1.000.000 B/s)
			if pos >= 5_000_000 and ap_per_second < 1_000_000:  # pylint: disable=chained-comparison
				logger.info(
					"Your system is too slow (%0.3f MB/s) to analyze local files in time, aborting zsync at position %d.",
					ap_per_second / 1_000_000,
					pos,
				)
				return True
			return False

		instructions = get_patch_instructions(zsync_file_info, files, optimized=True, progress_callback=progress_callback)
		remote_bytes = sum([i.size for i in instructions if i.source == SOURCE_REMOTE])  # pylint: disable=consider-using-generator
		speedup = (zsync_file_info.length - remote_bytes) * 100 / zsync_file_info.length
		logger.info("Need to fetch %d/%d bytes from remote, speedup is %0.1f%%", remote_bytes, zsync_file_info.length, speedup)

		class LoggingProgressListener(ProgressListener):  # pylint: disable=too-few-public-methods
			def __init__(self) -> None:
				self.last_completed = 0

			def progress_changed(self, patcher: RequestsHTTPPatcher, position: int, total: int, per_second: int) -> None:
				completed = round(position * 100 / total)
				if completed == self.last_completed:
					return
				self.last_completed = completed
				logger.info(
					"Zsyncing %r: %s%% - %0.2f/%0.2f MB - %0.f kB/s",
					patcher.url,
					completed,
					position / 1_000_000,
					total / 1_000_000,
					per_second / 1_000,
				)

		def patcher_factory(instructions: list[PatchInstruction], target_file: BinaryIO) -> RequestsHTTPPatcher:
			url = availablePackage["packageFile"]
			logger.info("Fetching ranges from %s", url)
			patcher = RequestsHTTPPatcher(
				session=session, url=url, instructions=instructions, target_file=target_file, headers=self.httpHeaders
			)
			patcher.register_progress_listener(LoggingProgressListener())
			return patcher

		sha1_digest = patch_file(files, instructions, patcher_factory=patcher_factory)

		if sha1_digest != zsync_file_info.sha1:
			raise RuntimeError("Failed to patch file, SHA-1 mismatch")

	def downloadPackage(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
		self, availablePackage: dict[str, str | ProductRepositoryInfo], session: Session, notifier: BaseNotifier | None = None
	) -> Response:
		url = availablePackage["packageFile"]
		outFile = os.path.join(self.config["packageDir"], availablePackage["filename"])

		headers = self.httpHeaders.copy()
		headers["Accept-Encoding"] = "identity"
		response = session.get(url, headers=headers, stream=True, timeout=3600 * 8)  # 8h timeout
		if response.status_code < 200 or response.status_code > 299:
			logger.error("Failed to download Package from %r: %s - %s", url, response.status_code, response.text)
			raise RuntimeError(f"Failed to download Package from {url!r}: {response.status_code} - {response.text}")

		size = int(response.headers["Content-Length"])
		logger.info("Downloading %r (%0.2f MB) to %s", url, size / (1_000_000), outFile)

		position = 0
		percent = 0.0
		last_time = time.time()
		last_position = 0
		last_percent = 0
		speed = 0

		with open(outFile, "wb") as out:
			for chunk in response.iter_content(chunk_size=32768):
				position += len(chunk)
				out.write(chunk)
				percent = int(position * 100 / size)
				if last_percent != percent:
					last_percent = percent
					now = time.time()
					if not speed or now - last_time > 2:
						speed = 8 * int(((position - last_position) / (now - last_time)) / 1000)
						last_time = now
						last_position = position
					logger.info("Downloading %r: %d%% (%0.2f kbit/s)", url, percent, speed)
			if size != position:
				raise RuntimeError(f"Failed to complete download, only {position} of {size} bytes transferred")

		message = f"Download of {url!r} completed (~{formatFileSize(size, base=10)})"
		logger.info(message)
		if notifier:
			notifier.appendLine(message)

	def cleanupPackages(self, newPackage: dict[str, str | ProductRepositoryInfo]) -> None:
		logger.info("Cleaning up in %s", self.config["packageDir"])

		try:
			set_rights(self.config["packageDir"])
		except Exception as err:  # pylint: disable=broad-except
			logger.warning("Failed to set rights on directory '%s': %s", self.config["packageDir"], err)

		for filename in os.listdir(self.config["packageDir"]):
			path = os.path.join(self.config["packageDir"], filename)
			if not os.path.isfile(path):
				continue
			if path.endswith(".zs-old") or path.endswith(".zsync-download"):
				os.unlink(path)
				continue

			try:
				productId, version = parseFilename(filename)
			except Exception as err:  # pylint: disable=broad-except
				logger.debug("Parsing '%s' failed: '%s'", filename, err)
				continue

			if productId == newPackage["productId"] and version != newPackage["version"]:
				logger.info("Deleting obsolete package file '%s'", path)
				os.unlink(path)

		packageFile = os.path.join(self.config["packageDir"], newPackage["filename"])

		md5sumFile = f"{packageFile}.md5"
		logger.info("Creating md5sum file '%s'", md5sumFile)

		with open(md5sumFile, mode="w", encoding="utf-8") as hashFile:
			hashFile.write(md5sum(packageFile))
		set_rights(md5sumFile)

		zsyncFile = f"{packageFile}.zsync"
		logger.info("Creating zsync file '%s'", zsyncFile)
		try:
			create_zsync_file(packageFile, zsyncFile, legacy_mode=True)
		except Exception as err:  # pylint: disable=broad-except
			logger.error("Failed to create zsync file '%s': %s", zsyncFile, err)
		set_rights(zsyncFile)

	def onlyNewestPackages(self, packages: list[dict[str, str | ProductRepositoryInfo]]) -> list[dict[str, str | ProductRepositoryInfo]]:
		newestPackages: list[dict[str, str | ProductRepositoryInfo]] = []
		for package in packages:
			found = None
			for i, newPackage in enumerate(newestPackages):
				if newPackage["productId"] == package["productId"]:
					found = i
					if compareVersions(package["version"], ">", newestPackages[i]["version"]):
						logger.debug("Package version '%s' is newer than version '%s'", package["version"], newestPackages[i]["version"])
						newestPackages[i] = package
					break

			if found is None:
				newestPackages.append(package)

		return newestPackages

	def getLocalPackages(self) -> list[dict[str, str]]:
		return getLocalPackages(self.config["packageDir"], forceChecksumCalculation=self.config["forceChecksumCalculation"])

	def getInstalledProducts(self) -> list[ProductOnDepot]:
		logger.info("Getting installed products")
		products = []
		configBackend = self.getConfigBackend()
		for product in configBackend.productOnDepot_getObjects(depotId=self.depotId):  # pylint: disable=no-member
			logger.info("Found installed product '%s_%s-%s'", product.productId, product.productVersion, product.packageVersion)
			products.append(product)
		return products

	def getDownloadablePackages(self) -> list[dict[str, str | ProductRepositoryInfo]]:
		downloadablePackages = []
		for repository in self.getActiveRepositories():
			logger.info("Getting package infos from repository '%s' (%s)", repository.name, repository.baseUrl)
			for package in self.getDownloadablePackagesFromRepository(repository):
				downloadablePackages.append(package)
		return downloadablePackages

	def read_repository_metafile(
		self, repository: ProductRepositoryInfo, data: bytes
	) -> list[dict[str, str | ProductRepositoryInfo | None]]:
		packages: list[dict[str, str | ProductRepositoryInfo | None]] = []
		filter_dirs = {PurePosixPath(d.lstrip("/").lstrip(".").rstrip("/")) for d in repository.dirs}
		# is_relative_to
		col = RepoMetaPackageCollection()
		col.read_metafile_data(data)
		for package in col.get_packages():
			package_urls = package.url if isinstance(package.url, list) else [package.url]
			package_zsync_urls = package.zsync_url if isinstance(package.zsync_url, list) else [package.zsync_url]
			selected_path = None
			selected_zsync_path = None
			for path, zsync_path in zip(package_urls, package_zsync_urls):
				if any(PurePosixPath(path).is_relative_to(fdir) for fdir in filter_dirs):
					selected_path = PurePosixPath(path)
					selected_zsync_path = PurePosixPath(zsync_path) if zsync_path else None
					break
			else:
				logger.debug("Skipping package: %s", package_urls)
				continue
			logger.info("Found opsi package: %s", selected_path)
			pdict: dict[str, str | ProductRepositoryInfo | None] = {
				"repository": repository,
				"productId": package.product_id,
				"version": package.version,
				"packageFile": f"{repository.baseUrl}/{selected_path}",
				"filename": selected_path.name,
				"md5sum": package.md5_hash,
				"zsyncFile": f"{repository.baseUrl}/{selected_zsync_path}" if selected_zsync_path else None,
			}
			packages.append(pdict)
		return packages

	def fetch_repository_metafile(self, session: Session, url: str) -> list[dict[str, str | ProductRepositoryInfo]] | None:
		if url not in self.metafile_cache:
			logger.info("Trying to fetch repository metafile: %s", url)
			response = session.get(url)
			if response.status_code == 200:
				logger.notice("Repository metafile successfully fetched: %s", url)
				self.metafile_cache[url] = response.content
			else:
				self.metafile_cache[url] = None
		return self.metafile_cache[url]

	def getDownloadablePackagesFromRepository(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements,invalid-name
		self, repository: ProductRepositoryInfo
	) -> list[dict[str, str | ProductRepositoryInfo]]:
		with self.makeSession(repository) as session:
			for meta_file in ("packages.msgpack.zstd", "packages.json", "packages.msgpack", "packages.json.zstd"):
				data = self.fetch_repository_metafile(session, f"{repository.baseUrl}/{meta_file}")
				if data is not None:
					# None = repository metafile missing, b"" = repository metafile empty
					if data:
						return self.read_repository_metafile(repository, data)
					break

			logger.info("No repository metafile found in repository: %s", repository.baseUrl)

			packages = []
			errors = set()

			for url in repository.getDownloadUrls():  # pylint: disable=too-many-nested-blocks
				try:
					url = quote(url.encode("utf-8"), safe="/#%[]=:;$&()+,!?*@'~")
					response = session.get(url, headers=self.httpHeaders)
					content = response.content.decode("utf-8")
					logger.debug("content: '%s'", content)

					htmlParser = LinksExtractor()
					htmlParser.feed(content)
					htmlParser.close()
					for link in htmlParser.getLinks():
						if not link.endswith(".opsi"):
							continue

						if link.startswith("/"):
							# absolute link to relative link
							path = "/" + url.split("/", 3)[-1]
							rlink = link[len(path) :].lstrip("/")
							logger.info("Absolute link: '%s', relative link: '%s'", link, rlink)
							link = rlink

						if repository.includes:
							if not any(include.search(link) for include in repository.includes):
								logger.info(
									"Package '%s' is not included. Please check your includeProductIds-entry in configurationfile.", link
								)
								continue

						if any(exclude.search(link) for exclude in repository.excludes):
							logger.info("Package '%s' excluded by regular expression", link)
							continue

						try:
							productId, version = parseFilename(link)
							packageFile = url.rstrip("/") + "/" + link.lstrip("/")
							logger.info("Found opsi package: %s", packageFile)
							packageInfo = {
								"repository": repository,
								"productId": forceProductId(productId),
								"version": version,
								"packageFile": packageFile,
								"filename": link,
								"md5sum": None,
								"zsyncFile": None,
							}
							logger.debug("Repository package info: %s", packageInfo)
							packages.append(packageInfo)
						except Exception as err:  # pylint: disable=broad-except
							logger.error("Failed to process link '%s': %s", link, err)

					for link in htmlParser.getLinks():
						isMd5 = link.endswith(".opsi.md5")
						isZsync = link.endswith(".opsi.zsync")

						# stripping directory part from link
						link = link.split("/")[-1]

						filename = None
						if isMd5:
							filename = link[:-4]
						elif isZsync:
							filename = link[:-6]
						else:
							continue

						try:
							for i, package in enumerate(packages):
								if package.get("filename") == filename:
									if isMd5:
										response = session.get(f"{url.rstrip('/')}/{link.lstrip('/')}")
										match = re.search(r"([a-z\d]{32})", response.content.decode("utf-8"))
										if match:
											foundMd5sum = match.group(1)
											packages[i]["md5sum"] = foundMd5sum
											logger.debug("Got md5sum for package %s: %s", filename, foundMd5sum)
									elif isZsync:
										zsyncFile = f"{url.rstrip('/')}/{link.lstrip('/')}"
										packages[i]["zsyncFile"] = zsyncFile
										logger.debug("Found zsync file for package '%s': %s", filename, zsyncFile)

									break
						except Exception as err:  # pylint: disable=broad-except
							logger.error("Failed to process link '%s': %s", link, err)
				except Exception as err:  # pylint: disable=broad-except
					logger.debug(err, exc_info=True)
					self.errors.append(err)
					errors.add(str(err))

			if errors:
				logger.warning("Problems processing repository %s: %s", repository.name, "; ".join(str(e) for e in errors))

			return packages

	@contextmanager
	def makeSession(self, repository: ProductRepositoryInfo) -> Session:
		logger.info("Opening session for repository '%s' (%s)", repository.name, repository.baseUrl)
		try:
			no_proxy_addresses = ["localhost", "127.0.0.1", "ip6-localhost", "::1"]
			session = prepare_proxy_environment(repository.baseUrl, repository.proxy, no_proxy_addresses=no_proxy_addresses)

			if os.path.exists(repository.authcertfile) and os.path.exists(repository.authkeyfile):
				logger.debug("setting session.cert to %s %s", repository.authcertfile, repository.authkeyfile)
				session.cert = (repository.authcertfile, repository.authkeyfile)
			session.verify = repository.verifyCert
			session.auth = (repository.username, repository.password)
			logger.debug("Initiating session with verify=%s", repository.verifyCert)
			yield session
		finally:
			session.close()


def getLocalPackages(packageDirectory: str, forceChecksumCalculation: bool = False) -> list[dict[str, str]]:
	"""
	Show what packages are available in the given `packageDirectory`.

	This function will not traverse into any subdirectories.

	:param packageDirectory: The directory whose packages should be listed.
	:type packageDirectory: str
	:param forceChecksumCalculation: If this is `False` an existing \
`.md5` of a package will be used. If this is `True` then the checksum \
will be calculated for each package independent of the possible \
existance of a corresponding `.md5` file.
	:returns: Information about the found opsi packages. For each \
package there will be the following information: _productId_, \
_version_, _packageFile_ (complete path), _filename_ and _md5sum_.
	:rtype: [{}]
	"""
	logger.info("Getting info for local packages in '%s'", packageDirectory)

	packages = []
	for filename in os.listdir(packageDirectory):
		if not filename.endswith(".opsi"):
			continue

		packageFile = os.path.join(packageDirectory, filename)
		logger.info("Found local package '%s'", packageFile)
		try:
			productId, version = parseFilename(filename)
			checkSumFile = packageFile + ".md5"
			if not forceChecksumCalculation and os.path.exists(checkSumFile):
				logger.debug("Reading existing checksum from %s", checkSumFile)
				with open(checkSumFile, mode="r", encoding="utf-8") as hashFile:
					packageMd5 = hashFile.read().strip()
			else:
				logger.debug("Calculating checksum for %s", packageFile)
				packageMd5 = md5sum(packageFile)

			packageInfo = {
				"productId": forceProductId(productId),
				"version": version,
				"packageFile": packageFile,
				"filename": filename,
				"md5sum": packageMd5,
			}
			logger.debug("Local package info: %s", packageInfo)
			packages.append(packageInfo)
		except Exception as err:  # pylint: disable=broad-except
			logger.error("Failed to process file '%s': %s", filename, err)

	return packages
