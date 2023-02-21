# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-setup - swiss army knife for opsi administration.
"""

# pylint: disable=too-many-lines

import getopt
import os
import sys

from opsicommon.types import forceFilename
from opsicommon.logging import (
	DEFAULT_COLORED_FORMAT,
	LOG_DEBUG,
	LOG_NOTICE,
	init_logging,
	logger,
	logging_config,
)

from OPSI import __version__ as python_opsi_version  # type: ignore[import]
from OPSI.System import Posix  # type: ignore[import]
from OPSI.Util.Task.Rights import setRights  # type: ignore[import]

from opsiutils import __version__

init_logging(stderr_level=LOG_NOTICE, stderr_format=DEFAULT_COLORED_FORMAT)

DHCPD_CONF = Posix.locateDHCPDConfig("/etc/dhcp3/dhcpd.conf")


def usage():
	print(f"\nUsage: {os.path.basename(sys.argv[0])} [options]")
	print("")
	print("Options:")
	print("   -h, --help     show this help")
	print("   -l             log-level 0..9")
	print("   -V, --version  Show version info and exit.")
	print("")
	print("   --log-file <path>             path to log file")
	print("   --set-rights [path]           set default rights on opsi files (in [path] only)")
	print("")


def opsisetup_main():  # pylint: disable=too-many-branches,too-many-return-statements
	try:
		(opts, args) = getopt.getopt(
			sys.argv[1:],
			"hVl:",
			[
				"help",
				"version",
				"log-file=",
				"ip-address=",
				"backend-config=",
				"init-current-config",
				"set-rights",
				"auto-configure-samba",
				"auto-configure-dhcpd",
				"register-depot",
				"configure-mysql",
				"update-mysql",
				"file-to-mysql",
				"edit-config-defaults",
				"cleanup-backend",
				"update-from=",
				"patch-sudoers-file",
				"unattended=",
				"no-backup",
				"no-restart",
			],
		)

	except Exception:
		usage()
		raise

	task = None

	for (opt, arg) in opts:
		if opt in ("-h", "--help"):
			usage()
			return
		if opt in ("-V", "--version"):
			print(f"{__version__} [python-opsi={python_opsi_version}]")
			return

	if os.geteuid() != 0:
		raise RuntimeError("This script must be startet as root")

	for (opt, arg) in opts:
		if opt == "--log-file":
			logging_config(log_file=arg, file_level=LOG_DEBUG)
		elif opt == "-l":
			logging_config(stderr_level=int(arg))
		elif opt == "--init-current-config":
			logger.warning("init-current-config is deprecated. The task is performed automatically at the start of opsiconfd.")
			return
		elif opt == "--set-rights":
			task = "set-rights"
		elif opt == "--register-depot":
			logger.warning("register-depot is deprecated. Use `opsiconfd setup --register-depot` instead.")
			return
		elif opt == "--configure-mysql":
			logger.warning("configure-mysql is deprecated. Use `opsiconfd setup --configure-mysql` instead.")
			return
		elif opt == "--update-mysql":
			logger.warning(
				"update-mysql is deprecated. The task is performed automatically at the start of opsiconfd or by running 'opsiconfd setup'"
			)
			return
		elif opt == "--file-to-mysql":
			logger.warning((
				"file-to-mysql is deprecated. "
				"The task is performed automatically at the start of opsiconfd or by running 'opsiconfd setup'."
			))
			return
		elif opt == "--cleanup-backend":
			logger.warning((
				"cleanup-backend is deprecated. "
				"The task is performed automatically at the start of opsiconfd or by running 'opsiconfd setup'."
			))
			return
		elif opt == "--update-from":
			logger.warning("update-from is deprecated.")
			return
		elif opt == "--auto-configure-samba":
			logger.warning((
				"auto-configure-samba is deprecated. "
				"The task is performed automatically at the start of opsiconfd or by running 'opsiconfd setup'."
		))
			return
		elif opt == "--auto-configure-dhcpd":
			logger.warning((
				"auto-configure-dhcp is deprecated. "
				"The task is performed automatically at the start of opsiconfd or by running 'opsiconfd setup'. "
				"Please make sure that '\"enabled\": True' is set in /etc/opsi/backends/dhcpd.conf."
			))
			return
		elif opt == "--patch-sudoers-file":
			logger.warning((
				"patch-sudoers-file is deprecated. "
				"The task is performed automatically at the start of opsiconfd or by running 'opsiconfd setup'."
			))
			return

	path = "/"
	if len(args) > 0:
		logger.debug("Additional arguments are: %s", args)
		if task == "set-rights" and len(args) == 1:
			path = os.path.abspath(forceFilename(args[0]))
		else:
			usage()
			raise RuntimeError("Too many arguments")

	if task == "set-rights":
		setRights(path)


def main():
	try:
		opsisetup_main()
	except SystemExit as err:
		sys.exit(err.code)
	except Exception as err:  # pylint: disable=broad-except
		logger.error(err, exc_info=True)
		print(f"\nERROR: {err}\n", file=sys.stderr)
		sys.exit(1)
