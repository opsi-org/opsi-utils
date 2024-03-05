# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsiutils
"""
import os
from pathlib import Path

from opsicommon.client.opsiservice import ServiceClient, ServiceVerificationFlags
from opsicommon.config import OpsiConfig
from opsicommon.logging import logger

__version__ = "4.3.2.9"

SESSION_LIFETIME = 15
OPSICONFD_CONF = "/etc/opsi/opsiconfd.conf"


def get_opsiconfd_config() -> dict[str, str]:
	config = {
		"ssl-server-cert": "",
		"ssl-server-key": "",
		"ssl-server-key-passphrase": "",
	}
	opsiconfd_conf = Path(OPSICONFD_CONF)
	if not opsiconfd_conf.exists():
		logger.info("opsiconfd config file not found at '%s'", opsiconfd_conf)
		return config
	try:
		for line in opsiconfd_conf.read_text(encoding="utf-8").splitlines():
			line = line.strip()
			if not line or line.startswith("#") or "=" not in line:
				continue
			attr, value = line.split("=", 1)
			attr = attr.strip()
			if attr in config:
				config[attr] = value.strip()
				logger.info("Using opsiconfd config '%s' from '%s'", attr, opsiconfd_conf)
	except Exception as err:
		logger.error("Failed to read opsiconfd config '%s': %s", opsiconfd_conf, err)
		return config

	for key in config:
		env_key = f"OPSICONFD_{key.upper().replace('-', '_')}"
		if env_val := os.environ.get(env_key):
			logger.info("Using opsiconfd config '%s' from environment", env_key)
			config[key] = env_val

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
		if (
			cfg["ssl-server-key"]
			and os.path.exists(cfg["ssl-server-key"])
			and cfg["ssl-server-cert"]
			and os.path.exists(cfg["ssl-server-cert"])
		):
			client_cert_file = cfg["ssl-server-cert"]
			client_key_file = cfg["ssl-server-key"]
			client_key_password = cfg["ssl-server-key-passphrase"]

	logger.debug("Creating service connection to '%s' as user '%s'", address, username)
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
