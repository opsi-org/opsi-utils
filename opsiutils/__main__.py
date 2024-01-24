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
		# Disable resource and deprecation warnings if frozen
		warnings.simplefilter("ignore", ResourceWarning)
		warnings.simplefilter("ignore", DeprecationWarning)


def main() -> None:  # pylint: disable=too-many-return-statements
	configure_warnings()
	name = os.path.splitext(os.path.basename(sys.argv[0]))[0].lower().replace("-", "")
	if name == "opsiadmin":
		# pylint: disable=import-outside-toplevel
		from opsiutils.opsiadmin import main as _main

		return _main()
	if name == "opsibackup":
		# pylint: disable=import-outside-toplevel
		from opsiutils.opsibackup import main as _main

		return _main()
	if name == "opsiconvert":
		raise RuntimeError(
			"opsiconvert not available with opsi 4.3. You can use `opsi-setup --file-to-mysql` to convert from FILE to MySQL backend."
		)
	if name == "opsimakepackage":
		# pylint: disable=import-outside-toplevel
		from opsiutils.opsimakepackage import main as _main

		return _main()
	if name == "opsinewprod":
		# pylint: disable=import-outside-toplevel
		from opsiutils.opsinewprod import main as _main

		return _main()
	if name == "opsipackagemanager":
		# pylint: disable=import-outside-toplevel
		from opsiutils.opsipackagemanager import main as _main

		return _main()
	if name == "opsipackageupdater":
		# pylint: disable=import-outside-toplevel
		from opsiutils.opsipackageupdater import main as _main

		return _main()
	if name == "opsisetup":
		# pylint: disable=import-outside-toplevel
		from opsiutils.opsisetup import main as _main

		return _main()
	if name == "opsipython":
		# pylint: disable=import-outside-toplevel
		from opsiutils.opsipython import main as _main

		return _main()
	if name == "opsiwakeupclients":
		# pylint: disable=import-outside-toplevel
		from opsiutils.opsiwakeupclients import main as _main

		return _main()
	if name == "opsioutdatedtosetup":
		# pylint: disable=import-outside-toplevel
		from opsiutils.opsioutdatedtosetup import main as _main

		return _main()
	return None
