"""
opsi-utils

Test utilities
"""

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator


@contextmanager
def temp_context() -> Generator[Path, None, None]:
	origin = Path().absolute()
	try:
		with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tempdir:
			os.chdir(tempdir)
			yield origin  # return original path
	finally:
		os.chdir(origin)
