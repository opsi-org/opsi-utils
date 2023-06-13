# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-package-updater

This tool can be used to update the packages on an opsi server
through a remote repository.
"""

import argparse
import operator
import sys
from pathlib import Path

from opsicommon.logging import (
	DEFAULT_COLORED_FORMAT,
	get_logger,
	init_logging,
	logging_config,
)
from opsicommon.system import ensure_not_already_running
from opsicommon.types import forceProductId

from OPSI import __version__ as python_opsi_version  # type: ignore[import,attr-defined]
from OPSI.Util import compareVersions  # type: ignore[import]
from opsiutils import __version__
from opsiutils.update_packages.Config import DEFAULT_CONFIG
from opsiutils.update_packages.Exceptions import NoActiveRepositoryError
from opsiutils.update_packages.Updater import OpsiPackageUpdater
from opsiutils.update_packages.Util import getUpdatablePackages

logger = get_logger("opsi-package-updater")

OFFICIAL_REPO_FILES = [
	"uib-linux.repo",
	"uib-windows.repo",
	"uib-macos.repo",
	"uib-local_image.repo",
	"testing.repo",
	"experimental.repo",
]
class OpsiPackageUpdaterClient(OpsiPackageUpdater):

	def listActiveRepos(self) -> None:
		logger.notice("Active repositories:")
		for repository in sorted(self.getActiveRepositories(), key=lambda repo: repo.name.lower()):
			descr = ''
			if repository.description:
				descr = f"- {repository.description}"

			print(f"{repository.name}: {repository.baseUrl} {descr}")

	def listRepos(self) -> None:
		logger.notice("All repositories:")
		for repository in sorted(self.getRepositories(), key=lambda repo: repo.name.lower()):
			descr = ''
			if repository.description:
				descr = f"- {repository.description}"

			status = 'active' if repository.active else 'inactive'
			print(f"{repository.name} ({status}): {repository.baseUrl} {descr}")

	def listPackagesUniqueInRepositories(self) -> None:
		"""
		Lists the products available at the actives repositories.
		Only display the newest version and where to get it.
		"""

		data = {}
		for repository in self.getActiveRepositories():
			for package in self.getDownloadablePackagesFromRepository(repository):
				name = package.get("productId")
				if name not in data or compareVersions(package.get("version"), ">", data[name].get("version")):
					data[name] = {"version": package.get("version"), "repository": repository.name}

		for name in sorted(data.keys()):
			print(f"\t{name} (Version {data[name].get('version')} in {data[name].get('repository')})")

	def listProductsInRepositories(self, withLocalInstallationStatus: bool = False, productId: str | None = None) -> None:
		"""
		Lists the products available at the actives repositories.

		If `withLocalInstallationStatus` is `True` it will also compare the version on the
		repository to the one locally installed and show if there is a
		difference.

		:type withLocalInstallationStatus: bool
		:param productId: Limit the output to products matching this
		:type productId: str
		"""
		if withLocalInstallationStatus:
			localProducts = self.getInstalledProducts()
			local_products_dict = {product.productId: product for product in localProducts}

		for repository in self.getActiveRepositories():
			logger.notice("Packages in %s:", repository.name)
			packages = sorted(
				self.getDownloadablePackagesFromRepository(repository),
				key=operator.itemgetter('productId')
			)

			if productId:
				logger.debug("Filtering for product IDs matching %s...", productId)
				productId = forceProductId(productId)
				packages = [
					package for package in packages
					if productId in package['productId']
				]

			for package in packages:
				if withLocalInstallationStatus:
					try:
						localProduct = local_products_dict[package['productId']]
					except KeyError as kerr:
						logger.debug(kerr)
						print(f"\t{package.get('productId')} (Version {package.get('version')}, not installed)")
						continue

					localVersion = f"{localProduct.productVersion}-{localProduct.packageVersion}"
					if compareVersions(package['version'], '==', localVersion):
						print(f"\t{package.get('productId')} (Version {package.get('version')}, installed)")
					else:
						print(f"\t{package.get('productId')} (Version {package.get('version')}, installed {localVersion})")
				else:
					print(f"\t{package.get('productId')} (Version {package.get('version')})")

	def listProductsWithVersionDifference(self) -> None:
		"""
		Lists the products available at the actives repositories.

		If `withLocalInstallationStatus` is `True` it will also compare the version on the
		repository to the one locally installed and show if there is a
		difference.
		"""
		localProducts = self.getInstalledProducts()
		localProducts = {product.productId: product for product in localProducts}

		for repository in self.getActiveRepositories():
			repoMessageShown = False
			packages = sorted(
				self.getDownloadablePackagesFromRepository(repository),
				key=lambda entry: entry.productId
			)
			for package in packages:
				try:
					localProduct = localProducts[package.productId]
				except KeyError:
					continue  # Not installed locally

				localVersion = f"{localProduct.productVersion}-{localProduct.packageVersion}"
				if not compareVersions(package.version, '==', localVersion):
					if not repoMessageShown:
						print(f"Packages in {repository.name}:")
						repoMessageShown = True

					print(f"\t{package.productId} (Version {package.version}, installed {localVersion})")

	def listUpdatableProducts(self) -> None:
		try:
			updates = getUpdatablePackages(self)
		except NoActiveRepositoryError:
			logger.warning("No repositories configured, nothing to do")
			return

		if updates:
			for productId in sorted(updates.keys()):
				up = updates[productId]
				print(f"{up.get('productId')}: {up.get('newVersion')} in {up.get('repository')} (updatable from: {up.get('oldVersion')})")
		else:
			logger.notice("No updates found.")


parser = argparse.ArgumentParser(
	description=(
		"Updater for local opsi products.\n"
		"Operates in different MODEs: install, update, download and list.\n"
		"Each mode has their own options that can be viewed with MODE -h"
	)
)


def parse_args() -> argparse.Namespace:
	parser.add_argument('--version', '-V', action='version', version=f"{__version__} [python-opsi={python_opsi_version}]")
	parser.add_argument(
		'--config',
		'-c',
		help="Location of config file",
		dest="configFile",
		default='/etc/opsi/opsi-package-updater.conf',
	)

	logGroup = parser.add_mutually_exclusive_group()
	logGroup.add_argument(
		'--verbose',
		'-v',
		dest="logLevel",
		default=4,
		action="count",
		help="Increase verbosity on console (can be used multiple times)",
	)
	logGroup.add_argument(
		'--log-level',
		'-l',
		dest="logLevel",
		type=int,
		choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
		help="Set the desired loglevel for the console.",
	)

	parser.add_argument(
		'--force-checksum-calculation',
		dest='forceChecksumCalculation',
		action="store_true",
		default=False,
		help=(
			"Force calculation of a checksum (MD5) for every package. "
			"Default is to use existing checksums from the .md5-file "
			"of a package if possible."
		),
	)

	parser.add_argument(
		'--no-patch-repo-files',
		help="Do not update repo files",
		dest="noPatchRepoFiles",
		action="store_true",
		default=False,
	)

	parser.add_argument(
		'--repo',
		metavar="repository_name",
		dest="repository",
		default=None,
		help="Limit the actions the given repository."
	)
	parser.add_argument(
		'--use-inactive-repository',
		action="store_true",
		dest="forceRepositoryActivation",
		help="Force the activation of an otherwise disabled repository. The repository must be given through --repo.",
	)
	parser.add_argument(
		"--ignore-errors",
		action="store_true",
		dest="ignoreErrors",
		help='Continue working even after download or installation of a package failed.'
	)

	parser.add_argument(
		"--no-zsync",
		dest='no_zsync',
		action="store_true",
		default=False,
		help="Forces to not use zsync. Instead the fallback command is used.",
	)

	modeparsers = parser.add_subparsers(dest='mode', title="Mode")
	installparser = modeparsers.add_parser(
		'install',
		help='Install all (or a given list of) downloadable packages from configured repositories (ignores excludes)',
	)
	installparser.add_argument(
		'processProductIds',
		nargs='*',
		metavar="productID",
		help="Limit installation to products with the given IDs.",
	)

	updateparser = modeparsers.add_parser('update', help='Update already installed packages from repositories.')
	updateparser.add_argument(
		'processProductIds',
		nargs='*',
		metavar="productID",
		help="Limit updates to products with the given IDs.",
	)

	downloadParser = modeparsers.add_parser('download', help='Download packages from repositories. This will not install packages.')
	downloadParser.add_argument(
		'--force',
		action="store_true",
		dest="forceDownload",
		help='Force the download of a product even though it would otherwise not be required.'
	)
	downloadParser.add_argument('processProductIds', nargs='*', metavar="productID", help="Limit downloads to products with the given IDs.")

	listparser = modeparsers.add_parser('list', help='Listing information')
	listmgroup = listparser.add_mutually_exclusive_group()
	listmgroup.add_argument('--repos', action="store_true", dest="listRepositories", help='Lists all repositories')
	listmgroup.add_argument('--active-repos', action="store_true", dest="listActiveRepos", help='Lists all active repositories')
	listmgroup.add_argument(
		'--packages',
		'--products',
		action="store_true",
		dest="listAvailableProducts",
		help='Lists the repositories and the packages they provide.',
	)
	listmgroup.add_argument(
		'--packages-unique',
		action="store_true",
		dest="listAvailablePackagesUnique",
		help='Lists the repositories and the packages they provide.',
	)
	listmgroup.add_argument(
		'--packages-and-installationstatus',
		'--products-and-installationstatus',
		action="store_true",
		dest="listProductsWithInstallationStatus",
		help='Lists the repositories with their provided packages and information about the local installation status.',
	)
	listmgroup.add_argument(
		'--package-differences',
		'--product-differences',
		action="store_true",
		dest="listProductsWithDifference",
		help='Lists packages where local and remote version are different.'
	)
	listmgroup.add_argument(
		'--updatable-packages',
		'--updatable-products',
		action="store_true",
		dest="listUpdatableProducts",
		help='Lists packages that have updates in the remote repositories.',
	)
	listmgroup.add_argument(
		'--search-package',
		'--search-product',
		metavar='text',
		dest="searchForProduct",
		help='Search for a package with the given name.',
	)

	# Setting a default to not stumble over possibly not present args.
	parser.set_defaults(processProductIds=[])

	return parser.parse_args()


def patch_repo_files(base_path: Path) -> None:
	"""
	Patches the repo files to point to 4.3 repositories.
	Old format example:
		baseUrl = http://download.uib.de
		dirs = opsi4.2/experimental/packages/linux/localboot/, opsi4.2/experimental/packages/linux/netboot/
	New format example:
		baseUrl = https://opsipackages.43.opsi.org
		dirs = experimental/linux/localboot/, experimental/linux/netboot/
	"""
	for repo in OFFICIAL_REPO_FILES:
		repo_file = base_path / repo
		if not repo_file.exists():
			continue
		content = repo_file.read_text(encoding="utf-8")
		if not "download.uib.de" in content or content.startswith("; This file has been patched by opsi-package-updater"):
			continue
		content = content.replace("http://download.uib.de", "https://opsipackages.43.opsi.org")
		content = content.replace("https://download.uib.de", "https://opsipackages.43.opsi.org")
		content = content.replace("/packages/", "/")
		content = content.replace("opsi4.2/", "")
		content = f"; This file has been patched by opsi-package-updater {__version__}\n{content}"
		repo_file.write_text(content, encoding="utf-8")


def updater_main() -> int:  # pylint: disable=too-many-branches,too-many-statements
	config = DEFAULT_CONFIG.copy()
	args = parse_args()

	init_logging(stderr_level=args.logLevel, stderr_format=DEFAULT_COLORED_FORMAT)
	if args.mode == 'list' and args.logLevel < 4:
		logging_config(stderr_level=4)
	logger.debug("Running in %s mode", args.mode)

	if not args.noPatchRepoFiles:
		patch_repo_files(Path(str(DEFAULT_CONFIG["repositoryConfigDir"])))

	config["configFile"] = args.configFile
	config["installAllAvailable"] = args.mode == 'install'
	if args.processProductIds:
		config["processProductIds"] = set(args.processProductIds)

	config["forceChecksumCalculation"] = args.forceChecksumCalculation

	if args.forceRepositoryActivation:
		if not args.repository:
			raise RuntimeError("No repository given.")

		logger.warning("ATTENTION: Using an inactive repository!")

	config['forceRepositoryActivation'] = args.forceRepositoryActivation
	config["repositoryName"] = args.repository

	if args.mode == 'download':
		config["forceDownload"] = args.forceDownload

	config["ignoreErrors"] = args.ignoreErrors
	config["useZsync"] = not args.no_zsync

	ensure_not_already_running("opsi-package-updater")

	with OpsiPackageUpdaterClient(config) as opu:
		if args.mode in ('install', 'update'):
			opu.processUpdates()
		elif args.mode == 'download':
			opu.downloadPackages()
		elif args.mode == 'list':
			if args.listActiveRepos:
				opu.listActiveRepos()
			elif args.listRepositories:
				opu.listRepos()
			elif args.listAvailableProducts:
				opu.listProductsInRepositories()
			elif args.listAvailablePackagesUnique:
				opu.listPackagesUniqueInRepositories()
			elif args.listProductsWithInstallationStatus:
				opu.listProductsInRepositories(withLocalInstallationStatus=True)
			elif args.listUpdatableProducts:
				opu.listUpdatableProducts()
			elif args.listProductsWithDifference:
				opu.listProductsWithVersionDifference()
			elif args.searchForProduct:
				opu.listProductsInRepositories(productId=args.searchForProduct)
		else:
			parser.error("No mode provided")
		if opu.errors:
			return 2  # things went wrong

	return 0  # no errors encountered


def main() -> None:
	try:
		exitCode = updater_main()
	except KeyboardInterrupt:
		exitCode = 1
	except Exception as exc:  # pylint: disable=broad-except
		logger.error(exc, exc_info=True)
		print(f"ERROR: {exc}", file=sys.stderr)
		exitCode = 1

	if exitCode:
		sys.exit(exitCode)
