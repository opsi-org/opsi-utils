# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-outdated-to-setup - set action_requests where outdated.
"""

import sys
from argparse import ArgumentParser, Namespace
from contextlib import contextmanager

from OPSI import __version__ as python_opsi_version
from OPSI.Backend.BackendManager import BackendManager

from opsicommon.logging import (
	DEFAULT_COLORED_FORMAT,
	LOG_NOTICE,
	logger,
	logging_config,
)

from opsiutils import __version__
STATIC_EXCLUDE_PRODUCTS = [
	"opsi-winst",
	"opsi-auto-update",
	"opsi-script",
	"shutdownwanted",
	"windows10-upgrade",
	"activate-win",
	"opsi-script-test",
	"opsi-bootimage-local",
	"opsi-uefi-netboot",
	"opsi-wan-config",
	"opsi-winpe",
	"win10-sysprep-app-update-blocker",
	"windomain",
]


@contextmanager
def service_connection():
	service = None
	try:
		service = BackendManager()
		yield service
	finally:
		if service:
			service.backend_exit()


def client_ids_from_group(backend: BackendManager, group: str):
	result = backend.group_getObjects(id=group, type="HostGroup")
	if not result:
		raise ValueError(f"Client group '{group}' not found")
	return [mapping.objectId for mapping in backend.objectToGroup_getObjects(groupId=result[0].id)]


class OutdatedToSetupWorker:  # pylint: disable=too-many-instance-attributes
	def __init__(self, args: Namespace) -> None:
		logger.trace("Called with arguments: %s", args)
		self.depot_versions = {}
		self.client_to_depot = {}
		self.depending_products = set()
		self.add_failed = args.add_failed
		self.uninstall_products = []
		self.uninstall_where_only_uninstall = args.uninstall_where_only_uninstall
		self.clients = args.clients
		self.client_groups = args.client_groups
		self.exclude_products = STATIC_EXCLUDE_PRODUCTS
		self.include_products = []
		if args.include_products:
			self.include_products = [entry.strip() for entry in args.include_products.split(",")]
			logger.notice("Limiting handled products to %s", self.include_products)
		if args.exclude_products:
			self.exclude_products.extend([entry.strip() for entry in args.exclude_products.split(",")])
		logger.info("List of excluded products: %s", self.exclude_products)

	def determine_clients(self, backend: BackendManager) -> None:
		if self.clients:
			self.clients = [entry.strip() for entry in self.clients.split(",")]
		else:
			self.clients = []
		if self.client_groups:
			for group in [entry.strip() for entry in self.client_groups.split(",")]:
				self.clients.extend(client_ids_from_group(backend, group))
		if not self.clients:
			raise ValueError("No clients selected")
		if "all" in self.clients:
			self.clients = []
		logger.notice("Selected clients: %s", self.clients or 'all')

	def determine_products_to_uninstall(self, backend: BackendManager) -> None:
		self.uninstall_products =  [
			entry.id for entry in backend.product_getObjects(type="LocalbootProduct")
			if entry.uninstallScript
			and not entry.setupScript
			and not entry.onceScript
			and not entry.customScript
			and not entry.updateScript
			and not entry.alwaysScript
			and not entry.userLoginScript
			and (not self.include_products or entry.id in self.include_products)
		]
		logger.notice("Uninstalling products (where installed): %s", self.uninstall_products)

	def run(self, dry_run: bool = False) -> None:  # pylint: disable=too-many-branches
		if dry_run:
			logger.notice("Operating in dry-run mode - not performing any actions")
		new_pocs = []
		with service_connection() as service:
			try:
				self.determine_clients(service)
			except ValueError:
				logger.notice("No clients selected")
				return
			if self.uninstall_where_only_uninstall:
				self.determine_products_to_uninstall(service)
			for clientToDepotserver in service.configState_getClientToDepotserver(clientIds=self.clients):  # pylint: disable=no-member
				self.client_to_depot[clientToDepotserver["clientId"]] = clientToDepotserver["depotId"]
			logger.trace("ClientToDepot mapping: %s", self.client_to_depot)
			for entry in service.productOnDepot_getObjects():  # pylint: disable=no-member
				if not self.depot_versions.get(entry.depotId):
					self.depot_versions[entry.depotId] = {}
				self.depot_versions[entry.depotId][entry.productId] = entry.version
			logger.trace("Product versions on depots: %s", self.depot_versions)
			for pdep in service.productDependency_getObjects():  # pylint: disable=no-member
				self.depending_products.add(pdep.productId)
			logger.trace("Products with dependencies: %s", self.depending_products)

			pocs = service.productOnClient_getObjects(  # pylint: disable=no-member
				clientId=self.clients or None,
				productType="LocalbootProduct",
				productId=self.include_products or None
			)
			for entry in pocs:
				logger.debug("Checking %s (%s) on %s", entry.productId, entry.version, entry.clientId)
				if entry.productId in self.exclude_products:
					logger.info("Skipping as %s is in excluded products", entry.productId)
					continue
				if entry.actionRequest not in (None, "none"):  # Could introduce --force to ignore/overwrite existing action Requests
					logger.debug("Skipping %s %s as an actionRequest is set: %s", entry.productId, entry.clientId, entry.actionRequest)
					continue  # existing actionRequests are left untouched
				try:
					available = self.depot_versions[self.client_to_depot[entry.clientId]][entry.productId]
				except KeyError:
					logger.error("Skipping check of %s %s (product not available on depot)", entry.clientId, entry.productId)
					continue
				if entry.productId in self.uninstall_products:
					logger.info("Setting 'uninstall' ProductActionRequest: %s -> %s", entry.productId, entry.clientId)
					if not dry_run:
						entry.setActionRequest("uninstall")
						new_pocs.append(entry)
				elif entry.version != available or (entry.actionResult == "failed" and self.add_failed):
					if entry.productId in self.depending_products:
						logger.info("Setting 'setup' ProductActionRequest with Dependencies: %s -> %s", entry.productId, entry.clientId)
						if not dry_run:
							service.setProductActionRequestWithDependencies(entry.productId, entry.clientId, "setup")  # pylint: disable=no-member
					else:
						logger.info("Setting 'setup' ProductActionRequest: %s -> %s", entry.productId, entry.clientId)
						if not dry_run:
							entry.setActionRequest("setup")
							new_pocs.append(entry)
			if not dry_run:
				logger.debug("Updating ProductOnClient")
				service.productOnClient_updateObjects(new_pocs)  # pylint: disable=no-member


def parse_args():
	parser = ArgumentParser(description='Set outdated localboot Products to setup.')
	parser.add_argument('--version', '-V', action='version', version=f"{__version__} [python-opsi={python_opsi_version}]")
	parser.add_argument('--log-level', '-l', default=LOG_NOTICE, type=int, choices=range(10), help="Set log-level (0..9)")
	parser.add_argument("--clients", help="comma-separated list of clients or 'all'")
	parser.add_argument("--dry-run", help="only simulate run", action="store_true")
	parser.add_argument("--client-groups", help="comma-separated list of host groups")
	parser.add_argument("--exclude-products", help="do not set actionRequests for these products")
	parser.add_argument("--include-products", help="set actionRequests ONLY for these products")
	parser.add_argument("--add-failed", help="If this is set, it will also add actionRequests for all failed products", action="store_true")
	parser.add_argument(
		"--uninstall-where-only-uninstall",
		help="If this is set, any installed package which only has an uninstall script will be set to uninstall",
		action="store_true"
	)
	return parser.parse_args()


def main():
	try:
		args = parse_args()
		logging_config(stderr_level=args.log_level, stderr_format=DEFAULT_COLORED_FORMAT)
		OutdatedToSetupWorker(args).run(dry_run=args.dry_run)
	except Exception as err:  # pylint: disable=broad-except
		logger.error(err, exc_info=True)
		sys.exit(1)
