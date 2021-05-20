# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-convert converts opsi-backends.
"""

import argparse
import fcntl
import getpass
import os
import re
import struct
import sys
import termios

from opsicommon.logging import logger, init_logging, logging_config, LOG_ERROR, LOG_NONE, DEFAULT_COLORED_FORMAT
from OPSI import __version__ as python_opsi_version
from OPSI.Types import forceUnicode, forceHostId, forceUnicodeLower
from OPSI.Util import getfqdn
from OPSI.Util.Message import ProgressObserver
from OPSI.Backend.BackendManager import BackendManager
from OPSI.Backend.JSONRPC import JSONRPCBackend
from OPSI.Backend.Replicator import BackendReplicator

from opsiutils import __version__

logLevel = LOG_NONE
init_logging(stderr_level=logLevel, stderr_format=DEFAULT_COLORED_FORMAT)

class ProgressNotifier(ProgressObserver):
	def __init__(self, backendReplicator):
		self.usedWidth = 120
		self.currentProgressSubject = backendReplicator.getCurrentProgressSubject()
		self.overallProgressSubject = backendReplicator.getOverallProgressSubject()
		self.currentProgressSubject.attachObserver(self)
		self.overallProgressSubject.attachObserver(self)
		self.logSubject = logger.getMessageSubject()
		self.logSubject.attachObserver(self)
		logger.setMessageSubjectLevel(LOG_ERROR)
		self.error = None

	def displayProgress(self):
		usedWidth = self.usedWidth
		try:
			tty = os.popen('tty').readline().strip()
			fd = open(tty)
			terminalWidth = struct.unpack('hh', fcntl.ioctl(fd, termios.TIOCGWINSZ, '1234'))[1]
			if usedWidth > terminalWidth:
				usedWidth = terminalWidth
			fd.close()
		except Exception:
			pass

		if logLevel <= LOG_NONE:
			sys.stdout.write("\033[2A")

		if self.error:
			sys.stdout.write("\033[K")
			print(u"Error occurred: %s" % self.error)
			sys.stdout.write("\033[K")
		self.error = None
		for subject in self.overallProgressSubject, self.currentProgressSubject:
			text = u''
			if subject is self.overallProgressSubject:
				text = u'Overall progress'
			else:
				text = subject.getTitle()
			barlen = usedWidth - 62
			filledlen = int("%0.0f" % (barlen * subject.getPercent() / 100))
			bar = u'=' * filledlen + u' ' * (barlen - filledlen)
			percent = '%0.2f%%' % subject.getPercent()
			print('%35s : %8s [%s] %5s/%-5s' % (text, percent, bar, subject.getState(), subject.getEnd()))

	def progressChanged(self, subject, state, percent, timeSpend, timeLeft, speed):
		self.displayProgress()

	def messageChanged(self, subject, message):
		if subject == self.logSubject:
			self.error = message
		self.displayProgress()


def opsiconvert_main():
	global logLevel

	parser = argparse.ArgumentParser(
		description="Convert an opsi database into an other.",
		epilog="""
The backends can either be the name of a backend as defined
in /etc/opsi/backends (file, mysql, ...) or the the url of an opsi
configuration service in the form of http(s)://<user>@<host>:<port>/rpc""")
	parser.add_argument('--version', '-V', action='version', version=f"{__version__} [python-opsi={python_opsi_version}]")
	parser.add_argument('--quiet', '-q', action='store_true', default=False,
						help="do not show progress")
	parser.add_argument('--verbose', '-v',
						dest="logLevel", default=LOG_NONE, action="count",
						help="increase verbosity (can be used multiple times)")
	parser.add_argument('--log-level', dest="logLevel", default=LOG_NONE, type=int,
						choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
 						help="Set log-level (0..9)")
	parser.add_argument('--clean-destination', '-c', dest="cleanupFirst",
						default=False, action='store_true',
						help="clean destination database before writing")
	parser.add_argument('--with-audit-data', '-a', dest="audit",
						action='store_true', default=False,
						help="including software/hardware inventory")
	parser.add_argument('-s', metavar="OLD SERVER ID", dest="oldServerId",
						help="use destination host as new server")
	parser.add_argument('--log-file', '-l', dest="logFile",
						help="Log to this file. The loglevel will be DEBUG.")
	parser.add_argument('readBackend', metavar="source", help="Backend to read data from.")
	parser.add_argument('writeBackend', metavar="destination", help="Backend to write data to.")

	args = parser.parse_args()

	logLevel = args.logLevel
	init_logging(stderr_level=logLevel)
	if args.logFile:
		logging_config(log_file=args.logFile, file_level=logLevel)

	cleanupFirst = args.cleanupFirst
	progress = not args.quiet
	convertAudit = args.audit

	if args.oldServerId:
		newServerId = getfqdn(conf='/etc/opsi/global.conf')
		oldServerId = forceHostId(args.oldServerId)
	else:
		newServerId = None
		oldServerId = None

	readBackend = forceUnicode(args.readBackend)
	writeBackend = forceUnicode(args.writeBackend)

	# Define read/write backend
	read = {
		'username': u'',
		'password': u'',
		'address': u'',
		'backend': u''
	}
	write = {
		'username': u'',
		'password': u'',
		'address': u'',
		'backend': u''
	}

	logger.comment("Converting from backend '%s' to backend '%s'.", readBackend, writeBackend)

	logger.debug("Parsing read backend")
	parseBackend(read, readBackend)
	logger.debug("Settings for read-backend: %s", read)

	logger.debug("Parsing write backend")
	parseBackend(write, writeBackend)
	logger.debug("Settings for write-backend: %s", write)
	if write['address'] and write['username'] and newServerId:
		match = re.search('^(\w+://)([^@]+)@([^:]+:\d+/.*)$', writeBackend)
		newServerId = match.group(3).split(':')[0]
		if re.search('^[\d\.]+$', newServerId):
			# Is an ip address
			newServerId = getfqdn(name=newServerId)
			if re.search('^[\d\.]+$', newServerId):
				raise ValueError(u"Cannot resolve '%s'" % newServerId)

	if newServerId:
		try:
			newServerId = forceHostId(newServerId)
		except Exception:
			raise ValueError(u"Bad server-id '%s' for new server" % newServerId)

	sanityCheck(read, write)

	logger.debug("Creating BackendManager instance for reading")
	bmRead = createBackend(read)

	logger.debug("Creating BackendManager instance for writing")
	bmWrite = createBackend(write)

	backendReplicator = BackendReplicator(
		readBackend=bmRead,
		writeBackend=bmWrite,
		newServerId=newServerId,
		oldServerId=oldServerId,
		cleanupFirst=cleanupFirst
	)
	if progress:
		ProgressNotifier(backendReplicator)
		print("")
	backendReplicator.replicate(audit=convertAudit)


def parseBackend(config, backendString):
	"Parse the string and write the results into config."

	match = re.search('^(\w+://)', backendString)
	if match:
		logger.debug("Read-backend seems to be an URL")
		match = re.search('^(\w+://)([^@]+)@([^:]+:\d+/.*)$', backendString)
		if match:
			config['backend'] = 'JSONRPC'
			config['address'] = match.group(1) + match.group(3)
			config['username'] = match.group(2)
		else:
			raise ValueError(u"Bad source URL '%s'" % backendString)
	else:
		logger.debug("Assuming '%s' is a backend name.", backendString)
		config['backend'] = backendString


def sanityCheck(read, write):
	if read['backend'] and write['backend']:
		if read['backend'] == write['backend']:
			if read['backend'] == 'JSONRPC':
				if read['address'] == write['address']:
					raise ValueError(u"Source and destination backend are the same.")
			else:
				raise ValueError(u"Source and destination backend are the same.")


def createBackend(config):
	logger.debug("Creating backend instance")
	if config['address'] and not config['password']:
		logger.comment("Connecting to %s as %s", config.get("address"), config.get("username"))
		config['password'] = getpass.getpass()

	config = cleanBackendConfig(config)

	try:
		backend = BackendManager(backendConfigDir=u'/etc/opsi/backends', **config)
	except Exception as error:
		logger.error(error, exc_info=True)
		if forceUnicodeLower(config['backend']) == u'jsonrpc':
			logger.debug("Creating a JSONRPC backend through BackendManager failed.")
			logger.debug("Trying with a direct connection.")
			backend = JSONRPCBackend(
				deflate=True,
				application='opsi-convert/%s' % __version__,
				**config
			)
		else:
			raise

	return backend


def cleanBackendConfig(config):
	cleanedConfig = dict(config)
	keysToRemove = set()
	for key, value in config.items():
		if not value:
			keysToRemove.add(key)

	for key in keysToRemove:
		del cleanedConfig[key]

	return cleanedConfig


def main():
	try:
		opsiconvert_main()
	except SystemExit:
		pass
	except Exception as e:
		logging_config(stderr_level=LOG_ERROR)
		logger.error(e, exc_info=True)
		print(u"ERROR: %s" % e, file=sys.stderr)
		sys.exit(1)
