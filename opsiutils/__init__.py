# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsiutils
"""
from opsicommon.client.opsiservice import ServiceClient, ServiceVerificationFlags
from opsicommon.config import OpsiConfig
from opsicommon.logging import logger

__version__ = '4.3.2.7'

SESSION_LIFETIME = 15


def get_service_client(  # pylint: disable=too-many-arguments
	address: str | None = None,
	username: str | None = None,
	password: str | None = None,
	session_cookie: str | None = None,
	user_agent: str | None = None,
	proxy_url: str | None = None,
	no_check_certificate: bool = False,
) -> ServiceClient:
	opsiconf = OpsiConfig()

	address = address or opsiconf.get("service", "url")
	username = username or opsiconf.get("host", "id")
	logger.debug("Creating service connection to '%s' as user '%s'", address, username)
	service_client = ServiceClient(
		address=address,
		username=username,
		password=password or opsiconf.get("host", "key"),
		user_agent=user_agent or f"opsi-utils/{__version__}",
		session_lifetime=SESSION_LIFETIME,
		session_cookie=session_cookie,
		verify=ServiceVerificationFlags.ACCEPT_ALL if no_check_certificate else ServiceVerificationFlags.STRICT_CHECK,
		ca_cert_file="/etc/opsi/ssl/opsi-ca-cert.pem",
		jsonrpc_create_objects=True,
		jsonrpc_create_methods=True,
		proxy_url=proxy_url,
	)
	service_client.connect()
	logger.info('Connected to %s', service_client.server_name)
	return service_client
