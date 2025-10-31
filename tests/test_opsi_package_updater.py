"""
opsi-utils

tests for opsi-package-updater
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator
from unittest import mock

import pytest
from opsicommon.objects import OpsiDepotserver
from opsicommon.package.associated_files import md5sum
from opsicommon.package.repo_meta import RepoMetaPackageCollection
from opsicommon.testing.helpers import http_test_server
from pyzsync import create_zsync_file

from opsiutils import __version__
from opsiutils.opsipackageupdater import patch_repo_files
from opsiutils.update_packages.Config import DEFAULT_CONFIG
from opsiutils.update_packages.Notifier import DummyNotifier
from opsiutils.update_packages.Updater import OpsiPackageUpdater

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


class InstallLog:
	def __init__(self) -> None:
		self.installed: list[str] = []

	def append(self, filename: str) -> None:
		self.installed.append(filename)


install_log = InstallLog()


class FakeService:
	def host_getObjects(self, **kwargs: Any) -> list[OpsiDepotserver]:
		depot = OpsiDepotserver(id="depot.opsi.org")
		depot.setDefaults()
		return [depot]

	def productOnDepot_getObjects(self, **kwargs: Any) -> list:
		return []

	def productPropertyState_getObjects(self, **kwargs: Any) -> list:
		return []

	def depot_installPackage(self, **kwargs: Any) -> None:
		install_log.append(kwargs["filename"])


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
	with (
		mock.patch.object(cls, "getConfigBackend", return_value=FakeService()),
		mock.patch.object(cls, "getDepotBackend", return_value=FakeService()),
	):
		yield cls


def write_repo_conf(
	repo_conf: Path,
	base_url: str,
	proxy: str = "",
	dirs: str = "/",
	excludes: list[str] | None = None,
	customVersions: list[str] | None = None,
) -> None:
	repo_conf.write_text(
		data=(
			f"[repository_test]\nactive = true\nbaseUrl = {base_url}\ndirs = {dirs}\nproxy = {proxy}\n"
			f"autoInstall = true\nusername = user\npassword = pass\nexcludes = {', '.join(excludes) if excludes else ''}\n"
			f"customVersions = {', '.join(customVersions) if customVersions else ''}\n"
		),
		encoding="utf-8",
	)


def prepare_updater(base_dir: Path, copy_files: bool = True, ignore_errors: bool = False) -> UpdaterInfo:
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
	(server_dir / "otherdir").mkdir()
	repo_conf_path = base_dir / "repos.d"
	repo_conf_path.mkdir()

	config = DEFAULT_CONFIG.copy()
	config["configFile"] = str(config_file)
	config["packageDir"] = str(local_dir)
	config["ignoreErrors"] = ignore_errors

	config_file.write_text(
		data=(f"[general]\npackageDir = {str(local_dir)}\nrepositoryConfigDir = {str(repo_conf_path)}\n"), encoding="utf-8"
	)
	return UpdaterInfo(
		test_repo_conf=repo_conf_path / "test.repo",
		server_log=base_dir / "server.log",
		server_dir=server_dir,
		local_dir=local_dir,
		config=config,
	)


@pytest.mark.parametrize(
	"excludes",
	(None, ["hwaudit"], ["opsi-client-agent", "opsi-configed", "hwaudit"]),
)
def test_get_packages(tmp_path: Path, package_updater_class: type[OpsiPackageUpdater], excludes: list[str] | None) -> None:  # pylint: disable=redefined-outer-name
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
		write_repo_conf(updater_info.test_repo_conf, base_url, excludes=excludes)
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
		if excludes:
			assert not new_packages
		else:
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
			assert request["headers"]["Range"] == "bytes=18432-40959,59392-81919,100352-102399"
		else:
			assert "Range" not in request["headers"]


@pytest.mark.parametrize(
	"metafile, num_requests", (("packages.msgpack.zstd", 3), ("packages.json", 4), ("packages.msgpack", 5), ("packages.json.zstd", 6))
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
		assert len(available_packages) == 6
		requests = [json.loads(line) for line in updater_info.server_log.read_text(encoding="utf-8").rstrip().split("\n")]
		assert len(requests) == num_requests
		assert requests[num_requests - 2]["path"] == f"/{metafile}"  # head, get meta 0, get meta 1, ... , head


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
		assert len(available_packages) == 6
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


def test_process_updates(tmp_path: Path, package_updater_class: type[OpsiPackageUpdater]) -> None:
	updater_info = prepare_updater(tmp_path, ignore_errors=True)

	rmpc = RepoMetaPackageCollection()
	rmpc.scan_packages(updater_info.server_dir)
	rmpc.write_metafile(updater_info.server_dir / "packages.json")

	with http_test_server(serve_directory=updater_info.server_dir, log_file=str(updater_info.server_log)) as server:
		base_url = f"http://localhost:{server.port}"
		write_repo_conf(updater_info.test_repo_conf, base_url)

		package_updater = package_updater_class(updater_info.config)  # type: ignore[arg-type]
		install_log.installed = []
		package_updater.processUpdates()
		assert len(install_log.installed) == 2
		for entry in install_log.installed:
			assert entry.endswith("localboot_new_42.0-1337.opsi") or entry.endswith("test-netboot_1.0-2.opsi")


@pytest.mark.parametrize(
	"custom_versions, expected_version",
	(
		(None, "42.0-1337"),
		([".*~en"], "42.0-1337~en"),
		([".*~en", "localboot_new~ita"], "42.0-1337~ita"),
		([".*~other", "localboot_new~foo"], "42.0-1337"),
	),
)
def test_prefer_custom_versions(
	tmp_path: Path, package_updater_class: type[OpsiPackageUpdater], custom_versions: list[str] | None, expected_version: str
) -> None:
	updater_info = prepare_updater(tmp_path, ignore_errors=True)

	rmpc = RepoMetaPackageCollection()
	rmpc.scan_packages(updater_info.server_dir)
	rmpc.write_metafile(updater_info.server_dir / "packages.json")

	with http_test_server(serve_directory=updater_info.server_dir, log_file=str(updater_info.server_log)) as server:
		base_url = f"http://localhost:{server.port}"
		write_repo_conf(
			updater_info.test_repo_conf,
			base_url,
			excludes=[],
			customVersions=custom_versions,
		)

		package_updater = package_updater_class(updater_info.config)  # type: ignore[arg-type]
		available_packages = package_updater.getDownloadablePackages()
		for reverse_sort in (False, True):
			available_packages.sort(key=lambda pkg: str(pkg["version"]), reverse=reverse_sort)
			localboot_new_versions = sorted(str(pkg["version"]) for pkg in available_packages if pkg["productId"] == "localboot_new")
			print("localboot_new_versions:", localboot_new_versions)
			assert localboot_new_versions == ["1.0-1", "2.0-1", "42.0-1337", "42.0-1337~en", "42.0-1337~ita"]
			newest_packages = package_updater.onlyNewestPackages(available_packages)
			latest_localboot_new_versions = sorted(str(pkg["version"]) for pkg in newest_packages if pkg["productId"] == "localboot_new")
			print("latest_localboot_new_versions:", latest_localboot_new_versions)
			print("expected_version:", expected_version)
			assert latest_localboot_new_versions == [expected_version]
