#!/usr/bin/python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import glob
import codecs
import shutil
import platform
import subprocess

SCRIPTS = [
	"opsi-admin",
	"opsi-backup",
	"opsi-convert",
	"opsi-makepackage",
	"opsi-newprod",
	"opsi-package-manager",
	"opsi-package-updater"
]
HIDDEN_IMPORTS = [
	"OPSI.Backend.MySQL",
	"snack"
]
os.chdir(os.path.dirname(os.path.abspath(__file__)))

def add_snack():
	try:
		subprocess.check_output(["dpkg", "-l", "python3-newt"])
	except:
		subprocess.check_call(["apt", "install", "python3-newt"])
	res = glob.glob("/usr/lib/python3/dist-packages/_snack.*")
	if not res:
		raise Exception("Failed to locate snack module (python3-newt)")
	ver = os.path.basename(glob.glob(".venv/lib/python3.*")[0]).replace("python", "")
	print(f"Python version: {ver}")
	shutil.copy(res[0], f".venv/lib/python{ver}/site-packages/_snack.cpython-{ver.replace('.','')}m-x86_64-linux-gnu.so")
	shutil.copy("/usr/lib/python3/dist-packages/snack.py", f".venv/lib/python{ver}/site-packages/snack.py")

subprocess.check_call(["poetry", "install"])
add_snack()

for d in ("dist", "build"):
	if os.path.isdir(d):
		shutil.rmtree(d)

spec_a = ""
spec_m = []
spec_o = ""
for script in SCRIPTS:
	cmd = ["poetry", "run", "pyi-makespec"]
	for hi in HIDDEN_IMPORTS:
		cmd.extend(["--hidden-import", hi])
	cmd.append(script)
	subprocess.check_call(cmd)
	with codecs.open(f"{script}.spec", "r", "utf-8") as f:
		varname = script.replace("-", "_")
		data = f.read()
		data = re.sub(r"([\s\(])a\.", r"\g<1>" + varname + "_a.", data)
		#print(data)
		match = re.search(r"(.*)(a\s*=\s*)(Analysis[^\)]+\))(.*)", data, re.MULTILINE|re.DOTALL)
		if not spec_a:
			spec_a += match.group(1)
		spec_a += f"{varname}_a = {match.group(3)}\n"
		spec_o += match.group(4)
		spec_m.append(f"({varname}_a, '{script}', '{script}')")

with codecs.open(f"opsi-utils.spec", "w", "utf-8") as f:
	f.write(spec_a)
	f.write(f"\nMERGE( {', '.join(spec_m)} )\n")
	f.write(spec_o)

subprocess.check_call(["poetry", "run", "pyinstaller", "--log-level", "INFO", "opsi-utils.spec"])


shutil.move(f"dist/{SCRIPTS[0]}", "dist/opsi-utils")
for script in SCRIPTS[1:]:
	shutil.move(f"dist/{script}/{script}", f"dist/opsi-utils/{script}")
	shutil.rmtree(f"dist/{script}")
