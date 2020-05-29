# -*- coding: utf-8 -*-

# opsi-package-updater is part of the desktop management solution opsi
# (open pc server integration) http://www.opsi.org

# Copyright (C) 2013-2019 uib GmbH

# http://www.uib.de/

# All rights reserved.

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License, version 3
# as published by the Free Software Foundation.

# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Affero General Public License for more details.

# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
opsi-package-updater

This tool can be used to update the packages on an opsi server
through a remote repository.

@copyright: uib GmbH <info@uib.de>
@author: Jan Schneider <j.schneider@uib.de>
@author: Erol Ueluekmen <e.ueluekmen@uib.de>
@author: Niko Wenselowski <n.wenselowski@uib.de>
@license: GNU Affero GPL version 3
"""

import argparse
import operator
import os
import sys

from OPSI import System
from OPSI.Logger import LOG_NOTICE, LOG_WARNING, Logger
from OPSI.Types import forceProductId, forceUnicode
from OPSI.Util import compareVersions
from OPSI.Util.Task.UpdatePackages.Config import DEFAULT_CONFIG
from OPSI.Util.Task.UpdatePackages.Exceptions import NoActiveRepositoryError
from OPSI.Util.Task.UpdatePackages.Updater import OpsiPackageUpdater
from OPSI.Util.Task.UpdatePackages.Util import getUpdatablePackages

from opsiutils import __version__

logger = Logger()


class OpsiPackageUpdaterClient(OpsiPackageUpdater):

	def listActiveRepos(self):
		logger.notice("Active repositories:")
		for repository in sorted(self.getActiveRepositories(), key=lambda repo: repo.name.lower()):
			descr = ''
			if repository.description:
				descr = "- {description}".format(description=repository.description)

			logger.notice("{name}: {url} {description}", name=repository.name, url=repository.baseUrl, description=descr)

	def listRepos(self):
		logger.notice("All repositories:")
		for repository in sorted(self.getRepositories(), key=lambda repo: repo.name.lower()):
			descr = ''
			if repository.description:
				descr = "- {description}".format(description=repository.description)

			logger.notice(
				"{name} ({status}): {url} {description}",
				name=repository.name,
				status='active' if repository.active else 'inactive',
				url=repository.baseUrl,
				description=descr
			)

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
			logger.notice("Packages in {name}:", name=repository.name)
			packages = sorted(
				self.getDownloadablePackagesFromRepository(repository),
				key=operator.itemgetter('productId')
			)

			if productId:
				logger.debug("Filtering for product IDs matching {0}...", productId)
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
						logger.notice('\t{productId} (Version {version}, not installed)', **package)
						continue

					localVersion = '{productVersion}-{packageVersion}'.format(**localProduct)
					if compareVersions(package['version'], '==', localVersion):
						logger.notice('\t{productId} (Version {version}, installed)', **package)
					else:
						logger.notice('\t{productId} (Version {version}, installed {localVersion})', localVersion=localVersion, **package)
				else:
					logger.notice('\t{productId} (Version {version})', **package)

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
						logger.notice("Packages in {name}:", name=repository.name)
						repoMessageShown = True

					logger.notice('\t{productId} (Version {version}, installed {localVersion})', localVersion=localVersion, **package)

	def listUpdatableProducts(self):
		try:
			updates = getUpdatablePackages(self)
		except NoActiveRepositoryError:
			logger.warning(u"No repositories configured, nothing to do")
			return

		if updates:
			for productId in sorted(updates.keys()):
				logger.notice("{productId}: {newVersion} in {repository} (updatable from: {oldVersion})", **updates[productId])
		else:
			logger.notice("No updates found.")


def updater_main():
	config = DEFAULT_CONFIG.copy()

	parser = argparse.ArgumentParser(
		description="Updater for local opsi products.",
		epilog="Modes have their own options that can be viewed with MODE -h."
	)
	parser.add_argument('--version', '-V', action='version', version=__version__)
	parser.add_argument('--config', '-c', help="Location of config file",
						dest="configFile",
						default='/etc/opsi/opsi-package-updater.conf')

	logGroup = parser.add_mutually_exclusive_group()
	logGroup.add_argument('--verbose', '-v',
		dest="logLevel", default=LOG_WARNING, action="count",
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

	modeparsers = parser.add_subparsers(dest='mode', title="Mode")
	installparser = modeparsers.add_parser('install', help='Install all (or a given list of) downloadable packages from configured repositories (ignores excludes)')
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
	listparser.set_defaults(processProductIds=[])
	updateparser.set_defaults(processProductIds=[])
	downloadParser.set_defaults(processProductIds=[])

	args = parser.parse_args()

	logger.setConsoleLevel(args.logLevel)
	if args.mode == 'list' and args.logLevel < LOG_NOTICE:
		logger.setConsoleLevel(LOG_NOTICE)
	logger.debug("Running in {0} mode", args.mode)

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

	try:
		config["zsyncCommand"] = System.which("zsync")
		logger.info(u"Zsync command found: %s" % config["zsyncCommand"])
	except Exception:
		logger.warning(u"Zsync command not found")

	pid = os.getpid()
	running = None
	try:
		for anotherPid in System.execute("%s -x %s" % (System.which("pidof"), os.path.basename(sys.argv[0])))[0].strip().split():
			if int(anotherPid) != pid:
				running = anotherPid
	except Exception as error:
		logger.debug(u"Check for running processes failed: {0}", error)

	if running:
		raise RuntimeError(u"Another %s process is running (pid: %s)." % (os.path.basename(sys.argv[0]), running))

	logger.info(u"We are the only {0} running.", os.path.basename(sys.argv[0]))

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

		if opu.errors:
			return 2  # things went wrong

	return 0  # no errors encountered


def main():
	logger.setConsoleColor(True)

	try:
		exitCode = updater_main()
	except KeyboardInterrupt:
		exitCode = 1
	except Exception as exc:
		logger.logException(exc)
		print(f"ERROR: {exc}", file=sys.stderr)
		exitCode = 1

	if exitCode:
		sys.exit(exitCode)
