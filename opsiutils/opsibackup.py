# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-backup
"""

import argparse
import os
import sys

from opsicommon.logging import (
	DEFAULT_COLORED_FORMAT,
	LOG_NOTICE,
	LOG_WARNING,
	init_logging,
	logger,
	logging_config,
)
from OPSI import __version__ as python_opsi_version
from OPSI.Util.Task.Backup import OpsiBackup

from opsiutils import __version__
from opsiutils.opsimakepackage import raw_tty

USAGE = f'''
Usage: {os.path.basename(sys.argv[0])} [common-options] <command> [command-options]

Creates and restores opsi backups.
Version {__version__}.

Common options:
   -h, --help                          Show this help message and exit.
   -v, --verbose                       Log to standard error.
   -V, --version                       Show version info and exit.
   -l, --log-level <log-level>         Set log level to <log-level> (default: 4)
                                          0=nothing, 1=essential, 2=critical, 3=error, 4=warning
                                          5=notice, 6=info, 7=debug, 8=debug2, 9=confidential
   --log-file <log-file>               Log to <log-file>.
                                          Default is /var/log/opsi/opsi-backup.log.

Commands:
   verify <backup-archive>             Verify integrity of <backup-archive>.

   list <backup-archive>               List contents of <backup-archive>.

   restore <backup-archive>            Restore data from <backup-archive>.
      --backends <backend>             Select a backend to restore or 'all' for all backends.
                                          Can be given multiple times.
      --configuration                  Restore opsi configuration.
      -f, --force                      Ignore sanity checks and try to apply anyways. Use with caution!
      --new-server-id <server-id>      Provide a new config server id.
   create [destination]                Create a new backup.
                                          If [destination] is omitted, a backup archive will be created
                                          in the current directory. If [destination] is an existing
                                          directory, the backup archive will be created in that directory.
                                          If [destination] does not exist or is a file, [destination]
                                          will be used as backup archive destination file.
      --flush-logs                     Causes mysql to flush table logs to disk before the backup (recommended).
      --backends <backend>             Select a backend to create a backup for. Use 'auto' for automatic detection
                                          of used backends or 'all' for all backends.
                                          Can be given multiple times.
      -c, --compression {{gz|bz2|none}}  Sets the compression format for the archive (default: bz2).

'''


class HelpFormatter(argparse.HelpFormatter):
	def format_help(self):
		return USAGE


def backup_main():  # pylint: disable=too-many-branches,too-many-statements
	init_logging(stderr_level=LOG_WARNING, stderr_format=DEFAULT_COLORED_FORMAT)

	parser = argparse.ArgumentParser(prog="opsi-backup", add_help=False, usage=USAGE, formatter_class=HelpFormatter)
	parser.add_argument("-h", "--help", action="help")
	parser.add_argument("-v", "--verbose", action="store_true", default=False)
	parser.add_argument("-V", "--version", action='store_true')
	parser.add_argument("-l", "--log-level", default=LOG_NOTICE, type=int, choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
	parser.add_argument("--log-file", metavar='FILE', default="/var/log/opsi/opsi-backup.log")

	subs = parser.add_subparsers(title="commands", dest="command")

	verifyParser = subs.add_parser("verify")
	verifyParser.add_argument("file", nargs="+")

	listParser = subs.add_parser("list")
	listParser.add_argument("file", nargs="+")

	restoreParser = subs.add_parser("restore")
	restoreParser.add_argument("file", nargs=1)
	restoreParser.add_argument("--backends", action="append")
	restoreParser.add_argument("--configuration", action="store_true", default=False)
	restoreParser.add_argument("--new-server-id", action="store")
	restoreParser.add_argument("-f", "--force", action="store_true", default=False)

	creationParser = subs.add_parser("create")
	creationParser.add_argument("destination", nargs="?")
	creationParser.add_argument("--flush-logs", action="store_true", default=False)
	creationParser.add_argument("--backends", action="append")
	creationParser.add_argument("--no-configuration", action="store_true", default=False)
	creationParser.add_argument("-c", "--compression", nargs="?", default="bz2", choices=['gz', 'bz2', 'none'])

	args = parser.parse_args()
	if args.version:
		print(f"{__version__} [python-opsi={python_opsi_version}]")
		return 0

	if args.verbose:
		logging_config(stderr_level=args.log_level)

	if args.log_file:
		logging_config(log_file=args.log_file, file_level=args.log_level)

	backup = OpsiBackup(stdout=sys.stdout)
	result = 0
	if args.command == 'restore':
		if not args.backends:
			logger.debug("No backend given, assuming 'auto'")
			args.backends = ['auto']
		logger.warning("opsi-backup is deprecated, please use `opsiconfd backup` and `opsiconfd restore`")
		configuration = args.configuration
		if configuration:
			print("Do you really want to restore pre opsi 4.3 configuration files? (y/n)", end=" ")
			sys.stdout.flush()
			with raw_tty():
				while True:
					ch = sys.stdin.read(1)
					if ch == "y":
						break
					if ch == "n":
						raise RuntimeError("Aborted `opsi-backup restore` to avoid restoring pre opsi 4.3 configuration files.")
		# restore doesnt return anything, in case of error, an exception is thrown
		backup.restore(
			file=args.file, backends=args.backends,
			configuration=configuration, force=args.force,
			new_server_id=args.new_server_id
		)
	elif args.command == 'verify':
		result = backup.verify(file=args.file)
		if result == 0:
			print("Verified", file=sys.stdout)
		else:
			print("Verification failed", file=sys.stdout)
	elif args.command == 'list':
		if not args.verbose:
			logging_config(stderr_level=LOG_NOTICE)
		backup.list(args.file)
	elif args.command == 'create':
		logger.warning("opsi-backup is deprecated, please use `opsiconfd backup`")
		if os.geteuid() != 0:
			logger.error("Only users with root access can create a backup.")
			raise PermissionError("Permission denied")

		if not args.backends:
			logger.debug("No backends given, assuming 'auto'")
			args.backends = ['auto']

		# create doesnt return anything, in case of error, an exception is thrown
		backup.create(
			destination=args.destination,
			backends=args.backends, noConfiguration=args.no_configuration,
			compression=args.compression, flushLogs=args.flush_logs
		)

	try:
		result = int(result)
	except (TypeError, ValueError):
		pass

	return result


def main():
	try:
		returnCode = backup_main()
	except Exception as err:  # pylint: disable=broad-except
		logger.info(err, exc_info=True)
		print(f"\nERROR: {err}\n", file=sys.stderr)
		returnCode = 1

	sys.exit(returnCode)
