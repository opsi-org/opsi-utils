# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
Utility functions for package updates.
"""

from opsi.logging import get_logger
from opsi_legacy.Util import compareVersions

from .Exceptions import NoActiveRepositoryError
from .Repository import ProductRepositoryInfo, sort_repository_list
from .Updater import OpsiPackageUpdater

__all__ = ("getUpdatablePackages",)

logger = get_logger("opsi.general")


def getUpdatablePackages(updater: OpsiPackageUpdater) -> dict[str, dict[str, str]]:
	"""
	Returns information about updatable packages from the given `updater`.


	:raises NoActiveRepositoryError: If not active repositories are found.
	:param updater: The update to use.
	:type updater: OpsiPackageUpdater
	:returns: A dict containing the productId as key and the value is another dict with the keys _productId_, _newVersion_, _oldVersion_ and _repository_.
	:rtype: {str: {}
	"""
	if not any(updater.getActiveRepositories()):
		raise NoActiveRepositoryError("No active repository configured.")

	updates: dict[str, dict[str, str]] = {}
	try:
		installed_products = updater.getInstalledProducts()
		pack_per_repo = updater.get_new_packages_per_repository()

		if not any(pack_per_repo.values()):
			return updates

		for repository in sort_repository_list(list(pack_per_repo)):
			for available_package in pack_per_repo[repository]:
				assert isinstance(repository, ProductRepositoryInfo)
				product_id = available_package.product_id
				for product in installed_products:
					if product.productId == product_id:
						logger.debug("Product '%s' is installed", product_id)
						logger.debug(
							"Available product version is '%s' (on %s), installed product version is '%s-%s'",
							repository.name,
							available_package.version,
							product.productVersion,
							product.packageVersion,
						)
						updateAvailable = compareVersions(
							available_package.version, ">", f"{product.productVersion}-{product.packageVersion}"
						)

						if updateAvailable:
							updates[product_id] = {
								"productId": product_id,
								"newVersion": f"{available_package.version}",
								"oldVersion": f"{product.productVersion}-{product.packageVersion}",
								"repository": repository.name,
							}
						break
	except Exception as error:
		raise error

	return updates
