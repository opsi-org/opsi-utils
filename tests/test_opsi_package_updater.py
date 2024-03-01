"""
opsi-utils

tests for opsi-package-updater
"""

from dataclasses import dataclass
import json
import shutil
from pathlib import Path
from typing import Any, Generator
from unittest import mock

import pytest
from pyzsync import create_zsync_file

from opsicommon.package.associated_files import md5sum
from opsicommon.package.repo_meta import RepoMetaPackageCollection
from opsicommon.testing.helpers import http_test_server
from opsicommon.objects import OpsiDepotserver

from opsiutils.update_packages.Updater import OpsiPackageUpdater
from opsiutils.update_packages.Config import DEFAULT_CONFIG
from opsiutils.update_packages.Notifier import DummyNotifier
from opsiutils.opsipackageupdater import patch_repo_files
from opsiutils import __version__


ORIGINAL_REPO = """; This is a testcomment
[repository_uib_linux_experimental]
description = opsi Linux Support (experimental packages)
active = false
baseUrl = http://download.uib.de
dirs = opsi4.2/experimental/packages/linux/localboot/, opsi4.2/experimental/packages/linux/netboot/
autoInstall = false
autoUpdate = true
autoSetup = false
proxy =

[repository_uib_windows_testing]
description = opsi Windows Support (testing packages)
active = false
baseUrl = http://download.uib.de
dirs = opsi4.2/testing/packages/windows/localboot/, opsi4.2/testing/packages/windows/netboot/
autoInstall = false
autoUpdate = true
autoSetup = false
proxy =
"""

WRONGLY_PATCHED_REPO = """; This file has been patched by opsi-package-updater 4.3.0.26
; This is a testcomment
[repository_uib_linux_experimental]
description = opsi Linux Support (experimental packages)
active = false
baseUrl = https://opsipackages.43.opsi.org
dirs = experimental/linux/localboot/, experimental/linux/netboot/
autoInstall = false
autoUpdate = true
autoSetup = false
proxy =

[repository_uib_windows_testing]
description = opsi Windows Support (testing packages)
active = false
baseUrl = https://opsipackages.43.opsi.org
dirs = testing/windows/localboot/, testing/windows/netboot/
autoInstall = false
autoUpdate = true
autoSetup = false
proxy =
"""

PATCHED_REPO = f"""; This file has been patched by opsi-package-updater {__version__}
; This is a testcomment
[repository_uib_linux_experimental]
description = opsi Linux Support (experimental packages)
active = false
baseUrl = https://opsipackages.43.opsi.org/experimental
dirs = linux/localboot/, linux/netboot/
autoInstall = false
autoUpdate = true
autoSetup = false
proxy =

[repository_uib_windows_testing]
description = opsi Windows Support (testing packages)
active = false
baseUrl = https://opsipackages.43.opsi.org/testing
dirs = windows/localboot/, windows/netboot/
autoInstall = false
autoUpdate = true
autoSetup = false
proxy =
"""


class FakeService:
	def host_getObjects(self, **kwargs: Any) -> list[OpsiDepotserver]:  # pylint: disable=invalid-name,unused-argument
		depot = OpsiDepotserver(id="depot.opsi.org")
		depot.setDefaults()
		return [depot]

	def productOnDepot_getObjects(self, **kwargs: Any) -> list:  # pylint: disable=invalid-name,unused-argument
		return []


@dataclass
class UpdaterInfo:
	test_repo_conf: Path
	server_log: Path
	server_dir: Path
	local_dir: Path
	config: dict[str, Any]


@pytest.fixture
def package_updater_class() -> Generator[type[OpsiPackageUpdater], None, None]:
	cls = OpsiPackageUpdater
	with mock.patch.object(cls, "getConfigBackend", return_value=FakeService()):
		yield cls


def write_repo_conf(repo_conf: Path, base_url: str, proxy: str = "", dirs: str = "/") -> None:
	repo_conf.write_text(
		data=(
			f"[repository_test]\nactive = true\nbaseUrl = {base_url}\ndirs = {dirs}\nproxy = {proxy}\n"
			"autoInstall = true\nusername = user\npassword = pass\n"
		),
		encoding="utf-8",
	)


def prepare_updater(base_dir: Path, copy_files: bool = True) -> UpdaterInfo:
	"""returns tuple of test_repo_conf and server_log"""
	config_file = base_dir / "empty.conf"
	config_file.touch()
	local_dir = base_dir / "local_packages"
	local_dir.mkdir()
	server_dir = base_dir / "server_packages"
	if copy_files:
		shutil.copytree("tests/data/package-repo", server_dir)
	else:
		server_dir.mkdir()
	repo_conf_path = base_dir / "repos.d"
	repo_conf_path.mkdir()

	config = DEFAULT_CONFIG.copy()
	config["configFile"] = str(config_file)
	config["packageDir"] = str(local_dir)

	config_file.write_text(
		data=("[general]\n" f"packageDir = {str(local_dir)}\n" f"repositoryConfigDir = {str(repo_conf_path)}\n"), encoding="utf-8"
	)
	return UpdaterInfo(
		test_repo_conf=repo_conf_path / "test.repo",
		server_log=base_dir / "server.log",
		server_dir=server_dir,
		local_dir=local_dir,
		config=config,
	)


def test_get_packages(tmp_path: Path, package_updater_class: type[OpsiPackageUpdater]) -> None:  # pylint: disable=redefined-outer-name
	updater_info = prepare_updater(tmp_path, copy_files=False)

	server_package_file = updater_info.server_dir / "hwaudit_4.2.0.0-1.opsi"
	md5sum_file = updater_info.server_dir / "hwaudit_4.2.0.0-1.opsi.md5"
	zsync_file = updater_info.server_dir / "hwaudit_4.2.0.0-1.opsi.zsync"
	server_package_file.write_bytes(b"a" * 2048 * 10)
	create_zsync_file(server_package_file, zsync_file)
	server_package_md5sum = md5sum(server_package_file)
	md5sum_file.write_text(server_package_md5sum, encoding="ascii")

	with http_test_server(serve_directory=updater_info.server_dir, log_file=str(updater_info.server_log)) as server:
		base_url = f"http://localhost:{server.port}"
		write_repo_conf(updater_info.test_repo_conf, base_url)
		package_updater = package_updater_class(updater_info.config)  # type: ignore[arg-type]

		available_packages = package_updater.getDownloadablePackages()
		package = None
		for available_package in available_packages:
			if available_package["productId"] == "hwaudit":
				package = available_package
				break
		assert package is not None

		assert package["version"] == "4.2.0.0-1"
		assert package["packageFile"] == f"{base_url}/hwaudit_4.2.0.0-1.opsi"
		assert package["filename"] == server_package_file.name
		assert package["zsyncFile"] == f"{base_url}/{zsync_file.name}"

		new_packages = package_updater.get_packages(DummyNotifier())  # type: ignore[no-untyped-call]
		assert len(new_packages) == 1
		for filename in ("hwaudit_4.2.0.0-1.opsi", "hwaudit_4.2.0.0-1.opsi.md5", "hwaudit_4.2.0.0-1.opsi.zsync"):
			assert (updater_info.local_dir / filename).exists()
			# set_rights only works as intended if running on opsi servers
			# assert (updater_info.local_dir / filename).stat().st_uid != 0


@pytest.mark.parametrize(
	"server_accept_ranges",
	(True, False),
)
def test_get_packages_zsync(  # pylint: disable=redefined-outer-name,too-many-locals,too-many-statements
	tmp_path: Path, package_updater_class: type[OpsiPackageUpdater], server_accept_ranges: bool
) -> None:
	updater_info = prepare_updater(tmp_path, copy_files=False)

	server_package_file = updater_info.server_dir / "hwaudit_4.2.0.0-1.opsi"
	local_package_file = updater_info.local_dir / "hwaudit_4.1.0.0-1.opsi"
	local_old_zsync_tmp_file = updater_info.local_dir / "hwaudit_4.2.0.0-1.opsi.zsync-tmp-1685607801000"
	md5sum_file = updater_info.server_dir / "hwaudit_4.2.0.0-1.opsi.md5"
	zsync_file = updater_info.server_dir / "hwaudit_4.2.0.0-1.opsi.zsync"

	parts = [b"a" * 2048 * 10, b"b" * 2048 * 10, b"c" * 2048 * 10, b"d" * 2048 * 10, b"e" * 2048 * 10]
	server_package_file.write_bytes(parts[0] + parts[1] + parts[2] + parts[3] + parts[4])
	# a + c
	local_package_file.write_bytes(parts[0] + parts[2])
	# e
	local_old_zsync_tmp_file.write_bytes(parts[4])

	create_zsync_file(server_package_file, zsync_file)
	server_package_md5sum = md5sum(server_package_file)
	md5sum_file.write_text(server_package_md5sum, encoding="ascii")

	with http_test_server(
		serve_directory=updater_info.server_dir,
		response_headers={"accept-ranges": "bytes"} if server_accept_ranges else None,
		log_file=str(updater_info.server_log),
	) as server:
		base_url = f"http://localhost:{server.port}"
		proxy = ""

		write_repo_conf(updater_info.test_repo_conf, base_url, proxy)

		package_updater = package_updater_class(updater_info.config)  # type: ignore[arg-type]

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
				package_updater._useZsync(session, package, local_packages[0]) == server_accept_ranges
			)

		if "localhost" in base_url:
			updater_info.server_log.unlink()
		new_packages = package_updater.get_packages(DummyNotifier())  # type: ignore[no-untyped-call]
		assert len(new_packages) == 1

		# for line in server_log.read_text(encoding="utf-8").rstrip().split("\n"):
		# 	request = json.loads(line)
		# 	print(request)
		request = json.loads(updater_info.server_log.read_text(encoding="utf-8").rstrip().split("\n")[-1])
		updater_info.server_log.unlink()
		# print(request)

		assert md5sum(updater_info.local_dir / server_package_file.name) == server_package_md5sum
		assert request["headers"].get("Authorization") == "Basic dXNlcjpwYXNz"
		assert request["headers"]["Accept-Encoding"] == "identity"
		if server_accept_ranges:
			assert request["headers"]["Range"] == "bytes=18432-40959, 59392-81919, 100352-102399"
		else:
			assert "Range" not in request["headers"]


@pytest.mark.parametrize(
	"metafile, num_requests", (("packages.msgpack.zstd", 1), ("packages.json", 2), ("packages.msgpack", 3), ("packages.json.zstd", 4))
)
def test_server_repo_meta(  # pylint: disable=redefined-outer-name,too-many-locals
	tmp_path: Path, package_updater_class: type[OpsiPackageUpdater], metafile: str, num_requests: int
) -> None:
	updater_info = prepare_updater(tmp_path)

	rmpc = RepoMetaPackageCollection()
	rmpc.scan_packages(updater_info.server_dir)
	rmpc.write_metafile(updater_info.server_dir / metafile)

	with http_test_server(serve_directory=updater_info.server_dir, log_file=str(updater_info.server_log)) as server:
		base_url = f"http://localhost:{server.port}"
		proxy = ""

		write_repo_conf(updater_info.test_repo_conf, base_url, proxy)

		package_updater = package_updater_class(updater_info.config)  # type: ignore[arg-type]
		available_packages = package_updater.getDownloadablePackages()
		# Next call must use cache
		available_packages = package_updater.getDownloadablePackages()
		assert len(available_packages) == 4
		requests = [json.loads(line) for line in updater_info.server_log.read_text(encoding="utf-8").rstrip().split("\n")]
		assert len(requests) == num_requests
		assert requests[num_requests - 1]["path"] == f"/{metafile}"


def test_server_repo_meta_multiurl(  # pylint: disable=redefined-outer-name,too-many-locals
	tmp_path: Path, package_updater_class: type[OpsiPackageUpdater]
) -> None:
	updater_info = prepare_updater(tmp_path)

	rmpc = RepoMetaPackageCollection()
	rmpc.scan_packages(updater_info.server_dir)
	print(rmpc.packages["localboot_new"]["1.0-1"])
	rmpc.packages["localboot_new"]["1.0-1"].url = [
		"localboot_new_1.0-1.opsi",
		"dir/localboot_new_1.0-1.opsi",
		"otherdir/localboot_new_1.0-1.opsi",
	]
	rmpc.packages["localboot_new"]["1.0-1"].zsync_url = ["localboot_new_1.0-1.opsi.zsync", "dir/localboot_new_1.0-1.opsi.zsync", None]
	rmpc.write_metafile(updater_info.server_dir / "packages.json")

	with http_test_server(serve_directory=updater_info.server_dir, log_file=str(updater_info.server_log)) as server:
		base_url = f"http://localhost:{server.port}"

		write_repo_conf(updater_info.test_repo_conf, base_url)  # no filter
		package_updater = package_updater_class(updater_info.config)  # type: ignore[arg-type]
		available_packages = package_updater.getDownloadablePackages()
		assert len(available_packages) == 4
		for package in available_packages:
			if package["version"] != "1.0-1":
				continue
			assert package["packageFile"] == f"{base_url}/localboot_new_1.0-1.opsi"
			assert package["zsyncFile"] == f"{base_url}/localboot_new_1.0-1.opsi.zsync"

		write_repo_conf(updater_info.test_repo_conf, base_url, dirs="otherdir/")
		package_updater = package_updater_class(updater_info.config)  # type: ignore[arg-type]
		available_packages = package_updater.getDownloadablePackages()
		assert len(available_packages) == 1
		assert available_packages[0]["packageFile"] == f"{base_url}/otherdir/localboot_new_1.0-1.opsi"
		assert available_packages[0]["zsyncFile"] is None


@pytest.mark.parametrize(
	"source, name, correct_result",
	(
		(ORIGINAL_REPO, "experimental.repo", PATCHED_REPO),
		(WRONGLY_PATCHED_REPO, "experimental.repo", PATCHED_REPO),
		(ORIGINAL_REPO, "custom.repo", ORIGINAL_REPO),
	),
)
def test_patch_repo_files(tmp_path: Path, source: str, name: str, correct_result: str) -> None:
	# Create a test repo file
	(tmp_path / name).write_text(source, encoding="utf-8")

	# Patch the repo files
	patch_repo_files(tmp_path)
	result = (tmp_path / name).read_text(encoding="utf-8")
	print(result)

	# empty entries are replaced by a space char (e.g. `proxy = `)
	for line in result.splitlines():
		assert line.strip() in correct_result
	assert "; This is a testcomment" in result.splitlines()
