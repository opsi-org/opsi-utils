# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
Handling repositories.
"""

import threading
from typing import Iterator
from html.parser import HTMLParser
import time
from datetime import datetime, timedelta

from opsicommon.client.opsiservice import ServiceClient
from opsicommon.logging import get_logger
from OPSI.Types import forceBool, forceUnicode, forceUnicodeList

__all__ = ("LinksExtractor", "ProductRepositoryInfo", "sort_repository_list")
logger = get_logger("opsi-package-updater")
RETENTION_HEARTBEAT_INTERVAL_DIFF = 10.0
MIN_HEARTBEAT_INTERVAL = 1.0


class ProductRepositoryInfo:  # pylint: disable=dangerous-default-value,too-many-instance-attributes,too-few-public-methods,too-many-arguments,too-many-locals
	def __init__(
		self,
		name,
		baseUrl,
		dirs=[],
		username="",
		password="",
		authcertfile="",
		authkeyfile="",
		opsiDepotId=None,
		autoInstall=False,
		autoUpdate=True,
		autoSetup=False,
		proxy=None,
		excludes=[],
		includes=[],
		active=False,
		autoSetupExcludes=[],
		verifyCert=False,
	):
		self.name = forceUnicode(name)
		self.baseUrl = forceUnicode(baseUrl)
		self.dirs = forceUnicodeList(dirs)
		self.excludes = excludes
		self.includes = includes
		self.username = forceUnicode(username)
		self.password = forceUnicode(password)
		self.authcertfile = forceUnicode(authcertfile)
		self.authkeyfile = forceUnicode(authkeyfile)
		self.autoInstall = autoInstall
		self.autoUpdate = autoUpdate
		self.autoSetup = autoSetup
		self.autoSetupExcludes = autoSetupExcludes
		self.opsiDepotId = opsiDepotId
		self.onlyDownload = None
		self.inheritProductProperties = None
		self.description = ""
		self.active = forceBool(active)
		self.verifyCert = forceBool(verifyCert)

		self.proxy = None
		if proxy:
			self.proxy = proxy
		if self.baseUrl.startswith("webdav"):
			self.baseUrl = f"http{self.baseUrl[6:]}"

	def getDownloadUrls(self):
		urls = set()
		for directory in self.dirs:
			if directory in ("", "/", "."):
				url = self.baseUrl
			else:
				url = f"{self.baseUrl}/{directory}"
			if not url.endswith("/"):
				url = f"{url}/"
			urls.add(url)
		return urls


class LinksExtractor(HTMLParser):  # pylint: disable=abstract-method
	def __init__(self):
		super().__init__()
		self.links = set()

	def handle_starttag(self, tag, attrs):
		if tag != "a":
			return

		for attr in attrs:
			if attr[0] != "href":
				continue
			link = attr[1]
			self.links.add(link)

	def getLinks(self):
		return self.links


def sort_repository_list(repositories: Iterator[ProductRepositoryInfo]) -> list[ProductRepositoryInfo]:
	depot_repos = []
	online_repos = []
	for repository in repositories:
		if repository.opsiDepotId:
			depot_repos.append(repository)
		else:
			online_repos.append(repository)
	return online_repos + depot_repos  # process depot_repos last because of possible time waiting for transfer slot


class TransferSlotHeartbeat(threading.Thread):
	def __init__(self, service_connection: ServiceClient, depot_id: str, host_id: str) -> None:
		super().__init__(daemon=True)
		self.should_stop = False
		self.service_connection = service_connection
		self.depot_id = depot_id
		self.host_id = host_id
		self.slot_id = None

	def acquire(self) -> dict[str, str | float]:
		response = self.service_connection.depot_acquireTransferSlot(self.depot_id, self.host_id, self.slot_id, "opsi_package_updater")
		self.slot_id = response.get("slot_id")
		logger.debug("Transfer slot Heartbeat %s, response: %s", self.slot_id, response)
		return response

	def release(self) -> None:
		response = self.service_connection.depot_releaseTransferSlot(self.depot_id, self.host_id, self.slot_id, "opsi_package_updater")
		logger.debug("releaseTransferSlot response: %s", response)

	def run(self) -> None:
		try:
			while not self.should_stop:
				response = self.acquire()
				if not response.get("retention"):
					logger.error("TransferSlotHeartbeat lost transfer slot (and did not get new one)")
					raise ConnectionError("TransferSlotHeartbeat lost transfer slot (and did not get new one)")
				wait_time = max(response["retention"] - RETENTION_HEARTBEAT_INTERVAL_DIFF, MIN_HEARTBEAT_INTERVAL)
				logger.debug("Waiting %s seconds before reaquiring slot", wait_time)
				end = datetime.now() + timedelta(seconds=wait_time)
				while not self.should_stop and datetime.now() < end:
					time.sleep(1.0)
				time.sleep(wait_time)
		finally:
			if self.slot_id:
				self.release()
