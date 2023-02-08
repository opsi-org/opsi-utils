# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsiutils.__main__
"""

import os
import sys
import warnings


def configure_warnings() -> None:
	if getattr(sys, "frozen", False):
		# Disable resource warnings if frozen
		warnings.simplefilter("ignore", ResourceWarning)


def main():  # pylint: disable=too-many-return-statements
	configure_warnings()
	name = os.path.splitext(os.path.basename(sys.argv[0]))[0].lower().replace("-", "")
	if name == "opsiadmin":
		from opsiutils.opsiadmin import (
			main as _main,  # pylint: disable=import-outside-toplevel
		)

		return _main()
	if name == "opsibackup":
		from opsiutils.opsibackup import (
			main as _main,  # pylint: disable=import-outside-toplevel
		)

		return _main()
	if name == "opsiconvert":
		raise RuntimeError(
			"opsiconvert not available with opsi 4.3. You can use `opsi-setup --file-to-mysql` to convert from FILE to MySQL backend."
		)
	if name == "opsimakepackage":
		from opsiutils.opsimakepackage import (
			main as _main,  # pylint: disable=import-outside-toplevel
		)

		return _main()
	if name == "opsinewprod":
		from opsiutils.opsinewprod import (
			main as _main,  # pylint: disable=import-outside-toplevel
		)

		return _main()
	if name == "opsipackagemanager":
		from opsiutils.opsipackagemanager import (
			main as _main,  # pylint: disable=import-outside-toplevel
		)

		return _main()
	if name == "opsipackageupdater":
		from opsiutils.opsipackageupdater import (
			main as _main,  # pylint: disable=import-outside-toplevel
		)

		return _main()
	if name == "opsisetup":
		from opsiutils.opsisetup import (
			main as _main,  # pylint: disable=import-outside-toplevel
		)

		return _main()
	if name == "opsipython":
		from opsiutils.opsipython import (
			main as _main,  # pylint: disable=import-outside-toplevel
		)

		return _main()
	if name == "opsiwakeupclients":
		from opsiutils.opsiwakeupclients import (
			main as _main,  # pylint: disable=import-outside-toplevel
		)

		return _main()
	if name == "opsioutdatedtosetup":
		from opsiutils.opsioutdatedtosetup import (
			main as _main,  # pylint: disable=import-outside-toplevel
		)

		return _main()
	return None
