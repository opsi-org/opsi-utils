# -*- coding: utf-8 -*-

# opsi-makepackage is part of the desktop management solution opsi
# (open pc server integration) http://www.opsi.org
# Copyright (C) 2010-2019 uib GmbH <info@uib.de>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License version 3
# as published by the Free Software Foundation.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
opsi-wakeup-clients - wakeup clients for deployment tasks

:copyright:	uib GmbH <info@uib.de>
:license: GNU Affero General Public License version 3
"""

import argparse
import os
import sys
import time
import gettext
import threading
from collections import defaultdict
from contextlib import contextmanager
from itertools import product

from opsicommon.logging import logger, init_logging, logging_config, secret_filter, LOG_DEBUG, LOG_ERROR, LOG_NONE, LOG_WARNING
from OPSI import __version__ as python_opsi_version
from OPSI.Backend.BackendManager import BackendManager
from OPSI.Backend.JSONRPC import JSONRPCBackend
from OPSI.Util.Ping import ping

from opsiutils import __version__

try:
	sp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	if os.path.exists(os.path.join(sp, "site-packages")):
		sp = os.path.join(sp, "site-packages")
	sp = os.path.join(sp, 'opsi-utils_data', 'locale')
	translation = gettext.translation('opsi-utils', sp)
	_ = translation.gettext
except Exception as error:
	logger.error("Failed to load locale from %s: %s", sp, error, exc_info=True)

	def _(string):
		""" Fallback function """
		return string


def parseOptions():
	parser = argparse.ArgumentParser(
		description="Wakeup clients for software installation.",
		formatter_class=argparse.ArgumentDefaultsHelpFormatter)
	parser.add_argument('--version', '-V', action='version', version=f"{__version__} [python-opsi={python_opsi_version}]")

	logGroup = parser.add_argument_group(title="Logging")
	logGroup.add_argument(
		'--verbose', '-v', dest="consoleLogLevel", default=3,
		action="count", help="increase verbosity on console (can be used multiple times)")
	logGroup.add_argument(
		'--log-file', action="store", dest="logFile", help="Set log file path")
	logGroup.add_argument(
		'--log-level', '-l', dest="fileLogLevel", type=int, default=0,
		choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], help="Set the desired loglevel for the logfile.")

	timeoutGroup = parser.add_argument_group(title="Timeouts")
	timeoutGroup.add_argument(
		'--wol-timeout', dest="wolTimeout", default=300, type=int,
		help='Time to wait until opsiclientd should be reachable.')
	timeoutGroup.add_argument(
		'--ping-timeout', dest="pingTimeout", default=300, type=int,
		help='Time to wait until client should be pingable. 0 = skip ping test.')
	timeoutGroup.add_argument(
		'--connect-timeout', dest="connectTimeout", default=15, type=int,
		help='Timeout for connecting to opsiclientd.')
	timeoutGroup.add_argument(
		'--event-timeout', dest="eventTimeout", default=300, type=int,
		help='Time to wait until opsiclientd should be processing.')
	timeoutGroup.add_argument(
		'--reboot-timeout', dest="rebootTimeout", default=60, type=int,
		help='Time to wait before opsiclientd will be reboot the client.')

	parser.add_argument(
		'--host-group-id', '-H', dest='hostGroupId',
		help='Group in which clients have to be to be waked up.')
	parser.add_argument(
		'--depotId', '-D', dest='depotId',
		help='DepotId in which clients have to be registered to be waked up.')
	parser.add_argument(
		'--host-file', '-F', dest='inputFile',
		help='Filename with clients per line have to be waked up.')
	parser.add_argument(
		'--product-group-id', '-P', dest='productGroupId',
		help="ID of the product group to set to setup on a client")
	parser.add_argument(
		'--event', '-E', dest='eventName',
		help="Event to be triggered on the clients")
	parser.add_argument(
		'--reboot', '-X', dest='reboot', default=False, action='store_true',
		help="Triggering reboot on the clients")
	parser.add_argument(
		'--no-auto-update', '-N', dest='noAutoUpdate', default=False, type=bool,
		help="Do not use opsi-auto-update product.")
	parser.add_argument(
		'--max-concurrent', dest="maxConcurrent", default=0, type=int,
		help='Maximum number of concurrent client deployments.')
	
	args = parser.parse_args()

	return args


def wakeClientsForUpdate(
	backend, depotId, inputFile, noAutoUpdate, reboot, rebootTimeout, hostGroupId, productGroupId, eventName,
	wolTimeout, eventTimeout, connectTimeout, pingTimeout, maxConcurrent
):
	logger.info("Using event: %s", eventName)

	if depotId:
		logger.notice("Getting list of clients to process by depot '%s'", depotId)
		clientsToWake = getClientIDsFromDepot(backend, depotId, hostGroupId)
	elif hostGroupId:
		logger.notice("Getting list of clients to process by group '%s'", hostGroupId)
		clientsToWake = getClientIDsFromGroup(backend, hostGroupId)
	elif inputFile:
		logger.notice("Getting list of clients to process from file '%s'", inputFile)
		clientsToWake = getClientIDsFromFile(backend, inputFile)
	else:
		logger.error("No criteria given to determine a list of clients to process. You have to provide either -D/-H or -F.")
		sys.exit(1)
	
	clientsToWake.sort()

	logger.notice("Processing %d clients", len(clientsToWake))
	for clientToWake in clientsToWake:
		logger.debug("   %s", clientToWake)

	logger.notice("Configuring products")
	if not noAutoUpdate:	
		logger.notice("Configuring opsi-auto-update product")
		configureOpsiAutoUpdate(backend, clientsToWake)

	productIds = set()
	if productGroupId:
		logger.notice("Getting list of products by product group '%s'", productGroupId)
		productIds = getProductsFromProductGroup(backend, productGroupId)
	logger.notice("List of products to process: %s", productIds)
	
	clientSum = len(clientsToWake)
	runningThreads = []
	failed = defaultdict(set)
	
	logger.info("Starting to wake up clients")

	while runningThreads or clientsToWake:
		# Start new threads
		newClients = []
		while clientsToWake and (maxConcurrent == 0 or len(runningThreads) + len(newClients) < maxConcurrent):
			newClients.append(clientsToWake.pop())
		if newClients:
			requireProductInstallation(backend, newClients, productIds)
			for client in newClients:
				thread = ClientMonitoringThread(backend, client, reboot, rebootTimeout, eventName, wolTimeout, eventTimeout, connectTimeout, pingTimeout)
				logger.info("Starting task on client '%s'", client)
				thread.start()
				runningThreads.append(thread)
		
		newRunningThreads = []
		for thread in runningThreads:
			if thread.success is None:
				newRunningThreads.append(thread)
			else:
				# Thread finished
				if isinstance(thread.success, Exception):
					exception = thread.success
					if isinstance(exception, NoPingReceivedError):
						reason = 'ping'
					elif isinstance(exception, WaitForOpsiclientdError):
						reason = 'opsiclientd start'
					elif isinstance(exception, WaitForEventTimeout):
						reason = 'event start'
					else:
						reason = 'unspecified'
					failed[reason].add(thread.clientId)
					logger.info("Tasks on client '%s' failed with reason '%s' (%s)", thread.clientId, reason, exception)
				else:
					logger.info("Tasks on client '%s' finished sucessfully", thread.clientId)
		
		runningThreads = newRunningThreads
		time.sleep(1)
	
	totalFails = 0
	for reason, clientIds in list(failed.items()):
		failcount = len(clientIds)
		totalFails += failcount
		logger.info("%s clients failed because of %s error: %s", failcount, reason, ', '.join(clientIds))

	logger.notice("Succesfully processed %s/%s clients", clientSum - totalFails, clientSum)

def getClientIDsFromDepot(backend, depotId, groupName):
	clientsFromGroup = []
	if groupName:
		clientsFromGroup = getClientIDsFromGroup(backend, groupName)
	
	depotClients = backend.getClientsOnDepot(depotId)
	if not clientsFromGroup:
		logger.notice("Deploying all Client from depot %s", depotId)
		return depotClients
	else:
		logger.notice("Combining HostGroup %s with depot %s", groupName, depotId)
		return [ x for x in depotClients if x in clientsFromGroup ]

def getClientIDsFromFile(backend, inputFile):
	if not os.path.exists(inputFile):
		logger.error("Given File %s not found.", inputFile)
		sys.exit(1)
	with open(inputFile, 'r') as f:
		return [l.strip() for line in f.readlines()]

def getClientIDsFromGroup(backend, groupName):
	group = backend.group_getObjects(id=groupName, type="HostGroup")

	try:
		group = group[0]
	except IndexError:
		raise ValueError("No HostGroup with id '{0}' found!".format(groupName))

	return [mapping.objectId for mapping in backend.objectToGroup_getObjects(groupId=group.id)]


def configureOpsiAutoUpdate(backend, clientIds):
	for clientId in clientIds:
		backend.setProductProperty('opsi-auto-update', 'rebootflag', 0, clientId)


def getProductsFromProductGroup(backend, productGroupId):
	group = backend.group_getObjects(id=productGroupId, type="ProductGroup")

	try:
		group = group[0]
	except IndexError:
		raise ValueError("No ProductGroup with id '{0}' found!".format(productGroupId))

	return set([mapping.objectId for mapping in backend.objectToGroup_getObjects(groupId=group.id)])


def requireProductInstallation(backend, clientIds, productIds):
	for clientId, productId in product(clientIds, productIds):
		backend.setProductActionRequestWithDependencies(productId, clientId, 'setup')


class ClientMonitoringThread(threading.Thread):
	def __init__(self, backend, clientId, reboot, rebootTimeout, eventName, wolTimeout, eventTimeout, connectTimeout, pingTimeout):
		threading.Thread.__init__(self)

		self.backend = backend
		self.opsiclientdbackend = None
		self.clientId = clientId
		self.hostKey = None

		self.wolTimeout = wolTimeout
		self.pingTimeout = pingTimeout

		self.eventName = eventName
		self.eventTimeout = eventTimeout

		self.reboot = reboot
		self.rebootTimeout = rebootTimeout

		self.connectTimeout = connectTimeout

		self.success = None

	def run(self):
		try:
			if self.reboot:
				try:
					self.waitForOpsiclientd()
					self.triggerReboot()
					time.sleep(self.rebootTimeout + 20)
					self.waitForOpsiclientd()
				except Exception as err:
					logger.error("Failed to trigger reboot on client %s: %s, trying to wake the client", self.clientId, err)
					self.wakeClient()
			else:
				self.wakeClient()
		except Exception as err:
			self.success = err
			logger.error("Failed to wake client %s: %s", self.clientId, err)
			return

		if self.eventName:
			try:
				self.triggerEvent()
			except Exception as err:
				self.success = err
				logger.error("Failed to trigger event on client %s: %s", self.clientId, err)
				return

		self.success = True

	def wakeClient(self):
		logger.info("Waking %s", self.clientId)
		self.backend.hostControlSafe_start(self.clientId)

		if os.geteuid() == 0:
			if self.pingTimeout > 0:
				# Only as root
				self.waitForPing()
			else:
				logger.notice("Did not try to ping %s...", self.clientId)
		self.waitForOpsiclientd()

	def waitForPing(self):
		timeout_event = threading.Event()
		retryTimeout = 10

		with timeoutThread(self.pingTimeout, timeout_event, NoPingReceivedError("Unable to ping %s" % self.clientId)):
			start_timeout = 1
			while not timeout_event.wait(start_timeout or retryTimeout):
				start_timeout = 0
				try:
					logger.info("Trying to ping %s...", self.clientId)
					delay = ping(self.clientId, retryTimeout - 2)
					if delay:
						logger.notice("Succesfully pinged %s", self.clientId)
						break
				except Exception as exc:
					logger.info("Failed to connect ping %s (%s)", self.clientId, exc)

	def waitForOpsiclientd(self):
		port = 4441
		address = "https://%s:%s/opsiclientd" % (self.clientId, port)  # We expect the FQDN here
		password = self.backend.host_getObjects(id=self.clientId)[0].opsiHostKey
		timeout_event = threading.Event()

		retryTimeout = 10

		with timeoutThread(self.wolTimeout, timeout_event):
			start_timeout = 1
			while not timeout_event.wait(start_timeout or retryTimeout):
				start_timeout = 0
				try:
					logger.info("Trying to connect to %s...", self.clientId)
					backend = JSONRPCBackend(
						address=address,
						username=self.clientId,
						password=password,
						connectTimeout=self.connectTimeout,
						socketTimeout=self.connectTimeout,
					)
					if not backend.isConnected():
						continue

					self.opsiclientdbackend = backend
					logger.notice("Connection to %s established", self.clientId)
					break
				except Exception as exc:
					logger.info("Failed to connect port %s to %s (%s)", port, self.clientId, exc)

	def triggerReboot(self):
		logger.info("Triggering Reboot on %s with a %s sec gap", self.clientId, self.rebootTimeout)
		self.opsiclientdbackend.reboot(str(self.rebootTimeout))

	def triggerEvent(self):
		"""
		Trigger an event and wait for it to run.
		"""
		timeout_event = threading.Event()
		retryTimeout = 5

		with timeoutThread(self.eventTimeout, timeout_event, WaitForEventTimeout("Did not see running event %s on %s"% (self.eventName, self.clientId))):
			start_timeout = 1
			runs = 0
			while not timeout_event.wait(start_timeout or retryTimeout):
				start_timeout = 0

				if runs % 3 == 0:
					logger.info("Triggering event %s on %s", self.eventName, self.clientId)
					self.opsiclientdbackend.fireEvent(self.eventName)

				try:
					if self.opsiclientdbackend.isEventRunning(self.eventName):
						logger.notice("Event %s is running on %s.", self.eventName, self.clientId)
						break
					if self.opsiclientdbackend.isEventRunning(self.eventName+"{user_logged_in}"):
						logger.notice("Event %s is running on %s.", self.eventName+"{user_logged_in}", self.clientId)
						break
				except Exception as exc:
					logger.info("Failed to check running event on %s (%s)", self.clientId, exc)

				runs += 1


@contextmanager
def timeoutThread(timeout, stop_event, exception=None):
	def stop_program():
		logger.debug("General timeout reached. Stopping.")
		stop_event.set()

	stopper = threading.Timer(timeout, stop_program)
	stopper.start()

	yield

	if not stopper.is_alive():
		if exception is None:
			exception = WaitForOpsiclientdError("Timed out.")

		raise exception

	try:
		stopper.cancel()
	finally:
		stopper.join(5)


class TimeoutError(RuntimeError):
	pass


class NoPingReceivedError(TimeoutError):
	pass


class WaitForOpsiclientdError(TimeoutError):
	pass


class WaitForEventTimeout(TimeoutError):
	pass


def opsiwakeupclients_main():
	options = parseOptions()

	if options.consoleLogLevel:
		init_logging(stderr_level=options.consoleLogLevel)
	if options.fileLogLevel and options.logFile:
		init_logging(log_file=options.logFile, file_level=options.fileLogLevel)
	
	with BackendManager() as backend:
		wakeClientsForUpdate(
			backend,
			options.depotId,
			options.inputFile, options.noAutoUpdate,
			options.reboot, options.rebootTimeout,
			options.hostGroupId, options.productGroupId,
			options.eventName,
			options.wolTimeout, options.eventTimeout,
			options.connectTimeout,
			options.pingTimeout,
			options.maxConcurrent
		)

def main():
	try:
		opsiwakeupclients_main()
	except SystemExit:
		pass
	except Exception as exception:
		logging_config(stderr_level=LOG_ERROR)
		logger.error(exception, exc_info=True)
		print(f"ERROR: {exception}", file=sys.stderr)
		sys.exit(1)
