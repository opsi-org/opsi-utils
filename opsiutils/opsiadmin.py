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
opsi-admin - a commandline tool for accessing opsi.

:copyright: uib GmbH <info@uib.de>
:author: Jan Schneider <j.schneider@uib.de>
:author: Erol Ueluekmen <e.ueluekmen@uib.de>
:author: Niko Wenselowski <n.wenselowski@uib.de>
:license: GNU Affero General Public License version 3
"""

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
import select
import subprocess
import sys
import time
from contextlib import closing, contextmanager

from opsicommon.logging import logger, logging_config, secret_filter, LOG_NONE, LOG_ERROR, LOG_DEBUG, LOG_WARNING
from OPSI import __version__ as python_opsi_version
from OPSI.Backend.BackendManager import BackendManager
from OPSI.Exceptions import OpsiRpcError
from OPSI.System import CommandNotFoundException
from OPSI.System import which
from OPSI.Types import forceBool, forceFilename, forceUnicode, forceUnicodeLower
from OPSI.Util import (
	blowfishDecrypt, deserialize, fromJson, getfqdn,
	objectToBeautifiedText, objectToBash, serialize, toJson)
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

backend = None
exitZero = False
shell = None
logFile = None
interactive = False

outEncoding = sys.stdout.encoding
inEncoding = sys.stdin.encoding
if not outEncoding or (outEncoding == 'ascii'):
	outEncoding = locale.getpreferredencoding()
if not outEncoding or (outEncoding == 'ascii'):
	outEncoding = 'utf-8'

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
	translation = gettext.translation('opsi-utils', '/usr/share/locale')
	_ = translation.gettext
except Exception as error:
	logger.error("Failed to load locale: %s", error, exc_info=True)

	def _(string):
		return string


class ErrorInResultException(Exception):
	"Indicates that there is an error in the result."


def signalHandler(signo, stackFrame):
	from signal import SIGINT, SIGQUIT
	logger.info(u"Received signal %s", signo)
	if signo == SIGINT:
		if shell:
			shell.sigint()
	elif signo == SIGQUIT:
		sys.exit(0)


def shell_main(argv):
	os.umask(0o077)
	global interactive
	global exitZero
	global logFile

	try:
		username = forceUnicode(pwd.getpwuid(os.getuid())[0])
	except Exception:
		username = u''

	parser = argparse.ArgumentParser()
	parser.add_argument('--version', '-V', action='version',
						version=f"{__version__} [python-opsi={python_opsi_version}]", help=_("Show version and exit"))
	parser.add_argument('--log-level', '-l', dest="logLevel", default=LOG_WARNING,
						type=int, choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
						help=_(u"Set log level (default: 3)"))
	parser.add_argument("--log-file", metavar='FILE', dest="logFile",
						help=_(u"Path to log file"))
	parser.add_argument('--address', '-a', default='https://localhost:4447/rpc',
						help=_("URL of opsiconfd (default: https://localhost:4447/rpc)"))
	parser.add_argument('--username', '-u', default=username,
						help=_(u"Username (default: current user)"))
	parser.add_argument('--password', '-p',
						help=_(u"Password (default: prompt for password)"))
	parser.add_argument('--opsirc', default=getOpsircPath(),
						help=(
							_(u"Path to the opsirc file to use (default: ~/.opsi.org/opsirc)") +
							_(u"An opsirc file contains login credentials to the web API.")
						))
	parser.add_argument('--direct', '-d', action='store_true',
						help=_(u"Do not use opsiconfd"))
	parser.add_argument('--no-depot', dest="depot",
						action="store_false", default=True,
						help=_(u"Do not use depotserver backend"))
	parser.add_argument('--interactive', '-i', action="store_true",
						help=_(u"Start in interactive mode"))
	parser.add_argument('--exit-zero', dest="exitZero", action='store_true',
						help=_(u"Always exit with exit code 0."))

	outputGroup = parser.add_argument_group(title=_("Output"))
	outputGroup.add_argument('--colorize', '-c', action="store_true",
						help=_(u"Colorize output"))

	outputFormat = outputGroup.add_mutually_exclusive_group()
	outputFormat.add_argument(
		'--simple-output', '-S', dest='output', const='SIMPLE', action='store_const',
		help=_(u"Simple output (only for scalars, lists)"))
	outputFormat.add_argument(
		'--shell-output', '-s', dest='output', const='SHELL', action='store_const',
		help=_(u"Shell output"))
	outputFormat.add_argument(
		'--raw-output', '-r', dest='output', const='RAW', action='store_const',
		help=_(u"Raw output"))

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

	logging_config(stderr_level = LOG_NONE if interactive else options.logLevel)

	global backend
	try:
		if direct:
			# Create BackendManager
			backend = BackendManager(
				dispatchConfigFile=u'/etc/opsi/backendManager/dispatch.conf',
				backendConfigDir=u'/etc/opsi/backends',
				extensionConfigDir=u'/etc/opsi/backendManager/extend.d',
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
					import pwd
					username = forceUnicode(pwd.getpwuid(os.getuid())[0])
				except Exception:
					username = u''

			try:
				address = address or opsircConfig['address']
			except KeyError:
				address = u'https://localhost:4447/rpc'

			# Connect to opsiconfd
			if not password:
				try:
					password = getpass.getpass()
				except Exception:
					pass

			opsiadminUserDir = forceFilename(os.path.join(os.environ.get('HOME'), u'.opsi.org'))
			if not os.path.exists(opsiadminUserDir):
				try:
					os.mkdir(opsiadminUserDir)
				except OSError as error:
					logger.info("Could not create %s.", opsiadminUserDir)

			sessionId = None
			sessionFile = os.path.join(opsiadminUserDir, u'session')
			try:
				with codecs.open(sessionFile, 'r', 'utf-8') as session:
					for line in session:
						line = line.strip()
						if line:
							sessionId = forceUnicode(line)
							break
			except IOError as error:
				if error.errno != 2:  # 2 is No such file or directory
					logger.error("Failed to read session file '%s': %s", sessionFile, forceUnicode(error))
			except Exception as error:
				logger.error("Failed to read session file '%s': %s", sessionFile, forceUnicode(error))

			from OPSI.Backend.JSONRPC import JSONRPCBackend
			backend = JSONRPCBackend(
				address=address,
				username=username,
				password=password,
				application='opsi-admin/%s' % __version__,
				sessionId=sessionId
			)
			logger.info('Connected')

			sessionId = backend.jsonrpc_getSessionId()
			if sessionId:
				try:
					with codecs.open(sessionFile, 'w', 'utf-8') as session:
						session.write(u"%s\n" % sessionId)
				except Exception as error:
					logger.error("Failed to write session file '%s': %s", sessionFile, forceUnicode(error))

		cmdline = u''
		for i, argument in enumerate(options.command, start=0):
			logger.info("arg[%s]: %s", i, argument)
			if i == 0:
				cmdline = argument
			elif ' ' in argument or len(argument) == 0:
				cmdline += u" '%s'" % argument
			else:
				cmdline += u" %s" % argument

		(readSelection, _unused, _unused) = select.select([sys.stdin], [], [], 0.2)
		if sys.stdin in readSelection:
			read = sys.stdin.read()
			read = read.replace('\r', '').replace('\n', '')
			if read:
				cmdline += u" '%s'" % read

		logger.debug("cmdline: '%s'", cmdline)

		global shell
		if interactive:
			try:
				logger.notice("Starting interactive mode")
				shell = Shell(prompt=u'%s@opsi-admin>' % username, output=output, color=color, cmdline=cmdline)
				shell.setInfoline(u"Connected to %s" % address)

				for line in LOGO:
					shell.appendLine(line.get('text'), line.get('color'))

				welcomeMessage = """\
Welcome to the interactive mode of opsi-admin.
You can use syntax completion via [TAB]. \
To exit opsi-admin please type 'exit'."""

				for line in welcomeMessage.split('\n'):
					shell.appendLine(line, COLOR_NORMAL)
				shell.run()
			except Exception as error:
				logger.error(error, exc_info=True)
				raise
		elif cmdline:
			def searchForError(obj):
				if isinstance(obj, dict):
					try:
						if obj['error']:
							raise ErrorInResultException(obj['error'])
					except KeyError:
						for key in obj:
							searchForError(obj[key])
				elif isinstance(obj, list):
					for element in obj:
						searchForError(element)

			try:
				shell = Shell(prompt=u'%s@opsi-admin>' % username, output=output, color=color)
				for cmd in cmdline.split(u'\n'):
					if cmd:
						shell.cmdline = cmd
						shell.execute()

				logger.debug("Shell lines are: '%s'", shell.getLines())
				for line in shell.lines:
					print(line['text'].rstrip())

				try:
					resultAsJSON = json.loads(u'\n'.join([line['text'] for line in shell.lines]))
					searchForError(dict(resultAsJSON))
				except (TypeError, ValueError) as error:
					logger.debug2("Conversion to dict failed: %s", error)
			except Exception as error:
				logger.logException(forceUnicode(error))
				raise error
		else:
			raise RuntimeError("Not running in interactive mode and no commandline arguments given.")
	finally:
		if backend:
			try:
				backend.backend_exit()
			except Exception:
				pass


def startLogFile(logFile, logLevel):
	with codecs.open(logFile, 'w', 'utf-8') as log:
		log.write(u"Starting log at: %s" % forceUnicode(time.strftime("%a, %d %b %Y %H:%M:%S")))
	logging_config(log_file = logFile, file_level = logLevel)

class Shell:

	def __init__(self, prompt=u'opsi-admin>', output=u'JSON', color=True, cmdline=u''):
		self.color = forceBool(color)
		self.output = forceUnicode(output)
		self.running = False
		self.screen = None
		self.cmdBufferSize = 1024
		self.userConfigDir = None
		self.prompt = forceUnicode(prompt)
		self.infoline = u'opsi admin started'
		self.yMax = 0
		self.xMax = 0
		self.pos = len(cmdline)
		self.lines = []
		self.linesBack = 0
		self.linesMax = 0
		self.paramPos = -1
		self.currentParam = None
		self.cmdListPos = 0
		self.cmdList = []
		self.cmdline = forceUnicode(cmdline)
		self.shellCommand = u''
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

		historyFile = forceFilename(os.path.join(self.userConfigDir, u'history'))
		try:
			with codecs.open(historyFile, 'r', 'utf-8', 'replace') as history:
				for line in history:
					if not line:
						continue
					self.cmdList.append(line.strip())
					self.cmdListPos += 1
		except FileNotFoundError:
			logger.debug("History %s file not found.", historyFile)
		except Exception as error:
			logger.error("Failed to read history file '%s': %s", historyFile, forceUnicode(error))

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
			except:
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
		self.setCmdline(u'')
		self.reverseSearch = None

	def run(self):
		self.running = True

		self.initScreen()

		if self.cmdline:
			for cmd in self.cmdline.split(u'\n'):
				self.cmdline = cmd
				self.appendLine(u"%s %s" % (self.prompt, self.cmdline))
				if self.cmdline:
					try:
						self.execute()
					except Exception as error:
						lines = forceUnicode(error).split(u'\n')
						lines[0] = u"ERROR: %s" % lines[0]
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
				except OSError as error:
					logger.error("Failed to delete log-file '%s': %s", logFile, forceUnicode(error))

		historyFilePath = os.path.join(self.userConfigDir, 'history')
		try:
			with codecs.open(historyFilePath, 'w', 'utf-8') as history:
				for line in self.cmdList:
					if not line or line in ('quit', 'exit'):
						continue
					history.write(u"%s\n" % line)
		except Exception as error:
			logger.error("Failed to write history file '%s': %s", historyFilePath, forceUnicode(error))

		self.exitScreen()
		self.running = False

	def bell(self):
		sys.stderr.write('\a')

	def display(self):
		if not self.screen:
			return
		self.screen.move(0, 0)
		self.screen.clrtoeol()
		shellLine = self.infoline + (self.xMax - len(self.infoline)) * u' '
		try:
			self.screen.addstr(shellLine, curses.A_REVERSE)
		except Exception as error:
			logger.error("Failed to add string '%s': %s", shellLine, forceUnicode(error))

		height = int(len(self.prompt + u' ' + self.cmdline) / self.xMax) + 1
		clear = self.xMax - (len(self.prompt + u' ' + self.cmdline) % self.xMax) - 1

		self.linesMax = self.yMax - height - 1
		self.screen.move(self.yMax - height, 0)
		self.screen.clrtoeol()
		shellLine = "%s %s%s" % (self.prompt, self.cmdline, u' ' * clear)
		try:
			self.screen.addstr(shellLine, curses.A_BOLD)
		except Exception as error:
			logger.error("Failed to add string '%s': %s", shellLine, forceUnicode(error))

		for i in range(0, self.linesMax):
			self.screen.move(self.linesMax - i, 0)
			self.screen.clrtoeol()
			shellLine = u''
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
			except Exception as error:
				logger.error("Failed to add string '%s': %s", shellLine, forceUnicode(error))

		moveY = self.yMax - height + int((len(self.prompt + u' ') + self.pos) / self.xMax)
		moveX = ((len(self.prompt + u' ') + self.pos) % self.xMax)
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
			line = line.replace(availableColor, u'')

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
		return u''

	def parseCmdline(self):
		self.params = []
		self.currentParam = None
		self.paramPos = -1

		if not self.cmdline:
			return

		self.shellCommand = u''
		cmdline = self.cmdline
		if '|' in cmdline:
			quoteCount = 0
			doubleQuoteCount = 0
			parts = cmdline.split(u'|')
			for i, part in enumerate(parts):
				quoteCount += part.count(u"'")
				doubleQuoteCount += part.count(u'"')
				if (quoteCount % 2 == 0) and (doubleQuoteCount % 2 == 0):
					cmdline = u'|'.join(parts[:i + 1])
					self.shellCommand = u'|'.join(parts[i + 1:]).lstrip()
					break

		cur = 0
		quote = None
		for i, element in enumerate(cmdline):
			logger.debug2(
				"parseCmdline(): char '%s', quote: '%s', cur: '%s', params: '%s'",
				element, quote, cur, self.params
			)
			if len(self.params) < cur + 1:
				self.params.append(u'')

			if i == self.pos - 1:
				self.paramPos = cur

			if element == u"'":
				if quote is None:
					quote = u"'"
				elif quote == u"'":
					if not self.params[cur]:
						cur += 1
					quote = None
				else:
					self.params[cur] += u'\''
			elif element == u'"':
				if quote is None:
					self.params[cur] += u'"'
					quote = u'"'
				elif quote == u'"':
					self.params[cur] += u'"'
					if not self.params[cur]:
						cur += 1
					quote = None
				else:
					self.params[cur] += u'"'
			elif element == u" ":
				if quote is not None:
					self.params[cur] += element
				elif len(self.params[cur]) > 0:
					cur += 1
			else:
				self.params[cur] += element

		if not quote and self.params and self.params[-1] and self.pos == len(cmdline) and cmdline.endswith(u' '):
			self.params.append(u'')
			self.paramPos += 1

		if self.params:
			self.currentParam = self.params[self.paramPos]
		else:
			self.currentParam = u''

		logger.debug("cmdline: '%s'", cmdline)
		logger.debug2(
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
				except Exception as error:
					message = "Failed to execute %s: %s" % (self.cmdline, forceUnicode(error))
					logger.error(message, exc_info=True)
					raise RuntimeError(message)
				break

		if invalid:
			raise ValueError(_(u"Invalid command: '%s'") % self.getParam(0))

	def question(self, question):
		question = forceUnicode(question)
		if interactive:
			self.screen.move(self.yMax - 1, 0)
			self.screen.clrtoeol()
			self.screen.addstr(question + u' (n/y)')
			self.screen.refresh()
			char = None
			while True:
				char = self.screen.getch()
				if char and char >= 0 and char < 256 and char != 10:
					if chr(char) == 'y':
						return True
					elif chr(char) == 'n':
						return False
		return False

	def getPassword(self):
		password1 = u''
		password2 = u''
		while not password1 or (password1 != password2):
			if interactive:
				self.screen.move(self.yMax - 1, 0)
				self.screen.clrtoeol()
				self.screen.addstr(_(u"Please type password:"))
				self.screen.refresh()
				password1 = self.screen.getstr()

				self.screen.move(self.yMax - 1, 0)
				self.screen.clrtoeol()
				self.screen.addstr(_(u"Please retype password:"))
				self.screen.refresh()
				password2 = self.screen.getstr()

				if password1 != password2:
					self.screen.move(self.yMax - 1, 0)
					self.screen.clrtoeol()
					self.screen.addstr(_(u"Supplied passwords do not match"))
					self.screen.refresh()
					time.sleep(2)
			else:
				password1 = password2 = getpass.getpass()

		logger.confidential("Got password '%s'", password1)
		return password1

	def getCommand(self):
		char = None
		self.pos = 0
		self.setCmdline(u'')
		self.reverseSearch = None

		while not char or (char != 10):
			char = self.screen.getch()
			textInput = False

			if not char or char < 0:
				continue

			if char == curses.KEY_RESIZE:
				# window resized
				self.yMax, self.xMax = self.screen.getmaxyx()
				self.display()
				continue

			elif char == curses.KEY_UP:
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
					if self.linesBack < 0:
						self.linesBack = 0

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
						for c in range(len(comp)):
							if c > len(match) - 1:
								break
							elif comp[c] != match[c]:
								match = match[:c]
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
						logger.debug2("Current char: %r", char)

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
							newCmdline = self.cmdline[0:self.pos] + char + self.cmdline[self.pos:]
					except Exception as error:
						logger.error("Failed to add char %r: %s", char, error)

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
				except Exception as error:
					self.setInfoline(forceUnicode(error))

			if not textInput:
				if self.reverseSearch is not None:
					self.reverseSearch = None
					self.setInfoline("")

		self.cmdline = self.cmdline.strip()

		self.appendLine(self.prompt + ' ' + self.cmdline)
		if self.cmdline:
			try:
				self.execute()
			except Exception as error:
				lines = forceUnicode(error).split('\n')
				lines[0] = "ERROR: %s" % lines[0]
				for line in lines:
					self.appendLine(line, COLOR_RED)

		if len(self.lines) > self.yMax - 2:
			self.linesBack = 0


class Command:
	def __init__(self, name):
		self.name = forceUnicode(name)

	def getName(self):
		return self.name

	def getDescription(self):
		return u""

	def completion(self, params, paramPos):
		return []

	def help(self, shell):
		shell.appendLine(u"")

	def execute(self, shell, params):
		raise NotImplementedError(u"Nothing to do.")


class CommandMethod(Command):
	def __init__(self):
		Command.__init__(self, u'method')
		self.interface = backend.backend_getInterface()

	def getDescription(self):
		return _(u"Execute a config-interface-method")

	def help(self, shell):
		shell.appendLine(u"\r{0}\n".format(_(u"Methods are:")))
		for method in backend.backend_getInterface():
			logger.debug(method)
			shell.appendLine(u"\r%s\n" % method.get('name'))

	def completion(self, params, paramPos):
		completions = []

		if paramPos == 0:
			completions.append(u'list')
			for m in self.interface:
				completions.append(m.get(u'name'))

		elif paramPos == 1:
			if u'list'.startswith(params[0]):
				completions.append(u'list')
			for m in self.interface:
				if m.get('name').startswith(params[0]):
					completions.append(m.get('name'))

		elif paramPos >= 2:
			for m in self.interface:
				if m.get('name') == params[0]:
					if len(m.get('params')) >= len(params) - 1:
						completions = [m.get('params')[paramPos - 2]]
					break

		return completions

	def execute(self, shell, params):
		if len(params) <= 0:
			shell.appendLine(_(u'No method defined'))
			return

		methodName = params[0]

		if methodName == u'list':
			for methodDescription in self.interface:
				shell.appendLine(u"%s%s" % (methodDescription.get('name'), tuple(methodDescription.get('params'))), refresh=False)
			shell.display()
			return

		for methodDescription in self.interface:
			if methodName == methodDescription['name']:
				methodInterface = methodDescription
				break
		else:
			raise OpsiRpcError(u"Method '%s' is not valid" % methodName)

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
					raise ValueError(u"kwargs param is not a dict: %s" % params[-1])

				for (key, value) in params.pop(-1).items():
					keywords[str(key)] = deserialize(value)

		def createObjectOrString(obj):
			"Tries to return object from JSON. If this fails returns unicode."
			try:
				return fromJson(obj)
			except Exception as error:
				logger.debug("Not a json string '%s': %s", obj, error)
				return forceUnicode(obj)

		params = [createObjectOrString(item) for item in params]

		pString = str(params)[1:-1]
		if keywords:
			pString += u', ' + str(keywords)
		if len(pString) > 200:
			pString = pString[:200] + u'...'

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
		shell.setInfoline(_(u'Took %0.3f seconds to process: %s(%s)') % (duration, methodName, pString))
		result = serialize(result)
		logger.debug2("Serialized result: '%s'", result)

		if result is not None:
			lines = []
			if shell.output == u'RAW':
				lines.append(toJson(result))

			elif shell.output == u'JSON':
				lines = objectToBeautifiedText(result).split(u'\n')

			elif shell.output == u'SHELL':
				bashVars = objectToBash(result, {})
				for index in range(len(bashVars) - 1, -2, -1):
					if index == -1:
						index = ''

					value = bashVars.get('RESULT%s' % index)
					if value:
						lines.append(u'RESULT%s=%s' % (index, value))

			elif shell.output == u'SIMPLE':
				if isinstance(result, dict):
					for (key, value) in result.items():
						if isinstance(value, bool):
							value = forceUnicodeLower(value)
						lines.append(u'%s=%s' % (key, value))
				elif isinstance(result, (tuple, list, set)):
					for resultElement in result:
						if isinstance(resultElement, dict):
							for (key, value) in resultElement.items():
								if isinstance(value, bool):
									value = forceUnicodeLower(value)
								lines.append(u'%s=%s' % (key, value))
							lines.append(u'')
						elif isinstance(resultElement, (tuple, list)):
							raise ValueError(u"Simple output not possible for list of lists")
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

				flags = fcntl.fcntl(proc.stdout, fcntl.F_GETFL)
				fcntl.fcntl(proc.stdout, fcntl.F_SETFL, flags | os.O_NONBLOCK)

				flags = fcntl.fcntl(proc.stderr, fcntl.F_GETFL)
				fcntl.fcntl(proc.stderr, fcntl.F_SETFL, flags | os.O_NONBLOCK)

				encoding = proc.stdout.encoding or inEncoding

				exitCode = None
				buf = ''
				err = ''

				while exitCode is None:
					exitCode = proc.poll()
					if lines:
						for line in lines:
							proc.stdin.write((u"%s\n" % line).encode(outEncoding, 'replace'))
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
							err += string
					except IOError as error:
						if error.errno != 11:
							raise

				if exitCode != 0:
					raise Exception(u'Exitcode: %s\n%s' % ((exitCode * -1), str(err, encoding, 'replace')))

				lines = str(buf, encoding, 'replace').split(u'\n')

			for line in lines:
				shell.appendLine(line, COLOR_GREEN)


class CommandSet(Command):
	def __init__(self):
		Command.__init__(self, u'set')

	def getDescription(self):
		return _(u"Settings")

	def completion(self, params, paramPos):
		completions = []

		if paramPos == 0 or not params[0]:
			completions = [u'color', u'log-file', u'log-level']

		elif paramPos == 1:
			if u'color'.startswith(params[0]):
				completions = [u'color']
			if u'log-file'.startswith(params[0]):
				completions = [u'log-file']
			if u'log-level'.startswith(params[0]):
				completions.append(u'log-level')

		elif paramPos == 2:
			if params[0] == u'color':
				completions = [u'on', u'off']
			elif params[0] == u'log-file':
				completions = [u'<filename>', u'off']
			elif params[0] == u'log-level':
				completions = [u'0', u'1', u'2', u'3', u'4', u'5', u'6', u'7', u'8', u'9']

		return completions

	def execute(self, shell, params):
		global logFile

		if len(params) <= 0:
			raise ValueError(_(u'Missing option'))
		if params[0] not in (u'color', u'log-file', u'log-level'):
			raise ValueError(_(u'Unknown option: %s') % params[0])
		if len(params) <= 1:
			raise ValueError(_(u'Missing value'))

		if params[0] == u'color':
			if params[1] == u'on':
				shell.setColor(True)
			elif params[1] == u'off':
				shell.setColor(False)
			else:
				raise ValueError(_(u'Bad value: %s') % params[1])

		elif params[0] == u'log-file':
			if params[1] == u'off':
				logging_config(file_level = LOG_NONE)
			else:
				logFile = params[1]
				startLogFile(logFile, LOG_DEBUG)

		elif params[0] == u'log-level':
			if not logFile:
				raise ValueError(_(u'No log-file set!'))
			logging_config(file_level = int(params[1]))


class CommandHelp(Command):
	def __init__(self):
		Command.__init__(self, u'help')

	def getDescription(self):
		return _(u"Show this text")

	def execute(self, shell, params):
		shell.appendLine(u'\r' + _(u"Commands are:") + u'\n', refresh=False)
		for cmd in shell.commands:
			shell.appendLine(u"\r\t%-20s%s\n" % (cmd.getName() + ':', cmd.getDescription()), refresh=False)
		shell.display()


class CommandQuit(Command):
	def __init__(self):
		Command.__init__(self, u'quit')

	def getDescription(self):
		return _(u"Exit opsi-admin")

	def execute(self, shell, params):
		shell.exit()


class CommandExit(CommandQuit):
	def __init__(self):
		Command.__init__(self, u'exit')


class CommandHistory(Command):
	def __init__(self):
		Command.__init__(self, u'history')

	def getDescription(self):
		return _(u"show / clear command history")

	def completion(self, params, paramPos):
		completions = []

		if paramPos == 0 or not params[0]:
			completions = [u'clear', u'show']

		elif paramPos == 1:
			if u'clear'.startswith(params[0]):
				completions = [u'clear']
			elif u'show'.startswith(params[0]):
				completions = [u'show']

		return completions

	def execute(self, shell, params):
		if len(params) <= 0:
			# By default: show history
			params = [u'show']
		elif params[0] not in (u'clear', u'show'):
			raise ValueError(_(u'Unknown command: %s') % params[0])

		if params[0] == u'show':
			for line in shell.cmdList:
				shell.appendLine(line, refresh=False)
			shell.display()
		elif params[0] == u'clear':
			shell.cmdList = []
			shell.cmdListPos = -1


class CommandLog(Command):
	def __init__(self):
		Command.__init__(self, u'log')

	def getDescription(self):
		return _(u"show log")

	def completion(self, params, paramPos):
		completions = []

		if paramPos == 0 or not params[0]:
			completions = [u'show']

		elif paramPos == 1:
			if u'show'.startswith(params[0]):
				completions = [u'show']

		return completions

	def execute(self, shell, params):
		if len(params) <= 0:
			# By default: show log
			params = [u'show']
		elif params[0] not in (u'show',):
			raise ValueError(_(u'Unknown command: %s') % params[0])

		if params[0] == u'show':
			if not logFile:
				raise RuntimeError(_(u'File logging is not activated'))

			with open(logFile) as log:
				for line in log:
					shell.appendLine(line, refresh=False)
			shell.display()


class CommandTask(Command):
	def __init__(self):
		Command.__init__(self, u'task')
		self._tasks = (
			(u'setupWhereInstalled', u'productId'),
			(u'setupWhereNotInstalled', u'productId'),
			(u'updateWhereInstalled', 'productId'),
			(u'uninstallWhereInstalled', 'productId'),
			(u'setActionRequestWhereOutdated', 'actionRequest', 'productId'),
			(u'setActionRequestWhereOutdatedWithDependencies', 'actionRequest', 'productId'),
			(u'setActionRequestWithDependencies', 'actionRequest', 'productId', 'clientId'),
			(u'decodePcpatchPassword', u'encodedPassword', u'opsiHostKey'),
			(u'setPcpatchPassword', u'*password')
		)

	def getDescription(self):
		return _(u"execute a task")

	def help(self, shell):
		shell.appendLine(u'')

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

	def execute(self, shell, params):
		tasknames = set([task[0] for task in self._tasks])

		if len(params) <= 0:
			return
		elif params[0] not in tasknames:
			raise ValueError(_(u'Unknown task: %s') % params[0])

		if params[0] == u'setupWhereInstalled':
			if len(params) < 2:
				raise ValueError(_(u'Missing product-id'))
			productId = params[1]

			logger.warning(
				"The task 'setupWhereInstalled' is obsolete. "
				"Please use 'method setupWhereInstalled' instead."
			)

			for clientId in backend.setupWhereInstalled(productId):
				shell.appendLine(clientId)

		elif params[0] == u'setupWhereNotInstalled':
			if len(params) < 2:
				raise ValueError(_(u'Missing product-id'))
			productId = params[1]

			logger.warning(
				"The task 'setupWhereNotInstalled' is obsolete. "
				"Please use 'method setupWhereNotInstalled' instead."
			)

			for clientId in backend.setupWhereNotInstalled(productId):
				shell.appendLine(clientId)

		elif params[0] == u'updateWhereInstalled':
			if len(params) < 2:
				raise ValueError(_(u'Missing product-id'))
			productId = params[1]

			logger.warning(
				"The task 'updateWhereInstalled' is obsolete. "
				"Please use 'method updateWhereInstalled' instead."
			)

			for clientId in backend.updateWhereInstalled(productId):
				shell.appendLine(clientId)

		elif params[0] == u'uninstallWhereInstalled':
			if len(params) < 2:
				raise ValueError(_(u'Missing product-id'))
			productId = params[1]

			logger.warning(
				"The task 'uninstallWhereInstalled' is obsolete. "
				"Please use 'method uninstallWhereInstalled' instead."
			)

			for clientId in backend.uninstallWhereInstalled(productId):
				shell.appendLine(clientId)

		elif params[0] == u'setActionRequestWhereOutdated':
			if len(params) < 2:
				raise ValueError(_(u'Missing action request'))
			elif len(params) < 3:
				raise ValueError(_(u'Missing product-id'))

			actionRequest = params[1]
			productId = params[2]

			logger.warning(
				"The task 'setActionRequestWhereOutdated' is obsolete. "
				"Please use 'method setActionRequestWhereOutdated' instead."
			)

			for clientId in backend.setActionRequestWhereOutdated(actionRequest, productId):
				shell.appendLine(clientId)

		elif params[0] == u'setActionRequestWithDependencies':
			if len(params) < 2:
				raise ValueError(_(u'Missing action request'))
			if len(params) < 3:
				raise ValueError(_(u'Missing product-id'))
			if len(params) < 4:
				raise ValueError(_(u'Missing client-id'))
			actionRequest = params[1]
			productId = params[2]
			clientId = params[3]

			if productId and clientId and actionRequest:
				backend.setProductActionRequestWithDependencies(productId, clientId, actionRequest)

		elif params[0] == u'setActionRequestWhereOutdatedWithDependencies':
			if len(params) < 2:
				raise ValueError(_(u'Missing action request'))
			if len(params) < 3:
				raise ValueError(_(u'Missing product-id'))

			actionRequest = params[1]
			productId = params[2]

			logger.warning(
				"The task 'setActionRequestWhereOutdatedWithDependencies' "
				"is obsolete. Please use 'method "
				"setActionRequestWhereOutdatedWithDependencies' instead."
			)

			for clientId in backend.setActionRequestWhereOutdatedWithDependencies(actionRequest, productId):
				shell.appendLine(clientId)

		elif params[0] == u'decodePcpatchPassword':
			if len(params) < 3:
				raise ValueError(_(u'Missing argument'))
			crypt = params[1]
			key = params[2]
			cleartext = blowfishDecrypt(key, crypt)
			shell.appendLine(cleartext)

		elif params[0] == u'setPcpatchPassword':
			if os.getuid() != 0:
				raise RuntimeError(_(u"You have to be root to change pcpatch password!"))

			fqdn = getfqdn(conf='/etc/opsi/global.conf')
			if fqdn.count('.') < 2:
				raise RuntimeError(_(u"Failed to get my own fully qualified domainname"))

			password = u''
			if len(params) < 2:
				password = shell.getPassword()
			else:
				password = params[1]

			if not password:
				raise ValueError("Can not use empty password!")
			secret_filter.add_secrets(password)
			
			backend.user_setCredentials(username='pcpatch', password=password)

			try:
				udm = which('univention-admin')
			except CommandNotFoundException:
				udm = None

			if udm:  # We are on Univention Corporate Server (UCS)
				dn = None
				command = u'{udm} users/user list --filter "(uid=pcpatch)"'.format(udm=udm)
				logger.debug("Filtering for pcpatch: %s", command)
				with closing(os.popen(command, 'r')) as process:
					for line in process.readlines():
						if line.startswith('DN'):
							dn = line.strip().split(' ')[1]
							break

				if not dn:
					raise RuntimeError(u"Failed to get DN for user pcpatch")

				command = (
					u"{udm} users/user modify --dn {dn} "
					u"--set password='{pw}' "
					u"--set overridePWLength=1 --set overridePWHistory=1 "
					u"1>/dev/null 2>/dev/null"
				).format(udm=udm, dn=dn, pw=password)
				logger.debug("Setting password with: %s", command)
				os.system(command)

				return  # Done with UCS

			try:
				# smbldap
				smbldapCommand = u'{cmd} pcpatch 1>/dev/null 2>/dev/null'.format(cmd=which('smbldap-passwd'))
				with closing(os.popen(smbldapCommand, 'w')) as process:
					process.write(u"%s\n%s\n" % (password, password))
			except Exception as error:
				logger.debug("Setting password through smbldap failed: %s", error)

				# unix
				chpasswdCommand = u'{chpasswd} 1>/dev/null 2>/dev/null'.format(chpasswd=which(u'chpasswd'))
				with os.popen(chpasswdCommand, 'w') as process:
					process.write(u"pcpatch:%s\n" % password)

				smbpasswdCommand = u'{smbpasswd} -a -s pcpatch 1>/dev/null 2>/dev/null'.format(smbpasswd=which('smbpasswd'))
				with os.popen(smbpasswdCommand, 'w') as process:
					process.write(u"%s\n%s\n" % (password, password))


def main():
	@contextmanager
	def shellExit():
		try:
			yield
		finally:
			try:
				shell.exit()
			except Exception:
				pass

	try:
		locale.setlocale(locale.LC_ALL, '')
	except Exception:
		pass

	if os.name == 'posix':
		from signal import signal, SIGINT, SIGQUIT
		signal(SIGINT, signalHandler)
		signal(SIGQUIT, signalHandler)

	try:
		with shellExit():
			shell_main(sys.argv[1:])
		exitCode = 0
	except ErrorInResultException as error:
		logger.warning("Error in result: %s", error)
		exitCode = 2
	except Exception as error:
		logging_config(stderr_level = LOG_ERROR)
		logger.error("Error during execution: %s", error, exc_info=True)
		exitCode = 1

	if exitZero:
		exitCode = 0

	sys.exit(exitCode)
