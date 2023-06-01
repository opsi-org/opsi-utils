"""
opsi-utils

tests for opsi-makepackage
"""

from pathlib import Path

from opsiutils.opsipackageupdater import patch_repo_files
from opsiutils import __version__



ORIGINAL_REPO = """[repository_uib_linux_experimental]
description = opsi Linux Support (experimental packages)
active = false
baseUrl = http://download.uib.de
dirs = opsi4.2/experimental/packages/linux/localboot/, opsi4.2/experimental/packages/linux/netboot/
autoInstall = false
autoUpdate = true
autoSetup = false
proxy =

[repository_uib_windows_experimental]
description = opsi Windows Support (experimental packages)
active = false
baseUrl = http://download.uib.de
dirs = opsi4.2/experimental/packages/windows/localboot/, opsi4.2/experimental/packages/windows/netboot/
autoInstall = false
autoUpdate = true
autoSetup = false
proxy =
"""

PATCHED_REPO = f"""; This file has been patched by opsi-package-updater {__version__}
[repository_uib_linux_experimental]
description = opsi Linux Support (experimental packages)
active = false
baseUrl = https://opsipackages.43.opsi.org
dirs = experimental/linux/localboot/, experimental/linux/netboot/
autoInstall = false
autoUpdate = true
autoSetup = false
proxy =

[repository_uib_windows_experimental]
description = opsi Windows Support (experimental packages)
active = false
baseUrl = https://opsipackages.43.opsi.org
dirs = experimental/windows/localboot/, experimental/windows/netboot/
autoInstall = false
autoUpdate = true
autoSetup = false
proxy =
"""

def test_patch_repo_files(tmp_path: Path):
	"""
	Test patch_repo_files
	"""
	# Create a test repo
	test_repo = tmp_path
	(test_repo / "experimental.repo").write_text(ORIGINAL_REPO, encoding="utf-8")
	(test_repo / "custom.repo").write_text(ORIGINAL_REPO, encoding="utf-8")

	# Patch the repo files
	patch_repo_files(test_repo)
	print((test_repo / "experimental.repo").read_text(encoding="utf-8"))

	# Check that the uib repo file has been patched
	assert (test_repo / "experimental.repo").read_text(encoding="utf-8") == PATCHED_REPO
	# Check that the custom repo file has been left untouched
	assert (test_repo / "custom.repo").read_text(encoding="utf-8") == ORIGINAL_REPO
