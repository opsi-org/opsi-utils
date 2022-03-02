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
import argparse

# import modules to trigger pyinstaller import hook -> available (non-pruned) in opsi-python
import pyasn1_modules  # pylint: disable=unused-import


def add_systempackages_to_path():
	ver = sys.version_info
	for path in (
		f"/usr/lib/python{ver.major}.{ver.minor}",
		f"/usr/lib/python{ver.major}.{ver.minor}/lib-dynload"
		f"/usr/local/lib/python{ver.major}.{ver.minor}/dist-packages",
		f"/usr/lib/python{ver.major}/dist-packages",
	):
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


def main():  # pylint: disable=inconsistent-return-statements
	try:
		parser = argparse.ArgumentParser(add_help=False)
		parser.add_argument("-V", "--version", action="store_true", help="print the Python version number and exit")
		parser.add_argument("-h", "--help", action="store_true", help="print this help message and exit")
		group = parser.add_mutually_exclusive_group()
		group.add_argument("-c", metavar="cmd", required=False, help="program passed in as string")
		group.add_argument("file", nargs="?", help="program read from script file")
		parser.add_argument("arg", nargs="*", help="arguments passed to program in sys.argv[1:]")
		args, _ = parser.parse_known_args()

		if args.file:
			return run_script()

		if args.help:
			return parser.print_help()

		if args.version:
			print(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
			return

		if args.c:
			add_systempackages_to_path()
			return exec(args.c)  # pylint: disable=exec-used

		return run_interactive()
	except Exception:  # pylint: disable=broad-except
		traceback.print_exc()
		sys.exit(1)
