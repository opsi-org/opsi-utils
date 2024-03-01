# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-makepackage - create opsi-packages for deployment.
"""
from __future__ import annotations

import argparse
import fcntl
import gettext
import os
import struct
import sys
import tempfile
import termios
import tty
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from OPSI import __version__ as python_opsi_version  # type: ignore
from OPSI.System import execute  # type: ignore[import]
from OPSI.Types import forceFilename  # type: ignore[import]
from OPSI.Util import compareVersions, md5sum  # type: ignore[import]
from OPSI.Util.File import ZsyncFile  # type: ignore[import]
from OPSI.Util.Message import ProgressObserver, ProgressSubject, Subject  # type: ignore[import]
from opsicommon.logging import (
	DEFAULT_COLORED_FORMAT,
	LOG_DEBUG,
	LOG_ERROR,
	LOG_NONE,
	LOG_WARNING,
	get_logger,
	init_logging,
	logging_config,
)
from opsicommon.objects import NetbootProduct, Product
from opsicommon.package import OpsiPackage
from opsicommon.server.rights import set_rights

from opsiutils import __version__

logger = get_logger()

try:
	sp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	if os.path.exists(os.path.join(sp, "site-packages")):
		sp = os.path.join(sp, "site-packages")
	sp = os.path.join(sp, "opsi-utils_data", "locale")
	translation = gettext.translation("opsi-utils", sp)
	_ = translation.gettext
except Exception as loc_err:
	logger.debug("Failed to load locale from %s: %s", sp, loc_err)

	def _(message: str) -> str:
		"""Fallback function"""
		return message


class CancelledByUserError(Exception):
	pass


class ProgressNotifier(ProgressObserver):
	def __init__(self) -> None:
		self.usedWidth = 60
		try:
			with os.popen("tty") as proc:
				_tty = proc.readline().strip()
			with open(_tty, "rb") as fd:
				terminalWidth = struct.unpack("hh", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"1234"))[1]
			self.usedWidth = min(self.usedWidth, terminalWidth)
		except Exception:
			pass

	def progressChanged(self, subject: Subject, state: int, percent: float, timeSpend: int, timeLeft: int, speed: float) -> None:
		if subject.getEnd() <= 0:
			return

		barlen = self.usedWidth - 10
		filledlen = round(barlen * percent / 100)
		_bar = "=" * filledlen + " " * (barlen - filledlen)
		percent_str = f"{percent:0.2f}%"
		sys.stderr.write(f"\r {percent_str:>8} [{_bar}]\r")
		sys.stderr.flush()

	def messageChanged(self, subject: Subject, message: str) -> None:
		sys.stderr.write(f"\n{message}\n")
		sys.stderr.flush()


@contextmanager
def raw_tty() -> Generator[None, None, None]:
	fd = sys.stdin.fileno()
	# fl = fcntl.fcntl(fd, fcntl.F_GETFL)
	at = termios.tcgetattr(fd)
	# fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
	tty.setraw(fd)
	try:
		yield
	finally:
		# fcntl.fcntl(fd, fcntl.F_SETFL, fl)
		# termios.tcsetattr(fd, termios.TCSADRAIN, at)
		termios.tcsetattr(fd, termios.TCSANOW, at)


def print_info(product: Product, customName: str, opsi_package: OpsiPackage) -> None:
	print("")
	print(_("Package info"))
	print("----------------------------------------------------------------------------")
	print("   %-20s : %s" % ("version", product.packageVersion))
	print("   %-20s : %s" % ("custom package name", customName))
	print(
		"   %-20s : %s"
		% (
			"package dependencies",
			", ".join(f"{dep.package}({dep.condition}{dep.version})" for dep in opsi_package.package_dependencies),
		)
	)

	print("")
	print(_("Product info"))
	print("----------------------------------------------------------------------------")
	print("   %-20s : %s" % ("product id", product.id))

	if product.getType() == "LocalbootProduct":
		print("   %-20s : %s" % ("product type", "localboot"))
	elif product.getType() == "NetbootProduct":
		print("   %-20s : %s" % ("product type", "netboot"))

	print("   %-20s : %s" % ("version", product.productVersion))
	print("   %-20s : %s" % ("name", product.name))
	print("   %-20s : %s" % ("description", product.description))
	print("   %-20s : %s" % ("advice", product.advice))
	print("   %-20s : %s" % ("priority", product.priority))
	print("   %-20s : %s" % ("licenseRequired", product.licenseRequired))
	print("   %-20s : %s" % ("product classes", ", ".join(product.productClassIds or [])))
	print("   %-20s : %s" % ("windows software ids", ", ".join(product.windowsSoftwareIds or [])))

	if isinstance(product, NetbootProduct):
		print("   %-20s : %s" % ("pxe config template", product.pxeConfigTemplate))

	print("")
	print(_("Product scripts"))
	print("----------------------------------------------------------------------------")
	print("   %-20s : %s" % ("setup", product.setupScript))
	print("   %-20s : %s" % ("uninstall", product.uninstallScript))
	print("   %-20s : %s" % ("update", product.updateScript))
	print("   %-20s : %s" % ("always", product.alwaysScript))
	print("   %-20s : %s" % ("once", product.onceScript))
	print("   %-20s : %s" % ("custom", product.customScript))
	if product.getType() == "LocalbootProduct":
		print("   %-20s : %s" % ("user login", product.userLoginScript))
	print("")


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		add_help=False,
		description=(
			"Provides an opsi package from a package source directory.\n"
			"If no source directory is supplied, the current directory will be used."
		),
	)
	parser.add_argument("--help", action="store_true", default=False, help="Show help.")  # Manual implementation because of -h
	parser.add_argument("--version", "-V", action="version", version=f"{__version__} [python-opsi={python_opsi_version}]")
	parser.add_argument("--quiet", "-q", action="store_true", default=False, help="do not show progress")
	parser.add_argument("--verbose", "-v", default=False, action="store_true", help="verbose")
	parser.add_argument(
		"--log-level",
		"-l",
		dest="logLevel",
		default=LOG_WARNING,
		type=int,
		choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
		help="Set log-level (0..9)",
	)
	parser.add_argument("--no-compression", "-n", action="store_true", default=False, help="Do not compress")
	parser.add_argument(
		"--compression", default=None, choices=["zstd", "bz2", "bzip2", "gz", "gzip"], help="Compression format (default: zstd)"
	)

	parser.add_argument(
		"--archive-format",
		"-F",
		dest="format",
		default="tar",
		choices=["tar"],
		help="DEPRECATED: Archive format to use. Default: tar",
	)
	parser.add_argument("--no-pigz", dest="disablePigz", default=False, action="store_true", help="Disable the usage of pigz")
	parser.add_argument(
		"--no-set-rights",
		dest="no_set_rights",
		default=False,
		action="store_true",
		help="Disable the setting of rights while building",
	)
	parser.add_argument("--follow-symlinks", "-h", dest="dereference", help="follow symlinks", default=False, action="store_true")
	customGroup = parser.add_mutually_exclusive_group()
	customGroup.add_argument(
		"--custom-name",
		"-i",
		metavar="custom name",
		dest="customName",
		default="",
		help="Add custom files and add custom name to the base package.",
	)
	customGroup.add_argument(
		"--custom-only",
		"-c",
		metavar="custom name",
		dest="customOnly",
		default=False,
		help="Only package custom files and add custom name to base package.",
	)
	parser.add_argument("--temp-directory", "-t", dest="tempDir", help="temp dir", default="/tmp", metavar="directory")
	parser.add_argument("--control-to-toml", action="store_true", default=False, help="Convert control file to toml format")
	hashSumGroup = parser.add_mutually_exclusive_group()
	hashSumGroup.add_argument(
		"--md5",
		"-m",
		dest="createMd5SumFile",
		default=True,
		action="store_true",
		help="Create file with md5 checksum.",
	)
	hashSumGroup.add_argument("--no-md5", dest="createMd5SumFile", action="store_false", help="Do not create file with md5 checksum.")
	zsyncGroup = parser.add_mutually_exclusive_group()
	zsyncGroup.add_argument(
		"--zsync",
		"-z",
		dest="createZsyncFile",
		default=True,
		action="store_true",
		help="Create zsync file.",
	)
	zsyncGroup.add_argument("--no-zsync", dest="createZsyncFile", action="store_false", help="Do not create zsync file.")
	parser.add_argument("packageSourceDir", metavar="source directory", nargs="?", default=os.getcwd())

	vgroup = parser.add_argument_group("Versions", "Set versions for package. Combinations are possible.")
	vgroup.add_argument("--keep-versions", "-k", action="store_true", help="Keep versions and overwrite package", dest="keepVersions")
	vgroup.add_argument(
		"--package-version", help="Set new package version ", default="", metavar="packageversion", dest="newPackageVersion"
	)
	vgroup.add_argument(
		"--product-version",
		default="",
		dest="newProductVersion",
		metavar="productversion",
		help="Set new product version for package",
	)

	args_namespace = parser.parse_args(args)  # falls back to sys.argv if None

	if args_namespace.help:
		parser.print_help()
		sys.exit(1)

	if not args_namespace.compression:
		if Path("/etc/opsi/makepackage_marker_use_gz").exists():
			logger.warning("Overriding compression to use 'gz' because of marker '/etc/opsi/makepackage_marker_use_gz'")
			args_namespace.compression = "gz"
		else:
			args_namespace.compression = "zstd"
	elif args_namespace.compression == "gzip":
		args_namespace.compression = "gz"
	elif args_namespace.compression == "bzip2":
		args_namespace.compression = "bz2"

	return args_namespace


def makepackage_main(str_args: list[str] | None = None) -> None:
	os.umask(0o022)

	init_logging(stderr_level=LOG_WARNING, stderr_format=DEFAULT_COLORED_FORMAT)

	args = parse_args(str_args)

	keepVersions = args.keepVersions
	needOneVersion = False
	newProductVersion = args.newProductVersion
	newPackageVersion = args.newPackageVersion
	if newProductVersion or newPackageVersion:
		needOneVersion = True
	doNotUseTerminal = False
	if keepVersions:
		doNotUseTerminal = True
	if newPackageVersion and newProductVersion:
		doNotUseTerminal = True

	customName = args.customName
	customOnly = bool(args.customOnly)
	if customOnly:
		customName = args.customOnly
	logLevel = args.logLevel
	compression = args.compression
	quiet = args.quiet
	tempDir = Path(forceFilename(args.tempDir))
	packageSourceDir = args.packageSourceDir

	if args.no_compression:
		raise ValueError("The option --no-compression has been removed. Default compression is zstd")
	if args.disablePigz:
		logger.warning("The option --no-pigz is deprecated. Default is to try pigz with a fallback in case of error")

	if args.verbose:
		logLevel = LOG_DEBUG

	if quiet:
		logLevel = LOG_NONE

	logging_config(stderr_level=logLevel)

	logger.info("Source dir: %s", packageSourceDir)
	logger.info("Temp dir: %s", tempDir)
	logger.info("Custom name: %s", customName)

	if not os.path.isdir(packageSourceDir):
		raise OSError(f"No such directory: {packageSourceDir}")

	if customName:
		packageControlFilePath = Path(packageSourceDir) / f"OPSI.{customName}" / "control.toml"
		if not packageControlFilePath.exists():
			packageControlFilePath = Path(packageSourceDir) / f"OPSI.{customName}" / "control"
	if not customName or not packageControlFilePath.exists():
		packageControlFilePath = Path(packageSourceDir) / "OPSI" / "control.toml"
	if not packageControlFilePath.exists():
		packageControlFilePath = packageControlFilePath.with_suffix("")  # strip .toml to fall back to old behaviour
		if not packageControlFilePath.exists():
			raise OSError(f"Control file '{packageControlFilePath}' not found")

	if not quiet:
		print("")
		print(_("Locking package"))
	opsi_package = OpsiPackage(temp_dir=tempDir)
	opsi_package.parse_control_file(packageControlFilePath)

	if packageControlFilePath.suffix == ".toml" and packageControlFilePath.with_suffix("").exists():
		opsi_package_tmp = OpsiPackage(temp_dir=tempDir)
		opsi_package_tmp.parse_control_file_legacy(packageControlFilePath.with_suffix(""))
		if compareVersions(opsi_package_tmp.product.version, ">", opsi_package.product.version):
			raise ValueError("control is newer than control.toml - Please update control.toml instead.")

	destination_dir = Path.cwd()
	archive = destination_dir / opsi_package.package_archive_name()
	if customName:
		archive = archive.parent / f"{archive.stem}~{customName}.opsi"
	lockPackage(tempDir, opsi_package)
	try:
		while True:
			if not quiet:
				print_info(opsi_package.product, customName, opsi_package)
			if not quiet and archive.exists():
				print(_("Package file '%s' already exists.") % archive)
				print(_("Press <O> to overwrite, <C> to abort or <N> to specify a new version:"), end=" ")
				sys.stdout.flush()
				newVersion: str | bool = False
				if keepVersions and needOneVersion:
					newVersion = True
				elif keepVersions:
					for path in (archive, Path(f"{archive}.md5"), Path(f"{archive}.zsync")):
						if path.exists():
							path.unlink()
				elif needOneVersion:
					newVersion = True

				if not doNotUseTerminal:
					with raw_tty():
						try:
							while True:
								ch = sys.stdin.read(1)
								if ch in ("o", "O"):
									for path in (archive, Path(f"{archive}.md5"), Path(f"{archive}.zsync")):
										if path.exists():
											path.unlink()
									break
								if ch in ("c", "C"):
									raise RuntimeError(_("Aborted"))
								if ch in ("n", "N"):
									newVersion = True
									break
						finally:
							print("\r\033[0K")

				if newVersion:
					while True:
						print(
							"\r%s"
							% _("Please specify new product version, press <ENTER> to keep current version (%s):")
							% opsi_package.product.productVersion,
							end=" ",
						)
						newVersion = newProductVersion
						if not keepVersions and not needOneVersion:
							newVersion = sys.stdin.readline().strip()
						else:
							if newProductVersion:
								newVersion = newProductVersion
							elif keepVersions:
								newVersion = opsi_package.product.productVersion
							else:
								newVersion = sys.stdin.readline().strip()

						try:
							if newVersion:
								opsi_package.product.setProductVersion(str(newVersion))
								opsi_package.generate_control_file(packageControlFilePath)
							break
						except Exception:
							print(_("Bad product version: %s") % newVersion)

					while True:
						print(
							"\r%s"
							% _("Please specify new package version, press <ENTER> to keep current version (%s):")
							% opsi_package.product.packageVersion,
							end=" ",
						)
						newVersion = newPackageVersion
						if not keepVersions and not needOneVersion:
							newVersion = sys.stdin.readline().strip()
						else:
							if newPackageVersion:
								newVersion = newPackageVersion
							elif keepVersions:
								newVersion = opsi_package.product.packageVersion
							else:
								newVersion = sys.stdin.readline().strip()

						try:
							if newVersion:
								opsi_package.product.setPackageVersion(str(newVersion))
								opsi_package.generate_control_file(packageControlFilePath)
							break
						except Exception:
							print(_("Bad package version: %s") % newVersion)

				archive = destination_dir / opsi_package.package_archive_name()
				if customName:
					archive = archive.parent / f"{archive.stem}~{customName}.opsi"
				if archive.exists():
					continue

			# Regenerating to fix encoding
			opsi_package.generate_control_file(packageControlFilePath)
			if args.control_to_toml:
				if packageControlFilePath.suffix == ".toml":
					raise ValueError("Already using toml format, do not use --control-to-toml")
				logger.notice("Creating control.toml from control.")
				opsi_package.generate_control_file(packageControlFilePath.with_suffix(".toml"))
				if not packageControlFilePath.with_suffix(".toml").exists():
					raise RuntimeError("Failed to create control.toml")
			elif packageControlFilePath.suffix == ".toml":
				opsi_package.generate_control_file(packageControlFilePath.with_suffix(""))

			progressSubject = None
			if not quiet:
				progressSubject = ProgressSubject("packing")
				progressSubject.attachObserver(ProgressNotifier())
				print(_("Creating package file '%s'") % archive)
			base_dir = Path(packageSourceDir)
			use_dirs = [base_dir / "CLIENT_DATA", base_dir / "SERVER_DATA", base_dir / "OPSI"]
			if customName:
				found = False
				for _dir in use_dirs.copy():
					if (base_dir / f"{_dir.name}.{customName}").exists():
						if customOnly:
							use_dirs.remove(_dir)
						use_dirs.append(base_dir / f"{_dir.name}.{customName}")
						found = True
				if not found:
					raise RuntimeError(f"No custom dirs found for '{customName}'")
				logger.info("Packing directories: %s", use_dirs)
				with tempfile.TemporaryDirectory(dir=Path()) as local_tmp_dir:
					created_archive = opsi_package.create_package_archive(
						base_dir, compression=compression, dereference=args.dereference, use_dirs=use_dirs, destination=Path(local_tmp_dir)
					)
					created_archive.rename(archive)
			else:
				created_archive = opsi_package.create_package_archive(
					base_dir, compression=compression, dereference=args.dereference, use_dirs=use_dirs
				)
			if not args.no_set_rights:
				try:
					set_rights(archive)
				except Exception as err:
					logger.warning("Failed to set rights: %s", err)
			if not quiet:
				print("\n")
			if args.createMd5SumFile:
				md5sumFile = f"{archive}.md5"
				if not quiet:
					print(_("Creating md5sum file '%s'") % md5sumFile)
				md5 = md5sum(str(archive))
				with open(md5sumFile, "w", encoding="utf-8") as file:
					file.write(md5)
				if not args.no_set_rights:
					try:
						set_rights(md5sumFile)
					except Exception as err:
						logger.warning("Failed to set rights: %s", err)

			if args.createZsyncFile:
				zsyncFilePath = f"{archive}.zsync"
				if not quiet:
					print(_("Creating zsync file '%s'") % zsyncFilePath)
				zsyncFile = ZsyncFile(zsyncFilePath)
				zsyncFile.generate(archive)
				if not args.no_set_rights:
					try:
						set_rights(zsyncFilePath)
					except Exception as err:
						logger.warning("Failed to set rights: %s", err)
			break
	finally:
		if not quiet:
			print(_("Unlocking package"))
		unlockPackage(tempDir, opsi_package)
		if not quiet:
			print("")


def lockPackage(tempDir: Path, opsiPackage: OpsiPackage) -> None:
	lockFile = os.path.join(tempDir, f".opsi-makepackage.lock.{opsiPackage.product.id}")
	# Test if other processes are accessing same product
	try:
		with open(lockFile, "r", encoding="utf-8") as file:
			pid = file.read().strip()

		if pid:
			for line in execute("ps -A"):
				line = line.strip()
				if not line:
					continue
				if pid == line.split()[0].strip():
					pName = line.split()[-1].strip()
					# process is running
					raise RuntimeError(f"Product '{opsiPackage.product.id}' is currently locked by process {pName} ({pid}).")

	except IOError:
		pass

	# Write lock-file
	with open(lockFile, "w", encoding="utf-8") as file:
		file.write(str(os.getpid()))


def unlockPackage(tempDir: Path, opsi_package: OpsiPackage) -> None:
	lockFile = os.path.join(tempDir, f".opsi-makepackage.lock.{opsi_package.product.id}")
	if os.path.isfile(lockFile):
		os.unlink(lockFile)


def main() -> None:
	try:
		makepackage_main()
	except SystemExit as err:
		sys.exit(err.code)
	except Exception as err:
		logging_config(stderr_level=LOG_ERROR)
		logger.error(err, exc_info=True)
		print(f"ERROR: {err}", file=sys.stderr)
		sys.exit(1)
