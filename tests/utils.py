"""
opsi-utils

Test utilities
"""

import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def temp_context() -> Generator[Path, None, None]:
	origin = Path().absolute()
	try:
		with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tempdir:
			os.chdir(tempdir)
			yield origin  # return original path
	finally:
		os.chdir(origin)
