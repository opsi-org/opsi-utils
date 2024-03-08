"""
opsi-utils
"""

import json
from typing import Any
from unittest.mock import patch

from opsiutils import get_opsiconfd_config


def test_get_opsiconfd_config() -> None:
	class Proc:
		stdout = json.dumps(
			{
				"ssl_server_cert": "/etc/opsi/env-ssl-server-cert.pem",
				"ssl_server_key": "/etc/opsi/ssl-server-key.pem",
				"ssl_server_key_passphrase": "passphrase",
			}
		)

	def execute1(*args: Any, **kwargs: Any) -> Proc:
		return Proc()

	with patch("opsiutils.subprocess.run", execute1):
		conf = get_opsiconfd_config()
		assert conf["ssl_server_cert"] == "/etc/opsi/env-ssl-server-cert.pem"
		assert conf["ssl_server_key"] == "/etc/opsi/ssl-server-key.pem"
		assert conf["ssl_server_key_passphrase"] == "passphrase"

	def execute2(*args: Any, **kwargs: Any) -> None:
		raise FileNotFoundError("opsiconfd not found")

	with patch("opsiutils.subprocess.run", execute2):
		conf = get_opsiconfd_config()
		assert conf["ssl_server_cert"] == ""
		assert conf["ssl_server_key"] == ""
		assert conf["ssl_server_key_passphrase"] == ""
