# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-admin - a commandline tool for accessing opsi.
"""

# pylint: disable=too-many-lines
from __future__ import annotations

import argparse
import curses
import fcntl
import getpass
import gettext
import json
import locale
import os
import os.path
import pwd
import select
import stat
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from types import FrameType
from typing import Any

from OPSI import __version__ as python_opsi_version  # type: ignore
from OPSI.System import (  # type: ignore[import]
	CommandNotFoundException,  # type: ignore[import]
	which,  # type: ignore[import]
)
from OPSI.System import execute as sys_execute  # type: ignore[import]
from OPSI.Util import (  # type: ignore[import]
	blowfishDecrypt,
	deserialize,
	fromJson,
	objectToBash,
	objectToBeautifiedText,
	serialize,
	toJson,
)
from OPSI.Util.File.Opsi.Opsirc import getOpsircPath, readOpsirc  # type: ignore[import]
from opsicommon.config import OpsiConfig
from opsicommon.exceptions import OpsiRpcError
from opsicommon.logging import (
	DEFAULT_COLORED_FORMAT,
	LOG_DEBUG,
	LOG_ERROR,
	LOG_NONE,
	LOG_WARNING,
	get_logger,
	logging_config,
	secret_filter,
)
from opsicommon.types import forceBool, forceFilename, forceUnicode, forceUnicodeLower

from opsiutils import __version__, get_service_client

COLOR_NORMAL = "\033[0;0;0m"
COLOR_BLACK = "\033[0;30;40m"
COLOR_RED = "\033[0;31;40m"
COLOR_GREEN = "\033[0;32;40m"
COLOR_YELLOW = "\033[0;33;40m"
COLOR_BLUE = "\033[0;34;40m"
COLOR_MAGENTA = "\033[0;35;40m"
COLOR_CYAN = "\033[0;36;40m"
COLOR_WHITE = "\033[0;37;40m"
COLOR_LIGHT_BLACK = "\033[1;30;40m"
COLOR_LIGHT_RED = "\033[1;31;40m"
COLOR_LIGHT_GREEN = "\033[1;32;40m"
COLOR_LIGHT_YELLOW = "\033[1;33;40m"
COLOR_LIGHT_BLUE = "\033[1;34;40m"
COLOR_LIGHT_MAGENTA = "\033[1;35;40m"
COLOR_LIGHT_CYAN = "\033[1;36;40m"
COLOR_LIGHT_WHITE = "\033[1;37;40m"
COLORS_AVAILABLE = [
	COLOR_NORMAL,
	COLOR_BLACK,
	COLOR_RED,
	COLOR_GREEN,
	COLOR_YELLOW,
	COLOR_BLUE,
	COLOR_MAGENTA,
	COLOR_CYAN,
	COLOR_WHITE,
	COLOR_LIGHT_BLACK,
	COLOR_LIGHT_RED,
	COLOR_LIGHT_GREEN,
	COLOR_LIGHT_YELLOW,
	COLOR_LIGHT_BLUE,
	COLOR_LIGHT_MAGENTA,
	COLOR_LIGHT_CYAN,
	COLOR_LIGHT_WHITE,
]

logger = get_logger()
service_client = None
exitZero = False
global_shell = None
logFile = None
interactive = False

outEncoding = sys.stdout.encoding
inEncoding = sys.stdin.encoding
if not outEncoding or outEncoding == "ascii":
	outEncoding = locale.getpreferredencoding()
if not outEncoding or outEncoding == "ascii":
	outEncoding = "utf-8"

if not inEncoding or (inEncoding == "ascii"):
	inEncoding = outEncoding

UNCOLORED_LOGO = f"""\
                                   .:.:::::.
                                   ;      ::
                                   ;      ;.
                                   -:....::
                                     . --
                                  ;:........
                                     -----  -
                                      ..
                                     .||.
                                  .._|||=_..
                               _=||++~~-~++||=,
                            _=|>~-           ~+|;.
                          .=|+-  _; ____=___.   +|;
                         .||-. .=i`++++++++||=   -|=.
           . ....        ||`. ..|>         =|+    -|=        . ....
          = -----:.     =|; ...:|= . .   . ||;     =|;      ; -- --::
         .:      ;.   ._||`.. . || . . .  .|+`     .||_,    =      :.
          ;.    ::  -<||+|.. ...:|;__...._=|=  . . .||+|+-  ;.    .;
          --::::-      -+|;.. . .-+||||||||+ .  .  :|;-      --:;::
        ..              -|+ ... ...  --- .  . .. ..||     .. .
         -:::::;:: .     =|=.._=;___:...:.:.____. =|`      --:;:;;::..
                          ~||,-~+||||||||||||>~ _||`
                           -=|=_...---~~-~--  _=i:
                             -~||=__:.-..:__|||~ .
                                -~+++||||++~--
          opsi-admin {__version__}
""".split("\n")

LOGO = [{"color": COLOR_CYAN, "text": line} for line in UNCOLORED_LOGO]

try:
	sp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	if os.path.exists(os.path.join(sp, "site-packages")):
		sp = os.path.join(sp, "site-packages")
	sp = os.path.join(sp, "opsi-utils_data", "locale")
	translation = gettext.translation("opsi-utils", sp)
	_ = translation.gettext
except Exception as loc_err:
	logger.debug("Failed to load locale from %s: %s", sp, loc_err)

	def _(message: str) -> str:
		"""Fallback function"""
		return message


class ErrorInResultException(Exception):
	"Indicates that there is an error in the result."


def signalHandler(signo: int, stackFrame: FrameType | None) -> None:
	from signal import SIGINT, SIGQUIT

	logger.info("Received signal %s", signo)
	if signo == SIGINT:
		if global_shell:
			global_shell.sigint()
		else:
			sys.exit(0)
	elif signo == SIGQUIT:
		sys.exit(0)


def shell_main() -> None:
	os.umask(0o077)
	global interactive
	global exitZero
	global logFile

	parser = argparse.ArgumentParser()
	parser.add_argument(
		"--version", "-V", action="version", version=f"{__version__} [python-opsi={python_opsi_version}]", help=_("Show version and exit")
	)
	parser.add_argument(
		"--log-level",
		"-l",
		dest="logLevel",
		default=LOG_WARNING,
		type=int,
		choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
		help=_("Set log level (default: 3)"),
	)
	parser.add_argument(
		"--log-file",
		metavar="FILE",
		dest="logFile",
		help=_("Path to log file"),
	)
	parser.add_argument(
		"--address",
		"-a",
		default="https://localhost:4447/rpc",
		help=_("URL of opsiconfd (default: https://localhost:4447/rpc)"),
	)
	parser.add_argument(
		"--no-check-certificate",
		action="store_true",
		default=False,
		help=_("Ignore certificate checks when connecting to server."),
	)
	parser.add_argument("--username", "-u", help=_("Username (default: host_id or current user)"))
	parser.add_argument("--password", "-p", help=_("Password (default: host_key or prompt for password)"))
	parser.add_argument(
		"--opsirc",
		default=getOpsircPath(),
		help=(
			_("Path to the opsirc file to use (default: ~/.opsi.org/opsirc)")
			+ _("An opsirc file contains login credentials to the web API.")
		),
	)
	parser.add_argument("--direct", "-d", action="store_true", help=_("Do not use opsiconfd - DEPRECATED will be ignored"))
	parser.add_argument(
		"--no-depot",
		dest="depot",
		action="store_false",
		default=True,
		help=_("Do not use depotserver backend - DEPRECATED will be ignored"),
	)
	parser.add_argument("--interactive", "-i", action="store_true", help=_("Start in interactive mode"))
	parser.add_argument("--exit-zero", dest="exitZero", action="store_true", help=_("Always exit with exit code 0."))

	outputGroup = parser.add_argument_group(title=_("Output"))
	outputGroup.add_argument("--colorize", "-c", action="store_true", help=_("Colorize output"))

	outputFormat = outputGroup.add_mutually_exclusive_group()
	outputFormat.add_argument(
		"--simple-output",
		"-S",
		dest="output",
		const="SIMPLE",
		action="store_const",
		help=_("Simple output (only for scalars, lists)"),
	)
	outputFormat.add_argument(
		"--shell-output",
		"-s",
		dest="output",
		const="SHELL",
		action="store_const",
		help=_("Shell output"),
	)
	outputFormat.add_argument(
		"--raw-output",
		"-r",
		dest="output",
		const="RAW",
		action="store_const",
		help=_("Raw output"),
	)

	parser.add_argument("command", nargs=argparse.REMAINDER, help=_("Command to execute."))

	options = parser.parse_args()

	interactive = options.interactive
	color = options.colorize
	output = options.output or "JSON"
	exitZero = options.exitZero

	if options.logFile:
		logFile = forceFilename(options.logFile)
		startLogFile(logFile, options.logLevel)

	logging_config(stderr_level=LOG_NONE if interactive else options.logLevel, stderr_format=DEFAULT_COLORED_FORMAT)

	global service_client
	try:
		if options.direct:
			logger.info("Option --direct/-d is deprecated and can be omitted.")

		# Reading opsirc file.
		# We should always prefer the settings from the commandline
		opsircConfig = readOpsirc(options.opsirc)
		opsiconf = OpsiConfig()
		username = options.username or opsircConfig.get("username") or opsiconf.get("host", "id")
		password = options.password or opsircConfig.get("password")
		address = options.address or opsircConfig.get("address") or opsiconf.get("service", "url")
		if not username:
			try:
				username = forceUnicode(pwd.getpwuid(os.getuid())[0])
			except Exception as error:
				logger.error("Failed to get username: %s", error)
				raise
		if not password:
			# Use host key if username is host id
			if username == opsiconf.get("host", "id"):
				password = opsiconf.get("host", "key")
			# otherwise prompt for password
			else:
				try:
					password = getpass.getpass()
				except Exception as error:
					logger.error("Failed to get password: %s", error)
					raise

		session_cookie = None
		sessionFile = None
		home = os.environ.get("HOME")
		if home:
			opsiadminUserDir = Path(home) / ".opsi.org"
			if not opsiadminUserDir.exists():
				try:
					opsiadminUserDir.mkdir()
				except OSError as err:
					logger.info("Could not create %s: %s", opsiadminUserDir, err)

			sessionFile = opsiadminUserDir / "session"
			try:
				with open(sessionFile, "r", encoding="utf-8") as session:
					for line in session:
						line = line.strip()
						if line:
							session_cookie = forceUnicode(line)
							break
			except IOError as err:
				if err.errno != 2:  # 2 is No such file or directory
					logger.error("Failed to read session file '%s': %s", sessionFile, err)
			except Exception as err:
				logger.error("Failed to read session file '%s': %s", sessionFile, err)
				raise err

		service_client = get_service_client(
			address=address,
			username=username,
			password=password,
			session_cookie=session_cookie,
			no_check_certificate=options.no_check_certificate,
		)

		session_cookie = service_client.session_cookie
		if session_cookie and sessionFile:
			try:
				with open(sessionFile, "w", encoding="utf-8") as session:
					session.write(f"{session_cookie}\n")
			except Exception as err:
				logger.error("Failed to write session file '%s': %s", sessionFile, err)

		cmdline = ""
		for i, argument in enumerate(options.command, start=0):
			logger.info("arg[%d]: %s", i, argument)
			if i == 0:
				cmdline = argument
			elif " " in argument or len(argument) == 0:
				cmdline = f"{cmdline} '{argument}'"
			else:
				cmdline = f"{cmdline} {argument}"

		mode = os.fstat(sys.stdin.fileno()).st_mode
		if stat.S_ISFIFO(mode) or stat.S_ISREG(mode):
			# pipe or redirected file
			data = ""
			timeout = 15.0
			while sys.stdin in select.select([sys.stdin], [], [], timeout)[0]:
				dat = sys.stdin.read()
				if not dat:
					break
				data += dat
			data = data.replace("\r", "").replace("\n", "")
			if data:
				logger.trace("Read %s from stdin", data)
				cmdline = f"{cmdline} '{data}'"

		logger.debug("cmdline: '%s'", cmdline)

		global global_shell
		if interactive:
			try:
				logger.notice("Starting interactive mode")
				global_shell = Shell(prompt=f"{username}@opsi-admin>", output=output, color=color, cmdline=cmdline)
				global_shell.setInfoline(f"Connected to {address}")

				for logo_line in LOGO:
					global_shell.appendLine(logo_line.get("text", ""), logo_line.get("color"))

				welcomeMessage = (
					"Welcome to the interactive mode of opsi-admin.\n"
					"You can use syntax completion via [TAB].\n"
					"To exit opsi-admin please type 'exit'.\n"
				)

				for line in welcomeMessage.split("\n"):
					global_shell.appendLine(line, COLOR_NORMAL)
				global_shell.run()
			except Exception as error:
				logger.error(error, exc_info=True)
				raise
		elif cmdline:

			def searchForError(obj: Any) -> None:
				if isinstance(obj, dict):
					try:
						if obj.get("error"):
							raise ErrorInResultException(obj["error"])
					except KeyError:
						for key in obj:
							searchForError(obj[key])
				elif isinstance(obj, list):
					for element in obj:
						searchForError(element)

			try:
				global_shell = Shell(prompt=f"{username}@opsi-admin>", output=output, color=color)
				for cmd in cmdline.split("\n"):
					if cmd:
						global_shell.cmdline = cmd
						global_shell.execute()

				logger.debug("Shell lines are: '%s'", global_shell.getLines())
				for gsline in global_shell.lines:
					print(gsline["text"].rstrip())

				try:
					resultAsJSON = json.loads("\n".join([line["text"] for line in global_shell.lines]))
					searchForError(dict(resultAsJSON))
				except (TypeError, ValueError) as error:
					logger.trace("Conversion to dict failed: %s", error)
			except Exception as err:
				logger.error(err, exc_info=True)
				raise err
		else:
			raise RuntimeError("Not running in interactive mode and no commandline arguments given.")

	finally:
		if service_client:
			try:
				service_client.stop()
			except Exception:
				pass


def startLogFile(log_file: str, logLevel: int) -> None:
	with open(log_file, "w", encoding="utf-8") as log:
		log.write(f"Starting log at: {time.strftime('%a, %d %b %Y %H:%M:%S')}")
	logging_config(log_file=log_file, file_level=logLevel)


class Shell:
	def __init__(self, prompt: str = "opsi-admin>", output: str = "JSON", color: bool = True, cmdline: str = "") -> None:
		self.color = forceBool(color)
		self.output = forceUnicode(output)
		self.running = False
		self.screen: curses._CursesWindow | None = None
		self.cmdBufferSize = 1024
		self.userConfigDir = None
		self.prompt = forceUnicode(prompt)
		self.infoline = "opsi admin started"
		self.yMax = 0
		self.xMax = 0
		self.pos = len(cmdline)
		self.lines: list[dict[str, str]] = []
		self.linesBack = 0
		self.linesMax = 0
		self.params: list[str] = []
		self.paramPos = -1
		self.currentParam: str | None = None
		self.cmdListPos = 0
		self.cmdList = []
		self.cmdline = forceUnicode(cmdline)
		self.shellCommand = ""
		self.reverseSearch: str | None = None
		self.exit_on_sigint = False
		self.commands: list[Command] = [
			CommandMethod(),
			CommandSet(),
			CommandHelp(),
			CommandQuit(),
			CommandExit(),
			CommandHistory(),
			CommandLog(),
			CommandTask(),
		]

		home = os.environ.get("HOME")
		if not home:
			logger.debug("Environment has no $HOME set.")
			home = os.path.expanduser("~")

		if home:
			self.userConfigDir = forceFilename(os.path.join(home, ".opsi.org"))
			if not os.path.isdir(self.userConfigDir):
				try:
					os.mkdir(self.userConfigDir)
				except OSError as error:
					logger.error("Failed to create user dir '%s': %s", self.userConfigDir, error)
		else:
			logger.error("Failed to get home directory from environment!")

		try:
			if not self.userConfigDir:
				raise ValueError("User config dir not set")
			historyFile = forceFilename(os.path.join(self.userConfigDir, "history"))
			with open(historyFile, "r", encoding="utf-8", errors="replace") as history:
				for line in history:
					if not line:
						continue
					self.cmdList.append(line.strip())
					self.cmdListPos += 1
		except FileNotFoundError:
			logger.debug("History %s file not found.", historyFile)
		except Exception as err:
			logger.error("Failed to read history file '%s': %s", historyFile, err)

	def setColor(self, color: bool) -> None:
		color = forceBool(color)
		if color != self.color:
			self.color = color
			self.initScreen()

	def getLines(self) -> list[dict[str, str]]:
		return self.lines

	def initScreen(self) -> None:
		if not self.screen:
			try:
				self.screen = curses.initscr()
			except Exception:
				# setupterm: could not find terminal
				os.environ["TERM"] = "linux"
				self.screen = curses.initscr()
		curses.noecho()
		curses.cbreak()
		self.screen.keypad(True)
		self.screen.clear()

		self.yMax, self.xMax = self.screen.getmaxyx()
		self.linesMax = self.yMax - 2

		if self.color:
			curses.start_color()

			curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
			curses.init_pair(2, curses.COLOR_CYAN, curses.COLOR_BLACK)
			curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
			curses.init_pair(4, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
			curses.init_pair(5, curses.COLOR_RED, curses.COLOR_BLACK)

	def exitScreen(self) -> None:
		if not self.screen:
			return
		logger.info("Exit screen")
		curses.nocbreak()
		self.screen.keypad(False)
		curses.echo()
		curses.endwin()

	def sigint(self) -> None:
		logger.info("Received SIGINT (exit_on_sigint: %r)", self.exit_on_sigint)
		if self.exit_on_sigint:
			sys.exit(0)
		self.pos = 0
		self.setCmdline("")
		self.reverseSearch = None

	def run(self) -> None:
		self.running = True

		self.initScreen()

		if self.cmdline:
			for cmd in self.cmdline.split("\n"):
				self.cmdline = cmd
				self.appendLine(f"{self.prompt} {self.cmdline}")
				if self.cmdline:
					try:
						self.execute()
					except Exception as err:
						lines = str(err).split("\n")
						lines[0] = f"ERROR: {lines[0]}"
						for line in lines:
							self.appendLine(line, COLOR_RED)

				if len(self.lines) > self.yMax - 2:
					self.linesBack = 0
		else:
			self.display()

		while self.running:
			self.getCommand()

	def exit(self) -> None:
		if interactive and logFile and os.path.exists(logFile):
			if self.question(_("Delete log-file '%s'?") % logFile):
				try:
					os.unlink(logFile)
				except OSError as err:
					logger.error("Failed to delete log-file '%s': %s", logFile, err)

		try:
			logger.info("Write history file")
			if not self.userConfigDir:
				raise ValueError("User config dir not set")
			historyFilePath = os.path.join(self.userConfigDir, "history")
			with open(historyFilePath, "w", encoding="utf-8") as history:
				for line in self.cmdList:
					if not line or line in ("quit", "exit"):
						continue
					history.write(f"{line}\n")
		except Exception as err:
			logger.error("Failed to write history file '%s': %s", historyFilePath, err)

		self.exitScreen()
		self.running = False

	def bell(self) -> None:
		sys.stderr.write("\a")

	def display(self) -> None:
		if not self.screen:
			return
		self.screen.move(0, 0)
		self.screen.clrtoeol()
		shellLine = self.infoline + (self.xMax - len(self.infoline)) * " "
		try:
			self.screen.addstr(shellLine, curses.A_REVERSE)
		except Exception as err:
			logger.error("Failed to add string '%s': %s", shellLine, err)

		height = int(len(self.prompt + " " + self.cmdline) / self.xMax) + 1
		clear = self.xMax - (len(self.prompt + " " + self.cmdline) % self.xMax) - 1

		self.linesMax = self.yMax - height - 1
		self.screen.move(self.yMax - height, 0)
		self.screen.clrtoeol()
		shellLine = f"{self.prompt} {self.cmdline}{' ' * clear}"
		try:
			self.screen.addstr(shellLine, curses.A_BOLD)
		except Exception as err:
			logger.error("Failed to add string '%s': %s", shellLine, err)

		for idx in range(0, self.linesMax):
			self.screen.move(self.linesMax - idx, 0)
			self.screen.clrtoeol()
			shellLine = ""
			color: str | int | None = None
			if len(self.lines) - self.linesBack > idx:
				shellLine = self.lines[len(self.lines) - self.linesBack - 1 - idx]["text"]
				if self.color:
					color = self.lines[len(self.lines) - self.linesBack - 1 - idx]["color"]

			if color:
				if color == COLOR_NORMAL:
					color = curses.A_BOLD
				elif color == COLOR_GREEN:
					color = curses.color_pair(1)
				elif color == COLOR_CYAN:
					color = curses.color_pair(2)
				elif color == COLOR_LIGHT_WHITE:
					color = curses.A_BOLD
				elif color == COLOR_YELLOW:
					color = curses.color_pair(3)
				elif color == COLOR_LIGHT_RED:
					color = curses.color_pair(4)
				elif color == COLOR_RED:
					color = curses.color_pair(5)

			try:
				try:
					self.screen.addstr(shellLine, int(color))  # type: ignore[arg-type]
				except ValueError:
					self.screen.addstr(shellLine)
			except Exception as err:
				logger.error("Failed to add string '%s': %s", shellLine, err, exc_info=True)

		moveY = self.yMax - height + int((len(self.prompt + " ") + self.pos) / self.xMax)
		moveX = (len(self.prompt + " ") + self.pos) % self.xMax
		self.screen.move(moveY, moveX)
		self.screen.refresh()

	def appendLine(self, line: str, color: str | None = None, refresh: bool = True) -> None:
		line = forceUnicode(line)

		if not color:
			if line.startswith(COLOR_NORMAL):
				color = COLOR_NORMAL
			elif line.startswith(COLOR_GREEN):
				color = COLOR_GREEN
			elif line.startswith(COLOR_CYAN):
				color = COLOR_CYAN
			elif line.startswith(COLOR_LIGHT_WHITE):
				color = COLOR_LIGHT_WHITE
			elif line.startswith(COLOR_YELLOW):
				color = COLOR_YELLOW
			elif line.startswith(COLOR_LIGHT_RED):
				color = COLOR_LIGHT_RED
			elif line.startswith(COLOR_RED):
				color = COLOR_RED

		for availableColor in COLORS_AVAILABLE:
			line = line.replace(availableColor, "")

		while self.xMax and (len(line) > self.xMax):
			self.lines.append({"text": line[: self.xMax], "color": str(color)})
			line = line[self.xMax :]

		self.lines.append({"text": line, "color": str(color)})
		if refresh:
			self.display()

	def setCmdline(self, cmdline: str, refresh: bool = True) -> None:
		self.cmdline = forceUnicode(cmdline)
		if refresh:
			self.display()

	def setInfoline(self, infoline: str, refresh: bool = True) -> None:
		self.infoline = forceUnicode(infoline)
		if refresh:
			self.display()

	def getParams(self) -> list[str]:
		self.parseCmdline()
		return self.params

	def getParam(self, idx: int) -> str:
		self.parseCmdline()
		if len(self.params) > idx:
			return self.params[idx]
		return ""

	def parseCmdline(self) -> None:
		self.params = []
		self.currentParam = None
		self.paramPos = -1

		if not self.cmdline:
			return

		self.shellCommand = ""
		cmdline = self.cmdline
		if "|" in cmdline:
			quoteCount = 0
			doubleQuoteCount = 0
			parts = cmdline.split("|")
			for i, part in enumerate(parts):
				quoteCount += part.count("'")
				doubleQuoteCount += part.count('"')
				if (quoteCount % 2 == 0) and (doubleQuoteCount % 2 == 0):
					cmdline = "|".join(parts[: i + 1])
					self.shellCommand = "|".join(parts[i + 1 :]).lstrip()
					break

		cur = 0
		quote = None
		for i, element in enumerate(cmdline):
			logger.trace("parseCmdline(): char '%s', quote: '%s', cur: '%s', params: '%s'", element, quote, cur, self.params)
			if len(self.params) < cur + 1:
				self.params.append("")

			if i == self.pos - 1:
				self.paramPos = cur

			if element == "'":
				if quote is None:
					quote = "'"
				elif quote == "'":
					if not self.params[cur]:
						cur += 1
					quote = None
				else:
					self.params[cur] += "'"
			elif element == '"':
				if quote is None:
					self.params[cur] += '"'
					quote = '"'
				elif quote == '"':
					self.params[cur] += '"'
					if not self.params[cur]:
						cur += 1
					quote = None
				else:
					self.params[cur] += '"'
			elif element == " ":
				if quote is not None:
					self.params[cur] += element
				elif len(self.params[cur]) > 0:
					cur += 1
			else:
				self.params[cur] += element

		if not quote and self.params and self.params[-1] and self.pos == len(cmdline) and cmdline.endswith(" "):
			self.params.append("")
			self.paramPos += 1

		if self.params:
			self.currentParam = self.params[self.paramPos]
		else:
			self.currentParam = ""

		logger.debug("cmdline: '%s'", cmdline)
		logger.trace("paramPos %s, currentParam: '%s', params: '%s'", self.paramPos, self.currentParam, self.params)

		if self.paramPos >= len(self.params):
			logger.error(
				"Assertion 'self.paramPos < len(self.params)' failed: self.paramPos: %s, len(self.params): %s",
				self.paramPos,
				len(self.params),
			)
			self.paramPos = len(self.params) - 1

	def execute(self) -> None:
		logger.info("Execute: '%s'", self.cmdline)
		self.cmdList.append(self.cmdline)
		if len(self.cmdList) > self.cmdBufferSize:
			del self.cmdList[0]
		self.cmdListPos = len(self.cmdList)
		invalid = True
		for command in self.commands:
			if command.getName() == self.getParam(0):
				invalid = False
				try:
					command.execute(self, self.getParams()[1:])
				except Exception as err:
					message = f"Failed to execute {self.cmdline}: {err}"
					logger.error(message, exc_info=True)
					raise RuntimeError(message) from err
				break

		if invalid:
			raise ValueError(_("Invalid command: '%s'") % self.getParam(0))

	def question(self, question: str) -> bool:
		assert self.screen
		question = forceUnicode(question)
		if interactive:
			self.screen.move(self.yMax - 1, 0)
			self.screen.clrtoeol()
			self.screen.addstr(question + " (n/y): ")
			self.screen.refresh()
			char = None
			while True:
				char = self.screen.getch()
				if char and 256 > char >= 0 and char != 10:
					if chr(char) == "y":
						return True
					if chr(char) == "n":
						return False
		return False

	def getPassword(self) -> str:
		assert self.screen
		password1 = ""
		password2 = ""
		while not password1 or (password1 != password2):
			if interactive:
				self.screen.move(self.yMax - 1, 0)
				self.screen.clrtoeol()
				self.screen.addstr(_("Please type password:"))
				self.screen.refresh()
				password1 = self.screen.getstr().decode("utf-8")

				self.screen.move(self.yMax - 1, 0)
				self.screen.clrtoeol()
				self.screen.addstr(_("Please retype password:"))
				self.screen.refresh()
				password2 = self.screen.getstr().decode("utf-8")

				if password1 != password2:
					self.screen.move(self.yMax - 1, 0)
					self.screen.clrtoeol()
					self.screen.addstr(_("Supplied passwords do not match"))
					self.screen.refresh()
					time.sleep(2)
			else:
				password1 = password2 = getpass.getpass()

		logger.confidential("Got password '%s'", password1)
		return password1

	def getCommand(self) -> None:
		assert self.screen
		char: int | str | None = None
		self.pos = 0
		self.setCmdline("")
		self.reverseSearch = None

		while not char or (char != 10):
			if not self.running:
				return

			char = self.screen.getch()
			textInput = False

			if not char or char < 0:
				continue

			if char == curses.KEY_RESIZE:
				# window resized
				self.yMax, self.xMax = self.screen.getmaxyx()
				self.display()
				continue

			if char == curses.KEY_UP:
				if len(self.cmdList) > 0 and self.cmdListPos > 0:
					self.cmdListPos -= 1
					self.pos = len(self.cmdList[self.cmdListPos])
					self.setCmdline(self.cmdList[self.cmdListPos])

			elif char == curses.KEY_DOWN:
				if len(self.cmdList) > 0 and self.cmdListPos < len(self.cmdList):
					self.cmdListPos += 1
					if self.cmdListPos == len(self.cmdList):
						self.pos = 0
						self.setCmdline("")
					else:
						self.pos = len(self.cmdList[self.cmdListPos])
						self.setCmdline(self.cmdList[self.cmdListPos])

			elif char == curses.KEY_LEFT:
				if self.pos > 0:
					self.pos -= 1
					self.setCmdline(self.cmdline)

			elif char == curses.KEY_RIGHT:
				if self.pos < len(self.cmdline):
					self.pos += 1
					self.setCmdline(self.cmdline)

			elif char == curses.KEY_HOME:
				self.pos = 0
				self.setCmdline(self.cmdline)

			elif char == curses.KEY_END:
				self.pos = len(self.cmdline)
				self.setCmdline(self.cmdline)

			elif char == curses.KEY_NPAGE:
				if len(self.lines) > self.yMax - 2:
					self.linesBack -= 5
					self.linesBack = max(self.linesBack, 0)
					self.display()

			elif char == curses.KEY_PPAGE:
				if len(self.lines) > self.yMax - 2:
					self.linesBack += 5
					if self.linesBack > len(self.lines) - self.yMax + 2:
						self.linesBack = len(self.lines) - self.yMax + 2

					self.display()

			elif char == 4:
				# ^D
				self.exit()

			elif char == 10:
				# Enter
				self.pos = len(self.cmdline)
				self.setCmdline(self.cmdline)

			elif char == 18:
				# ^R
				if self.reverseSearch is None:
					self.setInfoline("reverse-i-search")
					self.reverseSearch = ""
				else:
					self.setInfoline("")
					self.reverseSearch = None
				continue

			elif char == 9:
				# tab 		|<- ->|
				# Auto-completion
				completions = []

				params = self.getParams()
				if self.paramPos >= 0 and self.currentParam:
					params[self.paramPos] = self.currentParam

				for command in self.commands:
					if self.paramPos < 0:
						completions.append(command.getName())

					elif params[0] == command.getName():
						if self.paramPos >= 1:
							completions = command.completion(params[1:], self.paramPos)
						else:
							completions = [command.getName()]
						break

					elif command.getName().startswith(params[0]):
						completions.append(command.getName())

				if len(completions) == 1:
					self.setCmdline(self.cmdline[: self.pos] + completions[0][len(params[self.paramPos]) :] + self.cmdline[self.pos :])
					self.pos += len(completions[0][len(params[self.paramPos]) :])

					if self.pos == len(self.cmdline):
						self.cmdline += " "
						self.pos += 1

					self.setCmdline(self.cmdline)

				elif len(completions) > 1:
					match = completions[0]
					lines = []
					longest = 0
					for comp in completions:
						for idx, val in enumerate(comp):
							if idx > len(match) - 1:
								break
							if val != match[idx]:
								match = match[:idx]
								break
						if len(comp) > longest:
							longest = len(comp)

					curLine = ""
					i = 0
					while i < len(completions):
						while (i < len(completions)) and (not curLine or (len(curLine) + longest < self.xMax - 5)):
							pf = "%s %-" + str(longest) + "s"
							curLine = pf % (curLine, completions[i])
							i += 1
						lines.append({"text": curLine, "color": ""})
						curLine = ""

					if self.paramPos < 0:
						self.currentParam = ""

					text = (
						f"{self.prompt} {self.cmdline[:self.pos - len(self.currentParam or '')]}"
						f"{match.strip()}{self.cmdline[self.pos:]}"
					)
					self.lines.append({"text": text, "color": ""})

					self.lines.extend(lines)

					self.setCmdline(self.cmdline[: self.pos - len(self.currentParam or "")] + match.strip() + self.cmdline[self.pos :])

					self.pos += len(match) - len(self.currentParam or "")

					self.setCmdline(self.cmdline)
				else:
					self.bell()

			else:
				textInput = True
				newPos = self.pos
				newCmdline = self.cmdline

				if char == 263:
					# backspace	<--
					if self.reverseSearch is not None:
						self.reverseSearch = self.reverseSearch[:-1]
						self.setInfoline(f"reverse-i-search: {self.reverseSearch}")
					elif self.pos > 0:
						newPos = self.pos - 1
						newCmdline = self.cmdline[:newPos] + self.cmdline[self.pos :]
				elif char == 330:
					# del
					if self.reverseSearch is not None:
						pass
					elif len(self.cmdline) > 0:
						newCmdline = self.cmdline[: self.pos] + self.cmdline[self.pos + 1 :]

				else:
					try:
						curses.ungetch(char)
						char = self.screen.getkey()
						logger.trace("Current char: %r", char)

						if not isinstance(char, str):
							try:
								char = str(char)
							except Exception:
								char += self.screen.getkey()
								char = str(char)

						if self.reverseSearch is not None:
							self.reverseSearch += char
						else:
							newPos = self.pos + 1
							newCmdline = self.cmdline[0 : self.pos] + char + self.cmdline[self.pos :]
					except Exception as err:
						logger.error("Failed to add char %r: %s", char, err)

				try:
					if self.reverseSearch is not None:
						self.setInfoline(f"reverse-i-search: {self.reverseSearch}")
						found = False
						for i in range(len(self.cmdList) - 1, -1, -1):
							if self.reverseSearch in self.cmdList[i]:
								found = True
								newCmdline = self.cmdList[i]
								break

						if not found and char not in (263, 330):
							self.bell()
						newPos = len(newCmdline)

					self.pos = newPos
					self.setCmdline(newCmdline)
				except Exception as err:
					self.setInfoline(str(err))

			if not textInput:
				if self.reverseSearch is not None:
					self.reverseSearch = None
					self.setInfoline("")

		self.cmdline = self.cmdline.strip()

		self.appendLine(self.prompt + " " + self.cmdline)
		if self.cmdline:
			try:
				self.execute()
			except Exception as err:
				err_lines = str(err).split("\n")
				err_lines[0] = f"ERROR: {err_lines[0]}"
				for err_line in err_lines:
					self.appendLine(err_line, COLOR_RED)

		if len(self.lines) > self.yMax - 2:
			self.linesBack = 0


class Command:
	def __init__(self, name: str) -> None:
		self.name = forceUnicode(name)

	def getName(self) -> str:
		return self.name

	def getDescription(self) -> str:
		return ""

	def completion(self, params: list[str], paramPos: int) -> list[str]:
		return []

	def help(self, shell: Shell) -> None:
		shell.appendLine("")

	def execute(self, shell: Shell, params: list[str]) -> None:
		raise NotImplementedError("Nothing to do.")


class CommandMethod(Command):
	def __init__(self) -> None:
		Command.__init__(self, "method")
		assert service_client
		self.interface = service_client.jsonrpc_interface

	def getDescription(self) -> str:
		return _("Execute a config-interface-method")

	def help(self, shell: Shell) -> None:
		shell.appendLine(f'\r{_("Methods are:")}\n')
		assert service_client
		for method in service_client.jsonrpc_interface:
			logger.debug(method)
			shell.appendLine(f"\r{method.get('name')}\n")

	def completion(self, params: list[str], paramPos: int) -> list[str]:
		completions = []

		if paramPos == 0:
			completions.append("list")
			for param in self.interface:
				if comp := param.get("name"):
					completions.append(comp)

		elif paramPos == 1:
			if "list".startswith(params[0]):
				completions.append("list")
			for param in self.interface:
				comp = param.get("name")
				if comp and comp.startswith(params[0]):
					completions.append(comp)

		elif paramPos >= 2:
			for param in self.interface:
				if param.get("name") == params[0]:
					if len(param.get("params", [])) >= len(params) - 1:
						completions = [param["params"][paramPos - 2]]
					break

		return completions

	def execute(self, shell: Shell, params: list[str]) -> None:
		if len(params) <= 0:
			shell.appendLine(_("No method defined"))
			return

		methodName = params[0]

		if methodName == "list":
			for methodDescription in self.interface:
				shell.appendLine(f"{methodDescription.get('name')}{tuple(methodDescription.get('params', []))}", refresh=False)
			shell.display()
			return

		for methodDescription in self.interface:
			if methodName == methodDescription["name"]:
				methodInterface = methodDescription
				break
		else:
			raise OpsiRpcError(f"Method '{methodName}' is not valid")

		params = params[1:]
		keywords: dict[str, str] = {}
		if methodInterface["keywords"]:
			parameters = 0
			if methodInterface["args"]:
				parameters += len(methodInterface["args"])
			if methodInterface["varargs"]:
				parameters += len(methodInterface["varargs"])

			if len(params) >= parameters:
				# Do not create Object instances!
				params[-1] = fromJson(params[-1], preventObjectCreation=True)
				if not isinstance(params[-1], dict):
					raise ValueError(f"kwargs param is not a dict: {params[-1]}")

				for key, value in params.pop(-1).items():
					keywords[str(key)] = deserialize(value)

		def createObjectOrString(obj: Any) -> str:
			"Tries to return object from JSON. If this fails returns unicode."
			try:
				return fromJson(obj)
			except Exception as err:
				logger.debug("Not a json string '%s': %s", obj, err)
				return forceUnicode(obj)

		params = [createObjectOrString(item) for item in params]

		pString = str(params)[1:-1]
		if keywords:
			pString += ", " + str(keywords)
		if len(pString) > 200:
			pString = pString[:200] + "..."

		result = None

		logger.info("Executing:  %s(%s)", methodName, pString)
		shell.setInfoline(f"Executing:  {methodName}({pString})")
		start = time.time()

		# This needs ServiceClient with "jsonrpc_create_methods=True"
		method = getattr(service_client, methodName)
		if keywords:
			result = method(*params, **keywords)
		else:
			result = method(*params)

		duration = time.time() - start
		logger.debug("Took %0.3f seconds to process: %s(%s)", duration, methodName, pString)
		shell.setInfoline(_("Took %0.3f seconds to process: %s(%s)") % (duration, methodName, pString))
		result = serialize(result)
		logger.trace("Serialized result: '%s'", result)

		if result is not None:
			lines = []
			if shell.output == "RAW":
				lines.append(toJson(result))

			elif shell.output == "JSON":
				lines = objectToBeautifiedText(result).split("\n")

			elif shell.output == "SHELL":
				bashVars = objectToBash(result, {})
				for index in range(len(bashVars) - 1, -2, -1):
					str_index = str(index) if index != -1 else ""
					value = bashVars.get(f"RESULT{str_index}")
					if value:
						lines.append(f"RESULT{str_index}={value}")

			elif shell.output == "SIMPLE":
				if isinstance(result, dict):
					for key, value in result.items():
						if isinstance(value, bool):
							value = forceUnicodeLower(value)
						lines.append(f"{key}={value}")
				elif isinstance(result, (tuple, list, set)):
					for resultElement in result:
						if isinstance(resultElement, dict):
							for key, value in resultElement.items():
								if isinstance(value, bool):
									value = forceUnicodeLower(value)
								lines.append(f"{key}={value}")
							lines.append("")
						elif isinstance(resultElement, (tuple, list)):
							raise ValueError("Simple output not possible for list of lists")
						else:
							lines.append(forceUnicode(resultElement))
				else:
					lines.append(forceUnicode(result))
			else:
				lines.append(forceUnicode(result))

			if shell.shellCommand:
				logger.notice("Executing: '%s'", shell.shellCommand)

				proc = subprocess.Popen(
					shell.shellCommand,
					shell=True,
					stdin=subprocess.PIPE,
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE,
				)
				assert proc.stdout
				assert proc.stderr
				assert proc.stdin

				flags = fcntl.fcntl(proc.stdout, fcntl.F_GETFL)
				fcntl.fcntl(proc.stdout, fcntl.F_SETFL, flags | os.O_NONBLOCK)

				flags = fcntl.fcntl(proc.stderr, fcntl.F_GETFL)
				fcntl.fcntl(proc.stderr, fcntl.F_SETFL, flags | os.O_NONBLOCK)

				exitCode = None
				buf = b""
				serr = b""

				while exitCode is None:
					exitCode = proc.poll()
					if lines:
						for line in lines:
							proc.stdin.write(line.encode(outEncoding, "replace"))
							proc.stdin.write(b"\n")
						lines = []
						proc.stdin.close()

					try:
						string = proc.stdout.read()
						if len(string) > 0:
							buf += string
					except IOError as error:
						if error.errno != 11:
							raise

					try:
						string = proc.stderr.read()
						if len(string) > 0:
							serr += string
					except IOError as error:
						if error.errno != 11:
							raise

				if exitCode != 0:
					nwl = "\n"
					raise RuntimeError(f"Exitcode: {exitCode * -1}{nwl}{serr.decode(inEncoding, 'replace')}")

				lines = buf.decode(inEncoding, "replace").split("\n")

			for line in lines:
				shell.appendLine(line, COLOR_GREEN)


class CommandSet(Command):
	def __init__(self) -> None:
		Command.__init__(self, "set")

	def getDescription(self) -> str:
		return _("Settings")

	def completion(self, params: list[str], paramPos: int) -> list[str]:
		completions = []

		if paramPos == 0 or not params[0]:
			completions = ["color", "log-file", "log-level"]

		elif paramPos == 1:
			if "color".startswith(params[0]):
				completions = ["color"]
			if "log-file".startswith(params[0]):
				completions = ["log-file"]
			if "log-level".startswith(params[0]):
				completions.append("log-level")

		elif paramPos == 2:
			if params[0] == "color":
				completions = ["on", "off"]
			elif params[0] == "log-file":
				completions = ["<filename>", "off"]
			elif params[0] == "log-level":
				completions = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]

		return completions

	def execute(self, shell: Shell, params: list[str]) -> None:
		global logFile

		if len(params) <= 0:
			raise ValueError(_("Missing option"))
		if params[0] not in ("color", "log-file", "log-level"):
			raise ValueError(_("Unknown option: %s") % params[0])
		if len(params) <= 1:
			raise ValueError(_("Missing value"))

		if params[0] == "color":
			if params[1] == "on":
				shell.setColor(True)
			elif params[1] == "off":
				shell.setColor(False)
			else:
				raise ValueError(_("Bad value: %s") % params[1])

		elif params[0] == "log-file":
			if params[1] == "off":
				logging_config(file_level=LOG_NONE)
			else:
				logFile = params[1]
				startLogFile(logFile, LOG_DEBUG)

		elif params[0] == "log-level":
			if not logFile:
				raise ValueError(_("No log-file set!"))
			logging_config(file_level=int(params[1]))


class CommandHelp(Command):
	def __init__(self) -> None:
		Command.__init__(self, "help")

	def getDescription(self) -> str:
		return _("Show this text")

	def execute(self, shell: Shell, params: list[str]) -> None:
		shell.appendLine("\r" + _("Commands are:") + "\n", refresh=False)
		for cmd in shell.commands:
			shell.appendLine(f"\r\t{(cmd.getName() + ':'):<20}{cmd.getDescription()}\n", refresh=False)
		shell.display()


class CommandQuit(Command):
	def __init__(self) -> None:
		Command.__init__(self, "quit")

	def getDescription(self) -> str:
		return _("Exit opsi-admin")

	def execute(self, shell: Shell, params: list[str]) -> None:
		shell.exit()


class CommandExit(CommandQuit):
	def __init__(self) -> None:
		Command.__init__(self, "exit")


class CommandHistory(Command):
	def __init__(self) -> None:
		Command.__init__(self, "history")

	def getDescription(self) -> str:
		return _("show / clear command history")

	def completion(self, params: list[str], paramPos: int) -> list[str]:
		completions = []

		if paramPos == 0 or not params[0]:
			completions = ["clear", "show"]

		elif paramPos == 1:
			if "clear".startswith(params[0]):
				completions = ["clear"]
			elif "show".startswith(params[0]):
				completions = ["show"]

		return completions

	def execute(self, shell: Shell, params: list[str]) -> None:
		if len(params) <= 0:
			# By default: show history
			params = ["show"]
		elif params[0] not in ("clear", "show"):
			raise ValueError(_("Unknown command: %s") % params[0])

		if params[0] == "show":
			for line in shell.cmdList:
				shell.appendLine(line, refresh=False)
			shell.display()
		elif params[0] == "clear":
			shell.cmdList = []
			shell.cmdListPos = -1


class CommandLog(Command):
	def __init__(self) -> None:
		Command.__init__(self, "log")

	def getDescription(self) -> str:
		return _("show log")

	def completion(self, params: list[str], paramPos: int) -> list[str]:
		completions = []

		if paramPos == 0 or not params[0]:
			completions = ["show"]

		elif paramPos == 1:
			if "show".startswith(params[0]):
				completions = ["show"]

		return completions

	def execute(self, shell: Shell, params: list[str]) -> None:
		if len(params) <= 0:
			# By default: show log
			params = ["show"]
		elif params[0] not in ("show",):
			raise ValueError(_("Unknown command: %s") % params[0])

		if params[0] == "show":
			if not logFile:
				raise RuntimeError(_("File logging is not activated"))

			with open(logFile, encoding="utf-8") as log:
				for line in log:
					shell.appendLine(line, refresh=False)
			shell.display()


class CommandTask(Command):
	def __init__(self) -> None:
		Command.__init__(self, "task")
		self._tasks = (  # TODO: are these deprecated methods still needed?
			("setupWhereInstalled", "productId"),
			("setupWhereNotInstalled", "productId"),
			("updateWhereInstalled", "productId"),
			("uninstallWhereInstalled", "productId"),
			("setActionRequestWhereOutdated", "actionRequest", "productId"),
			("setActionRequestWhereOutdatedWithDependencies", "actionRequest", "productId"),
			("setActionRequestWithDependencies", "actionRequest", "productId", "clientId"),
			("decodePcpatchPassword", "encodedPassword", "opsiHostKey"),
			("setPcpatchPassword", "*password"),
			("activateTOTP", "userId"),
		)

	def getDescription(self) -> str:
		return _("execute a task")

	def help(self, shell: Shell) -> None:
		shell.appendLine("")

	def completion(self, params: list[str], paramPos: int) -> list[str]:
		completions = []

		if paramPos == 0 or not params[0]:
			for task in self._tasks:
				completions.append(task[0])

		elif paramPos == 1:
			for task in self._tasks:
				if task[0].startswith(params[0]):
					completions.append(task[0])

		elif paramPos >= 2:
			for task in self._tasks:
				if params[0] == task[0] and paramPos <= len(task):
					completions.append(task[paramPos - 1])

		return completions

	def execute(self, shell: Shell, params: list[str]) -> None:
		assert service_client
		tasknames = {task[0] for task in self._tasks}

		if len(params) <= 0:
			return

		if params[0] not in tasknames:
			raise ValueError(_("Unknown task: %s") % params[0])

		if params[0] == "setupWhereInstalled":
			if len(params) < 2:
				raise ValueError(_("Missing product-id"))
			productId = params[1]

			logger.warning("The task 'setupWhereInstalled' is obsolete. Please use 'method setupWhereInstalled' instead.")

			for clientId in service_client.jsonrpc("setupWhereInstalled", [productId]):
				shell.appendLine(clientId)

		elif params[0] == "setupWhereNotInstalled":
			if len(params) < 2:
				raise ValueError(_("Missing product-id"))
			productId = params[1]

			logger.warning("The task 'setupWhereNotInstalled' is obsolete. Please use 'method setupWhereNotInstalled' instead.")

			for clientId in service_client.jsonrpc("setupWhereNotInstalled", [productId]):
				shell.appendLine(clientId)

		elif params[0] == "updateWhereInstalled":
			if len(params) < 2:
				raise ValueError(_("Missing product-id"))
			productId = params[1]

			logger.warning("The task 'updateWhereInstalled' is obsolete. Please use 'method updateWhereInstalled' instead.")

			for clientId in service_client.jsonrpc("updateWhereInstalled", [productId]):
				shell.appendLine(clientId)

		elif params[0] == "uninstallWhereInstalled":
			if len(params) < 2:
				raise ValueError(_("Missing product-id"))
			productId = params[1]

			logger.warning("The task 'uninstallWhereInstalled' is obsolete. Please use 'method uninstallWhereInstalled' instead.")

			for clientId in service_client.jsonrpc("uninstallWhereInstalled", [productId]):
				shell.appendLine(clientId)

		elif params[0] == "setActionRequestWhereOutdated":
			if len(params) < 2:
				raise ValueError(_("Missing action request"))
			if len(params) < 3:
				raise ValueError(_("Missing product-id"))

			actionRequest = params[1]
			productId = params[2]

			logger.warning(
				"The task 'setActionRequestWhereOutdated' is obsolete. Please use 'method setActionRequestWhereOutdated' instead."
			)

			for clientId in service_client.jsonrpc("setActionRequestWhereOutdated", [actionRequest, productId]):
				shell.appendLine(clientId)

		elif params[0] == "setActionRequestWithDependencies":
			if len(params) < 2:
				raise ValueError(_("Missing action request"))
			if len(params) < 3:
				raise ValueError(_("Missing product-id"))
			if len(params) < 4:
				raise ValueError(_("Missing client-id"))
			actionRequest = params[1]
			productId = params[2]
			clientId = params[3]

			if productId and clientId and actionRequest:
				service_client.jsonrpc("setProductActionRequestWithDependencies", [productId, clientId, actionRequest])

		elif params[0] == "setActionRequestWhereOutdatedWithDependencies":
			if len(params) < 2:
				raise ValueError(_("Missing action request"))
			if len(params) < 3:
				raise ValueError(_("Missing product-id"))

			actionRequest = params[1]
			productId = params[2]

			logger.warning(
				"The task 'setActionRequestWhereOutdatedWithDependencies' "
				"is obsolete. Please use 'method "
				"setActionRequestWhereOutdatedWithDependencies' instead."
			)

			for clientId in service_client.jsonrpc("setActionRequestWhereOutdatedWithDependencies", [actionRequest, productId]):
				shell.appendLine(clientId)

		elif params[0] == "decodePcpatchPassword":
			if len(params) < 3:
				raise ValueError(_("Missing argument"))
			crypt = params[1]
			key = params[2]
			cleartext = blowfishDecrypt(key, crypt)
			shell.appendLine(cleartext)

		elif params[0] == "setPcpatchPassword":
			# if os.getuid() != 0:
			# 	raise RuntimeError(_("You have to be root to change pcpatch password!"))

			shell.exit_on_sigint = True

			password = ""
			if len(params) < 2:
				password = shell.getPassword()
			else:
				password = params[1]

			if not password:
				raise ValueError("Can not use empty password!")
			secret_filter.add_secrets(password)

			service_client.jsonrpc("user_setCredentials", ["pcpatch", password])

			try:
				udm = which("univention-admin")
				server_role = sys_execute("ucr get server/role")
				if server_role in ("domaincontroller_master", "domaincontroller_backup"):
					# We are on Univention Corporate Server (UCS)
					dn = None
					command = f'{udm} users/user list --filter "(uid=pcpatch)"'
					logger.debug("Filtering for pcpatch: %s", command)
					with closing(os.popen(command, "r")) as process:
						for line in process.readlines():
							if line.startswith("DN"):
								dn = line.strip().split(" ")[1]
								break

					if not dn:
						raise RuntimeError("Failed to get DN for user pcpatch")

					command = (
						f"{udm} users/user modify --dn {dn} "
						f"--set password='{password}' "
						"--set overridePWLength=1 --set overridePWHistory=1 "
						"1>/dev/null 2>/dev/null"
					)
					logger.debug("Setting password with: %s", command)
					sys_execute(command)
					# Done with UCS
					return
				logger.warning("Did not change the password for 'pcpatch', please change it on the master server.")

			except CommandNotFoundException:
				# Not on UCS
				pass

			try:
				pwd.getpwnam("pcpatch")
			except KeyError as err:
				raise KeyError("System user 'pcpatch' not found") from err

			password_set = False
			try:
				# smbldap
				smbldapCommand = f"{which('smbldap-passwd')} pcpatch"
				sys_execute(smbldapCommand, stdin_data=f"{password}\n{password}\n".encode("utf8"))
				password_set = True
			except Exception as err:
				logger.debug("Setting password through smbldap failed: %s", err)

			if not password_set:
				# unix
				is_local_user = False
				with open("/etc/passwd", "r", encoding="utf-8") as file:
					for line in file.readlines():
						if line.startswith("pcpatch:"):
							is_local_user = True
							break
				if is_local_user:
					chpasswdCommand = f"echo 'pcpatch:{password}' | {which('chpasswd')}"
					sys_execute(chpasswdCommand)

					smbpasswdCommand = f"{which('smbpasswd')} -a -s pcpatch"
					sys_execute(smbpasswdCommand, stdin_data=f"{password}\n{password}\n".encode("utf8"))
				else:
					logger.warning("The user 'pcpatch' is not a local user, please change password also in Active Directory")

		elif params[0] == "activateTOTP":
			if len(params) < 2:
				raise ValueError(_("Missing argument"))
			for line in service_client.user_updateMultiFactorAuth(userId=params[1], type="totp", returnType="qrcode").split("\n"):  # type: ignore[attr-defined]
				shell.appendLine(line)


def main() -> None:
	try:
		locale.setlocale(locale.LC_ALL, "")
	except Exception:
		pass

	if os.name == "posix":
		from signal import SIGINT, SIGQUIT, signal

		signal(SIGINT, signalHandler)
		signal(SIGQUIT, signalHandler)

	try:
		shell_main()
		exitCode = 0
	except ErrorInResultException as error:
		logger.warning("Error in result: %s", error)
		exitCode = 2
	except Exception as err:
		logging_config(stderr_level=LOG_ERROR)
		logger.error("Error during execution: %s", err, exc_info=True)
		exitCode = 1

	if exitZero:
		exitCode = 0

	sys.exit(exitCode)
