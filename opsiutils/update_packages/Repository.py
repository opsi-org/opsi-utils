# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
Handling repositories.
"""

import re
import threading
import time
from datetime import datetime, timedelta
from html.parser import HTMLParser

from opsicommon.client.opsiservice import ServiceClient
from opsicommon.logging import get_logger
from opsicommon.types import forceBool, forceStringList, forceUnicode

__all__ = ("LinksExtractor", "ProductRepositoryInfo", "sort_repository_list", "TransferSlotHeartbeat")
logger = get_logger("opsi-package-updater")
RETENTION_HEARTBEAT_INTERVAL_DIFF = 10.0
MIN_HEARTBEAT_INTERVAL = 1.0


class ProductRepositoryInfo:
	def __init__(
		self,
		name: str,
		baseUrl: str,
		dirs: list[str] | None = None,
		username: str = "",
		password: str = "",
		authcertfile: str = "",
		authkeyfile: str = "",
		opsiDepotId: str | None = None,
		autoInstall: bool = False,
		autoUpdate: bool = True,
		autoSetup: bool = False,
		proxy: str | None = None,
		excludes: list[re.Pattern] | None = None,
		includes: list[re.Pattern] | None = None,
		active: bool = False,
		autoSetupExcludes: list[re.Pattern] | None = None,
		verifyCert: bool = False,
	):
		self.name = forceUnicode(name)
		self.baseUrl = forceUnicode(baseUrl)
		self.dirs = forceStringList(dirs or [])
		self.excludes = excludes or []
		self.includes = includes or []
		self.username = forceUnicode(username)
		self.password = forceUnicode(password)
		self.authcertfile = forceUnicode(authcertfile)
		self.authkeyfile = forceUnicode(authkeyfile)
		self.autoInstall = autoInstall
		self.autoUpdate = autoUpdate
		self.autoSetup = autoSetup
		self.autoSetupExcludes = autoSetupExcludes or []
		self.opsiDepotId = opsiDepotId
		self.onlyDownload = False
		self.inheritProductProperties = False
		self.description = ""
		self.active = forceBool(active)
		self.verifyCert = forceBool(verifyCert)

		self.proxy = None
		if proxy:
			self.proxy = proxy
		if self.baseUrl.startswith("webdav"):
			self.baseUrl = f"http{self.baseUrl[6:]}"

	def getDownloadUrls(self) -> set[str]:
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


class LinksExtractor(HTMLParser):
	def __init__(self) -> None:
		super().__init__()
		self.links: set[str] = set()

	def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
		if tag != "a":
			return

		for attr in attrs:
			if attr[0] != "href":
				continue
			link = attr[1]
			if link:
				self.links.add(link)

	def getLinks(self) -> set[str]:
		return self.links


def sort_repository_list(repositories: list[ProductRepositoryInfo]) -> list[ProductRepositoryInfo]:
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
		response = self.service_connection.depot_acquireTransferSlot(self.depot_id, self.host_id, self.slot_id, "opsi_package_updater")  # type: ignore[attr-defined]
		self.slot_id = response.get("slot_id")
		logger.debug("Transfer slot Heartbeat %s, response: %s", self.slot_id, response)
		return response

	def release(self) -> None:
		response = self.service_connection.depot_releaseTransferSlot(self.depot_id, self.host_id, self.slot_id, "opsi_package_updater")  # type: ignore[attr-defined]
		logger.debug("releaseTransferSlot response: %s", response)

	def run(self) -> None:
		try:
			while not self.should_stop:
				response = self.acquire()
				if not response.get("retention"):
					logger.error("TransferSlotHeartbeat lost transfer slot (and did not get new one)")
					raise ConnectionError("TransferSlotHeartbeat lost transfer slot (and did not get new one)")
				wait_time = max(float(response["retention"]) - RETENTION_HEARTBEAT_INTERVAL_DIFF, MIN_HEARTBEAT_INTERVAL)
				logger.debug("Waiting %s seconds before reaquiring slot", wait_time)
				end = datetime.now() + timedelta(seconds=wait_time)
				while not self.should_stop and datetime.now() < end:
					time.sleep(1.0)
		finally:
			if self.slot_id:
				self.release()
