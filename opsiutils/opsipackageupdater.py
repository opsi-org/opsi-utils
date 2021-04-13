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

from opsicommon.logging import logger, init_logging, logging_config, DEFAULT_COLORED_FORMAT
from opsicommon.system import ensure_not_already_running

from OPSI import __version__ as python_opsi_version
from OPSI import System
from OPSI.Types import forceProductId
from OPSI.Util import compareVersions
from OPSI.Util.Task.UpdatePackages.Config import DEFAULT_CONFIG
from OPSI.Util.Task.UpdatePackages.Exceptions import NoActiveRepositoryError
from OPSI.Util.Task.UpdatePackages.Updater import OpsiPackageUpdater
from OPSI.Util.Task.UpdatePackages.Util import getUpdatablePackages

from opsiutils import __version__


class OpsiPackageUpdaterClient(OpsiPackageUpdater):

	def listActiveRepos(self):
		logger.notice("Active repositories:")
		for repository in sorted(self.getActiveRepositories(), key=lambda repo: repo.name.lower()):
			descr = ''
			if repository.description:
				descr = "- {description}".format(description=repository.description)

			print(f"{repository.name}: {repository.baseUrl} {descr}")

	def listRepos(self):
		logger.notice("All repositories:")
		for repository in sorted(self.getRepositories(), key=lambda repo: repo.name.lower()):
			descr = ''
			if repository.description:
				descr = "- {description}".format(description=repository.description)

			status = 'active' if repository.active else 'inactive'
			print(f"{repository.name} ({status}): {repository.baseUrl} {descr}")

	def listProductsInRepositories(self, withLocalInstallationStatus=False, productId=None):
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
			localProducts = {product['productId']: product for product in localProducts}

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
						localProduct = localProducts[package['productId']]
					except KeyError as kerr:
						logger.debug(kerr)
						print(f"\t{package.get('productId')} (Version {package.get('version')}, not installed)")
						continue

					localVersion = '%s-%s' % (localProduct.get("productVersion"), localProduct.get("packageVersion"))
					if compareVersions(package['version'], '==', localVersion):
						print(f"\t{package.get('productId')} (Version {package.get('version')}, installed)")
					else:
						print(f"\t{package.get('productId')} (Version {package.get('version')}, installed {localVersion})")
				else:
					print(f"\t{package.get('productId')} (Version {package.get('version')})")

	def listProductsWithVersionDifference(self):
		"""
		Lists the products available at the actives repositories.

		If `withLocalInstallationStatus` is `True` it will also compare the version on the
		repository to the one locally installed and show if there is a
		difference.
		"""
		localProducts = self.getInstalledProducts()
		localProducts = {product['productId']: product for product in localProducts}

		for repository in self.getActiveRepositories():
			repoMessageShown = False
			packages = sorted(
				self.getDownloadablePackagesFromRepository(repository),
				key=operator.itemgetter('productId')
			)
			for package in packages:
				try:
					localProduct = localProducts[package['productId']]
				except KeyError:
					continue  # Not installed locally

				localVersion = '{productVersion}-{packageVersion}'.format(**localProduct)
				if not compareVersions(package['version'], '==', localVersion):
					if not repoMessageShown:
						print(f"Packages in {repository.name}:")
						repoMessageShown = True

					print(f"\t{package.get('productId')} (Version {package.get('version')}, installed {localVersion})")

	def listUpdatableProducts(self):
		try:
			updates = getUpdatablePackages(self)
		except NoActiveRepositoryError:
			logger.warning(u"No repositories configured, nothing to do")
			return

		if updates:
			for productId in sorted(updates.keys()):
				up = updates[productId]
				print(f"{up.get('productId')}: {up.get('newVersion')} in {up.get('repository')} (updatable from: {up.get('oldVersion')})")
		else:
			logger.notice("No updates found.")

parser = argparse.ArgumentParser(
	description="Updater for local opsi products.",
	epilog="Modes have their own options that can be viewed with MODE -h."
)
def parse_args():
	parser.add_argument('--version', '-V', action='version', version=f"{__version__} [python-opsi={python_opsi_version}]")
	parser.add_argument('--config', '-c', help="Location of config file",
						dest="configFile",
						default='/etc/opsi/opsi-package-updater.conf')

	logGroup = parser.add_mutually_exclusive_group()
	logGroup.add_argument('--verbose', '-v',
		dest="logLevel", default=4, action="count",
		help="Increase verbosity on console (can be used multiple times)")
	logGroup.add_argument('--log-level', '-l',
		dest="logLevel", type=int, choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
		help="Set the desired loglevel for the console.")

	parser.add_argument('--force-checksum-calculation',
		dest='forceChecksumCalculation', action="store_true", default=False,
		help=(
			"Force calculation of a checksum (MD5) for every package. "
			"Default is to use existing checksums from the .md5-file "
			"of a package if possible."
		)
	)

	parser.add_argument('--repo', metavar="repository_name", dest="repository",
						default=None, help="Limit the actions the given repository.")
	parser.add_argument('--use-inactive-repository', action="store_true",
						dest="forceRepositoryActivation", help=(
							"Force the activation of an otherwise disabled "
							"repository. The repository must be given through "
							"--repo."
						)
	)
	parser.add_argument('--no-zsync',
		dest='no_zsync', action="store_true", default=False,
		help=("Forces to not use zsync. Instead the fallback command is used.")
	)

	modeparsers = parser.add_subparsers(dest='mode', title="Mode")
	installparser = modeparsers.add_parser(
		'install', help='Install all (or a given list of) downloadable packages from configured repositories (ignores excludes)'
	)
	installparser.add_argument('processProductIds', nargs='*',
								metavar="productID",
								help="Limit installation to products with the given IDs.")

	updateparser = modeparsers.add_parser('update', help='Update already installed packages from repositories.')
	updateparser.add_argument('processProductIds', nargs='*',
								metavar="productID",
								help="Limit updates to products with the given IDs.")

	downloadParser = modeparsers.add_parser('download',
											help=('Download packages from repositories. '
													'This will not install packages.'))
	downloadParser.add_argument('--force',
								action="store_true", dest="forceDownload",
								help='Force the download of a product even though it would otherwise not be required.')
	downloadParser.add_argument('processProductIds', nargs='*', metavar="productID",
								help="Limit downloads to products with the given IDs.")

	listparser = modeparsers.add_parser('list', help='Listing information')
	listmgroup = listparser.add_mutually_exclusive_group()
	listmgroup.add_argument('--repos',
							action="store_true", dest="listRepositories",
							help='Lists all repositories')
	listmgroup.add_argument('--active-repos',
							action="store_true", dest="listActiveRepos",
							help='Lists all active repositories')
	listmgroup.add_argument('--packages', '--products',
							action="store_true", dest="listAvailableProducts",
							help='Lists the repositories and the packages they provide.')
	listmgroup.add_argument('--packages-and-installationstatus', '--products-and-installationstatus',
							action="store_true", dest="listProductsWithInstallationStatus",
							help='Lists the repositories with their provided packages and information about the local installation status.')
	listmgroup.add_argument('--package-differences', '--product-differences',
							action="store_true", dest="listProductsWithDifference",
							help='Lists packages where local and remote version are different.')
	listmgroup.add_argument('--updatable-packages', '--updatable-products',
							action="store_true", dest="listUpdatableProducts",
							help='Lists packages that have updates in the remote repositories.')
	listmgroup.add_argument('--search-package', '--search-product', metavar='text',
							dest="searchForProduct",
							help='Search for a package with the given name.')

	# Setting a default to not stumble over possibly not present args.
	parser.set_defaults(processProductIds=[])

	return parser.parse_args()


def updater_main():  # pylint: disable=too-many-branches,too-many-statements
	config = DEFAULT_CONFIG.copy()
	args = parse_args()

	init_logging(stderr_level=args.logLevel, stderr_format=DEFAULT_COLORED_FORMAT)
	if args.mode == 'list' and args.logLevel < 4:
		logging_config(stderr_level=4)
	logger.debug("Running in %s mode", args.mode)

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

	if args.no_zsync:
		config["zsyncCommand"] = None
		logger.info("Not using zsync, instead using fallback")
	else:
		try:
			config["zsyncCommand"] = System.which("zsync")
			logger.info("Zsync command found: %s", config["zsyncCommand"])
		except Exception:  # pylint: disable=broad-except
			logger.warning("Zsync command not found")

	ensure_not_already_running("opsi-package-manager")

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


def main():
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
