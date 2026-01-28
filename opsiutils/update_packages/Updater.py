# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
Component for handling package updates.
"""
# pylint: disable=too-many-lines

from __future__ import annotations

import datetime
import os
import os.path
import re
import time
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import BinaryIO, Generator
from urllib.parse import quote, urlparse

from attr import dataclass
from cryptography import x509
from OPSI.Util import compareVersions, formatFileSize
from OPSI.Util.File.Opsi import parseFilename
from opsicommon.client.opsiservice import ServiceClient, get_service_client
from opsicommon.config.opsi import OpsiConfig
from opsicommon.logging import get_logger, secret_filter
from opsicommon.objects import NetbootProduct, Product, ProductOnClient, ProductOnDepot
from opsicommon.package import OpsiPackage
from opsicommon.package.repo_meta import RepoMetaPackageCollection
from opsicommon.server.rights import set_rights
from opsicommon.ssl import install_ca
from opsicommon.types import forceList, forceProductId, forceProductIdList, forceStringList
from opsicommon.utils import prepare_proxy_environment
from opsicommon.utils.hashing import compute_file_hash
from pyzsync import (
	SOURCE_REMOTE,
	CaseInsensitiveDict,
	HTTPPatcher,
	PatchInstruction,
	ProgressListener,
	create_zsync_file,
	get_patch_instructions,
	patch_file,
	read_zsync_file,
)
from requests import Response, Session
from requests.packages import urllib3  # type: ignore[import,attr-defined]

from opsiutils.update_packages.Config import DEFAULT_USER_AGENT, ConfigurationParser
from opsiutils.update_packages.Notifier import BaseNotifier, DummyNotifier, EmailNotifier
from opsiutils.update_packages.Repository import LinksExtractor, ProductRepositoryInfo, TransferSlotHeartbeat, sort_repository_list

urllib3.disable_warnings()

__all__ = ("OpsiPackageUpdater",)

logger = get_logger("opsi.general")


@dataclass(kw_only=True)
class LocalPackageInfo:
	package_file: Path
	product_id: str
	version: str

	@cached_property
	def md5_hash(self) -> str:
		return compute_file_hash(self.package_file, algorithm="md5")

	@cached_property
	def blake3_hash(self) -> str:
		return compute_file_hash(self.package_file, algorithm="blake3")


@dataclass(kw_only=True)
class RepositoryPackageInfo:
	repository: ProductRepositoryInfo
	package_file: str
	product_id: str
	version: str
	filename: str
	md5_hash: str | None = None
	blake3_hash: str | None = None
	zsync_file: str | None = None
	product: Product | None = None


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
		assert isinstance(self._response, Response) and self._response.raw
		return self._response.raw.read(size)


class OpsiPackageUpdater:
	def __init__(self, config: dict[str, str | int | bool | list[ProductRepositoryInfo] | None]) -> None:
		self.config = config
		self.httpHeaders = {"User-Agent": str(self.config.get("userAgent", DEFAULT_USER_AGENT))}
		self.configBackend: ServiceClient | None = None
		self.depotBackend: ServiceClient | None = None
		self.depotId = OpsiConfig().get("host", "id")
		self.depotServiceUrl = ""
		self.isConfigServer = OpsiConfig().get("host", "server-role") == "configserver"
		self.errors: list[Exception] = []
		self.metafile_cache: dict[str, bytes | None] = {}

		# Proxy is needed for getConfigBackend which is needed for ConfigurationParser.parse
		self.config["proxy"] = ConfigurationParser.get_proxy(str(self.config["configFile"]))

		depots = self.getConfigBackend().host_getObjects(type="OpsiDepotserver", id=self.depotId)  # type: ignore[attr-defined]
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

	def __enter__(self) -> OpsiPackageUpdater:
		return self

	def __exit__(self, type_: type[BaseException] | None, value: BaseException | None, traceback: TracebackType | None) -> bool | None:
		try:
			if self.configBackend:
				self.configBackend.backend_exit()  # type: ignore[attr-defined]
		except Exception:
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

		for repo in forceList(self.config.get("repositories", [])):
			if name and repo.name.strip().lower() != str(name).strip().lower():
				continue

			yield repo

	def readConfigFile(self) -> None:
		parser = ConfigurationParser(
			configFile=str(self.config["configFile"]),
			backend=self.getConfigBackend(),
			depotId=self.depotId,
			depotKey=self.depotKey,
		)
		self.config = parser.parse(self.config)

	def getConfigBackend(self) -> ServiceClient:
		if not self.configBackend:
			self.configBackend = get_service_client(
				proxy_url=str(self.config["proxy"] or ""),
				session_lifetime=30,
			)
			try:
				ca_crt = x509.load_pem_x509_certificate(data=self.configBackend.getOpsiCACert().encode("utf-8"))  # type: ignore[attr-defined]
				install_ca(ca_crt)
			except Exception as err:
				logger.info("Failed to update opsi CA: %s", err)
		return self.configBackend

	def getDepotBackend(self) -> ServiceClient:
		if self.isConfigServer:
			return self.getConfigBackend()

		if not self.depotBackend:
			self.depotBackend = get_service_client(
				address=self.depotServiceUrl,
				session_lifetime=30,
			)
		return self.depotBackend

	def filterPackages(self, packages: list[RepositoryPackageInfo]) -> list[RepositoryPackageInfo]:
		filteredPackages: list[RepositoryPackageInfo] = []
		for package in packages:
			repository = package.repository
			assert isinstance(repository, ProductRepositoryInfo)

			if repository.includes:
				if not any(include.search(package.product_id) for include in repository.includes):
					logger.info(
						"Package '%s' is not included. Please check your includeProductIds-entry in configurationfile.",
						package.product_id,
					)
					continue

			if any(exclude.search(package.product_id) for exclude in repository.excludes):
				logger.info("Package '%s' excluded by regular expression", package.product_id)
				continue

			filteredPackages.append(package)

		return filteredPackages

	def get_new_packages_per_repository(
		self,
	) -> dict[ProductRepositoryInfo, list[RepositoryPackageInfo]]:
		downloadable_packages = self.getDownloadablePackages()
		downloadable_packages = self.filterPackages(downloadable_packages)
		downloadable_packages = self.onlyNewestPackages(downloadable_packages)
		downloadable_packages = self._filterProducts(downloadable_packages)
		result: dict[ProductRepositoryInfo, list[RepositoryPackageInfo]] = {}
		for package in downloadable_packages:
			repository = package.repository
			if repository not in result:
				result[repository] = []
			result[repository].append(package)
		return result

	def _useZsync(
		self,
		session: Session,
		available_package: RepositoryPackageInfo,
		local_package: LocalPackageInfo | None,
	) -> bool:
		if not self.config["useZsync"]:
			return False
		if not local_package:
			logger.info("Cannot use zsync, no local package found")
			return False
		if not available_package.zsync_file:
			logger.info("Cannot use zsync, no zsync file on server found")
			return False

		response = session.head(available_package.package_file, headers=self.httpHeaders)
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
				if ppos < dpos:
					sequence.remove(dependency)
					sequence.insert(ppos, dependency)
					logger.debug("Changing order of packages to fulfill dependency requirement")
			except ValueError:
				logger.info("Dependency %s of package %s not in sequence.", dependency, productId)
		except Exception as err:
			logger.debug(
				"While processing package '%s', product_dependency '%s': %s",
				productId,
				dependency,
				err,
			)

	def processUpdates(self) -> None:
		if not any(self.getActiveRepositories()):
			logger.warning("No repositories configured, nothing to do")
			return

		notifier = self._getNotifier()
		try:
			new_packages = self.get_packages(notifier)
			if not new_packages:
				logger.notice("No new packages available")
				return

			logger.info(
				"New packages available: %s",
				", ".join(sorted([np.product_id for np in new_packages])),
			)

			def in_installation_window(start_str: str, end_str: str) -> bool:
				now = datetime.datetime.now().time()
				start = datetime.time(int(start_str.split(":")[0]), int(start_str.split(":")[1]))
				end = datetime.time(int(end_str.split(":")[0]), int(end_str.split(":")[1]))

				logger.debug(
					"Installation window configuration: start=%s, end=%s, now=%s",
					start,
					end,
					now,
				)

				in_window = False
				if start <= end:
					in_window = start <= now <= end
				else:
					# Crosses midnight
					in_window = now >= start or now <= end

				if in_window:
					logger.info(
						"Current time %s is within the configured installation window (%s-%s)",
						now,
						start,
						end,
					)
					return True

				logger.info(
					"Current time %s is outside the configured installation window (%s-%s)",
					now,
					start,
					end,
				)
				return False

			insideInstallWindow = True
			# Times have to be specified in the form HH:MM, i.e. 06:30
			if not self.config["installationWindowStartTime"] or not self.config["installationWindowEndTime"]:
				logger.info("Installation time window is not defined, installing products and setting actions")
			elif in_installation_window(
				str(self.config["installationWindowStartTime"]),
				str(self.config["installationWindowEndTime"]),
			):
				logger.notice("Running inside installation time window, installing products and setting actions")
			else:
				logger.notice(
					"Running outside installation time window, not installing products except product ids %s",
					self.config["installationWindowExceptions"],
				)
				insideInstallWindow = False

			sequence = []
			for package in new_packages:
				if not insideInstallWindow and package.product_id not in forceStringList(self.config["installationWindowExceptions"]):
					continue
				sequence.append(package.product_id)
			for package in new_packages:
				if package.product_id not in sequence:
					continue
				package_file = os.path.join(str(self.config["packageDir"]), package.filename)
				product_id = package.product_id
				opsi_package = OpsiPackage(
					Path(package_file),
					temp_dir=Path(str(self.config.get("tempdir"))) if self.config.get("tempdir") else None,
				)
				for dependency in opsi_package.package_dependencies:
					self.check_dependency_sequence(sequence, product_id, dependency.package)
				for prod_dependency in opsi_package.product_dependencies:
					self.check_dependency_sequence(sequence, product_id, prod_dependency.requiredProductId)

			sorted_packages: list[RepositoryPackageInfo] = []
			for product_id in sequence:
				for package in new_packages:
					if product_id == package.product_id:
						sorted_packages.append(package)
						break
			new_packages = sorted_packages

			backend = self.getConfigBackend()
			depot_backend = self.getDepotBackend()
			installed_packages: list[RepositoryPackageInfo] = []
			for package in new_packages:
				repository = package.repository
				assert isinstance(repository, ProductRepositoryInfo)
				package_file = os.path.join(str(self.config["packageDir"]), package.filename)

				if repository.onlyDownload:
					logger.debug(
						"Download only is set for repository, not installing package '%s'",
						package_file,
					)
					continue

				try:
					property_default_values = {}
					try:
						if repository.inheritProductProperties and repository.opsiDepotId:
							logger.info("Trying to get product property defaults from repository")
							productPropertyStates = backend.productPropertyState_getObjects(  # type: ignore[attr-defined]
								productId=package.product_id,
								objectId=repository.opsiDepotId,
							)
						else:
							productPropertyStates = backend.productPropertyState_getObjects(  # type: ignore[attr-defined]
								productId=package.product_id,
								objectId=self.depotId,
							)
						if productPropertyStates:
							for pps in productPropertyStates:
								property_default_values[pps.propertyId] = pps.values
						logger.notice("Using product property defaults: %s", property_default_values)
					except Exception as err:
						logger.warning("Failed to get product property defaults: %s", err)

					logger.info("Installing package '%s'", package_file)
					depot_backend.depot_installPackage(  # type: ignore[attr-defined]
						filename=package_file,
						propertyDefaultValues=property_default_values,
						tempDir=self.config.get("tempdir", "/tmp"),
					)
					productOnDepots = backend.productOnDepot_getObjects(depotId=self.depotId, productId=package["productId"])  # type: ignore[attr-defined]
					if not productOnDepots:
						raise ValueError(f"Product {package.product_id!r} not found on depot '{self.depotId}' after installation")
					package.product = backend.product_getObjects(  # type: ignore[attr-defined]
						id=productOnDepots[0].productId,
						productVersion=productOnDepots[0].productVersion,
						packageVersion=productOnDepots[0].packageVersion,
					)[0]

					message = f"Package '{package_file}' successfully installed"
					notifier.appendLine(message, pre="\n")
					logger.notice(message)
					installed_packages.append(package)

				except Exception as err:
					if not self.config.get("ignoreErrors"):
						raise
					logger.error(
						"Ignoring error for package %s: %s",
						package.product_id,
						err,
						exc_info=True,
					)
					notifier.appendLine(f"Ignoring error for package {package.product_id}: {err}")
			if not installed_packages:
				logger.notice("No new packages installed")
				return

			logger.debug("Mark redis product cache as dirty for depot: %s", self.depotId)
			config_id = f"opsiconfd.{self.depotId}.product.cache.outdated"
			backend.config_createBool(id=config_id, description="", defaultValues=[True])  # type: ignore[attr-defined]

			shutdownProduct = None
			if self.config["wolAction"] and self.config["wolShutdownWanted"]:
				try:
					shutdownProduct = backend.productOnDepot_getObjects(depotId=self.depotId, productId="shutdownwanted")[0]  # type: ignore[attr-defined]
					logger.info(
						"Found 'shutdownwanted' product on depot '%s': %s",
						self.depotId,
						shutdownProduct,
					)
				except IndexError:
					logger.error(
						"Product 'shutdownwanted' not avaliable on depot '%s'",
						self.depotId,
					)

			wakeOnLanClients: set[str] = set()
			for package in installed_packages:
				product = package.product
				assert isinstance(product, Product)

				if not product.setupScript:
					continue
				repository = package.repository
				assert isinstance(repository, ProductRepositoryInfo)

				if repository.autoSetup:
					if isinstance(product, NetbootProduct):
						logger.info(
							"Not setting action 'setup' for product '%s' where installation status 'installed' "
							"because auto setup is not allowed for netboot products",
							package.product_id,
						)
						continue
					if package.product_id.startswith(
						(
							"opsi-local-image-",
							"opsi-uefi-",
							"opsi-vhd-",
							"opsi-wim-",
							"windows10-upgrade",
							"opsi-auto-update",
							"windomain",
						)
					):
						logger.info(
							"Not setting action 'setup' for product '%s' where installation status 'installed' "
							"because auto setup is not allowed for opsi module products",
							package.product_id,
						)
						continue

					if any(exclude.search(package.product_id) for exclude in repository.autoSetupExcludes):
						logger.info(
							"Not setting action 'setup' for product '%s' because it's excluded by regular expression",
							package.product_id,
						)
						continue

					logger.notice(
						"Setting action 'setup' for product '%s' where installation status 'installed' "
						"because auto setup is set for repository '%s'",
						package.product_id,
						repository.name,
					)
				else:
					logger.info(
						"Not setting action 'setup' for product '%s' where installation status 'installed' "
						"because auto setup is not set for repository '%s'",
						package.product_id,
						repository.name,
					)
					continue

				clientToDepotserver = backend.configState_getClientToDepotserver(depotIds=[self.depotId])  # type: ignore[attr-defined]
				clientIds = set(ctd["clientId"] for ctd in clientToDepotserver if ctd["clientId"])

				if clientIds:
					productOnClients = backend.productOnClient_getObjects(  # type: ignore[attr-defined]
						attributes=["installationStatus"],
						productId=package.product_id,
						productType="LocalbootProduct",
						clientId=clientIds,
						installationStatus=["installed"],
					)
					if productOnClients:
						wolEnabled = self.config["wolAction"]
						excludedWolProducts = set(forceProductIdList(self.config["wolActionExcludeProductIds"]))

						for poc in productOnClients:
							poc.setActionRequest("setup")
							if wolEnabled and package.product_id not in excludedWolProducts:
								wakeOnLanClients.add(poc.clientId)

						backend.productOnClient_updateObjects(productOnClients)  # type: ignore[attr-defined]
						notifier.appendLine(
							(
								f"Product {package.product_id} set to 'setup' on clients: , ".join(
									sorted(poc.clientId for poc in productOnClients)
								)
							)
						)

			if wakeOnLanClients:
				logger.notice("Powering on clients %s", wakeOnLanClients)
				notifier.appendLine(f"Powering on clients: {', '.join(sorted(wakeOnLanClients))}")

				for clientId in wakeOnLanClients:
					try:
						logger.info("Powering on client '%s'", clientId)
						if self.config["wolShutdownWanted"] and shutdownProduct:
							logger.info(
								"Setting shutdownwanted to 'setup' for client '%s'",
								clientId,
							)

							backend.productOnClient_updateObjects(  # type: ignore[attr-defined]
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
						backend.hostControl_start([clientId])  # type: ignore[attr-defined]
						time.sleep(int(str(self.config["wolStartGap"])))
					except Exception as err:
						logger.error("Failed to power on client '%s': %s", clientId, err)
		except Exception as err:
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
			smtphost=str(self.config["smtphost"]),
			smtpport=int(str(self.config["smtpport"])),
			sender=str(self.config["sender"]),
			receivers=forceList(self.config["receivers"]),
			subject=str(self.config["subject"]),
		)

		if self.config["use_starttls"]:
			notifier.useStarttls = bool(self.config["use_starttls"])

		if self.config["smtpuser"] and self.config["smtppassword"] is not None:
			notifier.username = str(self.config["smtpuser"])
			notifier.password = str(self.config["smtppassword"])

		return notifier

	def _filterProducts(self, packages: list[RepositoryPackageInfo]) -> list[RepositoryPackageInfo]:
		if not self.config["processProductIds"]:
			return packages

		# Checking if given productIds are available and process only these products
		filtered_packages = []
		for product in forceProductIdList(self.config["processProductIds"]):
			matching_packages = [pac for pac in packages if product == pac.product_id]
			if matching_packages:
				filtered_packages.extend(matching_packages)
				continue

			logger.error("Product '%s' not found in repository!", product)
			possible_product_ids = sorted(set(pac.product_id for pac in packages))
			logger.notice("Possible products are: %s", ", ".join(possible_product_ids))
			raise ValueError(f"You have searched for a product, which was not found in configured repository: '{product}'")

		return filtered_packages

	def _verifyDownloadedPackage(self, packageFile: str, available_package: RepositoryPackageInfo) -> bool:
		"""
		Verify the downloaded package.

		This checks the hashsums of the downloaded package.

		:param packageFile: The path to the package that is checked.
		:type packageFile: str
		:param availablePackage: Information about the package.
		:type availablePackage: dict
		"""

		logger.info("Verifying download of package '%s'", packageFile)
		if not available_package.blake3_hash and not available_package.md5_hash:
			logger.warning(
				"%s: Cannot verify download of package: neither blake3 nor md5 hash available",
				available_package.product_id,
			)
			return True

		hash_algorithm = "blake3" if available_package.blake3_hash else "md5"
		expected_hash = available_package.blake3_hash if hash_algorithm == "blake3" else available_package.md5_hash
		computed_hash = compute_file_hash(Path(packageFile), algorithm=hash_algorithm)
		logger.debug("%s: computed %s hash: %s", available_package.product_id, hash_algorithm, computed_hash)

		if computed_hash != expected_hash:
			logger.info("%s: %s hash mismatch, package download failed", available_package.product_id, hash_algorithm)
			return False

		logger.info("%s: %s hash match, package download verified", available_package.product_id, hash_algorithm)
		return True

	def get_installed_package(
		self,
		available_package: RepositoryPackageInfo,
		installedProducts: list[ProductOnDepot],
	) -> ProductOnDepot | None:
		logger.info(
			"Testing if download/installation of package '%s' is needed",
			available_package.filename,
		)
		for product in installedProducts:
			if product.productId == available_package.product_id:
				logger.debug("Product '%s' is installed", available_package.product_id)
				logger.debug(
					"Available product version is '%s', installed product version is '%s-%s'",
					available_package.version,
					product.productVersion,
					product.packageVersion,
				)
				return product
		return None

	def is_download_needed(
		self,
		local_package_found: LocalPackageInfo | None,
		available_package: RepositoryPackageInfo,
		notifier: BaseNotifier | None = None,
	) -> bool:
		if (
			local_package_found
			and local_package_found.package_file.name == available_package.filename
			and local_package_found.md5_hash == available_package.md5_hash
		):
			logger.info(
				"%s - download of package is not required: found local package '%s' with matching md5sum",
				available_package.filename,
				local_package_found.package_file,
			)
			# No notifier message as nothing to do
			return False

		if self.config["forceDownload"]:
			message = f"{available_package.filename} - download of package is forced."
		elif local_package_found:
			message = (
				f"{available_package.filename} - download of package is required: "
				f"found local package '{local_package_found.package_file}' which differs from available"
			)
		else:
			message = f"{available_package.filename} - download of package is required: local package not found"
		logger.notice(message)
		if notifier is not None:
			notifier.appendLine(message)
		return True

	def is_install_needed(
		self,
		available_package: RepositoryPackageInfo,
		product: ProductOnDepot | None,
	) -> bool:
		repository = available_package.repository
		assert isinstance(repository, ProductRepositoryInfo)

		if not product:
			if repository.autoInstall:
				logger.notice(
					"%s - installation required: product '%s' is not installed and auto install is set for repository '%s'",
					available_package.filename,
					available_package.product_id,
					repository.name,
				)
				return True
			logger.info(
				"%s - installation not required: product '%s' is not installed but auto install is not set for repository '%s'",
				available_package.filename,
				available_package.product_id,
				repository.name,
			)
			return False

		if compareVersions(
			available_package.version,
			">",
			f"{product.productVersion}-{product.packageVersion}",
		):
			if repository.autoUpdate:
				logger.notice(
					"%s - installation required: a more recent version of product '%s' was found"
					" (installed: %s-%s, available: %s) and auto update is set for repository '%s'",
					available_package.filename,
					available_package.product_id,
					product.productVersion,
					product.packageVersion,
					available_package.version,
					repository.name,
				)
				return True
			logger.info(
				"%s - installation not required: a more recent version of product '%s' was found"
				" (installed: %s-%s, available: %s) but auto update is not set for repository '%s'",
				available_package.filename,
				available_package.product_id,
				product.productVersion,
				product.packageVersion,
				available_package.version,
				repository.name,
			)
			return False
		logger.info(
			"%s - installation not required: installed version '%s-%s' of product '%s' is up to date",
			available_package.filename,
			product.productVersion,
			product.packageVersion,
			available_package.product_id,
		)
		return False

	def get_packages(self, notifier: BaseNotifier, all_packages: bool = False) -> list[RepositoryPackageInfo]:
		installedProducts = self.getInstalledProducts()
		pack_per_repo = self.get_new_packages_per_repository()
		new_packages: list[RepositoryPackageInfo] = []
		if not any(pack_per_repo.values()):
			logger.warning("No downloadable packages found")
			return new_packages

		for repository in sort_repository_list(list(pack_per_repo)):
			downloadablePackages = pack_per_repo[repository]
			logger.debug("Processing downloadable packages on repository %s", repository)
			with self.makeSession(repository) as session:
				for available_package in downloadablePackages:
					logger.debug("Processing available package %s", available_package)
					try:
						# This ís called to keep the logs consistent
						product = self.get_installed_package(available_package, installedProducts)
						if not all_packages and not self.is_install_needed(available_package, product):
							continue

						local_package_found = get_local_package_info(
							product_id=available_package.product_id, package_directory=Path(str(self.config["packageDir"]))
						)

						zsync = self._useZsync(session, available_package, local_package_found)
						if self.is_download_needed(local_package_found, available_package, notifier=notifier):
							self.get_package(
								available_package,
								local_package_found,
								session,
								zsync=zsync,
								notifier=notifier,
							)
						packageFile = os.path.join(str(self.config["packageDir"]), available_package.filename)
						verified = self._verifyDownloadedPackage(packageFile, available_package)
						if not verified and zsync:
							logger.info(
								"%s: zsync download has failed, trying full download",
								available_package.product_id,
							)
							self.get_package(
								available_package,
								local_package_found,
								session,
								zsync=False,
								notifier=notifier,
							)
							verified = self._verifyDownloadedPackage(packageFile, available_package)
						if not verified:
							raise HashsumMissmatchError(f"{available_package.product_id}: md5sum mismatch")
						self.cleanupPackages(available_package)
						new_packages.append(available_package)
					except Exception as exc:
						if self.config.get("ignoreErrors"):
							logger.error(
								"Ignoring Error for package %s: %s",
								available_package.product_id,
								exc,
								exc_info=True,
							)
							notifier.appendLine(f"Ignoring Error for package {available_package.product_id}: {exc}")
						else:
							raise exc
		return new_packages

	def get_package(
		self,
		available_package: RepositoryPackageInfo,
		local_package_found: LocalPackageInfo | None,
		session: Session,
		notifier: BaseNotifier | None = None,
		zsync: bool = True,
	) -> None:
		package_file = Path(str(self.config["packageDir"])) / available_package.filename
		if zsync and local_package_found:
			if local_package_found.package_file != package_file:
				local_package_found.package_file = local_package_found.package_file.rename(package_file)

			message = None
			try:
				self.zsyncPackage(available_package, package_file, session)
				message = f"Zsync of {available_package.package_file!r} completed"
				logger.info(message)
			except Exception as err:
				if str(err) == "Aborted by progress callback":
					logger.info("Zsync aborted")
				else:
					logger.error(
						"Zsync of %r failed: %s",
						available_package.package_file,
						err,
						exc_info=True,
					)

			if notifier and message:
				notifier.appendLine(message)
		else:
			self.downloadPackage(available_package, session, notifier=notifier)

	def downloadPackages(self) -> None:
		if not any(self.getActiveRepositories()):
			logger.warning("No repositories configured, nothing to do")
			return

		notifier = self._getNotifier()
		try:
			newPackages = self.get_packages(notifier, all_packages=True)
			if not newPackages:
				logger.notice("No new packages downloaded")
				return
		except Exception as err:
			notifier.appendLine(f"Error occurred: {err}")
			if isinstance(notifier, EmailNotifier):
				notifier.setSubject(f"ERROR {self.config['subject']}")
			raise
		finally:
			if notifier and notifier.hasMessage():
				notifier.notify()

	def zsyncPackage(
		self,
		available_package: RepositoryPackageInfo,
		package_file: Path,
		session: Session,
	) -> None:
		logger.info("Zsyncing %s to %s", available_package.package_file, package_file)
		if not package_file.exists():
			raise FileNotFoundError(f"Package file {package_file} not found")

		url = available_package.zsync_file
		logger.info("Fetching zsync file %s", url)
		response = session.get(url, headers=self.httpHeaders, stream=True, timeout=1800)  # 30 minutes timeout
		if response.status_code < 200 or response.status_code > 299:
			logger.error(
				"Failed to fetch zsync file from %s: %s - %s",
				url,
				response.status_code,
				response.text,
			)
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

		def progress_callback(pos: int, total: int) -> bool:
			nonlocal ap_last_time, ap_last_position, ap_per_second
			now = time.time()
			elapsed = now - ap_last_time
			per_second = (pos - ap_last_position) / elapsed if elapsed else 0.0
			ap_per_second = int(ap_per_second * 0.7 + per_second * 0.3)
			ap_last_time = now
			ap_last_position = pos
			logger.debug("Local file analyze speed: %0.3f MB/s", ap_per_second / 1_000_000)
			# Check after 5MB if analyze speed is >= 1 MB/s (1.000.000 B/s)
			if pos >= 5_000_000 and ap_per_second < 1_000_000:
				logger.info(
					"Your system is too slow (%0.3f MB/s) to analyze local files in time, aborting zsync at position %d.",
					ap_per_second / 1_000_000,
					pos,
				)
				return True
			return False

		instructions = get_patch_instructions(zsync_file_info, files, optimized=True, progress_callback=progress_callback)
		remote_bytes = sum([i.size for i in instructions if i.source == SOURCE_REMOTE])
		speedup = (zsync_file_info.length - remote_bytes) * 100 / zsync_file_info.length
		logger.info(
			"Need to fetch %d/%d bytes from remote, speedup is %0.1f%%",
			remote_bytes,
			zsync_file_info.length,
			speedup,
		)

		class LoggingProgressListener(ProgressListener):
			def __init__(self) -> None:
				self.last_completed = 0

			def progress_changed(  # type: ignore[invaild-method-override]
				self,
				patcher: RequestsHTTPPatcher,
				position: int,
				total: int,
				per_second: int,
			) -> None:
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
			url = available_package.package_file
			logger.info("Fetching ranges from %s", url)
			patcher = RequestsHTTPPatcher(
				session=session,
				url=url,
				instructions=instructions,
				target_file=target_file,
				headers=self.httpHeaders,
			)
			patcher.register_progress_listener(LoggingProgressListener())
			return patcher

		sha1_digest = patch_file(files, instructions, patcher_factory=patcher_factory)

		if sha1_digest != zsync_file_info.sha1:
			raise RuntimeError("Failed to patch file, SHA-1 mismatch")

	def downloadPackage(
		self,
		available_package: RepositoryPackageInfo,
		session: Session,
		notifier: BaseNotifier | None = None,
	) -> None:
		url = available_package.package_file
		out_file = os.path.join(str(self.config["packageDir"]), available_package.filename)

		headers = self.httpHeaders.copy()
		headers["Accept-Encoding"] = "identity"
		response = session.get(url, headers=headers, stream=True, timeout=3600 * 8)  # 8h timeout
		if response.status_code < 200 or response.status_code > 299:
			logger.error(
				"Failed to download Package from %r: %s - %s",
				url,
				response.status_code,
				response.text,
			)
			raise RuntimeError(f"Failed to download Package from {url!r}: {response.status_code} - {response.text}")

		size = int(response.headers["Content-Length"])
		logger.info("Downloading %r (%0.2f MB) to %s", url, size / (1_000_000), out_file)

		position = 0
		percent = 0.0
		last_time = time.time()
		last_position = 0
		last_percent = 0
		speed = 0

		with open(out_file, "wb") as out:
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

	def cleanupPackages(self, new_package: RepositoryPackageInfo) -> None:
		logger.info("Cleaning up in %s", self.config["packageDir"])

		try:
			set_rights(str(self.config["packageDir"]))
		except Exception as err:
			logger.warning(
				"Failed to set rights on directory '%s': %s",
				self.config["packageDir"],
				err,
			)

		for filename in os.listdir(str(self.config["packageDir"])):
			path = os.path.join(str(self.config["packageDir"]), filename)
			if not os.path.isfile(path):
				continue
			if path.endswith(".zs-old") or path.endswith(".zsync-download"):
				os.unlink(path)
				continue

			try:
				productId, version = parseFilename(filename)
			except Exception as err:
				logger.debug("Parsing '%s' failed: '%s'", filename, err)
				continue

			if productId == new_package.product_id and version != new_package.version:
				logger.info("Deleting obsolete package file '%s'", path)
				os.unlink(path)

		package_file = os.path.join(str(self.config["packageDir"]), new_package.filename)
		md5sum_file = f"{package_file}.md5"
		logger.info("Creating md5sum file '%s'", md5sum_file)

		with open(md5sum_file, mode="w", encoding="utf-8") as hashFile:
			hashFile.write(compute_file_hash(Path(package_file), algorithm="md5"))
		set_rights(md5sum_file)

		zsync_file = f"{package_file}.zsync"
		logger.info("Creating zsync file '%s'", zsync_file)
		try:
			create_zsync_file(Path(package_file), Path(zsync_file), legacy_mode=True)
		except Exception as err:
			logger.error("Failed to create zsync file '%s': %s", zsync_file, err)
		set_rights(zsync_file)

	def onlyNewestPackages(self, packages: list[RepositoryPackageInfo]) -> list[RepositoryPackageInfo]:
		newest_packages: list[RepositoryPackageInfo] = []

		preferred_custom_versions: dict[str, str] = {}
		package_versions: dict[str, list[str]] = {}
		for package in packages:
			product_id = package.product_id
			if product_id not in package_versions:
				package_versions[product_id] = []
			package_versions[product_id].append(package.version)
			if product_id in preferred_custom_versions:
				continue
			repo = package.repository
			if not isinstance(repo, ProductRepositoryInfo) or not repo.customVersions:
				continue

			patterns = sorted(repo.customVersions, key=lambda x: len(x.pattern), reverse=True)
			for pattern in patterns:
				custom_version = repo.customVersions[pattern]
				if pattern.match(product_id):
					logger.info(
						"Preferring custom version '%s' for product '%s' from repository '%s'",
						custom_version,
						product_id,
						repo.name,
					)
					preferred_custom_versions[product_id] = custom_version
					break

		for package in packages:
			found = False
			repo = package.repository
			product_id = package.product_id
			package_version = package.version
			preferred_custom_version = preferred_custom_versions.get(product_id, "")
			custom_version = ""
			if "~" in package_version:
				package_version, custom_version = package_version.split("~", 1)
				if (not preferred_custom_version or custom_version != preferred_custom_version) and len(
					package_versions.get(product_id, [])
				) > 1:
					# Do not consider custom version if no preferred custom version is set and more than one version is available
					continue

			for i, newPackage in enumerate(newest_packages):
				if newPackage.product_id != product_id:
					continue

				found = True
				newest_package_version = newest_packages[i].version.split("~", 1)[0]

				if compareVersions(package_version, ">", newest_package_version):
					logger.debug(
						"Package version '%s' is newer than version '%s'",
						package_version,
						newest_package_version,
					)
					newest_packages[i] = package
					break

				if (
					preferred_custom_version
					and custom_version == preferred_custom_version
					and compareVersions(package_version, "==", newest_package_version)
				):
					logger.debug(
						"Package version '%s' matches preferred custom version '%s'",
						package_version,
						preferred_custom_version,
					)
					newest_packages[i] = package
					break

			if not found:
				newest_packages.append(package)

		return newest_packages

	def getInstalledProducts(self) -> list[ProductOnDepot]:
		logger.info("Getting installed products")
		products = []
		configBackend = self.getConfigBackend()
		for product in configBackend.productOnDepot_getObjects(depotId=self.depotId):  # type: ignore[attr-defined]
			logger.info(
				"Found installed product '%s_%s-%s'",
				product.productId,
				product.productVersion,
				product.packageVersion,
			)
			products.append(product)
		return products

	def getDownloadablePackages(self) -> list[RepositoryPackageInfo]:
		downloadable_packages = []
		for repository in self.getActiveRepositories():
			logger.info(
				"Getting package infos from repository '%s' (%s)",
				repository.name,
				repository.baseUrl,
			)
			for package in self.getDownloadablePackagesFromRepository(repository):
				downloadable_packages.append(package)
		return downloadable_packages

	def read_repository_metafile(self, repository: ProductRepositoryInfo, data: bytes) -> list[RepositoryPackageInfo]:
		packages: list[RepositoryPackageInfo] = []
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
			package_info = RepositoryPackageInfo(
				repository=repository,
				package_file=f"{repository.baseUrl}/{selected_path}",
				product_id=package.product_id,
				version=package.version,
				filename=selected_path.name,
				md5_hash=package.md5_hash,
				blake3_hash=package.blake3_hash,
				zsync_file=f"{repository.baseUrl}/{selected_zsync_path}" if selected_zsync_path else None,
			)
			logger.debug("Repository package info: %s", package_info)
			packages.append(package_info)
		return packages

	def fetch_repository_metafile(self, session: Session, url: str) -> bytes | None:
		if url not in self.metafile_cache:
			logger.info("Trying to fetch repository metafile: %s", url)
			response = session.get(url, headers=self.httpHeaders)
			if response.status_code == 200:
				logger.notice("Repository metafile successfully fetched: %s", url)
				self.metafile_cache[url] = response.content
			else:
				self.metafile_cache[url] = None
		return self.metafile_cache[url]

	def getDownloadablePackagesFromRepository(self, repository: ProductRepositoryInfo) -> list[RepositoryPackageInfo]:
		with self.makeSession(repository) as session:
			for meta_file in (
				"packages.msgpack.zstd",
				"packages.json",
				"packages.msgpack",
				"packages.json.zstd",
			):
				data = self.fetch_repository_metafile(session, f"{repository.baseUrl}/{meta_file}")
				if data is not None:
					# None = repository metafile missing, b"" = repository metafile empty
					if data:
						return self.read_repository_metafile(repository, data)
					break

			logger.info("No repository metafile found in repository: %s", repository.baseUrl)

			packages: list[RepositoryPackageInfo] = []
			errors = set()

			for url in repository.getDownloadUrls():
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

						try:
							productId, version = parseFilename(link)
							packageFile = url.rstrip("/") + "/" + link.lstrip("/")
							logger.info("Found opsi package: %s", packageFile)
							packageInfo = RepositoryPackageInfo(
								repository=repository,
								package_file=packageFile,
								product_id=forceProductId(productId),
								version=version,
								filename=link,
							)
							logger.debug("Repository package info: %s", packageInfo)
							packages.append(packageInfo)
						except Exception as err:
							logger.error("Failed to process link '%s': %s", link, err)

					for link in htmlParser.getLinks():
						is_md5 = link.endswith(".opsi.md5")
						is_zsync = link.endswith(".opsi.zsync")

						# stripping directory part from link
						link = link.split("/")[-1]

						filename = None
						if is_md5:
							filename = link[:-4]
						elif is_zsync:
							filename = link[:-6]
						else:
							continue

						try:
							for i, package in enumerate(packages):
								if package.filename == filename:
									if is_md5:
										response = session.get(f"{url.rstrip('/')}/{link.lstrip('/')}", headers=self.httpHeaders)
										match = re.search(
											r"([a-z\d]{32})",
											response.content.decode("utf-8"),
										)
										if match:
											found_md5_sum = match.group(1)
											packages[i].md5_hash = found_md5_sum
											logger.debug(
												"Got md5sum for package %s: %s",
												filename,
												found_md5_sum,
											)
									elif is_zsync:
										zsyncFile = f"{url.rstrip('/')}/{link.lstrip('/')}"
										packages[i].zsync_file = zsyncFile
										logger.debug(
											"Found zsync file for package '%s': %s",
											filename,
											zsyncFile,
										)

									break
						except Exception as err:
							logger.error("Failed to process link '%s': %s", link, err)
				except Exception as err:
					logger.debug(err, exc_info=True)
					self.errors.append(err)
					errors.add(str(err))

			if errors:
				logger.warning(
					"Problems processing repository %s: %s",
					repository.name,
					"; ".join(str(e) for e in errors),
				)

			return packages

	@contextmanager
	def makeSession(self, repository: ProductRepositoryInfo) -> Generator[Session, None, None]:
		logger.info(
			"Opening session for repository '%s' (%s)",
			repository.name,
			repository.baseUrl,
		)
		try:
			no_proxy_addresses = ["localhost", "127.0.0.1", "ip6-localhost", "::1"]
			session = prepare_proxy_environment(
				repository.baseUrl,
				repository.proxy,
				no_proxy_addresses=no_proxy_addresses,
			)

			if os.path.exists(repository.authcertfile) and os.path.exists(repository.authkeyfile):
				logger.debug(
					"setting session.cert to %s %s",
					repository.authcertfile,
					repository.authkeyfile,
				)
				session.cert = (repository.authcertfile, repository.authkeyfile)
			session.verify = repository.verifyCert
			session.auth = (repository.username, repository.password)
			logger.debug("Initiating session with verify=%s", repository.verifyCert)
			check_path = f"{repository.baseUrl}/{repository.dirs[0] if repository.dirs else ''}"
			result = session.head(check_path, headers=self.httpHeaders)
			if result.status_code < 200 or result.status_code > 399:
				logger.error(
					"Failed to connect to repository %s: %s - %s",
					repository.name,
					result.status_code,
					result.text,
				)
				raise ConnectionError(f"Failed to connect to repository {repository.name!r}: {result.status_code} - {result.text}")
			if repository.opsiDepotId:
				with self.transfer_slot(repository.opsiDepotId):
					yield session
			else:
				yield session
		finally:
			session.close()

	@contextmanager
	def transfer_slot(self, master_depot_id: str) -> Generator[None, None, None]:
		backend = self.getConfigBackend()
		heartbeat_thread = None
		try:
			while True:
				sleep_time = 0.0
				if hasattr(backend, "depot_acquireTransferSlot"):
					heartbeat_thread = TransferSlotHeartbeat(backend, master_depot_id, self.depotId)
					logger.notice("Acquiring transfer slot")
					response = heartbeat_thread.acquire()
					sleep_time = int(response.get("retry_after") or 0)
					logger.debug("depot_acquireTransferSlot produced response %s", response)
				if not sleep_time:
					break
				logger.notice("Did not start download, server suggested waiting time of %s seconds", sleep_time)
				time.sleep(sleep_time)
			if heartbeat_thread:
				logger.info("Starting transfer slot heartbeat thread")
				heartbeat_thread.start()
			logger.notice("Starting download")
			yield
		finally:
			if heartbeat_thread:
				logger.debug("Releasing transfer slot %s", heartbeat_thread.slot_id)
				heartbeat_thread.should_stop = True
				if heartbeat_thread.is_alive():
					logger.debug("Joining transfer slot heartbeat thread")
					heartbeat_thread.join()


def get_local_package_info(product_id: str, package_directory: Path) -> LocalPackageInfo | None:
	product_id = forceProductId(product_id)
	for package_file in package_directory.glob(f"{product_id}_*.opsi"):
		logger.info("Found local package '%s'", package_file)
		try:
			product_id, version = parseFilename(package_file.name)
			package_info = LocalPackageInfo(package_file=package_file, product_id=product_id, version=version)
			logger.debug("Local package info: %s", package_info)
			return package_info
		except Exception as err:
			logger.error("Failed to process file '%s': %s", package_file, err)
	return None
