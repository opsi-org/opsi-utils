# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0

import os
import sys
import codecs
import traceback
# paramiko is needed for opsi-deploy-client-agent
import paramiko

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
