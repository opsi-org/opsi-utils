"""
opsi-utils

tests for opsi-makepackage
"""

import shutil
from pathlib import Path

import pytest
from opsicommon.package import OpsiPackage

from opsiutils.opsimakepackage import makepackage_main

from .utils import temp_context


@pytest.mark.parametrize(
	"args",
	(["--no-set-rights", "--no-md5", "--no-zsync"],),
)
def test_makepackage_new(args: list[str]) -> None:
	with temp_context() as orig_path:
		opsi_dir = Path("OPSI")
		opsi_dir.mkdir()
		shutil.copy(orig_path / "tests" / "data" / "control.toml", opsi_dir / "control.toml")
		makepackage_main(args)
		assert Path("prod-1750_1.0-1.opsi").exists()
		assert (opsi_dir / "control").exists()  # control should be generated for compatibility


@pytest.mark.parametrize(
	"args",
	(["--no-set-rights", "--no-md5", "--no-zsync"], ["--no-set-rights", "--no-md5", "--no-zsync", "--control-to-toml"]),
)
def test_makepackage_old(args: list[str]) -> None:
	with temp_context() as orig_path:
		opsi_dir = Path("OPSI")
		opsi_dir.mkdir()
		shutil.copy(orig_path / "tests" / "data" / "control", opsi_dir / "control")
		makepackage_main(args)
		assert Path("prod-1750_1.0-1.opsi").exists()
		# control.toml should be generated iff --control-to-toml is used
		if "--control-to-toml" in args:
			assert (opsi_dir / "control.toml").exists()
		else:
			assert not (opsi_dir / "control.toml").exists()


@pytest.mark.parametrize(("prod_ver", "pack_ver"), (("1.0", "1"), ("2.0", "1"), ("1.0", "2"), ("2.0", "2")))
def test_makepackage_explicite_version(prod_ver: str, pack_ver: str) -> None:
	args = ["--no-set-rights", "--no-md5", "--no-zsync", "--product-version", prod_ver, "--package-version", pack_ver]
	with temp_context() as orig_path:
		opsi_dir = Path("OPSI")
		opsi_dir.mkdir()
		shutil.copy(orig_path / "tests" / "data" / "control.toml", opsi_dir / "control.toml")
		makepackage_main(args)
		assert Path(f"prod-1750_{prod_ver}-{pack_ver}.opsi").exists()


def test_error_on_control_to_toml_present() -> None:
	with temp_context() as orig_path:
		opsi_dir = Path("OPSI")
		opsi_dir.mkdir()
		shutil.copy(orig_path / "tests" / "data" / "control", opsi_dir / "control")
		shutil.copy(orig_path / "tests" / "data" / "control.toml", opsi_dir / "control.toml")
		with pytest.raises(ValueError):
			makepackage_main(["--no-set-rights", "--no-md5", "--no-zsync", "--control-to-toml"])
		assert not Path("prod-1750_1.0-1.opsi").exists()


def test_error_on_control_newer_than_toml() -> None:
	with temp_context() as orig_path:
		opsi_dir = Path("OPSI")
		opsi_dir.mkdir()
		with open(orig_path / "tests" / "data" / "control", "r", encoding="utf-8") as infile:
			with open(opsi_dir / "control", "w", encoding="utf-8") as outfile:
				for line in infile.readlines():
					outfile.write(line if not line.startswith("version") else "version: 2\n")
		shutil.copy(orig_path / "tests" / "data" / "control.toml", opsi_dir / "control.toml")
		with pytest.raises(ValueError):
			makepackage_main(["--no-set-rights", "--no-md5", "--no-zsync"])
		assert not Path("prod-1750_1.0-1.opsi").exists()
		assert not Path("prod-1750_2-2.opsi").exists()


def test_makepackage_None_entries() -> None:
	with temp_context() as orig_path:
		opsi_dir = Path("OPSI")
		opsi_dir.mkdir()
		shutil.copy(orig_path / "tests" / "data" / "control.toml", opsi_dir / "control.toml")

		package = OpsiPackage()
		package.find_and_parse_control_file(opsi_dir)
		assert package.product.onceScript is None
		assert package.product.productClassIds == []

		makepackage_main(["--no-set-rights", "--no-md5", "--no-zsync"])
		old_control = (opsi_dir / "control").read_text(encoding="utf-8")
		assert "onceScript: \n" in old_control
		assert "productClasses: \n" in old_control
