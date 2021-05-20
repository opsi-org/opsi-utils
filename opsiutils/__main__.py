# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0

import os
import sys

def main():
	name = os.path.splitext(os.path.basename(sys.argv[0]))[0].lower().replace("-", "")
	if name == "opsiadmin":
		from opsiutils.opsiadmin import main
		return main()
	if name == "opsibackup":
		from opsiutils.opsibackup import main
		return main()
	if name == "opsiconvert":
		from opsiutils.opsiconvert import main
		return main()
	if name == "opsimakepackage":
		from opsiutils.opsimakepackage import main
		return main()
	if name == "opsinewprod":
		from opsiutils.opsinewprod import main
		return main()
	if name == "opsipackagemanager":
		from opsiutils.opsipackagemanager import main
		return main()
	if name == "opsipackageupdater":
		from opsiutils.opsipackageupdater import main
		return main()
	if name == "opsisetup":
		from opsiutils.opsisetup import main
		return main()
	if name == "opsipython":
		from opsiutils.opsipython import main
		return main()
	if name == "opsiwakeupclients":
		from opsiutils.opsiwakeupclients import main
		return main()
