"""
opsi-utils

tests for opsi-makepackage
"""

from opsiutils.opsimakepackage import makepackage_main
from pathlib import Path
import shutil
import pytest

from .utils import temp_context

@pytest.mark.parametrize(
	"args",
	(("--no-set-rights", "--no-md5", "--no-zsync"), ("--no-set-rights", "--no-md5", "--no-zsync", "--control-to-toml")),
)
def test_makepackage_new(args):
	args = ["--no-set-rights", "--no-md5", "--no-zsync"]
	with temp_context() as orig_path:
		opsi_dir = Path() / "OPSI"
		opsi_dir.mkdir()
		shutil.copy(orig_path / "tests" / "data" / "control.toml", opsi_dir / "control.toml")
		makepackage_main(args)
		assert (Path() / "prod-1750_1.0-1.opsi").exists()
		assert (opsi_dir / "control").exists()  # control should be generated for compatibility


@pytest.mark.parametrize(
	"args",
	(("--no-set-rights", "--no-md5", "--no-zsync"), ("--no-set-rights", "--no-md5", "--no-zsync", "--control-to-toml")),
)
def test_makepackage_old(args):
	with temp_context() as orig_path:
		opsi_dir = Path() / "OPSI"
		opsi_dir.mkdir()
		shutil.copy(orig_path / "tests" / "data" / "control", opsi_dir / "control")
		makepackage_main(args)
		assert (Path() / "prod-1750_1.0-1.opsi").exists()
		# control.toml should be generated iff --control-to-toml is used
		if "--control-to-toml" in args:
			assert (opsi_dir / "control.toml").exists()
		else:
			assert not (opsi_dir / "control.toml").exists()


def test_error_on_control_to_toml_present():
	with temp_context() as orig_path:
		opsi_dir = Path() / "OPSI"
		opsi_dir.mkdir()
		shutil.copy(orig_path / "tests" / "data" / "control", opsi_dir / "control")
		shutil.copy(orig_path / "tests" / "data" / "control.toml", opsi_dir / "control.toml")
		with pytest.raises(ValueError):
			makepackage_main(("--no-set-rights", "--no-md5", "--no-zsync", "--control-to-toml"))
		assert not (Path() / "prod-1750_1.0-1.opsi").exists()


def test_error_on_control_newer_than_toml():
	with temp_context() as orig_path:
		opsi_dir = Path() / "OPSI"
		opsi_dir.mkdir()
		with open(orig_path / "tests" / "data" / "control", "r", encoding="utf-8") as infile:
			with open(opsi_dir / "control", "w", encoding="utf-8") as outfile:
				for line in infile.readlines():
					outfile.write(line if not line.startswith("version") else "version: 2\n")
		shutil.copy(orig_path / "tests" / "data" / "control.toml", opsi_dir / "control.toml")
		with pytest.raises(ValueError):
			makepackage_main(("--no-set-rights", "--no-md5", "--no-zsync"))
		assert not (Path() / "prod-1750_1.0-1.opsi").exists()
		assert not (Path() / "prod-1750_2-2.opsi").exists()
