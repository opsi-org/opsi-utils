"""
opsi-utils

tests for opsi-package-updater
"""

import json
import random
import shutil
import tempfile
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import requests
from opsi.crypt.hash import FileHashAlgorithm, hash_file
from opsi.opsi.package import OpsiPackage, RepoMetaPackageCollection
from opsi.opsi.service.model.object import OpsiDepotserver
from opsi.opsi.service.server import OpsiConfig
from opsi.testing.helper import http_test_server
from pyzsync import create_zsync_file

from opsiutils import __version__
from opsiutils.opsipackageupdater import patch_repo_files
from opsiutils.update_packages.Config import DEFAULT_CONFIG
from opsiutils.update_packages.Notifier import DummyNotifier
from opsiutils.update_packages.Updater import OpsiPackageUpdater, get_local_package_info

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


def test_get_local_package_info(tmp_path: Path) -> None:
	matching_package = tmp_path / "hwaudit_4.2.0.0-1.opsi"
	non_matching_package = tmp_path / "hwaudit_extra_4.2.0.0-1.opsi"
	matching_package.touch()
	non_matching_package.touch()

	package_info = get_local_package_info(product_id="hwaudit", package_directory=tmp_path)

	assert package_info is not None
	assert package_info.package_file == matching_package
	assert package_info.product_id == "hwaudit"
	assert package_info.version == "4.2.0.0-1"


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


@pytest.fixture(autouse=True)
def opsi_config(tmp_path: Path) -> Generator[None, None, None]:
	config_file = tmp_path / "opsi.conf"
	config_file.write_text('[host]\nid = "depot.opsi.org"\nserver-role = "configserver"\n', encoding="utf-8")
	OpsiConfig.reset_singleton()
	with mock.patch.object(OpsiConfig, "config_file", str(config_file)):
		yield
	OpsiConfig.reset_singleton()


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
	data = (
		f"[repository_test]\nactive = true\nbaseUrl = {base_url}\ndirs = {dirs}\nproxy = {proxy}\n"
		f"autoInstall = true\nusername = user\npassword = pass\nexcludes = {', '.join(excludes) if excludes else ''}\n"
		f"customVersions = {', '.join(customVersions) if customVersions else ''}\n"
	)
	repo_conf.write_text(data, encoding="utf-8")


SERVER_PACKAGES = (
	("localboot_new_1.0-1.opsi", "LocalbootProduct"),
	("localboot_new_2.0-1.opsi", "LocalbootProduct"),
	("localboot_new_42.0-1337.opsi", "LocalbootProduct"),
	("localboot_new_42.0-1337~en.opsi", "LocalbootProduct"),
	("localboot_new_42.0-1337~ita.opsi", "LocalbootProduct"),
	("subdir/test-netboot_1.0-2.opsi", "NetbootProduct"),
)


def create_package(package_file: Path, product_type: str = "LocalbootProduct") -> None:
	"""Create an opsi package, product id and version are taken from the file name (`<id>_<version>-<package_version>[~<custom>].opsi`)."""
	product_id, version = package_file.name.removesuffix(".opsi").rsplit("_", 1)
	product_version, package_version = version.split("~", 1)[0].rsplit("-", 1)
	with tempfile.TemporaryDirectory() as temp_dir:
		base_dir = Path(temp_dir)
		opsi_dir = base_dir / "OPSI"
		opsi_dir.mkdir()
		(opsi_dir / "control.toml").write_text(
			f'[Package]\nversion = "{package_version}"\ndepends = []\n\n'
			f'[Product]\ntype = "{product_type}"\nid = "{product_id}"\nname = "{product_id}"\nversion = "{product_version}"\n',
			encoding="utf-8",
		)
		package_archive = OpsiPackage().create_package_archive(base_dir, destination=base_dir)
		package_file.parent.mkdir(parents=True, exist_ok=True)
		shutil.move(package_archive, package_file)


def prepare_updater(base_dir: Path, create_packages: bool = True, ignore_errors: bool = False) -> UpdaterInfo:
	"""returns tuple of test_repo_conf and server_log"""
	config_file = base_dir / "empty.conf"
	config_file.touch()
	local_dir = base_dir / "local_packages"
	local_dir.mkdir()
	server_dir = base_dir / "server_packages"
	server_dir.mkdir()
	if create_packages:
		for package_file, product_type in SERVER_PACKAGES:
			create_package(server_dir / package_file, product_type)
	(server_dir / "otherdir").mkdir()
	repo_conf_path = base_dir / "repos.d"
	repo_conf_path.mkdir()

	config = DEFAULT_CONFIG.copy()
	config["configFile"] = str(config_file)
	config["packageDir"] = str(local_dir)
	config["ignoreErrors"] = ignore_errors

	config_file.write_text(data=(f"[general]\npackageDir = {local_dir!s}\nrepositoryConfigDir = {repo_conf_path!s}\n"), encoding="utf-8")
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
	updater_info = prepare_updater(tmp_path, create_packages=False)

	server_package_file = updater_info.server_dir / "hwaudit_4.2.0.0-1.opsi"
	md5sum_file = updater_info.server_dir / "hwaudit_4.2.0.0-1.opsi.md5"
	zsync_file = updater_info.server_dir / "hwaudit_4.2.0.0-1.opsi.zsync"
	server_package_file.write_bytes(b"a" * 2048 * 10)
	create_zsync_file(server_package_file, zsync_file)
	server_package_md5sum = hash_file(server_package_file, FileHashAlgorithm.MD5)
	md5sum_file.write_text(server_package_md5sum, encoding="ascii")

	with http_test_server(serve_directory=updater_info.server_dir, log_file=str(updater_info.server_log)) as server:
		base_url = f"http://localhost:{server.port}"
		write_repo_conf(updater_info.test_repo_conf, base_url, excludes=excludes)
		package_updater = package_updater_class(updater_info.config)

		available_packages = package_updater.getDownloadablePackages()
		package = None
		for available_package in available_packages:
			if available_package.product_id == "hwaudit":
				package = available_package
				break

		assert package is not None

		assert package.version == "4.2.0.0-1"
		assert package.package_file == f"{base_url}/hwaudit_4.2.0.0-1.opsi"
		assert package.filename == server_package_file.name
		assert package.zsync_file == f"{base_url}/{zsync_file.name}"

		new_packages = package_updater.get_packages(DummyNotifier())
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
	updater_info = prepare_updater(tmp_path, create_packages=False)

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
	server_package_md5sum = hash_file(server_package_file, FileHashAlgorithm.MD5)
	md5sum_file.write_text(server_package_md5sum, encoding="ascii")

	with http_test_server(
		serve_directory=updater_info.server_dir,
		response_headers={"accept-ranges": "bytes"} if server_accept_ranges else None,
		log_file=str(updater_info.server_log),
	) as server:
		base_url = f"http://localhost:{server.port}"
		proxy = ""

		write_repo_conf(updater_info.test_repo_conf, base_url, proxy)

		package_updater = package_updater_class(updater_info.config)

		available_packages = package_updater.getDownloadablePackages()
		package = None
		for available_package in available_packages:
			if available_package.product_id == "hwaudit":
				package = available_package
				break
		assert package is not None

		assert package.version == "4.2.0.0-1"
		assert package.package_file == f"{base_url}/hwaudit_4.2.0.0-1.opsi"
		assert package.filename == server_package_file.name
		assert package.zsync_file == f"{base_url}/{zsync_file.name}"
		with package_updater.makeSession(package.repository) as session:
			assert (
				# pylint: disable=protected-access
				package_updater._useZsync(
					session,
					package,
					local_package=get_local_package_info(product_id="hwaudit", package_directory=updater_info.local_dir),
				)
				== server_accept_ranges
			)

		if "localhost" in base_url:
			updater_info.server_log.unlink()
		new_packages = package_updater.get_packages(DummyNotifier())
		assert len(new_packages) == 1

		# for line in server_log.read_text(encoding="utf-8").rstrip().split("\n"):
		# 	request = json.loads(line)
		# 	print(request)
		request = json.loads(updater_info.server_log.read_text(encoding="utf-8").rstrip().split("\n")[-1])
		updater_info.server_log.unlink()
		# print(request)

		assert hash_file(updater_info.local_dir / server_package_file.name, FileHashAlgorithm.MD5) == server_package_md5sum
		assert request["headers"].get("Authorization") == "Basic dXNlcjpwYXNz"
		assert request["headers"]["Accept-Encoding"] == "identity"
		if server_accept_ranges:
			assert request["headers"]["Range"] == "bytes=20480-40959,59392-81919,100352-102399"
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

		package_updater = package_updater_class(updater_info.config)
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
		package_updater = package_updater_class(updater_info.config)
		available_packages = package_updater.getDownloadablePackages()
		assert len(available_packages) == 6
		for package in available_packages:
			if package.version != "1.0-1":
				continue
			assert package.package_file == f"{base_url}/localboot_new_1.0-1.opsi"
			assert package.zsync_file == f"{base_url}/localboot_new_1.0-1.opsi.zsync"

		write_repo_conf(updater_info.test_repo_conf, base_url, dirs="otherdir/")
		package_updater = package_updater_class(updater_info.config)
		available_packages = package_updater.getDownloadablePackages()
		assert len(available_packages) == 1
		assert available_packages[0].package_file == f"{base_url}/otherdir/localboot_new_1.0-1.opsi"
		assert available_packages[0].zsync_file is None


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

		package_updater = package_updater_class(updater_info.config)
		install_log.installed = []
		package_updater.processUpdates()
		assert len(install_log.installed) == 2
		for entry in install_log.installed:
			assert entry.endswith(("localboot_new_42.0-1337.opsi", "test-netboot_1.0-2.opsi"))


@pytest.mark.parametrize("ignore_errors", (True, False))
def test_process_updates_unreachable_repository(
	tmp_path: Path, package_updater_class: type[OpsiPackageUpdater], ignore_errors: bool
) -> None:
	updater_info = prepare_updater(tmp_path, ignore_errors=ignore_errors)

	rmpc = RepoMetaPackageCollection()
	rmpc.scan_packages(updater_info.server_dir)
	rmpc.write_metafile(updater_info.server_dir / "packages.json")

	with http_test_server(serve_directory=updater_info.server_dir, log_file=str(updater_info.server_log)) as server:
		write_repo_conf(updater_info.test_repo_conf, f"http://localhost:{server.port}")
		updater_info.test_repo_conf.with_name("unreachable.repo").write_text(
			"[repository_unreachable]\nactive = true\nbaseUrl = http://localhost:1\ndirs = /\nproxy =\nautoInstall = true\n",
			encoding="utf-8",
		)

		package_updater = package_updater_class(updater_info.config)
		install_log.installed = []
		if not ignore_errors:
			with pytest.raises(requests.exceptions.ConnectionError):
				package_updater.processUpdates()
			return

		package_updater.processUpdates()
		assert package_updater.errors
		assert len(install_log.installed) == 2


@pytest.mark.parametrize(
	"available_versions, custom_versions, expected_version",
	(
		(["2.0-1", "42.0-1337", "42.0-1337~en", "42.0-1337~ita"], None, "42.0-1337"),
		(["2.0-1", "42.0-1337", "42.0-1337~en", "42.0-1337~ita"], [".*~en"], "42.0-1337~en"),
		(["2.0-1", "42.0-1337", "42.0-1337~en", "42.0-1337~ita"], [".*~en", "localboot_new~ita"], "42.0-1337~ita"),
		(["2.0-1", "42.0-1337", "42.0-1337~en", "42.0-1337~ita"], [".*~other", "localboot_new~foo"], "42.0-1337"),
		(["2.0-1", "42.0-1337~ita"], None, "2.0-1"),
		(["42.0-1337~ita"], None, "42.0-1337~ita"),
		(["153.5.1esror158.0.1-1", "153.5.0esror158.0-1", "153.5.1esror158.0.1-1~fra3264"], None, "153.5.1esror158.0.1-1"),
		(["153.5.1esror158.0.1-1", "153.5.0esror158.0-1", "153.5.1esror158.0.1-1~fra3264"], [""], "153.5.1esror158.0.1-1"),
	),
)
def test_prefer_custom_versions(
	tmp_path: Path,
	package_updater_class: type[OpsiPackageUpdater],
	available_versions: list[str],
	custom_versions: list[str] | None,
	expected_version: str,
) -> None:
	updater_info = prepare_updater(tmp_path, create_packages=False, ignore_errors=True)
	with http_test_server(serve_directory=updater_info.server_dir, log_file=str(updater_info.server_log)) as server:
		for sub_dir in (None, "localboot_new", "other"):
			for version in random.sample(available_versions, len(available_versions)):
				# Build each package only once and copy it, as packages with identical versions must have identical hashes
				package_file = updater_info.server_dir / f"localboot_new_{version}.opsi"
				if not package_file.exists():
					create_package(package_file)
				if sub_dir:
					(updater_info.server_dir / sub_dir).mkdir(exist_ok=True)
					shutil.copy2(package_file, updater_info.server_dir / sub_dir / package_file.name)

			meta_file = updater_info.server_dir / "packages.json"
			meta_file.unlink(missing_ok=True)
			rmpc = RepoMetaPackageCollection()
			rmpc.scan_packages(updater_info.server_dir)
			rmpc.write_metafile(meta_file)

			base_url = f"http://localhost:{server.port}"
			write_repo_conf(
				updater_info.test_repo_conf,
				base_url,
				excludes=[],
				customVersions=custom_versions,
			)

			package_updater = package_updater_class(updater_info.config)
			available_packages = [p for p in package_updater.getDownloadablePackages() if p.product_id == "localboot_new"]
			for reverse_sort in (False, True):
				available_packages.sort(key=lambda pkg: str(pkg.version), reverse=reverse_sort)
				localboot_new_versions = sorted(str(pkg.version) for pkg in available_packages if pkg.product_id == "localboot_new")
				print("localboot_new_versions:", localboot_new_versions)
				assert localboot_new_versions == sorted(available_versions)
				newest_packages = package_updater.onlyNewestPackages(available_packages)
				latest_localboot_new_versions = sorted(str(pkg.version) for pkg in newest_packages if pkg.product_id == "localboot_new")
				print("latest_localboot_new_versions:", latest_localboot_new_versions)
				print("expected_version:", expected_version)
				assert latest_localboot_new_versions == [expected_version]
