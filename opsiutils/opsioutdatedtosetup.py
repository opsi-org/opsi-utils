# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-outdated-to-setup - set action_requests where outdated.
"""

import sys
from argparse import ArgumentParser
from subprocess import check_call

from OPSI import __version__ as python_opsi_version
from opsicommon.logging import logger

from opsiutils import __version__

def parse_args():
	parser = ArgumentParser(description='Set outdated localboot Products to setup.')
	parser.add_argument('--version', '-V', action='version', version=f"{__version__} [python-opsi={python_opsi_version}]")
	parser.add_argument('--log-level', '-l', default=5, type=int, choices=range(10), help="Set log-level (0..9)")
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
		opsi_cli_call = [
			"opsi-cli",
			"-l",
			str(args.log_level),
		]
		if args.dry_run:
			opsi_cli_call.append("--dry-run")
		opsi_cli_call.append("client-action")
		if args.clients:
			opsi_cli_call.extend([
				"--clients",
				args.clients
			])
		if args.client_groups:
			opsi_cli_call.extend([
				"--client-groups",
				args.client_groups
			])
		opsi_cli_call.extend([
			"set-action-request",
			"--where-outdated"
		])
		if args.include_products:
			opsi_cli_call.extend([
				"--products",
				args.include_products
			])
		if args.exclude_products:
			opsi_cli_call.extend([
				"--exclude-products",
				args.exclude_products
			])
		if args.add_failed:
			opsi_cli_call.append("--where-failed")
		if args.uninstall_where_only_uninstall:
			opsi_cli_call.append("--uninstall-where-only-uninstall")
		logger.essential("Executing '%s'", opsi_cli_call)
		check_call(opsi_cli_call)
	except Exception as err:  # pylint: disable=broad-except
		logger.error(err)
		sys.exit(1)
