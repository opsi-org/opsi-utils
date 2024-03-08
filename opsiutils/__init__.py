# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsiutils
"""

import json
import os

from opsicommon.client.opsiservice import ServiceClient, ServiceVerificationFlags
from opsicommon.config import OpsiConfig
from opsicommon.logging import logger, secret_filter
from opsicommon.utils import execute

__version__ = "4.3.2.9"

SESSION_LIFETIME = 15
OPSICONFD_CONF = "/etc/opsi/opsiconfd.conf"


def get_opsiconfd_config() -> dict[str, str]:
	config = {"ssl_server_key": "", "ssl_server_cert": "", "ssl_server_key_passphrase": ""}
	try:
		proc = execute(["opsiconfd", "get-config"])
		for attr, value in json.loads(proc.stdout).items():
			if attr in config.keys() and value is not None:
				config[attr] = value
				if attr == "ssl_server_key_passphrase":
					secret_filter.add_secrets(value)
	except Exception as err:
		logger.debug("Failed to get opsiconfd config %s", err)
	return config


def get_service_client(
	address: str | None = None,
	*,
	username: str | None = None,
	password: str | None = None,
	session_cookie: str | None = None,
	user_agent: str | None = None,
	proxy_url: str | None = None,
	no_check_certificate: bool = False,
	client_cert_auth: bool | None = None,
) -> ServiceClient:
	opsiconf = OpsiConfig()

	address = address or opsiconf.get("service", "url")
	client_cert_file = None
	client_key_file = None
	client_key_password = None

	if not username:
		username = opsiconf.get("host", "id")
		password = opsiconf.get("host", "key")
		if client_cert_auth is None:
			client_cert_auth = True

	if client_cert_auth:
		cfg = get_opsiconfd_config()
		logger.debug("opsiconfd config: %r", cfg)
		if (
			cfg["ssl_server_key"]
			and os.path.exists(cfg["ssl_server_key"])
			and cfg["ssl_server_cert"]
			and os.path.exists(cfg["ssl_server_cert"])
		):
			client_cert_file = cfg["ssl_server_cert"]
			client_key_file = cfg["ssl_server_key"]
			client_key_password = cfg["ssl_server_key_passphrase"]

	logger.debug("Creating service connection to '%s' as user '%s' (client_cert_file=%s)", address, username, client_cert_file)
	service_client = ServiceClient(
		address=address,
		username=username,
		password=password,
		user_agent=user_agent or f"opsi-utils/{__version__}",
		session_lifetime=SESSION_LIFETIME,
		session_cookie=session_cookie,
		verify=ServiceVerificationFlags.ACCEPT_ALL if no_check_certificate else ServiceVerificationFlags.STRICT_CHECK,
		ca_cert_file="/etc/opsi/ssl/opsi-ca-cert.pem",
		client_cert_file=client_cert_file,
		client_key_file=client_key_file,
		client_key_password=client_key_password,
		jsonrpc_create_objects=True,
		jsonrpc_create_methods=True,
		proxy_url=proxy_url,
	)
	service_client.connect()
	logger.info("Connected to %s", service_client.server_name)
	return service_client
