# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-wakeup-clients - wakeup clients for deployment tasks
"""
from __future__ import annotations

import argparse
import gettext
import os
import socket
import sys
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from itertools import product
from typing import Generator

from OPSI import __version__ as python_opsi_version  # type: ignore[import]
from OPSI.Util.Ping import ping  # type: ignore[import]
from opsicommon.client.jsonrpc import JSONRPCClient
from opsicommon.client.opsiservice import ServiceClient
from opsicommon.logging import (
	DEFAULT_COLORED_FORMAT,
	LOG_ERROR,
	get_logger,
	init_logging,
	logging_config,
)

from opsiutils import __version__, get_service_client

logger = get_logger("")
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


def parseOptions() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Wakeup clients for software installation.", formatter_class=argparse.ArgumentDefaultsHelpFormatter
	)
	parser.add_argument("--version", "-V", action="version", version=f"{__version__} [python-opsi={python_opsi_version}]")

	logGroup = parser.add_argument_group(title="Logging")
	logGroup.add_argument(
		"--verbose",
		"-v",
		dest="consoleLogLevel",
		default=4,
		action="count",
		help="increase verbosity on console (can be used multiple times)",
	)
	logGroup.add_argument(
		"--log-file", action="store", dest="logFile", default="/var/log/opsi/opsi-wakeup-clients.log", help="Set log file path"
	)
	logGroup.add_argument(
		"--log-level",
		"-l",
		dest="fileLogLevel",
		type=int,
		default=4,
		choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
		help="Set the desired loglevel for the logfile.",
	)

	timeoutGroup = parser.add_argument_group(title="Timeouts")
	timeoutGroup.add_argument(
		"--wol-timeout", dest="wolTimeout", default=300, type=int, help="Time to wait until opsiclientd should be reachable."
	)
	timeoutGroup.add_argument(
		"--ping-timeout",
		dest="pingTimeout",
		default=300,
		type=int,
		help="Time to wait until client should be pingable. 0 = skip ping test.",
	)
	timeoutGroup.add_argument(
		"--connect-timeout", dest="connectTimeout", default=15, type=int, help="Timeout for connecting to opsiclientd."
	)
	timeoutGroup.add_argument(
		"--event-timeout", dest="eventTimeout", default=300, type=int, help="Time to wait until opsiclientd should be processing."
	)
	timeoutGroup.add_argument(
		"--reboot-timeout", dest="rebootTimeout", default=60, type=int, help="Time to wait before opsiclientd will be reboot the client."
	)

	parser.add_argument("--host-group-id", "-H", dest="hostGroupId", help="Group in which clients have to be to be waked up.")
	parser.add_argument("--depot-id", "-D", dest="depotId", help="DepotId in which clients have to be registered to be waked up.")
	parser.add_argument("--host-file", "-F", dest="inputFile", help="Filename with clients per line have to be waked up.")
	parser.add_argument("--product-group-id", "-P", dest="productGroupId", help="ID of the product group to set to setup on a client")
	parser.add_argument("--event", "-E", dest="eventName", help="Event to be triggered on the clients")
	parser.add_argument("--reboot", "-X", dest="reboot", default=False, action="store_true", help="Triggering reboot on the clients")
	parser.add_argument(
		"--shutdownwanted",
		"-s",
		dest="shutdownwanted",
		default=False,
		action="store_true",
		help="Triggering shutdown as last action (via Product shutdownwanted)",
	)
	parser.add_argument(
		"--no-auto-update", "-N", dest="noAutoUpdate", default=False, action="store_true", help="Do not use opsi-auto-update product."
	)
	parser.add_argument(
		"--max-concurrent", dest="maxConcurrent", default=0, type=int, help="Maximum number of concurrent client deployments."
	)

	args = parser.parse_args()

	if args.fileLogLevel and not args.logFile:
		raise ValueError(f"log-level set to {args.fileLogLevel} but no log-file not set")
	return args


def wakeClientsForUpdate(
	service_client: ServiceClient,
	depotId: str,
	inputFile: str,
	noAutoUpdate: bool,
	shutdownwanted: bool,
	reboot: bool,
	rebootTimeout: int,
	hostGroupId: str,
	productGroupId: str,
	eventName: str,
	wolTimeout: int,
	eventTimeout: int,
	connectTimeout: int,
	pingTimeout: int,
	maxConcurrent: int,
) -> None:
	logger.info(
		(
			"Using params: depotId=%s, inputFile=%s, noAutoUpdate=%s, reboot=%s, rebootTimeout=%s, "
			"hostGroupId=%s, productGroupId=%s, eventName=%s, wolTimeout=%s, eventTimeout=%s, "
			"connectTimeout=%s, pingTimeout=%s, maxConcurrent=%s"
		),
		depotId,
		inputFile,
		noAutoUpdate,
		reboot,
		rebootTimeout,
		hostGroupId,
		productGroupId,
		eventName,
		wolTimeout,
		eventTimeout,
		connectTimeout,
		pingTimeout,
		maxConcurrent,
	)
	clientsToWake = []

	if depotId:
		if hostGroupId:
			logger.notice("Getting list of clients to process by depot '%s' and client group '%s'", depotId, hostGroupId)
		else:
			logger.notice("Getting list of clients to process by depot '%s'", depotId)
		clientsToWake = getClientIDsFromDepot(service_client, depotId, hostGroupId)
	elif hostGroupId:
		logger.notice("Getting list of clients to process by group '%s'", hostGroupId)
		clientsToWake = getClientIDsFromGroup(service_client, hostGroupId)
	elif inputFile:
		logger.notice("Getting list of clients to process from file '%s'", inputFile)
		clientsToWake = getClientIDsFromFile(service_client, inputFile)
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
		configureOpsiAutoUpdate(service_client, clientsToWake)

	if shutdownwanted:
		logger.notice("Setting shutdownwanted for clients")
		setShutdownwanted(service_client, clientsToWake)

	productIds: set[str] = set()
	if productGroupId:
		logger.notice("Getting list of products to set to setup by product group '%s'", productGroupId)
		productIds = getProductsFromProductGroup(service_client, productGroupId)
	if productIds:
		logger.notice("List of products to set to setup: %s", productIds)
	else:
		logger.notice("No products to set to setup")

	clientSum = len(clientsToWake)
	runningThreads: list[ClientMonitoringThread] = []
	failed: dict[str, set[str]] = defaultdict(set)

	logger.info("Starting to wake up clients")

	while runningThreads or clientsToWake:
		# Start new threads
		newClients: list[str] = []
		while clientsToWake and (maxConcurrent == 0 or len(runningThreads) + len(newClients) < maxConcurrent):
			newClients.append(clientsToWake.pop())
		if newClients:
			if productIds:
				requireProductInstallation(service_client, newClients, list(productIds))
			for client in newClients:
				thread = ClientMonitoringThread(
					service_client, client, reboot, rebootTimeout, eventName, wolTimeout, eventTimeout, connectTimeout, pingTimeout
				)
				logger.notice("Starting task on client '%s'", client)
				thread.daemon = True
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
						reason = "ping"
					elif isinstance(exception, WaitForOpsiclientdError):
						reason = "opsiclientd start"
					elif isinstance(exception, WaitForEventTimeout):
						reason = "event start"
					else:
						reason = "unspecified"
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
		logger.warning("%s clients failed because of %s error: %s", failcount, reason, ", ".join(clientIds))

	logger.notice("Succesfully processed %s/%s clients", clientSum - totalFails, clientSum)


def getClientIDsFromDepot(service_client: ServiceClient, depotId: str, groupName: str) -> list[str]:
	clientsFromGroup = []
	if groupName:
		clientsFromGroup = getClientIDsFromGroup(service_client, groupName)

	depotClients = service_client.jsonrpc("getClientsOnDepot", [depotId])
	if not clientsFromGroup:
		return depotClients
	return [x for x in depotClients if x in clientsFromGroup]


def getClientIDsFromFile(service_client: ServiceClient, inputFile: str) -> list[str]:
	if not os.path.exists(inputFile):
		raise FileNotFoundError(f"Host-file '{inputFile}' not found")
	knownIds = service_client.jsonrpc("host_getIdents", [[], {"type": "OpsiClient"}])
	clientIds = []
	with open(inputFile, "r", encoding="utf8") as file:
		for line in file.readlines():
			line = line.strip()
			if not line or line.startswith("#"):
				continue
			if line not in knownIds:
				logger.warning("Client '%s' from host-file not found in backend", line)
				continue
			clientIds.append(line)
	return clientIds


def getClientIDsFromGroup(service_client: ServiceClient, groupName: str) -> list[str]:
	group = service_client.jsonrpc("group_getObjects", [[], {"id": groupName, "type": "HostGroup"}])

	try:
		group = group[0]
	except IndexError as err:
		raise ValueError(f"Client group '{groupName}' not found") from err

	return [mapping.objectId for mapping in service_client.jsonrpc("objectToGroup_getObjects", [[], {"groupId": group.id}])]


def configureOpsiAutoUpdate(service_client: ServiceClient, clientIds: list[str]) -> None:
	for clientId in clientIds:
		service_client.jsonrpc("setProductProperty", ["opsi-auto-update", "rebootflag", 0, clientId])


def setShutdownwanted(service_client: ServiceClient, clientIds: list[str]) -> None:
	for clientId in clientIds:
		service_client.jsonrpc("setProductActionRequest", ["shutdownwanted", clientId, "setup"])


def getProductsFromProductGroup(service_client: ServiceClient, productGroupId: str) -> set[str]:
	group = service_client.jsonrpc("group_getObjects", [[], {"id": productGroupId, "type": "ProductGroup"}])

	try:
		group = group[0]
	except IndexError as err:
		raise ValueError(f"Product group '{productGroupId}' not found") from err

	return {mapping.objectId for mapping in service_client.jsonrpc("objectToGroup_getObjects", [[], {"groupId": group.id}])}


def requireProductInstallation(service_client: ServiceClient, clientIds: list[str], productIds: list[str]) -> None:
	for clientId, productId in product(clientIds, productIds):
		service_client.jsonrpc("setProductActionRequestWithDependencies", [productId, clientId, "setup"])


class ClientMonitoringThread(threading.Thread):
	def __init__(
		self,
		service_client: ServiceClient,
		clientId: str,
		reboot: bool,
		rebootTimeout: int,
		eventName: str,
		wolTimeout: int,
		eventTimeout: int,
		connectTimeout: int,
		pingTimeout: int,
	):
		threading.Thread.__init__(self)

		self.service_client = service_client
		self.opsiclientdbackend: JSONRPCClient | None = None
		self.clientId = clientId
		self.hostKey = None

		self.wolTimeout = wolTimeout
		self.pingTimeout = pingTimeout

		self.eventName = eventName
		self.eventTimeout = eventTimeout

		self.reboot = reboot
		self.rebootTimeout = rebootTimeout

		self.connectTimeout = connectTimeout

		self.success: Exception | bool | None = None

	def run(self) -> None:
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

	def wakeClient(self) -> None:
		logger.notice("Waking up client '%s'", self.clientId)
		self.service_client.jsonrpc("hostControlSafe_start", [self.clientId])

		if os.geteuid() == 0:
			if self.pingTimeout > 0:
				# Only as root
				self.waitForPing()
			else:
				logger.debug("Did not try to ping client '%s'", self.clientId)
		self.waitForOpsiclientd()

	def waitForPing(self) -> None:
		logger.notice("Waiting for ping response of '%s'", self.clientId)
		timeout_event = threading.Event()
		retryTimeout = 10

		with timeoutThread(self.pingTimeout, timeout_event, NoPingReceivedError(f"Unable to ping client '{self.clientId}'")):
			start_timeout = 1
			while not timeout_event.wait(start_timeout or retryTimeout):
				start_timeout = 0
				try:
					logger.debug("Trying to ping client '%s'", self.clientId)
					delay = ping(self.clientId, retryTimeout - 2)
					if delay:
						logger.notice("Succesfully pinged '%s'", self.clientId)
						break
				except Exception as err:
					logger.debug("Failed to ping client '%s': %s", self.clientId, err)

	def waitForOpsiclientd(self) -> None:
		logger.notice("Connecting to opsi-client-agent on '%s'", self.clientId)
		port = 4441
		address = f"https://{self.clientId}:{port}/opsiclientd"  # We expect the FQDN here
		password = self.service_client.jsonrpc("host_getObjects", [[], {"id": self.clientId}])[0].opsiHostKey
		timeout_event = threading.Event()

		retryTimeout = 10

		with timeoutThread(self.wolTimeout, timeout_event):
			start_timeout = 1
			while not timeout_event.wait(start_timeout or retryTimeout):
				start_timeout = 0
				try:
					logger.debug("Trying to connect to opsi-client-agent on client '%s'", self.clientId)
					sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
					sock.settimeout(5)
					res = sock.connect_ex((self.clientId, port))
					sock.close()
					if res != 0:
						raise RuntimeError(f"Port {port} unreachable")

					backend = JSONRPCClient(
						address=address,
						username=self.clientId,
						password=password,
						connectTimeout=self.connectTimeout,
						socketTimeout=self.connectTimeout,
					)
					if not backend.session:
						continue

					self.opsiclientdbackend = backend
					logger.notice("Connection to client '%s' established", self.clientId)
					break
				except Exception as err:
					logger.debug("Failed to connect to client '%s': %s", self.clientId, err)

	def triggerReboot(self) -> None:
		if not self.opsiclientdbackend:
			raise RuntimeError(f"Connection to client '{self.clientId}' failed")
		logger.info("Triggering reboot on client '%s' with a delay of %s seconds", self.clientId, self.rebootTimeout)
		self.opsiclientdbackend.reboot(str(self.rebootTimeout))  # type: ignore[attr-defined]

	def triggerEvent(self) -> None:
		"""
		Trigger an event and wait for it to run.
		"""
		logger.notice("Triggering event '%s' on '%s'", self.eventName, self.clientId)
		assert self.opsiclientdbackend
		timeout_event = threading.Event()
		retryTimeout = 5

		with timeoutThread(
			self.eventTimeout, timeout_event, WaitForEventTimeout(f"Did not see running event '{self.eventName}' on '{self.clientId}'")
		):
			start_timeout = 1
			runs = 0
			while not timeout_event.wait(start_timeout or retryTimeout):
				start_timeout = 0

				if runs % 3 == 0:
					logger.debug("Triggering event '%s' on '%s'", self.eventName, self.clientId)
					try:
						self.opsiclientdbackend.fireEvent(self.eventName)  # type: ignore[attr-defined]
					except Exception as exc:
						logger.debug("Failed to trigger event on '%s': %s", self.clientId, exc)

				try:
					if self.opsiclientdbackend.isEventRunning(self.eventName):  # type: ignore[attr-defined]
						logger.notice("Event '%s' is running on '%s'", self.eventName, self.clientId)
						break
					if self.opsiclientdbackend.isEventRunning(self.eventName + "{user_logged_in}"):  # type: ignore[attr-defined]
						logger.notice("Event '%s' is running on '%s'", self.eventName + "{user_logged_in}", self.clientId)
						break
				except Exception as exc:
					logger.debug("Failed to check running event on '%s': %s", self.clientId, exc)

				runs += 1


@contextmanager
def timeoutThread(timeout: int, stop_event: threading.Event, exception: Exception | None = None) -> Generator[None, None, None]:
	def stop_program() -> None:
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


class NoPingReceivedError(TimeoutError):
	pass


class WaitForOpsiclientdError(TimeoutError):
	pass


class WaitForEventTimeout(TimeoutError):
	pass


def opsiwakeupclients_main() -> None:
	options = parseOptions()

	if options.consoleLogLevel:
		init_logging(stderr_level=options.consoleLogLevel, stderr_format=DEFAULT_COLORED_FORMAT)
	if options.fileLogLevel and options.logFile:
		if os.path.exists(options.logFile):
			os.remove(options.logFile)
		init_logging(log_file=options.logFile, file_level=options.fileLogLevel)

	try:
		service_client = get_service_client(user_agent=f"opsi-wakeup-clients/{__version__}")
		wakeClientsForUpdate(
			service_client,
			options.depotId,
			options.inputFile,
			options.noAutoUpdate,
			options.shutdownwanted,
			options.reboot,
			options.rebootTimeout,
			options.hostGroupId,
			options.productGroupId,
			options.eventName,
			options.wolTimeout,
			options.eventTimeout,
			options.connectTimeout,
			options.pingTimeout,
			options.maxConcurrent,
		)
	finally:
		service_client.disconnect()


def main() -> None:
	try:
		opsiwakeupclients_main()
	except SystemExit as err:
		sys.exit(err.code)
	except Exception as err:
		logging_config(stderr_level=LOG_ERROR)
		logger.error(err, exc_info=True)
		print(f"ERROR: {err}", file=sys.stderr)
		sys.exit(1)
