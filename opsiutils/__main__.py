# -*- coding: utf-8 -*-

# opsi-admin is part of the desktop management solution opsi
# (open pc server integration) http://www.opsi.org
# Copyright (C) 2010-2019 uib GmbH <info@uib.de>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License version 
# 3 as published by the Free Software Foundation 

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

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
