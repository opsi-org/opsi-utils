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
	LOG_INFO,
	logger,
	logging_config,
)

from opsiutils import __version__
STATIC_EXCLUDE_PRODUCTS = ["opsi-winst", "opsi-auto-update", "opsi-script", "shutdownwanted", "windows10-upgrade", "activate-win", "opsi-script-test", "opsi-bootimage-local", "opsi-uiefi-netboot", "opsi-wan-config", "opsi-winpe", "win10-sysprep-app-update-blocker", "windomain"]


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


def outdated_to_setup(args: Namespace) -> None:
	if args.dry_run:
		logger.notice("operating in dry-run mode - not performing any actions")
	clients = args.clients
	client_groups = args.client_groups

	depot_versions = {}
	client_to_depot = {}
	depending_products = set()
	new_pocs = []
	exclude_products = STATIC_EXCLUDE_PRODUCTS
	if args.exclude_products:
		exclude_products.extend([entry.strip() for entry in args.exclude_products.split(",")])

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
		logger.notice(f"Selected clients: {clients or 'all'}")
		for clientToDepotserver in service.configState_getClientToDepotserver(clientIds=clients):
			client_to_depot[clientToDepotserver["clientId"]] = clientToDepotserver["depotId"]
		for entry in service.productOnDepot_getObjects():
			if not depot_versions.get(entry.depotId):
				depot_versions[entry.depotId] = {}
			depot_versions[entry.depotId][entry.productId] = entry.version
		for pdep in service.productDependency_getObjects():
			depending_products.add(pdep.productId)

		pocs = service.productOnClient_getObjects(clientId=clients or None, productType="LocalbootProduct")
		for entry in pocs:
			if entry.productId in exclude_products:
				continue
			#existing actionRequests are left untouched
			try:
				available = depot_versions[client_to_depot[entry.clientId]][entry.productId]
			except KeyError:
				logger.error("Skipping check of", entry.clientId, entry.productId, "(product not available on depot)")
				continue
			if (entry.actionRequest in (None, "none") and entry.version != available) or (entry.actionResult == "failed" and args.add_failed):
				if entry.productId in depending_products:
					logger.info(f"Setting ProductActionRequest with Dependencies: {entry.productId} -> {entry.clientId}")
					if not args.dry_run:
						service.setProductActionRequestWithDependencies(entry.productId, entry.clientId, "setup")
				else:
					logger.info(f"Marking ProductActionRequest to add: {entry.productId} -> {entry.clientId}")
					if not args.dry_run:
						entry.setActionRequest("setup")
						new_pocs.append(entry)
		if not args.dry_run:
			service.productOnClient_updateObjects(new_pocs)


def parse_args():
	parser = ArgumentParser(description='Set outdated localboot Products to setup.')
	parser.add_argument('--version', '-V', action='version', version=f"{__version__} [python-opsi={python_opsi_version}]")
	parser.add_argument('--log-level', '-l',
						default=LOG_INFO,
						type=int,
						choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
						help="Set log-level (0..9)")
	parser.add_argument("--clients", help="comma-separated list of clients or 'all'")
	parser.add_argument("--dry-run", help="only simulate run", action="store_true")
	parser.add_argument("--client-groups", help="comma-separated list of host groups")
	parser.add_argument("--exclude-products", help="do not set actionRequests for these products")
	parser.add_argument("--add-failed", help="If this is set, it will also add actionRequests for all failed products", action="store_true")

	return parser.parse_args()


def main():
	try:
		print("test")
		args = parse_args()
		logging_config(stderr_level=args.log_level, stderr_format=DEFAULT_COLORED_FORMAT)
		outdated_to_setup(args)
	except Exception as err:  # pylint: disable=broad-except
		logger.error(err, exc_info=True)
		sys.exit(1)
