# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-makepackage - create opsi-packages for deployment.
"""

import argparse
import fcntl
import gettext
import os
from pathlib import Path
import struct
import sys
import termios
import tty
from contextlib import contextmanager

import OPSI.Util.File.Archive
from OPSI import __version__ as python_opsi_version
from OPSI.System import execute
from OPSI.Types import forceFilename, forceUnicode
from OPSI.Util import md5sum
from OPSI.Util.File import ZsyncFile
from OPSI.Util.File.Opsi import PackageControlFile
from OPSI.Util.Message import ProgressObserver, ProgressSubject
from OPSI.Util.Product import ProductPackageSource
from OPSI.Util.Task.Rights import setRights
from opsicommon.logging import (
	DEFAULT_COLORED_FORMAT,
	LOG_DEBUG,
	LOG_ERROR,
	LOG_NONE,
	LOG_WARNING,
	init_logging,
	logger,
	logging_config,
)

from opsiutils import __version__

try:
	sp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	if os.path.exists(os.path.join(sp, "site-packages")):
		sp = os.path.join(sp, "site-packages")
	sp = os.path.join(sp, 'opsi-utils_data', 'locale')
	translation = gettext.translation('opsi-utils', sp)
	_ = translation.gettext
except Exception as loc_err:  # pylint: disable=broad-except
	logger.debug("Failed to load locale from %s: %s", sp, loc_err)

	def _(string):
		""" Fallback function """
		return string


class CancelledByUserError(Exception):
	pass


class ProgressNotifier(ProgressObserver):
	def __init__(self):  # pylint: disable=super-init-not-called
		self.usedWidth = 60
		try:
			with os.popen('tty') as proc:
				_tty = proc.readline().strip()
			with open(_tty, "rb") as fd:
				terminalWidth = struct.unpack('hh', fcntl.ioctl(fd, termios.TIOCGWINSZ, '1234'))[1]
			self.usedWidth = min(self.usedWidth, terminalWidth)
		except Exception:  # pylint: disable=broad-except
			pass

	def progressChanged(self, subject, state, percent, timeSpend, timeLeft, speed):  # pylint: disable=too-many-arguments
		if subject.getEnd() <= 0:
			return

		barlen = self.usedWidth - 10
		filledlen = round(barlen * percent / 100)
		_bar = '='*filledlen + ' ' * (barlen - filledlen)
		percent = f'{percent:0.2f}%'
		sys.stderr.write(f'\r {percent:>8} [{_bar}]\r')
		sys.stderr.flush()

	def messageChanged(self, subject, message):
		sys.stderr.write(f'\n{message}\n')
		sys.stderr.flush()

@contextmanager
def raw_tty():
	fd = sys.stdin.fileno()
	#fl = fcntl.fcntl(fd, fcntl.F_GETFL)
	at = termios.tcgetattr(fd)
	#fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
	tty.setraw(fd)
	try:
		yield
	finally:
		#fcntl.fcntl(fd, fcntl.F_SETFL, fl)
		#termios.tcsetattr(fd, termios.TCSADRAIN, at)
		termios.tcsetattr(fd, termios.TCSANOW, at)

def print_info(product, customName, pcf):
	print("")
	print(_("Package info"))
	print("----------------------------------------------------------------------------")
	print("   %-20s : %s" % ('version', product.packageVersion))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('custom package name', customName))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % (  # pylint: disable=consider-using-f-string
		'package dependencies',
		', '.join('{package}({condition}{version})'.format(**dep) for dep in pcf.getPackageDependencies()))  # pylint: disable=consider-using-f-string
	)

	print("")
	print(_("Product info"))
	print("----------------------------------------------------------------------------")
	print("   %-20s : %s" % ('product id', product.id))  # pylint: disable=consider-using-f-string

	if product.getType() == 'LocalbootProduct':
		print("   %-20s : %s" % ('product type', 'localboot'))  # pylint: disable=consider-using-f-string
	elif product.getType() == 'NetbootProduct':
		print("   %-20s : %s" % ('product type', 'netboot'))  # pylint: disable=consider-using-f-string

	print("   %-20s : %s" % ('version', product.productVersion))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('name', product.name))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('description', product.description))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('advice', product.advice))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('priority', product.priority))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('licenseRequired', product.licenseRequired))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('product classes', ', '.join(product.productClassIds)))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('windows software ids', ', '.join(product.windowsSoftwareIds)))  # pylint: disable=consider-using-f-string

	if product.getType() == 'NetbootProduct':
		print("   %-20s : %s" % ('pxe config template', product.pxeConfigTemplate))  # pylint: disable=consider-using-f-string

	print("")
	print(_("Product scripts"))
	print("----------------------------------------------------------------------------")
	print("   %-20s : %s" % ('setup', product.setupScript))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('uninstall', product.uninstallScript))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('update', product.updateScript))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('always', product.alwaysScript))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('once', product.onceScript))  # pylint: disable=consider-using-f-string
	print("   %-20s : %s" % ('custom', product.customScript))  # pylint: disable=consider-using-f-string
	if product.getType() == 'LocalbootProduct':
		print("   %-20s : %s" % ('user login', product.userLoginScript))  # pylint: disable=consider-using-f-string
	print("")

def parse_args():
	parser = argparse.ArgumentParser(add_help=False,
		description=("Provides an opsi package from a package source directory.\n"
				"If no source directory is supplied, the current directory will be used.")
	)
	parser.add_argument('--help', action='store_true', default=False,
						help="Show help.")  # Manual implementation because of -h
	parser.add_argument('--version', '-V', action='version', version=f"{__version__} [python-opsi={python_opsi_version}]")
	parser.add_argument('--quiet', '-q', action='store_true', default=False,
						help="do not show progress")
	parser.add_argument('--verbose', '-v', default=False, action="store_true",
						help="verbose")
	parser.add_argument('--log-level', '-l', dest="logLevel",
						default=LOG_WARNING,
						type=int,
						choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
						help="Set log-level (0..9)")
	parser.add_argument('--no-compression', '-n',
						action='store_true', default=False,
						help="Do not compress")
	parser.add_argument('--compression',
						default='gzip', choices=['gzip', 'zstd'],
						help="Compression format")
	parser.add_argument('--archive-format', '-F', dest="format", default='cpio', choices=['cpio', 'tar'],
						help="Archive format to use. Default: cpio")
	parser.add_argument('--no-pigz', dest="disablePigz",
					default=False, action='store_true',
					help="Disable the usage of pigz")
	parser.add_argument('--no-set-rights', dest="no_set_rights",
					default=False, action='store_true',
					help="Disable the setting of rights while building")
	parser.add_argument('--follow-symlinks', '-h',
						dest="dereference", help="follow symlinks",
						default=False, action='store_true')
	customGroup = parser.add_mutually_exclusive_group()
	customGroup.add_argument('--custom-name', '-i', metavar='custom name',
							dest="customName", default='',
							help="Add custom files and add custom name to the base package.")
	customGroup.add_argument('--custom-only', '-c', metavar='custom name',
							dest="customOnly", default=False,
							help="Only package custom files and add custom name to base package.")
	parser.add_argument('--temp-directory', '-t',
						dest="tempDir", help="temp dir", default='/tmp',
						metavar='directory')
	parser.add_argument('--control-to-toml', action='store_true', default=False, help="Convert control file to toml format")
	hashSumGroup = parser.add_mutually_exclusive_group()
	hashSumGroup.add_argument(
		'--md5', '-m',
		dest="createMd5SumFile", default=True, action='store_true',
		help="Create file with md5 checksum.")
	hashSumGroup.add_argument(
		'--no-md5', dest="createMd5SumFile", action='store_false',
		help="Do not create file with md5 checksum.")
	zsyncGroup = parser.add_mutually_exclusive_group()
	zsyncGroup.add_argument(
		'--zsync', '-z', dest="createZsyncFile",
		default=True, action='store_true',
		help="Create zsync file.")
	zsyncGroup.add_argument(
		'--no-zsync', dest="createZsyncFile", action='store_false',
		help="Do not create zsync file.")
	parser.add_argument('packageSourceDir', metavar="source directory",
						nargs='?', default=os.getcwd())

	vgroup = parser.add_argument_group('Versions',
		'Set versions for package. Combinations are possible.')
	vgroup.add_argument('--keep-versions', '-k', action='store_true',
				help="Keep versions and overwrite package", dest="keepVersions")
	vgroup.add_argument('--package-version', help="Set new package version ",
				default='', metavar='packageversion', dest="newPackageVersion")
	vgroup.add_argument('--product-version', default='',
				dest="newProductVersion", metavar='productversion',
				help="Set new product version for package")

	args = parser.parse_args()
	if args.help:
		parser.print_help()
		sys.exit(1)
	if args.no_compression:
		args.compression = None
	return args

def makepackage_main():  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
	os.umask(0o022)

	init_logging(stderr_level=LOG_WARNING, stderr_format=DEFAULT_COLORED_FORMAT)

	args = parse_args()

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
	dereference = args.dereference
	logLevel = args.logLevel
	compression = args.compression
	quiet = args.quiet
	tempDir = forceFilename(args.tempDir)
	arch_format = forceUnicode(args.format)
	createMd5SumFile = args.createMd5SumFile
	createZsyncFile = args.createZsyncFile
	packageSourceDir = args.packageSourceDir
	disablePigz = args.disablePigz

	if args.verbose:
		logLevel = LOG_DEBUG

	if quiet:
		logLevel = LOG_NONE

	logging_config(stderr_level=logLevel)

	logger.info("Source dir: %s", packageSourceDir)
	logger.info("Temp dir: %s", tempDir)
	logger.info("Custom name: %s", customName)
	logger.info("Archive format: %s", arch_format)

	if arch_format not in ['tar', 'cpio']:
		raise ValueError(f"Unsupported archive format: {arch_format}")

	if not os.path.isdir(packageSourceDir):
		raise OSError(f"No such directory: {packageSourceDir}")

	if customName:
		packageControlFilePath = Path(packageSourceDir) / f'OPSI.{customName}' / 'control.toml'
	if not customName or not packageControlFilePath.exists():
		packageControlFilePath = Path(packageSourceDir) / 'OPSI' / 'control.toml'
	if not packageControlFilePath.exists():
		packageControlFilePath = packageControlFilePath.with_suffix("")  # strip .toml to fall back to old behaviour
		if not packageControlFilePath.exists():
			raise OSError(f"Control file '{packageControlFilePath}' not found")

	if not quiet:
		print("")
		print(_("Locking package"))
	pcf = PackageControlFile(str(packageControlFilePath))

	lockPackage(tempDir, pcf)
	pps = None
	try:
		while True:
			product = pcf.getProduct()

			if not quiet:
				print_info(product, customName, pcf)
			if disablePigz:
				logger.debug("Disabling pigz")
				OPSI.Util.File.Archive.PIGZ_ENABLED = False

			pps = ProductPackageSource(
				packageSourceDir=packageSourceDir,
				tempDir=tempDir,
				customName=customName,
				customOnly=customOnly,
				packageFileDestDir=os.getcwd(),
				format=arch_format,
				compression=compression,
				dereference=dereference
			)

			if not quiet and os.path.exists(pps.getPackageFile()):
				print(_("Package file '%s' already exists.") % pps.getPackageFile())
				print(_("Press <O> to overwrite, <C> to abort or <N> to specify a new version:"), end=' ')
				sys.stdout.flush()
				newVersion = False
				if keepVersions and needOneVersion:
					newVersion = True
				elif keepVersions:
					if os.path.exists(pps.packageFile):
						os.remove(pps.packageFile)
					if os.path.exists(pps.packageFile + '.md5'):
						os.remove(pps.packageFile + '.md5')
					if os.path.exists(pps.packageFile + '.zsync'):
						os.remove(pps.packageFile + '.zsync')
				elif needOneVersion:
					newVersion = True

				if not doNotUseTerminal:
					with raw_tty():
						try:
							while True:
								ch = sys.stdin.read(1)
								if ch in ('o', 'O'):
									if os.path.exists(pps.packageFile):
										os.remove(pps.packageFile)
									if os.path.exists(pps.packageFile + '.md5'):
										os.remove(pps.packageFile + '.md5')
									if os.path.exists(pps.packageFile + '.zsync'):
										os.remove(pps.packageFile + '.zsync')
									break
								if ch in ('c', 'C'):
									raise Exception(_("Aborted"))
								if ch in ('n', 'N'):
									newVersion = True
									break
						finally:
							print('\r\033[0K')

				if newVersion:
					while True:
						print('\r%s' % _("Please specify new product version, press <ENTER> to keep current version (%s):") % product.productVersion, end=' ')  # pylint: disable=consider-using-f-string
						newVersion = newProductVersion
						if not keepVersions and not needOneVersion:
							newVersion = sys.stdin.readline().strip()
						else:
							if newProductVersion:
								newVersion = newProductVersion
							elif keepVersions:
								newVersion = product.productVersion
							else:
								newVersion = sys.stdin.readline().strip()

						try:
							if newVersion:
								product.setProductVersion(newVersion)
								pcf.generate()
							break
						except Exception:  # pylint: disable=broad-except
							print(_("Bad product version: %s") % newVersion)

					while True:
						print('\r%s' % _("Please specify new package version, press <ENTER> to keep current version (%s):") % product.packageVersion, end=' ')  # pylint: disable=consider-using-f-string
						newVersion = newPackageVersion
						if not keepVersions and not needOneVersion:
							newVersion = sys.stdin.readline().strip()
						else:
							if newPackageVersion:
								newVersion = newPackageVersion
							elif keepVersions:
								newVersion = product.packageVersion
							else:
								newVersion = sys.stdin.readline().strip()

						try:
							if newVersion:
								product.setPackageVersion(newVersion)
								pcf.generate()
							break
						except Exception:  # pylint: disable=broad-except
							print(_("Bad package version: %s") % newVersion)

					keepVersions = True
					needOneVersion = False
					newProductVersion = newPackageVersion = None
					newVersion = None
					continue

			# Regenerating to fix encoding
			pcf.generate()
			if args.control_to_toml and pcf._filename.endswith(".toml"):
				logger.warning("Already using toml format, no need to use --control-to-toml")
			elif args.control_to_toml:
				logger.warning("Creating control.toml from control and deleting old control file.")
				pcf.generate_toml()
				if not Path(pcf._filename + ".toml").exists():
					raise RuntimeError("Failed to create control.toml")
				os.remove(pcf._filename)
				pcf._filename += ".toml"

			progressSubject = None
			if not quiet:
				progressSubject = ProgressSubject('packing')
				progressSubject.attachObserver(ProgressNotifier())
				print(_("Creating package file '%s'") % pps.getPackageFile())
			pps.pack(progressSubject=progressSubject)
			if not args.no_set_rights:
				try:
					setRights(pps.getPackageFile())
				except Exception as err:  # pylint: disable=broad-except
					logger.warning("Failed to set rights: %s", err)

			if not quiet:
				print("\n")
			if createMd5SumFile:
				md5sumFile = f'{pps.getPackageFile()}.md5'
				if not quiet:
					print(_("Creating md5sum file '%s'") % md5sumFile)
				md5 = md5sum(pps.getPackageFile())
				with open(md5sumFile, 'w', encoding='utf-8') as file:
					file.write(md5)
				if not args.no_set_rights:
					try:
						setRights(md5sumFile)
					except Exception as err:  # pylint: disable=broad-except
						logger.warning("Failed to set rights: %s", err)

			if createZsyncFile:
				zsyncFilePath = f'{pps.getPackageFile()}.zsync'
				if not quiet:
					print(_("Creating zsync file '%s'") % zsyncFilePath)
				zsyncFile = ZsyncFile(zsyncFilePath)
				zsyncFile.generate(pps.getPackageFile())
				if not args.no_set_rights:
					try:
						setRights(zsyncFilePath)
					except Exception as err:  # pylint: disable=broad-except
						logger.warning("Failed to set rights: %s", err)
			break
	finally:
		if pps:
			if not quiet:
				print(_("Cleaning up"))
			pps.cleanup()
		if not quiet:
			print(_("Unlocking package"))
		unlockPackage(tempDir, pcf)
		if not quiet:
			print("")


def lockPackage(tempDir, packageControlFile):
	lockFile = os.path.join(tempDir, f'.opsi-makepackage.lock.{packageControlFile.getProduct().id}')
	# Test if other processes are accessing same product
	try:
		with open(lockFile, 'r', encoding='utf-8') as file:
			pid = file.read().strip()

		if pid:
			for line in execute("ps -A"):
				line = line.strip()
				if not line:
					continue
				if pid == line.split()[0].strip():
					pName = line.split()[-1].strip()
					# process is running
					raise RuntimeError(
						f"Product '{packageControlFile.getProduct().id}' is currently locked by process {pName} ({pid})."
					)

	except IOError:
		pass

	# Write lock-file
	with open(lockFile, 'w', encoding='utf-8') as file:
		file.write(str(os.getpid()))


def unlockPackage(tempDir, packageControlFile):
	lockFile = os.path.join(tempDir, f".opsi-makepackage.lock.{packageControlFile.getProduct().id}")
	if os.path.isfile(lockFile):
		os.unlink(lockFile)


def main():
	try:
		makepackage_main()
	except SystemExit as err:
		sys.exit(err.code)
	except Exception as err:  # pylint: disable=broad-except
		logging_config(stderr_level=LOG_ERROR)
		logger.error(err, exc_info=True)
		print(f"ERROR: {err}", file=sys.stderr)
		sys.exit(1)
