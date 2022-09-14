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


def outdated_to_setup(args: Namespace) -> None:  # pylint: disable=too-many-branches,too-many-statements
	logger.trace("Called with arguments: %s", args)
	if args.dry_run:
		logger.notice("Operating in dry-run mode - not performing any actions")
	clients = args.clients
	client_groups = args.client_groups

	depot_versions = {}
	client_to_depot = {}
	depending_products = set()
	new_pocs = []
	exclude_products = STATIC_EXCLUDE_PRODUCTS
	include_products = []
	if args.include_products:
		include_products = [entry.strip() for entry in args.include_products.split(",")]
		logger.notice("Limiting handled products to %s", include_products)
	if args.exclude_products:
		exclude_products.extend([entry.strip() for entry in args.exclude_products.split(",")])
	logger.info("List of excluded products: %s", exclude_products)
	with service_connection() as service:
		if clients:
			clients = [entry.strip() for entry in clients.split(",")]
		else:
			clients = []
		if client_groups:
			for group in [entry.strip() for entry in client_groups.split(",")]:
				clients.extend(client_ids_from_group(service, group))
		if not clients:
			logger.notice("No clients selected.")
			return
		if "all" in clients:
			clients = []
		logger.notice("Selected clients: %s", clients or 'all')
		for clientToDepotserver in service.configState_getClientToDepotserver(clientIds=clients):  # pylint: disable=no-member
			client_to_depot[clientToDepotserver["clientId"]] = clientToDepotserver["depotId"]
		logger.trace("ClientToDepot mapping: %s", client_to_depot)
		for entry in service.productOnDepot_getObjects():  # pylint: disable=no-member
			if not depot_versions.get(entry.depotId):
				depot_versions[entry.depotId] = {}
			depot_versions[entry.depotId][entry.productId] = entry.version
		logger.trace("Product versions on depots: %s", depot_versions)
		for pdep in service.productDependency_getObjects():  # pylint: disable=no-member
			depending_products.add(pdep.productId)
		logger.trace("Products with dependencies: %s", depending_products)

		pocs = service.productOnClient_getObjects(clientId=clients or None, productType="LocalbootProduct", productId=include_products or None)  # pylint: disable=no-member
		for entry in pocs:
			logger.debug("Checking %s (%s) on %s", entry.productId, entry.version, entry.clientId)
			if entry.productId in exclude_products:
				logger.info("Skipping as %s is in excluded products", entry.productId)
				continue
			# existing actionRequests are left untouched
			try:
				available = depot_versions[client_to_depot[entry.clientId]][entry.productId]
			except KeyError:
				logger.error("Skipping check of %s %s (product not available on depot)", entry.clientId, entry.productId)
				continue
			if (entry.actionRequest in (None, "none") and entry.version != available) or (entry.actionResult == "failed" and args.add_failed):
				if entry.productId in depending_products:
					logger.info("Setting ProductActionRequest with Dependencies: %s -> %s", entry.productId, entry.clientId)
					if not args.dry_run:
						service.setProductActionRequestWithDependencies(entry.productId, entry.clientId, "setup")  # pylint: disable=no-member
				else:
					logger.info("Marking ProductActionRequest to add: %s -> %s", entry.productId, entry.clientId)
					if not args.dry_run:
						entry.setActionRequest("setup")
						new_pocs.append(entry)
		if not args.dry_run:
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

	return parser.parse_args()


def main():
	try:
		args = parse_args()
		logging_config(stderr_level=args.log_level, stderr_format=DEFAULT_COLORED_FORMAT)
		outdated_to_setup(args)
	except Exception as err:  # pylint: disable=broad-except
		logger.error(err, exc_info=True)
		sys.exit(1)
