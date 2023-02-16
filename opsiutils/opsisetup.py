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

from OPSI import __version__ as python_opsi_version
from OPSI.System import Posix
from OPSI.Types import forceFilename
from OPSI.Util.Task.BackendMigration import migrate_file_to_mysql
from OPSI.Util.Task.CleanupBackend import cleanupBackend
from OPSI.Util.Task.ConfigureBackend.ConfigDefaults import editConfigDefaults
from OPSI.Util.Task.ConfigureBackend.DHCPD import configureDHCPD
from OPSI.Util.Task.Rights import setRights
from OPSI.Util.Task.Samba import configureSamba
from OPSI.Util.Task.Sudoers import patchSudoersFileForOpsi
from opsicommon.logging import (
	DEFAULT_COLORED_FORMAT,
	LOG_DEBUG,
	LOG_NOTICE,
	init_logging,
	logger,
	logging_config,
)
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
	print("   --backend-config <json hash>  overwrite backend config hash values")
	print("   --set-rights [path]           set default rights on opsi files (in [path] only)")
	print("   --file-to-mysql               migrate file to mysql backend and adjust dispatch.conf")
	print("     --no-backup                 do not run a backup before migration")
	print("     --no-restart                do not restart services on migration")
	print("   --edit-config-defaults        edit global config defaults")
	print("   --auto-configure-samba        patch smb.conf")
	print("   --auto-configure-dhcpd        patch dhcpd.conf")
	print("   --patch-sudoers-file	        patching sudoers file for tasks in opsiadmin context.")
	print("")


def opsisetup_main():  # pylint: disable=too-many-branches.too-many-statements
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
	autoConfigureSamba = False
	autoConfigureDhcpd = False
	noBackup = False
	noRestart = False

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
			logger.warning("update-mysql is deprecated. The task is performed automatically at the start of opsiconfd.")
			return
		elif opt == "--file-to-mysql":
			task = "file-to-mysql"
		elif opt == "--edit-config-defaults":
			task = "edit-config-defaults"
		elif opt == "--cleanup-backend":
			logger.warning("cleanup-backend is deprecated. The task is performed automatically at the start of opsiconfd.")
			return
		elif opt == "--no-backup":
			noBackup = True
		elif opt == "--no-restart":
			noRestart = True
		elif opt == "--update-from":
			logger.warning("update-from is deprecated.")
			return
		elif opt == "--auto-configure-samba":
			autoConfigureSamba = True
		elif opt == "--auto-configure-dhcpd":
			autoConfigureDhcpd = True
		elif opt == "--patch-sudoers-file":
			task = "patch-sudoers-file"

	path = "/"
	if len(args) > 0:
		logger.debug("Additional arguments are: %s", args)
		if task == "set-rights" and len(args) == 1:
			path = os.path.abspath(forceFilename(args[0]))
		else:
			usage()
			raise RuntimeError("Too many arguments")

	if noBackup and task != "file-to-mysql":
		raise RuntimeError("--no-backup only valid with --file-to-mysql")
	if noRestart and task != "file-to-mysql":
		raise RuntimeError("--no-restart only valid with --file-to-mysql")

	if autoConfigureSamba:
		configureSamba()

	if autoConfigureDhcpd:
		configureDHCPD()

	if task == "set-rights":
		setRights(path)

	elif task == "file-to-mysql":
		if not migrate_file_to_mysql(create_backup=not noBackup, restart_services=not noRestart):
			# Nothing to do
			sys.exit(2)

	elif task == "edit-config-defaults":
		editConfigDefaults()

	elif task == "cleanup-backend":
		cleanupBackend()

	elif task == "patch-sudoers-file":
		patchSudoersFileForOpsi()

	elif not autoConfigureSamba and not autoConfigureDhcpd:
		usage()
		sys.exit(1)


def main():
	try:
		opsisetup_main()
	except SystemExit as err:
		sys.exit(err.code)
	except Exception as err:  # pylint: disable=broad-except
		logger.error(err, exc_info=True)
		print(f"\nERROR: {err}\n", file=sys.stderr)
		sys.exit(1)
