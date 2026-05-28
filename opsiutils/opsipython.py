# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-python interpreter for custom opsi python scripts
"""

import argparse
import os
import sys
import traceback
import warnings
from typing import Any

import opsi_legacy
import opsi_legacy.Backend.Manager._Manager
from opsi.opsi.service.client import ServiceClient, ServiceVerificationFlags
from opsi.opsi.service.server import OpsiConfig
from opsi.opsi.service.server._config import OPSI_CA_CERT_FILE

from opsiutils import __version__

sys.modules["OPSI"] = opsi_legacy

import OPSI.Backend.Manager._Manager  # ty: ignore[unresolved-import] # noqa


class BackendManager(ServiceClient):
	"""
	For backwards compatibility
	"""

	def __init__(self, username: str | None = None, password: str | None = None, **kwargs: Any) -> None:
		warnings.warn("BackendManager is deprecated, please use opsi.opsi.service.client.get_service_client()")
		opsi_config = OpsiConfig(upgrade_config=False)
		super().__init__(
			address=opsi_config.get("service", "url"),
			username=username or opsi_config.get("host", "id"),
			password=password or opsi_config.get("host", "key"),
			user_agent=f"opsi-python/{__version__}",
			# BackendManager can only be used to connect to the local opsi service.
			# Using local CA cert file read-only with strict verification and.
			ca_cert_file=OPSI_CA_CERT_FILE,
			verify=ServiceVerificationFlags.STRICT_CHECK,
			jsonrpc_create_objects=True,
			jsonrpc_create_methods=True,
		)
		self.connect()


# Replace BackendManager with compatibility class
OPSI.Backend.Manager._Manager.BackendManager = BackendManager
opsi_legacy.Backend.Manager._Manager.BackendManager = BackendManager  # ty: ignore[invalid-assignment]


def add_systempackages_to_path() -> None:
	ver = sys.version_info
	for path in (
		f"/usr/lib/python{ver.major}.{ver.minor}",
		f"/usr/lib/python{ver.major}.{ver.minor}/lib-dynload",
		f"/usr/local/lib/python{ver.major}.{ver.minor}/dist-packages",
		f"/usr/lib/python{ver.major}/dist-packages",
	):
		if os.path.exists(path):
			sys.path.append(path)


def run_script() -> None:
	script = sys.argv[1]
	sys.argv.pop(0)

	imp_new_module = type(sys)
	new_module = imp_new_module(script)
	new_module.__dict__["__name__"] = "__main__"
	new_module.__dict__["__file__"] = script

	with open(script, "r", encoding="utf-8") as file:
		code = file.read()

	add_systempackages_to_path()
	exec(code, new_module.__dict__)


def run_interactive() -> None:
	import code

	add_systempackages_to_path()
	code.interact(local=locals())


def main() -> None:
	try:
		parser = argparse.ArgumentParser(add_help=False)
		parser.add_argument("-V", "--version", action="store_true", help="print the Python version number and exit")
		parser.add_argument("-h", "--help", action="store_true", help="print this help message and exit")
		group = parser.add_mutually_exclusive_group()
		group.add_argument("-c", metavar="cmd", required=False, help="program passed in as string")
		group.add_argument("file", nargs="?", help="program read from script file")
		parser.add_argument("arg", nargs="*", help="arguments passed to program in sys.argv[1:]")
		args, _ = parser.parse_known_args()

		if args.file:
			run_script()
			return

		if args.help:
			parser.print_help()
			return

		if args.version:
			print(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
			return

		if args.c:
			add_systempackages_to_path()
			exec(args.c)
			return

		run_interactive()
	except Exception:
		traceback.print_exc()
		sys.exit(1)
