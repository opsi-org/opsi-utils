# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-python interpreter for custom opsi python scripts
"""

import os
import sys
import codecs
import traceback
# paramiko is needed for opsi-deploy-client-agent
import paramiko  # pylint: disable=unused-import

def add_systempackages_to_path():
	for path in ("/usr/lib/python3/dist-packages",):
		if os.path.exists(path):
			sys.path.append(path)

def run_script():
	script = sys.argv[1]
	sys.argv.pop(0)

	imp_new_module = type(sys)
	new_module = imp_new_module(script)
	new_module.__dict__['__name__'] = '__main__'
	new_module.__dict__['__file__'] = script

	with codecs.open(script, "r", "utf-8") as file:
		code = file.read()

	add_systempackages_to_path()
	exec(code, new_module.__dict__)  # pylint: disable=exec-used

def run_interactive():
	import code  # pylint: disable=import-outside-toplevel
	add_systempackages_to_path()
	code.interact(local=locals())

def main():
	try:
		if len(sys.argv) > 1:
			run_script()
		else:
			run_interactive()
	except Exception:  # pylint: disable=broad-except
		traceback.print_exc()
		sys.exit(1)
