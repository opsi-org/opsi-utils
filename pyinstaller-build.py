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
	shutil.copy(res[0], ".venv/lib/python%s/site-packages/_snack.cpython-%sm-x86_64-linux-gnu.so" % (ver, ver.replace('.','')))
	shutil.copy("/usr/lib/python3/dist-packages/snack.py", ".venv/lib/python%s/site-packages/snack.py" % ver)

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
	with codecs.open("%s.spec" % script, "r", "utf-8") as f:
		varname = script.replace("-", "_")
		data = f.read()
		data = re.sub(r"([\s\(])a\.", r"\g<1>" + varname + "_a.", data)
		#print(data)
		match = re.search(r"(.*)(a\s*=\s*)(Analysis[^\)]+\))(.*)", data, re.MULTILINE|re.DOTALL)
		if not spec_a:
			spec_a += match.group(1)
		spec_a += "%s_a = {match.group(3)}\n" % varname
		spec_o += match.group(4)
		spec_m.append("(%s_a, '%s', '%s')" % (varname, script, script))

with codecs.open("opsi-utils.spec", "w", "utf-8") as f:
	f.write(spec_a)
	f.write("\nMERGE( %s )\n" % ', '.join(spec_m))
	f.write(spec_o)

subprocess.check_call(["poetry", "run", "pyinstaller", "--log-level", "INFO", "opsi-utils.spec"])


shutil.move("dist/%s" % SCRIPTS[0], "dist/opsi-utils")
for script in SCRIPTS[1:]:
	shutil.move("dist/%s/%s" % (script, script), "dist/opsi-utils/%s" % script)
	shutil.rmtree("dist/%s" % script)
