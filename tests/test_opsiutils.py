"""
opsi-utils
"""

from pathlib import Path
from unittest.mock import patch

from opsicommon.testing.helpers import environment

from opsiutils import get_opsiconfd_config


def test_get_opsiconfd_config(tmp_path: Path) -> None:
	opsiconfd_conf = tmp_path / "opsiconfd.conf"
	opsiconfd_conf.write_text(
		"""

		ssl-server-cert  =  /etc/opsi/ssl-server-cert.pem
		ssl-server-key  = /etc/opsi/ssl-server-key.pem
		# ssl-server-key-passphrase   = wrongpassphrase
		ssl-server-key-passphrase  = passphrase
		other = value
		abc
		"""
	)
	with (
		environment({"OPSICONFD_SSL_SERVER_CERT": "/etc/opsi/env-ssl-server-cert.pem"}),
		patch("opsiutils.OPSICONFD_CONF", str(opsiconfd_conf)),
	):
		conf = get_opsiconfd_config()
		assert conf["ssl-server-cert"] == "/etc/opsi/env-ssl-server-cert.pem"
		assert conf["ssl-server-key"] == "/etc/opsi/ssl-server-key.pem"
		assert conf["ssl-server-key-passphrase"] == "passphrase"
