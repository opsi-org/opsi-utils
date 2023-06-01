"""
opsi-utils

tests for opsi-package-updater
"""

import json
from pathlib import Path
from typing import Any, Generator
from unittest import mock

import pytest
from pyzsync import create_zsync_file

from opsicommon.package.associated_files import md5sum
from opsicommon.testing.helpers import http_test_server
from opsicommon.objects import OpsiDepotserver

from opsiutils.update_packages.Updater import OpsiPackageUpdater
from opsiutils.update_packages.Config import DEFAULT_CONFIG
from opsiutils.update_packages.Notifier import DummyNotifier


class FakeService:
	def host_getObjects(self, **kwargs: Any) -> list[OpsiDepotserver]:  # pylint: disable=invalid-name,unused-argument
		depot = OpsiDepotserver(id="depot.opsi.org")
		depot.setDefaults()
		return [depot]

	def productOnDepot_getObjects(self, **kwargs: Any) -> list:  # pylint: disable=invalid-name,unused-argument
		return []


@pytest.fixture
def package_updater_class() -> Generator[type[OpsiPackageUpdater], None, None]:
	cls = OpsiPackageUpdater
	with mock.patch.object(cls, "getConfigBackend", return_value=FakeService()):
		yield cls


@pytest.mark.parametrize(
	"server_accept_ranges",
	(True, False),
)
def test_get_packages(  # pylint: disable=redefined-outer-name,too-many-locals,too-many-statements
	tmp_path: Path, package_updater_class: type[OpsiPackageUpdater], server_accept_ranges: bool
) -> None:
	config_file = tmp_path / "empty.conf"
	config_file.touch()
	local_dir = tmp_path / "local_packages"
	local_dir.mkdir()
	server_dir = tmp_path / "server_packages"
	server_dir.mkdir()
	repo_conf_path = tmp_path / "repos.d"
	repo_conf_path.mkdir()
	test_repo_conf = repo_conf_path / "test.repo"
	server_log = tmp_path / "server.log"

	config = DEFAULT_CONFIG.copy()
	config["configFile"] = str(config_file)
	config["packageDir"] = str(local_dir)

	config_file.write_text(
		data=("[general]\n" f"packageDir = {str(local_dir)}\n" f"repositoryConfigDir = {str(repo_conf_path)}\n"), encoding="utf-8"
	)

	server_package_file = server_dir / "hwaudit_4.2.0.0-1.opsi"
	local_package_file = local_dir / "hwaudit_4.1.0.0-1.opsi"
	local_old_zsync_tmp_file = local_dir / "hwaudit_4.2.0.0-1.opsi.zsync-tmp-1685607801000"
	md5sum_file = server_dir / "hwaudit_4.2.0.0-1.opsi.md5"
	zsync_file = server_dir / "hwaudit_4.2.0.0-1.opsi.zsync"

	parts = [b"a" * 2048, b"b" * 2048, b"c" * 2048, b"d" * 2048, b"e" * 2048]
	server_package_file.write_bytes(parts[0] + parts[1] + parts[2] + parts[3] + parts[4])
	local_package_file.write_bytes(parts[0] + parts[2])
	local_old_zsync_tmp_file.write_bytes(parts[4])

	create_zsync_file(server_package_file, zsync_file)
	server_package_md5sum = md5sum(server_package_file)
	md5sum_file.write_text(server_package_md5sum, encoding="ascii")

	def write_repo_conf(base_url: str, proxy: str) -> None:
		test_repo_conf.write_text(
			data=(
				f"[repository_test]\nactive = true\nbaseUrl = {base_url}\ndirs = /\nproxy = {proxy}\n"
				"autoInstall = true\nusername = user\npassword = pass\n"
			),
			encoding="utf-8",
		)

	with http_test_server(
		serve_directory=server_dir, response_headers={"accept-ranges": "bytes"} if server_accept_ranges else None, log_file=str(server_log)
	) as server:
		base_url = f"http://localhost:{server.port}"
		proxy = ""

		write_repo_conf(base_url, proxy)

		package_updater = package_updater_class(config)  # type: ignore[arg-type]

		available_packages = package_updater.getDownloadablePackages()
		package = None
		for available_package in available_packages:
			if available_package["productId"] == "hwaudit":
				package = available_package
				break
		assert package is not None

		local_packages = package_updater.getLocalPackages()

		assert package["version"] == "4.2.0.0-1"
		assert package["packageFile"] == f"{base_url}/hwaudit_4.2.0.0-1.opsi"
		assert package["filename"] == server_package_file.name
		assert package["zsyncFile"] == f"{base_url}/{zsync_file.name}"
		with package_updater.makeSession(package["repository"]) as session:  # type: ignore[arg-type,var-annotated]
			assert (
				# pylint: disable=protected-access
				package_updater._useZsync(session, package, local_packages[0])
				== server_accept_ranges
			)

		if "localhost" in base_url:
			server_log.unlink()
		new_packages = package_updater.get_packages(DummyNotifier())  # type: ignore[no-untyped-call]
		assert len(new_packages) == 1

		# for line in server_log.read_text(encoding="utf-8").rstrip().split("\n"):
		# 	request = json.loads(line)
		# 	print(request)
		request = json.loads(server_log.read_text(encoding="utf-8").rstrip().split("\n")[-1])
		server_log.unlink()
		# print(request)

		assert md5sum(local_dir / server_package_file.name) == server_package_md5sum
		assert request["headers"].get("Authorization") == "Basic dXNlcjpwYXNz"
		assert request["headers"]["Accept-Encoding"] == "identity"
		if server_accept_ranges:
			assert request["headers"]["Range"] == "bytes=2048-4095, 6144-8191"
		else:
			assert "Range" not in request["headers"]
