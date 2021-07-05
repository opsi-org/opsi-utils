# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-admin - a commandline tool for accessing opsi.
"""

# pylint: disable=too-many-lines

import argparse
import codecs
import curses
import fcntl
import gettext
import getpass
import json
import locale
import os
import os.path
import pwd
import subprocess
import sys
import time
from contextlib import closing, contextmanager

from opsicommon.logging import logger, logging_config, secret_filter, LOG_NONE, LOG_ERROR, LOG_DEBUG, LOG_WARNING, DEFAULT_COLORED_FORMAT
from OPSI import __version__ as python_opsi_version
from OPSI.Backend.BackendManager import BackendManager
from OPSI.Exceptions import OpsiRpcError
from OPSI.System import CommandNotFoundException
from OPSI.System import which
from OPSI.System import execute as sys_execute
from OPSI.Types import forceBool, forceFilename, forceUnicode, forceUnicodeLower
from OPSI.Util import (
	blowfishDecrypt, deserialize, fromJson, getfqdn,
	objectToBeautifiedText, objectToBash, serialize, toJson
)
from OPSI.Util.File.Opsi.Opsirc import getOpsircPath, readOpsirc
from opsiutils import __version__

COLOR_NORMAL = '\033[0;0;0m'
COLOR_BLACK = '\033[0;30;40m'
COLOR_RED = '\033[0;31;40m'
COLOR_GREEN = '\033[0;32;40m'
COLOR_YELLOW = '\033[0;33;40m'
COLOR_BLUE = '\033[0;34;40m'
COLOR_MAGENTA = '\033[0;35;40m'
COLOR_CYAN = '\033[0;36;40m'
COLOR_WHITE = '\033[0;37;40m'
COLOR_LIGHT_BLACK = '\033[1;30;40m'
COLOR_LIGHT_RED = '\033[1;31;40m'
COLOR_LIGHT_GREEN = '\033[1;32;40m'
COLOR_LIGHT_YELLOW = '\033[1;33;40m'
COLOR_LIGHT_BLUE = '\033[1;34;40m'
COLOR_LIGHT_MAGENTA = '\033[1;35;40m'
COLOR_LIGHT_CYAN = '\033[1;36;40m'
COLOR_LIGHT_WHITE = '\033[1;37;40m'
COLORS_AVAILABLE = [
	COLOR_NORMAL, COLOR_BLACK, COLOR_RED, COLOR_GREEN, COLOR_YELLOW,
	COLOR_BLUE, COLOR_MAGENTA, COLOR_CYAN, COLOR_WHITE, COLOR_LIGHT_BLACK,
	COLOR_LIGHT_RED, COLOR_LIGHT_GREEN, COLOR_LIGHT_YELLOW, COLOR_LIGHT_BLUE,
	COLOR_LIGHT_MAGENTA, COLOR_LIGHT_CYAN, COLOR_LIGHT_WHITE
]

backend = None  # pylint: disable=invalid-name
exitZero = False  # pylint: disable=invalid-name
global_shell = None  # pylint: disable=invalid-name
logFile = None  # pylint: disable=invalid-name
interactive = False  # pylint: disable=invalid-name

outEncoding = sys.stdout.encoding  # pylint: disable=invalid-name
inEncoding = sys.stdin.encoding  # pylint: disable=invalid-name
if not outEncoding or outEncoding == 'ascii':
	outEncoding = locale.getpreferredencoding()
if not outEncoding or outEncoding == 'ascii':
	outEncoding = 'utf-8'  # pylint: disable=invalid-name

if not inEncoding or (inEncoding == 'ascii'):
	inEncoding = outEncoding

UNCOLORED_LOGO = """\
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
          opsi-admin {version}
""".format(version=__version__).split('\n')

LOGO = [{"color": COLOR_CYAN, "text": line} for line in UNCOLORED_LOGO]

try:
	sp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	if os.path.exists(os.path.join(sp, "site-packages")):
		sp = os.path.join(sp, "site-packages")
	sp = os.path.join(sp, 'opsi-utils_data', 'locale')
	translation = gettext.translation('opsi-utils', sp)
	_ = translation.gettext
except Exception as err:  # pylint: disable=broad-except
	logger.debug("Failed to load locale from %s: %s", sp, err)

	def _(string):
		""" Fallback function """
		return string


class ErrorInResultException(Exception):
	"Indicates that there is an error in the result."


def signalHandler(signo, stackFrame):  # pylint: disable=unused-argument
	from signal import SIGINT, SIGQUIT  # pylint: disable=import-outside-toplevel
	logger.info("Received signal %s", signo)
	if signo == SIGINT:
		if global_shell:
			global_shell.sigint()
	elif signo == SIGQUIT:
		sys.exit(0)


def shell_main():  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
	os.umask(0o077)
	global interactive  # pylint: disable=global-statement,invalid-name
	global exitZero  # pylint: disable=global-statement,invalid-name
	global logFile  # pylint: disable=global-statement,invalid-name

	try:
		username = forceUnicode(pwd.getpwuid(os.getuid())[0])
	except Exception:  # pylint: disable=broad-except
		username = ''

	parser = argparse.ArgumentParser()
	parser.add_argument('--version', '-V', action='version',
						version=f"{__version__} [python-opsi={python_opsi_version}]", help=_("Show version and exit"))
	parser.add_argument('--log-level', '-l', dest="logLevel", default=LOG_WARNING,
						type=int, choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
						help=_("Set log level (default: 3)"))
	parser.add_argument("--log-file", metavar='FILE', dest="logFile",
						help=_("Path to log file"))
	parser.add_argument('--address', '-a', default='https://localhost:4447/rpc',
						help=_("URL of opsiconfd (default: https://localhost:4447/rpc)"))
	parser.add_argument('--username', '-u', default=username,
						help=_("Username (default: current user)"))
	parser.add_argument('--password', '-p',
						help=_("Password (default: prompt for password)"))
	parser.add_argument('--opsirc', default=getOpsircPath(),
						help=(
							_("Path to the opsirc file to use (default: ~/.opsi.org/opsirc)") +
							_("An opsirc file contains login credentials to the web API.")
						))
	parser.add_argument('--direct', '-d', action='store_true',
						help=_("Do not use opsiconfd"))
	parser.add_argument('--no-depot', dest="depot",
						action="store_false", default=True,
						help=_("Do not use depotserver backend"))
	parser.add_argument('--interactive', '-i', action="store_true",
						help=_("Start in interactive mode"))
	parser.add_argument('--exit-zero', dest="exitZero", action='store_true',
						help=_("Always exit with exit code 0."))

	outputGroup = parser.add_argument_group(title=_("Output"))
	outputGroup.add_argument('--colorize', '-c', action="store_true",
						help=_("Colorize output"))

	outputFormat = outputGroup.add_mutually_exclusive_group()
	outputFormat.add_argument(
		'--simple-output', '-S', dest='output', const='SIMPLE', action='store_const',
		help=_("Simple output (only for scalars, lists)"))
	outputFormat.add_argument(
		'--shell-output', '-s', dest='output', const='SHELL', action='store_const',
		help=_("Shell output"))
	outputFormat.add_argument(
		'--raw-output', '-r', dest='output', const='RAW', action='store_const',
		help=_("Raw output"))

	parser.add_argument('command', nargs=argparse.REMAINDER,
						help=_("Command to execute."))

	options = parser.parse_args()

	username = options.username
	password = options.password
	opsircfile = options.opsirc
	address = options.address
	direct = options.direct
	depotBackend = options.depot
	interactive = options.interactive
	color = options.colorize
	output = options.output or 'JSON'
	exitZero = options.exitZero

	if options.logFile:
		logFile = forceFilename(options.logFile)
		startLogFile(logFile, options.logLevel)

	logging_config(stderr_level = LOG_NONE if interactive else options.logLevel, stderr_format=DEFAULT_COLORED_FORMAT)

	global backend  # pylint: disable=global-statement,invalid-name
	try:
		if direct:
			# Create BackendManager
			backend = BackendManager(
				dispatchConfigFile='/etc/opsi/backendManager/dispatch.conf',
				backendConfigDir='/etc/opsi/backends',
				extensionConfigDir='/etc/opsi/backendManager/extend.d',
				depotBackend=depotBackend,
				hostControlBackend=True,
				hostControlSafeBackend=True
			)

		else:
			# Reading opsirc file.
			# We should alway prefer the settings from the commandline
			# in case an value is to be overridden.
			opsircConfig = readOpsirc(opsircfile)

			try:
				password = password or opsircConfig['password']
			except KeyError:
				pass

			try:
				username = username or opsircConfig['username']
			except KeyError:
				try:
					username = forceUnicode(pwd.getpwuid(os.getuid())[0])
				except Exception:  # pylint: disable=broad-except
					username = ''

			try:
				address = address or opsircConfig['address']
			except KeyError:
				address = 'https://localhost:4447/rpc'

			# Connect to opsiconfd
			if not password:
				try:
					password = getpass.getpass()
				except Exception:  # pylint: disable=broad-except
					pass

			sessionId = None
			sessionFile = None
			home = os.environ.get('HOME')
			if home:
				opsiadminUserDir = forceFilename(os.path.join(home, '.opsi.org'))
				if not os.path.exists(opsiadminUserDir):
					try:
						os.mkdir(opsiadminUserDir)
					except OSError as error:
						logger.info("Could not create %s.", opsiadminUserDir)

				sessionFile = os.path.join(opsiadminUserDir, 'session')
				try:
					with codecs.open(sessionFile, 'r', 'utf-8') as session:
						for line in session:
							line = line.strip()
							if line:
								sessionId = forceUnicode(line)
								break
				except IOError as err:
					if err.errno != 2:  # 2 is No such file or directory
						logger.error("Failed to read session file '%s': %s", sessionFile, err)
				except Exception as err:  # pylint: disable=broad-except
					logger.error("Failed to read session file '%s': %s", sessionFile, err)

			from OPSI.Backend.JSONRPC import JSONRPCBackend  # pylint: disable=import-outside-toplevel
			backend = JSONRPCBackend(
				address=address,
				username=username,
				password=password,
				application='opsi-admin/%s' % __version__,
				sessionId=sessionId,
				compression=True
			)
			logger.info('Connected')

			sessionId = backend.jsonrpc_getSessionId()
			if sessionId and sessionFile:
				try:
					with codecs.open(sessionFile, 'w', 'utf-8') as session:
						session.write(f"{sessionId}\n" % sessionId)
				except Exception as err:  # pylint: disable=broad-except
					logger.error("Failed to write session file '%s': %s", sessionFile, err)

		cmdline = ''
		for i, argument in enumerate(options.command, start=0):
			logger.info("arg[%d]: %s", i, argument)
			if i == 0:
				cmdline = argument
			elif ' ' in argument or len(argument) == 0:
				cmdline = "{cmdline} '{argument}'"
			else:
				cmdline = "{cmdline} {argument}"

		if not sys.stdin.isatty():
			read = sys.stdin.read().replace('\r', '').replace('\n', '')
			if read:
				logger.trace("Read %s from stdin", read)
				cmdline = f"{cmdline} '{read}'"

		logger.debug("cmdline: '%s'", cmdline)

		global global_shell  # pylint: disable=global-statement,invalid-name
		if interactive:
			try:
				logger.notice("Starting interactive mode")
				global_shell = Shell(prompt='%s@opsi-admin>' % username, output=output, color=color, cmdline=cmdline)
				global_shell.setInfoline("Connected to %s" % address)

				for line in LOGO:
					global_shell.appendLine(line.get('text'), line.get('color'))

				welcomeMessage = """\
Welcome to the interactive mode of opsi-admin.
You can use syntax completion via [TAB]. \
To exit opsi-admin please type 'exit'."""

				for line in welcomeMessage.split('\n'):
					global_shell.appendLine(line, COLOR_NORMAL)
				global_shell.run()
			except Exception as error:
				logger.error(error, exc_info=True)
				raise
		elif cmdline:
			def searchForError(obj):
				if isinstance(obj, dict):
					try:
						if obj.get('error'):
							raise ErrorInResultException(obj['error'])
					except KeyError:
						for key in obj:
							searchForError(obj[key])
				elif isinstance(obj, list):
					for element in obj:
						searchForError(element)

			try:
				global_shell = Shell(prompt='%s@opsi-admin>' % username, output=output, color=color)
				for cmd in cmdline.split('\n'):
					if cmd:
						global_shell.cmdline = cmd
						global_shell.execute()

				logger.debug("Shell lines are: '%s'", global_shell.getLines())
				for line in global_shell.lines:
					print(line['text'].rstrip())

				try:
					resultAsJSON = json.loads('\n'.join([line['text'] for line in global_shell.lines]))
					searchForError(dict(resultAsJSON))
				except (TypeError, ValueError) as error:
					logger.trace("Conversion to dict failed: %s", error)
			except Exception as err:
				logger.error(err, exc_info=True)
				raise err
		else:
			raise RuntimeError("Not running in interactive mode and no commandline arguments given.")
	finally:
		if backend:
			try:
				backend.backend_exit()
			except Exception:  # pylint: disable=broad-except
				pass


def startLogFile(log_file, logLevel):
	with codecs.open(log_file, 'w', 'utf-8') as log:
		log.write(f"Starting log at: {time.strftime('%a, %d %b %Y %H:%M:%S')}")
	logging_config(log_file=log_file, file_level=logLevel)

class Shell:  # pylint: disable=too-many-instance-attributes

	def __init__(self, prompt='opsi-admin>', output='JSON', color=True, cmdline=''):
		self.color = forceBool(color)
		self.output = forceUnicode(output)
		self.running = False
		self.screen = None
		self.cmdBufferSize = 1024
		self.userConfigDir = None
		self.prompt = forceUnicode(prompt)
		self.infoline = 'opsi admin started'
		self.yMax = 0
		self.xMax = 0
		self.pos = len(cmdline)
		self.lines = []
		self.linesBack = 0
		self.linesMax = 0
		self.params = []
		self.paramPos = -1
		self.currentParam = None
		self.cmdListPos = 0
		self.cmdList = []
		self.cmdline = forceUnicode(cmdline)
		self.shellCommand = ''
		self.reverseSearch = None
		self.commands = [
			CommandMethod(),
			CommandSet(),
			CommandHelp(),
			CommandQuit(),
			CommandExit(),
			CommandHistory(),
			CommandLog(),
			CommandTask()
		]

		home = os.environ.get('HOME')
		if not home:
			logger.debug('Environment has no $HOME set.')
			home = os.path.expanduser('~')

		if home:
			self.userConfigDir = forceFilename(os.path.join(home, '.opsi.org'))
			if not os.path.isdir(self.userConfigDir):
				try:
					os.mkdir(self.userConfigDir)
				except OSError as error:
					logger.error("Failed to create user dir '%s': %s", self.userConfigDir, error)
		else:
			logger.error('Failed to get home directory from environment!')

		historyFile = forceFilename(os.path.join(self.userConfigDir, 'history'))
		try:
			with codecs.open(historyFile, 'r', 'utf-8', 'replace') as history:
				for line in history:
					if not line:
						continue
					self.cmdList.append(line.strip())
					self.cmdListPos += 1
		except FileNotFoundError:
			logger.debug("History %s file not found.", historyFile)
		except Exception as err:  # pylint: disable=broad-except
			logger.error("Failed to read history file '%s': %s", historyFile, err)

	def setColor(self, color):
		color = forceBool(color)
		if color != self.color:
			self.color = color
			self.initScreen()

	def getLines(self):
		return self.lines

	def initScreen(self):
		if not self.screen:
			try:
				self.screen = curses.initscr()
			except Exception:  # pylint: disable=broad-except
				# setupterm: could not find terminal
				os.environ["TERM"] = "linux"
				self.screen = curses.initscr()
		curses.noecho()
		curses.cbreak()
		self.screen.keypad(1)
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

	def exitScreen(self):
		if self.screen:
			curses.nocbreak()
			self.screen.keypad(0)
			curses.echo()
			curses.endwin()

	def sigint(self):
		self.pos = 0
		self.setCmdline('')
		self.reverseSearch = None

	def run(self):
		self.running = True

		self.initScreen()

		if self.cmdline:
			for cmd in self.cmdline.split('\n'):
				self.cmdline = cmd
				self.appendLine("%s %s" % (self.prompt, self.cmdline))
				if self.cmdline:
					try:
						self.execute()
					except Exception as err:  # pylint: disable=broad-except
						lines = str(err).split('\n')
						lines[0] = f"ERROR: {lines[0]}"
						for line in lines:
							self.appendLine(line, COLOR_RED)

				if len(self.lines) > self.yMax - 2:
					self.linesBack = 0
		else:
			self.display()

		while self.running:
			self.getCommand()

	def exit(self):
		if interactive and logFile and os.path.exists(logFile):
			if self.question(_("Delete log-file '%s'?") % logFile):
				try:
					os.unlink(logFile)
				except OSError as err:
					logger.error("Failed to delete log-file '%s': %s", logFile, err)

		historyFilePath = os.path.join(self.userConfigDir, 'history')
		try:
			with codecs.open(historyFilePath, 'w', 'utf-8') as history:
				for line in self.cmdList:
					if not line or line in ('quit', 'exit'):
						continue
					history.write("%s\n" % line)
		except Exception as err:  # pylint: disable=broad-except
			logger.error("Failed to write history file '%s': %s", historyFilePath, err)

		self.exitScreen()
		self.running = False

	def bell(self):  # pylint: disable=no-self-use
		sys.stderr.write('\a')

	def display(self):  # pylint: disable=too-many-branches,too-many-statements
		if not self.screen:
			return
		self.screen.move(0, 0)
		self.screen.clrtoeol()
		shellLine = self.infoline + (self.xMax - len(self.infoline)) * ' '
		try:
			self.screen.addstr(shellLine, curses.A_REVERSE)
		except Exception as err:  # pylint: disable=broad-except
			logger.error("Failed to add string '%s': %s", shellLine, err)

		height = int(len(self.prompt + ' ' + self.cmdline) / self.xMax) + 1
		clear = self.xMax - (len(self.prompt + ' ' + self.cmdline) % self.xMax) - 1

		self.linesMax = self.yMax - height - 1
		self.screen.move(self.yMax - height, 0)
		self.screen.clrtoeol()
		shellLine = "%s %s%s" % (self.prompt, self.cmdline, ' ' * clear)
		try:
			self.screen.addstr(shellLine, curses.A_BOLD)
		except Exception as err:  # pylint: disable=broad-except
			logger.error("Failed to add string '%s': %s", shellLine, err)

		for i in range(0, self.linesMax):
			self.screen.move(self.linesMax - i, 0)
			self.screen.clrtoeol()
			shellLine = ''
			color = None
			if len(self.lines) - self.linesBack > i:
				shellLine = self.lines[len(self.lines) - self.linesBack - 1 - i]['text']
				if self.color:
					color = self.lines[len(self.lines) - self.linesBack - 1 - i]['color']

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
				if color is not None:
					self.screen.addstr(shellLine, color)
				else:
					self.screen.addstr(shellLine)
			except Exception as err:  # pylint: disable=broad-except
				logger.error("Failed to add string '%s': %s", shellLine, err)

		moveY = self.yMax - height + int((len(self.prompt + ' ') + self.pos) / self.xMax)
		moveX = ((len(self.prompt + ' ') + self.pos) % self.xMax)
		self.screen.move(moveY, moveX)
		self.screen.refresh()

	def appendLine(self, line, color=None, refresh=True):
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
			line = line.replace(availableColor, '')

		while self.xMax and (len(line) > self.xMax):
			self.lines.append({"text": line[:self.xMax], "color": color})
			line = line[self.xMax:]

		self.lines.append({"text": line, "color": color})
		if refresh:
			self.display()

	def setCmdline(self, cmdline, refresh=True):
		self.cmdline = forceUnicode(cmdline)
		if refresh:
			self.display()

	def setInfoline(self, infoline, refresh=True):
		self.infoline = forceUnicode(infoline)
		if refresh:
			self.display()

	def getParams(self):
		self.parseCmdline()
		return self.params

	def getParam(self, i):
		self.parseCmdline()
		if len(self.params) > i:
			return self.params[i]
		return ''

	def parseCmdline(self):  # pylint: disable=too-many-branches,too-many-statements
		self.params = []
		self.currentParam = None
		self.paramPos = -1

		if not self.cmdline:
			return

		self.shellCommand = ''
		cmdline = self.cmdline
		if '|' in cmdline:
			quoteCount = 0
			doubleQuoteCount = 0
			parts = cmdline.split('|')
			for i, part in enumerate(parts):
				quoteCount += part.count("'")
				doubleQuoteCount += part.count('"')
				if (quoteCount % 2 == 0) and (doubleQuoteCount % 2 == 0):
					cmdline = '|'.join(parts[:i + 1])
					self.shellCommand = '|'.join(parts[i + 1:]).lstrip()
					break

		cur = 0
		quote = None
		for i, element in enumerate(cmdline):
			logger.trace(
				"parseCmdline(): char '%s', quote: '%s', cur: '%s', params: '%s'",
				element, quote, cur, self.params
			)
			if len(self.params) < cur + 1:
				self.params.append('')

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
					self.params[cur] += '\''
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

		if not quote and self.params and self.params[-1] and self.pos == len(cmdline) and cmdline.endswith(' '):
			self.params.append('')
			self.paramPos += 1

		if self.params:
			self.currentParam = self.params[self.paramPos]
		else:
			self.currentParam = ''

		logger.debug("cmdline: '%s'", cmdline)
		logger.trace(
			"paramPos %s, currentParam: '%s', params: '%s'",
			self.paramPos, self.currentParam, self.params
		)

		if self.paramPos >= len(self.params):
			logger.error(
				"Assertion 'self.paramPos < len(self.params)' failed: "
				"self.paramPos: %s, len(self.params): %s",
				self.paramPos,
				len(self.params)
			)
			self.paramPos = len(self.params) - 1

	def execute(self):
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

	def question(self, question):
		question = forceUnicode(question)
		if interactive:
			self.screen.move(self.yMax - 1, 0)
			self.screen.clrtoeol()
			self.screen.addstr(question + ' (n/y)')
			self.screen.refresh()
			char = None
			while True:
				char = self.screen.getch()
				if char and 256 > char >= 0 and char != 10:
					if chr(char) == 'y':
						return True
					if chr(char) == 'n':
						return False
		return False

	def getPassword(self):
		password1 = ''
		password2 = ''
		while not password1 or (password1 != password2):
			if interactive:
				self.screen.move(self.yMax - 1, 0)
				self.screen.clrtoeol()
				self.screen.addstr(_("Please type password:"))
				self.screen.refresh()
				password1 = self.screen.getstr()

				self.screen.move(self.yMax - 1, 0)
				self.screen.clrtoeol()
				self.screen.addstr(_("Please retype password:"))
				self.screen.refresh()
				password2 = self.screen.getstr()

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

	def getCommand(self):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
		char = None
		self.pos = 0
		self.setCmdline('')
		self.reverseSearch = None

		while not char or (char != 10):  # pylint: disable=too-many-nested-blocks
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
						self.setCmdline('')
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
					self.reverseSearch = ''
				else:
					self.setInfoline("")
					self.reverseSearch = None
				continue

			elif char == 9:
				# tab 		|<- ->|
				# Auto-completion
				completions = []

				params = self.getParams()
				if self.paramPos >= 0:
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
					self.setCmdline(
						self.cmdline[:self.pos] +
						completions[0][len(params[self.paramPos]):] +
						self.cmdline[self.pos:]
					)
					self.pos += len(completions[0][len(params[self.paramPos]):])

					if self.pos == len(self.cmdline):
						self.cmdline += ' '
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

					curLine = ''
					i = 0
					while i < len(completions):
						while (i < len(completions)) and (not curLine or (len(curLine) + longest < self.xMax - 5)):
							pf = '%s %-' + str(longest) + 's'
							curLine = pf % (curLine, completions[i])
							i += 1
						lines.append({"text": curLine, "color": None})
						curLine = ''

					if self.paramPos < 0:
						self.currentParam = ""

					text = (
						"%s %s%s%s" % (
							self.prompt,
							self.cmdline[:self.pos - len(self.currentParam)],
							match.strip(),
							self.cmdline[self.pos:]
						)
					)
					self.lines.append({"text": text, "color": None})

					self.lines.extend(lines)

					self.setCmdline(self.cmdline[:self.pos - len(self.currentParam)] + match.strip() + self.cmdline[self.pos:])

					self.pos += len(match) - len(self.currentParam)

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
						self.setInfoline('reverse-i-search: %s' % self.reverseSearch)
					elif self.pos > 0:
						newPos = self.pos - 1
						newCmdline = self.cmdline[:newPos] + self.cmdline[self.pos:]
				elif char == 330:
					# del
					if self.reverseSearch is not None:
						pass
					elif len(self.cmdline) > 0:
						newCmdline = self.cmdline[:self.pos] + self.cmdline[self.pos + 1:]

				else:
					try:
						curses.ungetch(char)
						char = self.screen.getkey()
						logger.trace("Current char: %r", char)

						if not isinstance(char, str):
							try:
								char = str(char)
							except Exception:  # pylint: disable=broad-except
								char += self.screen.getkey()
								char = str(char)

						if self.reverseSearch is not None:
							self.reverseSearch += char
						else:
							newPos = self.pos + 1
							newCmdline = self.cmdline[0:self.pos] + char + self.cmdline[self.pos:]
					except Exception as err:  # pylint: disable=broad-except
						logger.error("Failed to add char %r: %s", char, err)

				try:
					if self.reverseSearch is not None:
						self.setInfoline('reverse-i-search: %s' % self.reverseSearch)
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
				except Exception as err:  # pylint: disable=broad-except
					self.setInfoline(str(err))

			if not textInput:
				if self.reverseSearch is not None:
					self.reverseSearch = None
					self.setInfoline("")

		self.cmdline = self.cmdline.strip()

		self.appendLine(self.prompt + ' ' + self.cmdline)
		if self.cmdline:
			try:
				self.execute()
			except Exception as err:  # pylint: disable=broad-except
				lines = str(err).split('\n')
				lines[0] = f"ERROR: {lines[0]}"
				for line in lines:
					self.appendLine(line, COLOR_RED)

		if len(self.lines) > self.yMax - 2:
			self.linesBack = 0


class Command:
	def __init__(self, name):
		self.name = forceUnicode(name)

	def getName(self):
		return self.name

	def getDescription(self):  # pylint: disable=no-self-use
		return ""

	def completion(self, params, paramPos):  # pylint: disable=unused-argument,no-self-use
		return []

	def help(self, shell):  # pylint: disable=unused-argument,redefined-outer-name,no-self-use
		shell.appendLine("")

	def execute(self, shell, params):  # pylint: disable=unused-argument,redefined-outer-name,no-self-use
		raise NotImplementedError("Nothing to do.")


class CommandMethod(Command):
	def __init__(self):
		Command.__init__(self, 'method')
		self.interface = backend.backend_getInterface()

	def getDescription(self):
		return _("Execute a config-interface-method")

	def help(self, shell):  # pylint: disable=redefined-outer-name
		shell.appendLine("\r{0}\n".format(_("Methods are:")))
		for method in backend.backend_getInterface():
			logger.debug(method)
			shell.appendLine("\r%s\n" % method.get('name'))

	def completion(self, params, paramPos):
		completions = []

		if paramPos == 0:
			completions.append('list')
			for param in self.interface:
				completions.append(param.get('name'))

		elif paramPos == 1:
			if 'list'.startswith(params[0]):
				completions.append('list')
			for param in self.interface:
				if param.get('name').startswith(params[0]):
					completions.append(param.get('name'))

		elif paramPos >= 2:
			for param in self.interface:
				if param.get('name') == params[0]:
					if len(param.get('params')) >= len(params) - 1:
						completions = [param.get('params')[paramPos - 2]]
					break

		return completions

	def execute(self, shell, params):  # pylint: disable=too-many-statements,redefined-outer-name,too-many-locals,too-many-branches
		if len(params) <= 0:
			shell.appendLine(_('No method defined'))
			return

		methodName = params[0]

		if methodName == 'list':
			for methodDescription in self.interface:
				shell.appendLine("%s%s" % (methodDescription.get('name'), tuple(methodDescription.get('params'))), refresh=False)
			shell.display()
			return

		for methodDescription in self.interface:
			if methodName == methodDescription['name']:
				methodInterface = methodDescription
				break
		else:
			raise OpsiRpcError("Method '%s' is not valid" % methodName)

		params = params[1:]
		keywords = {}
		if methodInterface['keywords']:
			parameters = 0
			if methodInterface['args']:
				parameters += len(methodInterface['args'])
			if methodInterface['varargs']:
				parameters += len(methodInterface['varargs'])

			if len(params) >= parameters:
				# Do not create Object instances!
				params[-1] = fromJson(params[-1], preventObjectCreation=True)
				if not isinstance(params[-1], dict):
					raise ValueError("kwargs param is not a dict: %s" % params[-1])

				for (key, value) in params.pop(-1).items():
					keywords[str(key)] = deserialize(value)

		def createObjectOrString(obj):
			"Tries to return object from JSON. If this fails returns unicode."
			try:
				return fromJson(obj)
			except Exception as err:  # pylint: disable=broad-except
				logger.debug("Not a json string '%s': %s", obj, err)
				return forceUnicode(obj)

		params = [createObjectOrString(item) for item in params]

		pString = str(params)[1:-1]
		if keywords:
			pString += ', ' + str(keywords)
		if len(pString) > 200:
			pString = pString[:200] + '...'

		result = None

		logger.info("Executing:  %s(%s)", methodName, pString)
		shell.setInfoline("Executing:  %s(%s)" % (methodName, pString))
		start = time.time()

		method = getattr(backend, methodName)
		if keywords:
			result = method(*params, **keywords)
		else:
			result = method(*params)

		duration = time.time() - start
		logger.debug('Took %0.3f seconds to process: %s(%s)', duration, methodName, pString)
		shell.setInfoline(_('Took %0.3f seconds to process: %s(%s)') % (duration, methodName, pString))
		result = serialize(result)
		logger.trace("Serialized result: '%s'", result)

		if result is not None:  # pylint: disable=too-many-nested-blocks
			lines = []
			if shell.output == 'RAW':
				lines.append(toJson(result))

			elif shell.output == 'JSON':
				lines = objectToBeautifiedText(result).split('\n')

			elif shell.output == 'SHELL':
				bashVars = objectToBash(result, {})
				for index in range(len(bashVars) - 1, -2, -1):
					if index == -1:
						index = ''

					value = bashVars.get('RESULT%s' % index)
					if value:
						lines.append('RESULT%s=%s' % (index, value))

			elif shell.output == 'SIMPLE':
				if isinstance(result, dict):
					for (key, value) in result.items():
						if isinstance(value, bool):
							value = forceUnicodeLower(value)
						lines.append('%s=%s' % (key, value))
				elif isinstance(result, (tuple, list, set)):
					for resultElement in result:
						if isinstance(resultElement, dict):
							for (key, value) in resultElement.items():
								if isinstance(value, bool):
									value = forceUnicodeLower(value)
								lines.append('%s=%s' % (key, value))
							lines.append('')
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

				proc = subprocess.Popen(  # pylint: disable=consider-using-with
					shell.shellCommand,
					shell=True,
					stdin=subprocess.PIPE,
					stdout=subprocess.PIPE,
					stderr=subprocess.PIPE,
				)

				flags = fcntl.fcntl(proc.stdout, fcntl.F_GETFL)
				fcntl.fcntl(proc.stdout, fcntl.F_SETFL, flags | os.O_NONBLOCK)

				flags = fcntl.fcntl(proc.stderr, fcntl.F_GETFL)
				fcntl.fcntl(proc.stderr, fcntl.F_SETFL, flags | os.O_NONBLOCK)

				encoding = proc.stdout.encoding or inEncoding

				exitCode = None
				buf = ''
				serr = ''

				while exitCode is None:
					exitCode = proc.poll()
					if lines:
						for line in lines:
							proc.stdin.write(line.encode(outEncoding, 'replace'))
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
					raise Exception(
						f"Exitcode: {exitCode * -1}{nwl}{serr.decode(encoding, 'replace')}"
					)

				lines = buf.decode(encoding, 'replace').split('\n')

			for line in lines:
				shell.appendLine(line, COLOR_GREEN)


class CommandSet(Command):
	def __init__(self):
		Command.__init__(self, 'set')

	def getDescription(self):
		return _("Settings")

	def completion(self, params, paramPos):
		completions = []

		if paramPos == 0 or not params[0]:
			completions = ['color', 'log-file', 'log-level']

		elif paramPos == 1:
			if 'color'.startswith(params[0]):
				completions = ['color']
			if 'log-file'.startswith(params[0]):
				completions = ['log-file']
			if 'log-level'.startswith(params[0]):
				completions.append('log-level')

		elif paramPos == 2:
			if params[0] == 'color':
				completions = ['on', 'off']
			elif params[0] == 'log-file':
				completions = ['<filename>', 'off']
			elif params[0] == 'log-level':
				completions = ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']

		return completions

	def execute(self, shell, params):
		global logFile  # pylint: disable=global-statement,invalid-name

		if len(params) <= 0:
			raise ValueError(_('Missing option'))
		if params[0] not in ('color', 'log-file', 'log-level'):
			raise ValueError(_('Unknown option: %s') % params[0])
		if len(params) <= 1:
			raise ValueError(_('Missing value'))

		if params[0] == 'color':
			if params[1] == 'on':
				shell.setColor(True)
			elif params[1] == 'off':
				shell.setColor(False)
			else:
				raise ValueError(_('Bad value: %s') % params[1])

		elif params[0] == 'log-file':
			if params[1] == 'off':
				logging_config(file_level = LOG_NONE)
			else:
				logFile = params[1]
				startLogFile(logFile, LOG_DEBUG)

		elif params[0] == 'log-level':
			if not logFile:
				raise ValueError(_('No log-file set!'))
			logging_config(file_level = int(params[1]))


class CommandHelp(Command):
	def __init__(self):
		Command.__init__(self, 'help')

	def getDescription(self):
		return _("Show this text")

	def execute(self, shell, params):
		shell.appendLine('\r' + _("Commands are:") + '\n', refresh=False)
		for cmd in shell.commands:
			shell.appendLine("\r\t%-20s%s\n" % (cmd.getName() + ':', cmd.getDescription()), refresh=False)
		shell.display()


class CommandQuit(Command):
	def __init__(self):
		Command.__init__(self, 'quit')

	def getDescription(self):
		return _("Exit opsi-admin")

	def execute(self, shell, params):
		shell.exit()


class CommandExit(CommandQuit):
	def __init__(self):  # pylint: disable=super-init-not-called
		Command.__init__(self, 'exit')  # pylint: disable=non-parent-init-called


class CommandHistory(Command):
	def __init__(self):
		Command.__init__(self, 'history')

	def getDescription(self):
		return _("show / clear command history")

	def completion(self, params, paramPos):
		completions = []

		if paramPos == 0 or not params[0]:
			completions = ['clear', 'show']

		elif paramPos == 1:
			if 'clear'.startswith(params[0]):
				completions = ['clear']
			elif 'show'.startswith(params[0]):
				completions = ['show']

		return completions

	def execute(self, shell, params):
		if len(params) <= 0:
			# By default: show history
			params = ['show']
		elif params[0] not in ('clear', 'show'):
			raise ValueError(_('Unknown command: %s') % params[0])

		if params[0] == 'show':
			for line in shell.cmdList:
				shell.appendLine(line, refresh=False)
			shell.display()
		elif params[0] == 'clear':
			shell.cmdList = []
			shell.cmdListPos = -1


class CommandLog(Command):
	def __init__(self):
		Command.__init__(self, 'log')

	def getDescription(self):
		return _("show log")

	def completion(self, params, paramPos):
		completions = []

		if paramPos == 0 or not params[0]:
			completions = ['show']

		elif paramPos == 1:
			if 'show'.startswith(params[0]):
				completions = ['show']

		return completions

	def execute(self, shell, params):
		if len(params) <= 0:
			# By default: show log
			params = ['show']
		elif params[0] not in ('show',):
			raise ValueError(_('Unknown command: %s') % params[0])

		if params[0] == 'show':
			if not logFile:
				raise RuntimeError(_('File logging is not activated'))

			with open(logFile) as log:
				for line in log:
					shell.appendLine(line, refresh=False)
			shell.display()


class CommandTask(Command):
	def __init__(self):
		Command.__init__(self, 'task')
		self._tasks = (
			('setupWhereInstalled', 'productId'),
			('setupWhereNotInstalled', 'productId'),
			('updateWhereInstalled', 'productId'),
			('uninstallWhereInstalled', 'productId'),
			('setActionRequestWhereOutdated', 'actionRequest', 'productId'),
			('setActionRequestWhereOutdatedWithDependencies', 'actionRequest', 'productId'),
			('setActionRequestWithDependencies', 'actionRequest', 'productId', 'clientId'),
			('decodePcpatchPassword', 'encodedPassword', 'opsiHostKey'),
			('setPcpatchPassword', '*password')
		)

	def getDescription(self):
		return _("execute a task")

	def help(self, shell):
		shell.appendLine('')

	def completion(self, params, paramPos):
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

	def execute(self, shell, params):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
		tasknames = {[task[0] for task in self._tasks]}

		if len(params) <= 0:
			return

		if params[0] not in tasknames:
			raise ValueError(_('Unknown task: %s') % params[0])

		if params[0] == 'setupWhereInstalled':
			if len(params) < 2:
				raise ValueError(_('Missing product-id'))
			productId = params[1]

			logger.warning(
				"The task 'setupWhereInstalled' is obsolete. "
				"Please use 'method setupWhereInstalled' instead."
			)

			for clientId in backend.setupWhereInstalled(productId):  # pylint: disable=no-member
				shell.appendLine(clientId)

		elif params[0] == 'setupWhereNotInstalled':
			if len(params) < 2:
				raise ValueError(_('Missing product-id'))
			productId = params[1]

			logger.warning(
				"The task 'setupWhereNotInstalled' is obsolete. "
				"Please use 'method setupWhereNotInstalled' instead."
			)

			for clientId in backend.setupWhereNotInstalled(productId):  # pylint: disable=no-member
				shell.appendLine(clientId)

		elif params[0] == 'updateWhereInstalled':
			if len(params) < 2:
				raise ValueError(_('Missing product-id'))
			productId = params[1]

			logger.warning(
				"The task 'updateWhereInstalled' is obsolete. "
				"Please use 'method updateWhereInstalled' instead."
			)

			for clientId in backend.updateWhereInstalled(productId):  # pylint: disable=no-member
				shell.appendLine(clientId)

		elif params[0] == 'uninstallWhereInstalled':
			if len(params) < 2:
				raise ValueError(_('Missing product-id'))
			productId = params[1]

			logger.warning(
				"The task 'uninstallWhereInstalled' is obsolete. "
				"Please use 'method uninstallWhereInstalled' instead."
			)

			for clientId in backend.uninstallWhereInstalled(productId):  # pylint: disable=no-member
				shell.appendLine(clientId)

		elif params[0] == 'setActionRequestWhereOutdated':
			if len(params) < 2:
				raise ValueError(_('Missing action request'))
			if len(params) < 3:
				raise ValueError(_('Missing product-id'))

			actionRequest = params[1]
			productId = params[2]

			logger.warning(
				"The task 'setActionRequestWhereOutdated' is obsolete. "
				"Please use 'method setActionRequestWhereOutdated' instead."
			)

			for clientId in backend.setActionRequestWhereOutdated(actionRequest, productId):  # pylint: disable=no-member
				shell.appendLine(clientId)

		elif params[0] == 'setActionRequestWithDependencies':
			if len(params) < 2:
				raise ValueError(_('Missing action request'))
			if len(params) < 3:
				raise ValueError(_('Missing product-id'))
			if len(params) < 4:
				raise ValueError(_('Missing client-id'))
			actionRequest = params[1]
			productId = params[2]
			clientId = params[3]

			if productId and clientId and actionRequest:
				backend.setProductActionRequestWithDependencies(productId, clientId, actionRequest)  # pylint: disable=no-member

		elif params[0] == 'setActionRequestWhereOutdatedWithDependencies':
			if len(params) < 2:
				raise ValueError(_('Missing action request'))
			if len(params) < 3:
				raise ValueError(_('Missing product-id'))

			actionRequest = params[1]
			productId = params[2]

			logger.warning(
				"The task 'setActionRequestWhereOutdatedWithDependencies' "
				"is obsolete. Please use 'method "
				"setActionRequestWhereOutdatedWithDependencies' instead."
			)

			for clientId in backend.setActionRequestWhereOutdatedWithDependencies(actionRequest, productId):  # pylint: disable=no-member
				shell.appendLine(clientId)

		elif params[0] == 'decodePcpatchPassword':
			if len(params) < 3:
				raise ValueError(_('Missing argument'))
			crypt = params[1]
			key = params[2]
			cleartext = blowfishDecrypt(key, crypt)
			shell.appendLine(cleartext)

		elif params[0] == 'setPcpatchPassword':
			#if os.getuid() != 0:
			#	raise RuntimeError(_("You have to be root to change pcpatch password!"))

			fqdn = getfqdn(conf='/etc/opsi/global.conf')
			if fqdn.count('.') < 2:
				raise RuntimeError(_("Failed to get my own fully qualified domainname"))

			password = ''
			if len(params) < 2:
				password = shell.getPassword()
			else:
				password = params[1]

			if not password:
				raise ValueError("Can not use empty password!")
			secret_filter.add_secrets(password)

			backend.user_setCredentials(username='pcpatch', password=password)  # pylint: disable=no-member

			try:
				udm = which('univention-admin')
				# We are on Univention Corporate Server (UCS)
				dn = None
				command = '{udm} users/user list --filter "(uid=pcpatch)"'.format(udm=udm)
				logger.debug("Filtering for pcpatch: %s", command)
				with closing(os.popen(command, 'r')) as process:
					for line in process.readlines():
						if line.startswith('DN'):
							dn = line.strip().split(' ')[1]
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
			except Exception as err:  # pylint: disable=broad-except
				logger.debug("Setting password through smbldap failed: %s", err)

			if not password_set:
				# unix
				is_local_user = False
				with codecs.open("/etc/passwd", "r", "utf-8") as file:
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

def main():
	@contextmanager
	def shellExit():
		try:
			yield
		finally:
			try:
				global_shell.exit()
			except Exception:  # pylint: disable=broad-except
				pass

	try:
		locale.setlocale(locale.LC_ALL, '')
	except Exception:  # pylint: disable=broad-except
		pass

	if os.name == 'posix':
		from signal import signal, SIGINT, SIGQUIT  # pylint: disable=import-outside-toplevel
		signal(SIGINT, signalHandler)
		signal(SIGQUIT, signalHandler)

	try:
		with shellExit():
			shell_main()
		exitCode = 0
	except ErrorInResultException as error:
		logger.warning("Error in result: %s", error)
		exitCode = 2
	except Exception as err:  # pylint: disable=broad-except
		logging_config(stderr_level = LOG_ERROR)
		logger.error("Error during execution: %s", err, exc_info=True)
		exitCode = 1

	if exitZero:
		exitCode = 0

	sys.exit(exitCode)
