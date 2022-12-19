# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsiutils
"""
from opsicommon.logging import logger
from opsicommon.client.opsiservice import (  # type: ignore[import]
	ServiceClient,
	ServiceVerificationModes,
)
from opsicommon.config import OpsiConfig  # type: ignore[import]

__version__ = '4.3.0.0'

SESSION_LIFETIME = 15


def get_service_client(
	address: str | None = None,
	username: str | None = None,
	password: str | None = None,
	session_cookie: str | None = None,
	user_agent: str | None = None,
) -> ServiceClient:
	opsiconf = OpsiConfig()

	service_client = ServiceClient(
		address=address or "https://localhost:4447/rpc",  # Address from opsiconf?
		username=username or opsiconf.get("host", "id"),
		password=password or opsiconf.get("host", "key"),
		user_agent=user_agent or f"opsi-admin/{__version__}",
		session_lifetime=SESSION_LIFETIME,
		verify=ServiceVerificationModes.ACCEPT_ALL,
		session_cookie=session_cookie
	)
	service_client.connect()
	logger.info('Connected')