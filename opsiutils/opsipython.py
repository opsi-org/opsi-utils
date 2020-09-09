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
"""
:copyright: uib GmbH <info@uib.de>
:license: GNU Affero General Public License version 3
"""

import os
import sys
import codecs
import traceback

def main():
	if len(sys.argv) > 1:
		script = sys.argv[1]
		sys.argv.pop(0)
		
		imp_new_module = type(sys)
		new_module = imp_new_module(script)
		new_module.__dict__['__name__'] = '__main__'
		new_module.__dict__['__file__'] = script
		
		with codecs.open(script, "r", "utf-8") as f:
			code = f.read()
		try:
			exec(code, new_module.__dict__)
		except SystemExit:
			raise
		except Exception as e:
			traceback.print_exc()
			sys.exit(1)
