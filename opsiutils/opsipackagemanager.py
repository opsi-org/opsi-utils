# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-package-manager
"""
# pylint: disable=too-many-lines

import base64
import curses
import fcntl
import gettext
import glob
import locale
import os
import random
import stat
import struct
import sys
import termios
import threading
import time
from argparse import ArgumentParser
from contextlib import contextmanager
from signal import SIGINT, SIGTERM, SIGWINCH, signal

from OPSI import __version__ as python_opsi_version
from OPSI.Backend.BackendManager import BackendManager
from OPSI.Backend.JSONRPC import JSONRPCBackend
from OPSI.Types import (
	forceActionRequest,
	forceBool,
	forceHostId,
	forceInt,
	forceList,
	forceProductId,
	forceUnicode,
	forceUnicodeList,
)
from OPSI.UI import SnackUI
from OPSI.Util import getfqdn, md5sum
from OPSI.Util.File.Opsi import parseFilename
from OPSI.Util.Message import (
	MessageSubject,
	ProgressObserver,
	ProgressSubject,
	SubjectsObserver,
)
from OPSI.Util.Product import ProductPackageFile
from OPSI.Util.Repository import getRepository

try:
	from OPSI.Util.Sync import librsyncDeltaFile
except ImportError:
	librsyncDeltaFile = None
from opsicommon.logging import (
	DEFAULT_COLORED_FORMAT,
	LOG_NONE,
	LOG_WARNING,
	logger,
	logging_config,
)

from opsiutils import __version__

USER_AGENT = f"opsi-package-manager/{__version__}"

try:
	sp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	if os.path.exists(os.path.join(sp, "site-packages")):
		sp = os.path.join(sp, "site-packages")
	sp = os.path.join(sp, 'opsi-utils_data', 'locale')
	translation = gettext.translation('opsi-utils', sp)
	_ = translation.gettext
except Exception as error:  # pylint: disable=broad-except
	logger.debug("Failed to load locale from %s: %s", sp, error)

	def _(string):
		""" Fallback function """
		return string


class TaskError(RuntimeError):
	pass


class Task:
	def __init__(self, name, opsiPackageManager, method, params):
		self.name = forceUnicode(name)
		self.opsiPackageManager = opsiPackageManager
		self.method = method
		self.params = forceList(params)
		self.started = False
		self.ended = False
		self.exception = None

	def abort(self):
		pass

	def isRunning(self):
		return self.started and not self.ended

	def start(self):
		logger.debug("Task start()")
		self.started = True
		try:
			logger.trace("Method: %s", self.method)
			logger.trace("Params: %s", self.params)
			self.method(*self.params)
		except Exception as err:
			logger.error(err, exc_info=True)
			self.exception = err
			raise
		finally:
			self.ended = True


class UploadTask(Task):
	def __init__(self, name, opsiPackageManager, method, params):
		Task.__init__(self, name, opsiPackageManager, method, params)

	def start(self):
		while self.opsiPackageManager.maxTransfersReached():
			logger.debug("Maximum number transfers reached, waiting")
			time.sleep(1)
		Task.start(self)

	def abort(self):
		pass


class InstallTask(Task):
	def __init__(self, name, opsiPackageManager, method, params):
		Task.__init__(self, name, opsiPackageManager, method, params)


class UninstallTask(Task):
	def __init__(self, name, opsiPackageManager, method, params):
		Task.__init__(self, name, opsiPackageManager, method, params)


class CursesWindow:  # pylint: disable=too-many-instance-attributes
	def __init__(self, height, width, y, x, title='', border=False):  # pylint: disable=too-many-arguments,invalid-name
		self.height = forceInt(height)
		self.width = forceInt(width)
		self.y = forceInt(y)  # pylint: disable=invalid-name
		self.x = forceInt(x)  # pylint: disable=invalid-name
		self.title = forceUnicode(title)
		self.border = forceBool(border)
		self.color = None
		self.win = curses.newwin(self.height, self.width, self.y, self.x)
		if self.border:
			self.win.border()
		self.setTitle(self.title)
		self.refresh()

	def resize(self, height, width, y, x):  # pylint: disable=invalid-name
		self.height = forceInt(height)
		self.width = forceInt(width)
		self.y = forceInt(y)
		self.x = forceInt(x)
		try:
			self.win.resize(height, width)
			self.win.mvwin(y, x)
			self.win.redrawwin()
			self.win.refresh()
		except Exception:  # pylint: disable=broad-except
			pass

	def setTitle(self, title):
		self.title = forceUnicode(title)
		if not self.title:
			return
		if len(self.title) > self.width - 4:
			self.title = self.title[:self.width - 4]
		self.title = f'| {self.title} |'
		attr = curses.A_NORMAL
		if self.color:
			attr |= self.color
		try:
			self.win.addstr(
				0,
				int((self.width - len(self.title)) / 2),
				self.title,
				attr
			)
		except Exception:  # pylint: disable=broad-except
			pass

	def setColor(self, colorPair):
		if not curses.has_colors():
			return
		self.color = colorPair
		self.win.attrset(self.color)
		self.win.bkgdset(' ', self.color)
		self.win.clear()
		if self.border:
			self.win.border()
		self.setTitle(self.title)
		self.refresh()

	def setScrollable(self, scrollable):
		if scrollable:
			scrollable = 1
		else:
			scrollable = 0
		self.win.scrollok(scrollable)
		self.win.idlok(scrollable)

	def addstr(self, *attr):
		try:
			newAttr = []
			for idx, val in enumerate(attr):
				if idx == 0:
					newAttr.append(forceUnicode(val))
				else:
					newAttr.append(val)
			newAttr = tuple(newAttr)
			self.win.addstr(*newAttr)
		except Exception:  # pylint: disable=broad-except
			pass

	def clrtoeol(self):
		try:
			self.win.clrtoeol()
		except Exception as err:  # pylint: disable=broad-except
			logger.trace(err)

	def move(self, y, x):  # pylint: disable=invalid-name
		try:
			self.win.move(y, x)
		except Exception as err:  # pylint: disable=broad-except
			logger.trace(err)

	def clear(self):
		try:
			self.win.clear()
		except Exception as err:  # pylint: disable=broad-except
			logger.trace(err)

	def refresh(self):
		try:
			self.win.refresh()
		except Exception as err:  # pylint: disable=broad-except
			logger.trace(err)

	def redraw(self):
		try:
			self.win.redrawwin()
			self.win.refresh()
		except Exception as err:  # pylint: disable=broad-except
			logger.trace(err)


class CursesMainWindow(CursesWindow):
	def __init__(self):  # pylint: disable=super-init-not-called
		self.initScreen()

	def __del__(self):
		self.exitScreen()

	def initScreen(self):
		try:
			self.win = curses.initscr()
		except Exception:  # pylint: disable=broad-except
			# setupterm: could not find terminal
			os.environ["TERM"] = "linux"
			self.win = curses.initscr()
		(self.height, self.width) = self.win.getmaxyx()
		(self.x, self.y) = (0, 0)
		curses.noecho()
		curses.cbreak()
		self.win.keypad(1)
		curses.start_color()
		self.refresh()

	def exitScreen(self):
		curses.nocbreak()
		self.win.keypad(0)
		curses.echo()
		curses.endwin()

	def resize(self):  # pylint: disable=arguments-differ
		return


class CursesTextWindow(CursesWindow):
	def __init__(self, height, width, y, x, title='', border=False):  # pylint: disable=too-many-arguments
		CursesWindow.__init__(self, height, width, y, x, title, border)
		self.lines = []
		self._lock = threading.Lock()

	def addLine(self, line, *params):
		line = forceUnicode(line)
		with self._lock:
			if len(line) > self.width:
				line = line[:self.width - 1]
				self.lines.append((line, params))
			self.build()

	def addLines(self, lines, *params):
		lines = forceUnicodeList(lines)
		with self._lock:
			for line in lines:
				if len(line) > self.width:
					line = line[:self.width - 1]
				self.lines.append((line, params))
			self.build()

	def setLines(self, lines, *params):
		lines = forceUnicodeList(lines)
		with self._lock:
			self.lines = []
			for line in lines:
				if len(line) > self.width:
					line = line[:self.width - 1]
				self.lines.append((line, params))
			self.build()

	def getLines(self):
		return self.lines

	def build(self):
		if len(self.lines) > self.height:
			self.lines = self.lines[-1 * self.height:]

		for idx, (line, params) in enumerate(self.lines):
			if idx >= self.height:
				return
			self.move(idx, 0)
			self.clrtoeol()

			if params:
				self.addstr(line, *params)
			else:
				self.addstr(line)

	def resize(self, height, width, y, x):
		CursesWindow.resize(self, height, width, y, x)
		newLines = []
		for (line, params) in self.lines:
			if len(line) > self.width:
				line = line[:self.width - 1]
			newLines.append((line, params))
		self.lines = newLines


class UserInterface(SubjectsObserver):  # pylint: disable=too-many-instance-attributes
	def __init__(self, config={}, subjects=[]):  # pylint: disable=dangerous-default-value
		SubjectsObserver.__init__(self)
		self.config = config
		self.opmSubjects = subjects
		self.mainWindow = None
		self.initScreen()

	def initScreen(self):
		# Important for ncurses to use the right encoding!?
		try:
			locale.setlocale(locale.LC_ALL, '')
		except Exception as err:
			raise RuntimeError(
				f"Setting locale failed: {err} - do you have $LC_ALL set?"
			) from err

		if self.config['consoleLogLevel'] <= LOG_NONE:
			self.loggerWindowHeight = 0
		elif self.config['consoleLogLevel'] <= LOG_WARNING:
			self.loggerWindowHeight = 2
		else:
			self.loggerWindowHeight = 5

		self._colors = {}
		self.__lock = threading.Lock()

		self.mainWindow = CursesMainWindow()
		self.infoWindow = CursesTextWindow(
			height=1,
			width=self.mainWindow.width,
			x=0,
			y=0
		)
		self.progressWindow = CursesWindow(
			height=self.mainWindow.height - self.loggerWindowHeight - 2,
			width=self.mainWindow.width,
			x=0,
			y=1
		)

		self.loggerHeaderWindow = None
		self.loggerWindow = None
		if self.loggerWindowHeight > 0:
			self.loggerHeaderWindow = CursesTextWindow(
				height=1,
				width=self.mainWindow.width,
				x=0,
				y=self.mainWindow.height - self.loggerWindowHeight - 1
			)
			self.loggerWindow = CursesTextWindow(
				height=self.loggerWindowHeight,
				width=self.mainWindow.width,
				x=0,
				y=self.mainWindow.height - self.loggerWindowHeight
			)

		if curses.has_colors():
			logger.debug('init colors')
			curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_GREEN)
			curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)
			curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)
			curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLACK)
			curses.init_pair(5, curses.COLOR_RED, curses.COLOR_BLACK)
			curses.init_pair(6, curses.COLOR_YELLOW, curses.COLOR_BLACK)
			curses.init_pair(7, curses.COLOR_WHITE, curses.COLOR_BLUE)

			self._colors = {
				'INFO_WINDOW': curses.color_pair(7),
				'LOG_HEADER': curses.color_pair(2),
				1: curses.color_pair(5),
				2: curses.color_pair(5),
				3: curses.color_pair(6),
				4: curses.color_pair(3),
				5: curses.color_pair(4),
				6: curses.color_pair(4),
				7: curses.color_pair(4),
				8: curses.color_pair(4),
				9: curses.color_pair(4)
			}
			self.infoWindow.setColor(self._colors['INFO_WINDOW'])
			if self.loggerHeaderWindow:
				self.loggerHeaderWindow.setColor(self._colors['LOG_HEADER'])

		if self.loggerHeaderWindow:
			self.loggerHeaderWindow.setLines([_('Log messages')])
			self.loggerHeaderWindow.refresh()

		self.mainWindow.refresh()

		self.setSubjects(self.opmSubjects)

		signal(SIGWINCH, self.resized)
		logger.info("UserInterface initialized")

	def resized(self, signo, stackFrame):  # pylint: disable=unused-argument
		try:
			self.mainWindow.resize()
			self.infoWindow.resize(
				height=1,
				width=self.mainWindow.width,
				x=0,
				y=0
			)
			self.progressWindow.resize(
				height=self.mainWindow.height - self.loggerWindowHeight - 2,
				width=self.mainWindow.width,
				x=0,
				y=1
			)

			if self.loggerWindowHeight > 0:
				self.loggerHeaderWindow.resize(
					height=1,
					width=self.mainWindow.width,
					x=0,
					y=self.mainWindow.height - self.loggerWindowHeight - 1
				)
				self.loggerWindow.resize(
					height=self.loggerWindowHeight,
					width=self.mainWindow.width,
					x=0,
					y=self.mainWindow.height - self.loggerWindowHeight
				)
		except Exception as err:  # pylint: disable=broad-except
			logger.trace(err)

		try:
			self.subjectsChanged(self.getSubjects())
		except Exception:  # pylint: disable=broad-except
			pass

	def subjectsChanged(self, subjects):
		for subject in subjects:
			if subject.getMessage():
				self.messageChanged(subject, subject.getMessage())

	def progressChanged(self, subject, state, percent, timeSpend, timeLeft, speed):  # pylint: disable=too-many-arguments
		self.showProgress()

	def messageChanged(self, subject, message):
		if not message:
			logger.warning("Message deleted: %s %s", subject.getType(), subject.getId())

		if self.__lock.locked():
			return

		if subject.getType() == 'Logger':
			with self.__lock:
				# Do not log anything to avoid log loops !!!
				params = []
				ll = subject.getSeverity()
				if ll in self._colors:
					params = [self._colors[ll]]
				self.loggerWindow.addLines(message.split('\n'), *params)
				self.loggerWindow.refresh()

		elif subject.getId() in ('info', 'transfers'):
			with self.__lock:
				info = ''
				transfers = ''
				for subj in self.getSubjects():
					if subj.getId() == 'info':
						info = subj.getMessage()
					elif subj.getId() == 'transfers':
						transfers = subj.getMessage()

				free = self.infoWindow.width - len(info) - len(transfers) - 1
				free = max(free, 0)
				self.infoWindow.setLines([info + ' ' * free + transfers])
				self.infoWindow.refresh()
		else:
			self.showProgress()

	def exit(self):
		for subject in self.getSubjects():
			subject.detachObserver(self)
		self.exitScreen()

	def exitScreen(self):
		logger.debug("UserInterface: exitScreen()")
		if not self.mainWindow:
			return
		self.mainWindow.exitScreen()
		self.mainWindow = None

	def showProgress(self):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
		if self.__lock.locked():
			return

		with self.__lock:
			subjects = {}
			for subject in self.getSubjects():
				if subject.getType() == 'depot':
					subjects[subject.getId()] = subject

			for subject in self.getSubjects():
				if subject.getType() == 'upload':
					subjects[subject.getId()] = subject

			ids = list(subjects.keys())
			ids.sort()
			maxIdLength = max([len(currentID) for currentID in ids] or [0])

			y = 0  # pylint: disable=invalid-name
			for currentID in ids:
				subject = subjects[currentID]
				if y >= self.progressWindow.height:
					# Screen full
					logger.debug("Screen to small to display all progresses")
					break

				x = 0  # pylint: disable=invalid-name
				self.progressWindow.move(y, x)

				idString = f"{subject.getId():{maxIdLength}} | "
				if len(idString) > self.progressWindow.width:
					idString = idString[:self.progressWindow.width]
				self.progressWindow.addstr(idString, curses.A_BOLD)

				if len(idString) < self.progressWindow.width:
					color = None
					x += len(idString)  # pylint: disable=invalid-name
					self.progressWindow.move(y, x)
					maxSize = self.progressWindow.width - len(idString)
					message = subject.getMessage()
					severity = subject.getSeverity()
					if severity and severity in self._colors:
						color = self._colors[severity]

					if subject.getClass() == 'ProgressSubject':
						minutes_left = f"{int(subject.getTimeLeft() / 60):02}"
						seconds_left = f"{int(subject.getTimeLeft() % 60):02}"
						percent = f"{subject.getPercent():.2f}"
						progress = (
							f" {percent:>6}% {(subject.getState() / 1000):>8} "
							f"KB{(int(subject.getSpeed() / 1000)):>6} KB/s"
							f"{minutes_left:>6}:{seconds_left} ETA"
						)
						free = max(maxSize - len(message) - len(progress), 0)
						message = message + ' ' * free + progress

					if len(message) > maxSize:
						message = message[:maxSize]

					if color:
						self.progressWindow.addstr(message, color)
					else:
						self.progressWindow.addstr(message)
					x += len(message)  # pylint: disable=invalid-name
					self.progressWindow.move(y, x)
					self.progressWindow.clrtoeol()
				y += 1  # pylint: disable=invalid-name

			self.progressWindow.refresh()


class TaskQueue(threading.Thread):
	def __init__(self, name):
		threading.Thread.__init__(self)
		self.name = forceUnicode(name)
		self.tasks = []
		self.started = False
		self.ended = False
		self.errors = []
		self.currentTaskNumber = -1

	def abort(self):
		self.ended = True
		task = self.getCurrentTask()
		if task:
			task.abort()

	def getCurrentTask(self):
		if self.currentTaskNumber < 0:
			return None
		return self.tasks[self.currentTaskNumber]

	def run(self):
		self.currentTaskNumber = -1
		if not self.tasks:
			raise RuntimeError("No tasks in queue")
		self.started = True
		logger.debug("TaskQueue '%s' started", self.name)
		i = 0
		while i < len(self.tasks):
			if self.ended:
				return

			task = self.tasks[i]
			try:
				logger.debug("Starting task '%s'", task.name)
				self.currentTaskNumber += 1
				task.start()
				logger.debug("Task '%s' ended", task.name)
			except Exception as err:  # pylint: disable=broad-except
				logger.error("Task '%s' failed: %s", task.name, err)
				self.errors.append(err)
				if i < (len(self.tasks) - 1) and isinstance(task, UploadTask) and isinstance(self.tasks[i + 1], InstallTask):
					# Upload task failed => do not execute install task
					logger.notice("Upload task failed, skipping install task")
					i += 1
			if i < len(self.tasks) - 1:
				time.sleep(2)
				if isinstance(task, UploadTask) and isinstance(self.tasks[i + 1], UploadTask):
					# Waiting a little more to provide the opportunity to start other upload tasks
					time.sleep(2)
			i += 1
		self.ended = True

	def addTask(self, task):
		if not isinstance(task, Task):
			raise ValueError(f"Task wanted, '{task}' passed")
		self.tasks.append(task)


class OpsiPackageManager:  # pylint: disable=too-many-instance-attributes,too-many-public-methods

	def __init__(self, config, backend):
		self.config = config
		self.backend = backend

		self.aborted = False
		self.userInterface = None
		self.taskQueues = []
		self.productPackageFiles = {}
		self.productPackageFileMd5sums = {}  # pylint: disable=invalid-name
		self.runningTransfers = 0

		self.infoSubject = MessageSubject('info')
		self.transferSubject = MessageSubject('transfers')
		self.depotSubjects = {}

		self.productPackageFilesLock = threading.Lock()
		self.productPackageFilesMd5sumLock = threading.Lock()  # pylint: disable=invalid-name
		self.runningTransfersLock = threading.Lock()

		self.infoSubject.setMessage('opsi-package-manager')

		self.depotConnections = {}

		if not self.config['quiet']:
			logging_config(stderr_level=LOG_NONE)
			self.userInterface = UserInterface(config=self.config, subjects=[self.infoSubject, self.transferSubject])
		logger.info("OpsiPackageManager initiated")

	def abort(self):
		self.aborted = True
		running = True
		while running:
			running = False
			for tq in self.taskQueues:
				if not tq.ended:
					logger.notice("Aborting task queue '%s'", tq.name)
					tq.abort()

	def cleanup(self):
		logger.info("Cleaning up")
		if self.userInterface:
			self.userInterface.exit()

		for productPackageFile in self.productPackageFiles.values():
			productPackageFile.cleanup()

		for connection in self.depotConnections.values():
			connection.disconnect()

	def getDepotConnection(self, depotId):
		try:
			connection = self.depotConnections[depotId]
		except KeyError:
			depot = self.backend.host_getObjects(type='OpsiDepotserver', id=depotId)[0]

			connection = JSONRPCBackend(
				username=depotId,
				password=depot.getOpsiHostKey(),
				address=depotId,
				application=USER_AGENT,
				compression=True
			)
			self.depotConnections[depotId] = connection

		return connection

	def getRunningTransfers(self):
		with self.runningTransfersLock:
			return self.runningTransfers

	def setRunningTransfers(self, num):
		with self.runningTransfersLock:
			self.runningTransfers = num
		self.updateRunningTransfersSubject()

	def addRunningTransfer(self):
		with self.runningTransfersLock:
			self.runningTransfers += 1
		self.updateRunningTransfersSubject()

	def removeRunningTransfer(self):
		with self.runningTransfersLock:
			self.runningTransfers -= 1
		self.updateRunningTransfersSubject()

	def updateRunningTransfersSubject(self):
		if self.config['maxTransfers']:
			self.transferSubject.setMessage(
				_("%d/%d transfers running")
				% (self.runningTransfers, self.config['maxTransfers'])
			)
		else:
			self.transferSubject.setMessage(
				_("%d transfers running")
				% self.runningTransfers
			)

	def maxTransfersReached(self):
		if self.config['maxTransfers'] and (self.getRunningTransfers() >= self.config['maxTransfers']):
			return True
		return False

	def createDepotSubjects(self):
		if self.depotSubjects and self.userInterface:
			for subject in list(self.depotSubjects.values()):
				self.userInterface.removeSubject(subject)

		for depotId in self.config['depotIds']:
			self.depotSubjects[depotId] = MessageSubject(id=depotId, type='depot')
			if self.userInterface:
				self.userInterface.addSubject(self.depotSubjects[depotId])

	def getDepotSubject(self, depotId):
		if depotId not in self.depotSubjects:
			self.createDepotSubjects()
		return self.depotSubjects.get(depotId)

	def openProductPackageFile(self, packageFile):
		filename = os.path.basename(packageFile)
		with self.productPackageFilesLock:
			if filename not in self.productPackageFiles:
				self.infoSubject.setMessage(_('Opening package file %s') % filename)
				self.productPackageFiles[filename] = ProductPackageFile(packageFile, tempDir=self.config['tempDir'])
				self.productPackageFiles[filename].getMetaData()

	def getPackageControlFile(self, packageFile):
		filename = os.path.basename(packageFile)
		try:
			return self.productPackageFiles[filename].packageControlFile
		except KeyError:
			self.openProductPackageFile(packageFile)
			return self.productPackageFiles[filename].packageControlFile

	def getPackageMd5Sum(self, packageFile):
		filename = os.path.basename(packageFile)
		with self.productPackageFilesMd5sumLock:
			try:
				checksum = self.productPackageFileMd5sums[filename]
			except KeyError:
				checksum = md5sum(packageFile)
				self.productPackageFileMd5sums[filename] = checksum

			return checksum

	def waitForTaskQueues(self):
		self.infoSubject.setMessage(_('Waiting for task queues to finish up'))
		running = 1
		while running:
			running = 0
			for tq in self.taskQueues:
				if not tq.ended:
					running += 1
			self.infoSubject.setMessage(
				_('%d/%d task queues running') % (running, len(self.taskQueues))
			)
			time.sleep(1)

	def getTaskQueueErrors(self):
		errors = {}
		for tq in self.taskQueues:
			if not tq.errors:
				continue
			errors[tq.name] = tq.errors
		return errors

	def setActionRequestWhereInstalled(self, productId, depotId, actionRequest='setup', dependency=False):
		try:
			subject = self.getDepotSubject(depotId)
			subject.setMessage(_("Setting action setup for product %s where installed") % productId)
			actionRequest = forceActionRequest(actionRequest)
			clientIds = []
			for clientToDepot in self.backend.configState_getClientToDepotserver(depotIds=[depotId]):
				clientIds.append(clientToDepot['clientId'])

			if not clientIds:
				return

			productOnClients = self.backend.productOnClient_getObjects(clientId=clientIds, productId=productId, installationStatus='installed')
			if not productOnClients:
				return

			if dependency:
				for client in [x.clientId for x in productOnClients]:
					logger.notice(
						"Setting action '%s' with Dependencies for product '%s' on client: %s",
						actionRequest, productId, client
					)
					subject.setMessage(
						_("Setting action %s with Dependencies for product %s on client: %s")
						% (actionRequest, productId, client)
					)
					self.backend.setProductActionRequestWithDependencies(clientId=client, productId=productId, actionRequest=actionRequest)
				return

			clientIds = []
			for idx, poc in enumerate(productOnClients):
				productOnClients[idx].setActionRequest(actionRequest)
				clientIds.append(poc.clientId)

			clientIds.sort()
			logger.notice("Setting action '%s' for product '%s' on client(s): %s", actionRequest, productId, ', '.join(clientIds))
			subject.setMessage(_("Setting action %s for product %s on client(s): %s") % (actionRequest, productId, ', '.join(clientIds)))
			self.backend.productOnClient_updateObjects(productOnClients)
		except Exception as err:
			logger.error(err)
			subject.setMessage(_("Error: %s") % err, severity=2)
			raise

	def purgeProductPropertyStates(self, productId, depotId):
		try:
			subject = self.getDepotSubject(depotId)
			subject.setMessage(_("Purging product property states for product %s") % productId)
			depotClientIds = [
				clientToDepot['clientId'] for clientToDepot
				in self.backend.configState_getClientToDepotserver(depotIds=[depotId])
			]

			if not depotClientIds:
				return

			productPropertyStates = []
			clientIds = []
			for productPropertyState in self.backend.productPropertyState_getObjects(productId=productId, objectId=depotClientIds):
				productPropertyStates.append(productPropertyState)
				if productPropertyState.objectId not in clientIds:
					clientIds.append(productPropertyState.objectId)

			logger.notice("Purging product property states for product '%s' on client(s): %s", productId, ', '.join(clientIds))
			subject.setMessage(_("Purging product property states for product '%s' on client(s): %s") % (productId, ', '.join(clientIds)))

			self.backend.productPropertyState_deleteObjects(productPropertyStates)
		except Exception as err:  # pylint: disable=broad-except
			logger.error(err)
			subject.setMessage(_("Error: %s") % err, severity=2)
			raise

	def uploadToRepositories(self):
		for packageFile in self.config['packageFiles']:
			self.openProductPackageFile(packageFile)

		for depotId in self.config['depotIds']:
			tq = TaskQueue(name=f"Upload of package(s) {', '.join(self.config['packageFiles'])} to repository '{depotId}'")
			for packageFile in self.config['packageFiles']:
				tq.addTask(
					UploadTask(
						name=f"Upload of package '{packageFile}' to repository '{depotId}'",
						opsiPackageManager=self,
						method=self.uploadToRepository,
						params=[packageFile, depotId]
					)
				)

			if not self.aborted:
				self.taskQueues.append(tq)
				logger.info("Starting task queue '%s'", tq.name)
				tq.start()
		self.waitForTaskQueues()

	def uploadToRepository(self, packageFile, depotId):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
		subject = self.getDepotSubject(depotId)
		repository = None

		try:  # pylint: disable=too-many-nested-blocks
			# Process upload
			if self.maxTransfersReached():
				logger.notice("Waiting for free upload slot for upload of '%s' to depot '%s'", os.path.basename(packageFile), depotId)
				subject.setMessage(_("Waiting for free upload slot for %s") % os.path.basename(packageFile))
				while self.maxTransfersReached():
					time.sleep(0.1 * random.randint(1, 20))
			self.addRunningTransfer()

			logger.notice(
				"Processing upload of '%s' to depot '%s'",
				os.path.basename(packageFile), depotId
			)
			subject.setMessage(_("Processing upload of %s") % os.path.basename(packageFile))

			packageSize = os.stat(packageFile)[stat.ST_SIZE]
			localChecksum = self.getPackageMd5Sum(packageFile)
			destination = os.path.basename(packageFile)

			if "~" in destination:
				logger.notice("Custom-package detected, try to fix that.")
				destination = f"{destination.split('~')[0]}.opsi"

			productId = self.getPackageControlFile(packageFile).getProduct().getId()

			depot = self.backend.host_getObjects(type='OpsiDepotserver', id=depotId)[0]
			if not depot.repositoryLocalUrl.startswith('file://'):
				raise ValueError(f"Repository local url '{depot.repositoryLocalUrl}' not supported")
			depotRepositoryPath = depot.repositoryLocalUrl[7:]
			if depotRepositoryPath.endswith('/'):
				depotRepositoryPath = depotRepositoryPath[:-1]
			logger.info("Depot repository path is '%s'", depotRepositoryPath)
			logger.info("Using '%s' as repository url", depot.repositoryRemoteUrl)

			maxBandwidth = max(depot.maxBandwidth, 0)
			if not maxBandwidth and self.config['maxBandwidth']:
				maxBandwidth = self.config['maxBandwidth']
			if maxBandwidth:
				logger.info("Setting max bandwidth for depot '%s' to %d kBytes/s", depotId, maxBandwidth)

			repository = getRepository(
				url=depot.repositoryRemoteUrl,
				username=depotId,
				password=depot.opsiHostKey,
				maxBandwidth=maxBandwidth * 1000,
				application=USER_AGENT,
				readTimeout=24*3600  # Upload can take a long time
			)

			for dest in repository.content():
				if dest['name'] == destination:
					logger.info("Destination '%s' already exists on depot '%s'", destination, depotId)
					if not self.config['overwriteAlways']:
						# Not overwriting always => checking file sizes first
						if repository.fileInfo(destination)['size'] != packageSize:
							# Size differs => overwrite
							logger.info("Size of source and destination differs on depot '%s'", depotId)
						else:
							# Sizes match => check md5sum
							logger.info("Size of source and destination matches on depot '%s'", depotId)
							depotConnection = self.getDepotConnection(depotId)
							remoteChecksum = depotConnection.depot_getMD5Sum(depotRepositoryPath + '/' + destination)
							if localChecksum == remoteChecksum:
								# md5sum match => do not overwrite
								logger.info("MD5sum of source and destination matches on depot '%s'", depotId)
								logger.notice("No need to upload, '%s' is up to date on '%s'", os.path.basename(packageFile), depotId)
								subject.setMessage(_("No need to upload, %s is up to date") % os.path.basename(packageFile), severity=4)
								self.removeRunningTransfer()
								return

							# md5sums differ => overwrite
							logger.info("MD5sum of source and destination differs on depot '%s'", depotId)

					logger.info("Overwriting destination '%s' on depot '%s'", destination, depotId)
					subject.setMessage(_("Overwriting destination %s") % destination)
					break

			depotConnection = self.getDepotConnection(depotId)
			info = depotConnection.depot_getDiskSpaceUsage(depotRepositoryPath)
			if info['available'] < packageSize:
				subject.setMessage(
					_("Not enough disk space: %dMB needed, %dMB available")
					% ((packageSize / (1024 * 1024)), (info['available'] / (1024 * 1024)))
				)

				raise OSError(
					f"Not enough disk space on depot '{depotId}': "
					f"{(packageSize / (1024 * 1024))}MB needed, {(info['available'] / (1024 * 1024))}MB available"
				)

			oldPackages = []
			for dest in repository.content():
				fileInfo = parseFilename(dest['name'])
				if not fileInfo:
					continue

				if fileInfo.productId == productId and dest['name'] != destination:
					# same product, other version
					oldPackages.append(dest['name'])

			subject.setMessage(_("Starting upload"))
			try:
				if self.config['deltaUpload'] and oldPackages:
					deltaFile = None
					try:
						oldPackage = oldPackages[0]
						depotConnection = self.getDepotConnection(depotId)

						logger.notice("Getting librsync signature of '%s' on depot '%s'", oldPackage, depotId)
						subject.setMessage(_("Getting librsync signature of %s") % oldPackage)

						sig = depotConnection.depot_librsyncSignature(depotRepositoryPath + '/' + oldPackage)
						if not isinstance(sig, bytes):
							sig = sig.encode("ascii")
						sig = base64.b64decode(sig)

						logger.notice("Calculating delta for depot '%s'", depotId)
						subject.setMessage(_("Calculating delta"))

						deltaFilename = f'{productId}_{depotId}.delta'

						if deltaFilename in oldPackages:
							newDeltaFilename = deltaFilename
							i = 0
							while newDeltaFilename in oldPackages:
								newDeltaFilename = deltaFilename + '.' + str(i)
								i += 1
							deltaFilename = newDeltaFilename

						deltaFile = os.path.join(self.config['tempDir'], deltaFilename)

						librsyncDeltaFile(packageFile, sig, deltaFile)

						packageSize = os.stat(packageFile)[stat.ST_SIZE]
						deltaSize = os.stat(deltaFile)[stat.ST_SIZE]
						speedup = max((float(packageSize) / float(deltaSize)) - 1, 0)
						logger.notice("Delta calculated, upload speedup is %.3f", speedup)
						logger.notice("Starting delta upload of '%s' to depot '%s'", deltaFilename, depotId)
						subject.setMessage(
							_("Starting delta upload of %s") % os.path.basename(packageFile)
						)

						progressSubject = ProgressSubject(id=depotId, type='upload')
						progressSubject.setMessage(
							f"Uploading {os.path.basename(packageFile)} (delta upload, speedup {(speedup * 100):.1f}%)"
						)
						if self.userInterface:
							self.userInterface.addSubject(progressSubject)

						try:
							repository.upload(deltaFile, deltaFilename, progressSubject)
						finally:
							if self.userInterface:
								self.userInterface.removeSubject(progressSubject)

						logger.notice("Patching '%s'", oldPackage)
						subject.setMessage(_("Patching %s") % oldPackage)

						depotConnection.depot_librsyncPatchFile(
							f"{depotRepositoryPath}/{oldPackage}",
							f"{depotRepositoryPath}/{deltaFilename}",
							f"{depotRepositoryPath}/{destination}"
						)

						repository.delete(deltaFilename)
					finally:
						if deltaFile and os.path.exists(deltaFile):
							os.unlink(deltaFile)
				else:
					logger.notice("Starting upload of '%s' to depot '%s'", os.path.basename(packageFile), depotId)
					subject.setMessage(_("Starting upload of %s") % os.path.basename(packageFile))

					progressSubject = ProgressSubject(id=depotId, type='upload')
					progressSubject.setMessage(f"Uploading {os.path.basename(packageFile)}")
					if self.userInterface:
						self.userInterface.addSubject(progressSubject)
					try:
						repository.upload(packageFile, destination, progressSubject)
					finally:
						if self.userInterface:
							self.userInterface.removeSubject(progressSubject)

				logger.notice("Upload of '%s' to depot '%s' finished", os.path.basename(packageFile), depotId)
				subject.setMessage(_("Upload of %s finished") % os.path.basename(packageFile))

				for oldPackage in oldPackages:
					if oldPackage == destination:
						continue

					try:
						logger.notice("Deleting '%s' from depot '%s'", oldPackage, depotId)
						repository.delete(oldPackage)
					except Exception as err:  # pylint: disable=broad-except
						logger.error("Failed to delete '%s' from depot '%s': %s", oldPackage, depotId, err)

				logger.notice("Verifying upload")
				subject.setMessage(_("Verifying upload"))

				remotePackageFile = f"{depotRepositoryPath}/{destination}"
				depotConnection = self.getDepotConnection(depotId)
				remoteChecksum = depotConnection.depot_getMD5Sum(remotePackageFile)
				info = depotConnection.depot_getDiskSpaceUsage(depotRepositoryPath)
				if localChecksum != remoteChecksum:
					raise ValueError(
						f"MD5sum of source '{localChecksum}' and destination '{remoteChecksum}'"
						f"differ after upload to depot '{depotId}'"
					)

				if info['usage'] >= 0.9:
					logger.warning(
						"Warning: %d%% filesystem usage at repository on depot '%s'",
						int(100 * info['usage']), depotId
					)
					subject.setMessage(_("Warning: %d%% filesystem usage") % int(100 * info['usage']), severity=3)

				logger.notice("Upload of '%s' to depot '%s' successful", os.path.basename(packageFile), depotId)
				subject.setMessage(_("Upload of %s successful") % os.path.basename(packageFile), severity=4)

				remotePackageMd5sumFile = remotePackageFile + '.md5'
				try:
					depotConnection.depot_createMd5SumFile(remotePackageFile, remotePackageMd5sumFile)
				except Exception as err:  # pylint: disable=broad-except
					logger.warning("Failed to create md5sum file '%s': %s", remotePackageMd5sumFile, err)

				remotePackageZsyncFile = remotePackageFile + '.zsync'
				try:
					depotConnection.depot_createZsyncFile(remotePackageFile, remotePackageZsyncFile)
				except Exception as err:  # pylint: disable=broad-except
					logger.warning("Failed to create zsync file '%s': %s", remotePackageZsyncFile, err)
			finally:
				self.removeRunningTransfer()
		except Exception as uploadError:
			logger.info(uploadError, exc_info=True)
			logger.error(uploadError)
			subject.setMessage(_("Error: %s") % uploadError, severity=2)
			raise
		finally:
			if repository:
				logger.debug("Closing repository connection")
				try:
					repository.disconnect()
				except Exception as upload_error:  # pylint:disable=broad-except
					logger.error("Failed to disconnect from repository: %s", upload_error, exc_info=True)

	def installOnDepots(self):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
		sequence = [
			self.getPackageControlFile(packageFile).getProduct().id
			for packageFile in self.config['packageFiles']
		]

		for packageFile in self.config['packageFiles']:
			productId = self.getPackageControlFile(packageFile).getProduct().id
			for dependency in self.getPackageControlFile(packageFile).getPackageDependencies():
				try:
					ppos = sequence.index(productId)
					dpos = sequence.index(dependency['package'])
					if ppos < dpos:
						sequence.remove(dependency['package'])
						sequence.insert(ppos, dependency['package'])
				except Exception as err:  # pylint: disable=broad-except
					logger.debug(
						"While processing package '%s', dependency '%s': %s",
						packageFile, dependency['package'], err
					)

		sortedPackageFiles = []
		for productId in sequence:
			for packageFile in self.config['packageFiles']:
				if productId == self.getPackageControlFile(packageFile).getProduct().id:
					sortedPackageFiles.append(packageFile)
					break

		self.config['packageFiles'] = sortedPackageFiles

		if not self.config['forceInstall']:
			logger.info("Checking product locks")
			productIds = [
				self.getPackageControlFile(packageFile).getProduct().getId()
				for packageFile in self.config['packageFiles']
			]
			lockedProductsOnDepot = self.backend.productOnDepot_getObjects(
				productId=productIds,
				depotId=self.config['depotIds'],
				locked=True
			)

			if lockedProductsOnDepot:
				errors = [
					f"Product '{productOnDepot.productId}' currently locked on depot '{productOnDepot.depotId}'"
					for productOnDepot in lockedProductsOnDepot
				]
				nwl = "\n"
				raise RuntimeError(f"{nwl}{nwl.join(errors)}{nwl}Use --force to force installation")

		if self.userInterface and (self.config['properties'] == 'ask'):  # pylint: disable=too-many-nested-blocks
			productProperties = []
			products = {}
			for packageFile in self.config['packageFiles']:
				product = self.getPackageControlFile(packageFile).getProduct()
				for productProperty in self.getPackageControlFile(packageFile).getProductProperties():
					productProperties.append(productProperty)
					products[productProperty.getIdent(returnType='unicode')] = product

			if productProperties:
				self.userInterface.exit()
				ui = SnackUI()

				i = 0
				productProperties = sorted(productProperties, key=lambda pp: pp.propertyId)

				while i < len(productProperties):
					productProperty = productProperties[i]
					product = products[productProperty.getIdent(returnType='unicode')]

					logger.notice("Getting product property defaults from user")
					title = _('Please select product property defaults')
					text = (
						f"{_('Product')}: {product.id}\n   {product.name}\n\n"
						f"{_('Property')}: {productProperty.propertyId}\n   {productProperty.description}"
					)
					cancelLabel = _('Back')
					addNewValue = False
					if productProperty.possibleValues:
						entries = []
						for possibleValue in productProperty.possibleValues:
							entries.append(
								{
									'name': possibleValue,
									'value': possibleValue,
									'selected': possibleValue in productProperty.defaultValues
								}
							)
						radio = not productProperty.multiValue
						if productProperty.editable:
							entries.append(
								{
									'name': _('<other value>'),
									'value': '<other value>',
									'selected': False
								}
							)

						selection = ui.getSelection(entries, radio=radio, width=65, height=10, title=title, text=text, cancelLabel=cancelLabel)
						if selection is None:
							# back
							i -= 1
							i = max(i, 0)
							continue

						if _('<other value>') in selection:
							addNewValue = True

						productProperties[i].setDefaultValues(selection)
					else:
						addNewValue = True

					if addNewValue:
						default = ''
						if productProperty.defaultValues:
							default = productProperty.defaultValues[0]
						value = ui.getValue(width=65, height=13, title=title, default=default, password=False, text=text, cancelLabel=cancelLabel)
						if value is None:
							# back
							i -= 1
							i = max(i, 0)
							continue

						possibleValues = productProperties[i].getPossibleValues()
						if value not in possibleValues:
							possibleValues.append(value)
							productProperties[i].setPossibleValues(possibleValues)
						productProperties[i].setDefaultValues(value)
					logger.notice(
						"Product '%s', property '%s': default values set to: %s",
						productProperties[i].productId, productProperties[i].propertyId, productProperties[i].defaultValues
					)
					i += 1
				ui.exit()
				self.userInterface.initScreen()

		for depotId in self.config['depotIds']:
			tq = TaskQueue(name=f"Install of package(s) {', '.join(self.config['packageFiles'])} on depot '{depotId}'")
			for packageFile in self.config['packageFiles']:
				if self.config['uploadToLocalDepot'] or (depotId != self.config['localDepotId']):
					tq.addTask(
						UploadTask(
							name=f"Upload of package '{packageFile}' to repository '{depotId}'",
							opsiPackageManager=self,
							method=self.uploadToRepository,
							params=[packageFile, depotId]
						)
					)
				tq.addTask(
					InstallTask(
						name=f"Install of package '{os.path.basename(packageFile)}' on depot '{depotId}'",
						opsiPackageManager=self,
						method=self.installPackage,
						params=[packageFile, depotId]
					)
				)
			if not self.aborted:
				self.taskQueues.append(tq)
				logger.info("Starting task queue '%s'", tq.name)
				tq.start()
		self.waitForTaskQueues()

	def installPackage(self, packageFile, depotId):  # pylint: disable=too-many-branches,too-many-statements
		subject = self.getDepotSubject(depotId)
		depotPackageFile = packageFile

		try:
			depot = self.backend.host_getObjects(type='OpsiDepotserver', id=depotId)[0]
			if self.config['uploadToLocalDepot'] or (depotId != self.config['localDepotId']):
				if not depot.repositoryLocalUrl.startswith('file://'):
					raise ValueError(f"Repository local url '{depot.repositoryLocalUrl}' not supported")
				depotPackageFile = depot.repositoryLocalUrl[7:]
				if depotPackageFile.endswith('/'):
					depotPackageFile = depotPackageFile[:-1]
				depotPackageFile += '/' + os.path.basename(packageFile)

			if "~" in depotPackageFile and not os.path.exists(depotPackageFile):
				depotPackageFile = depotPackageFile.split("~")[0] + ".opsi"

			logger.info("Path to package file on depot '%s' is '%s'", depotId, depotPackageFile)

			packageFile = os.path.basename(packageFile)
			if self.config['newProductId']:
				logger.notice(
					"Installing package '%s' as '%s' on depot '%s'",
					packageFile,
					self.config['newProductId'],
					depotId
				)
				subject.setMessage(_("Installing package '%s' as '%s'") % (packageFile, self.config['newProductId']))
			else:
				logger.notice("Installing package '%s' on depot '%s'", packageFile, depotId)
				subject.setMessage(_("Installing package %s") % packageFile)

			packageControlFile = self.getPackageControlFile(packageFile)
			product = packageControlFile.getProduct()
			if self.config['newProductId']:
				product.setId(self.config['newProductId'])
			productId = product.getId()

			propertyDefaultValues = {}
			for productProperty in packageControlFile.getProductProperties():
				if self.config['newProductId']:
					productProperty.productId = productId

				propertyDefaultValues[productProperty.propertyId] = productProperty.defaultValues
				if propertyDefaultValues[productProperty.propertyId] is None:
					propertyDefaultValues[productProperty.propertyId] = []

			if self.config['properties'] == 'keep':
				for productPropertyState in self.backend.productPropertyState_getObjects(productId=productId, objectId=depotId):
					if productPropertyState.propertyId in propertyDefaultValues:
						propertyDefaultValues[productPropertyState.propertyId] = productPropertyState.values
						if propertyDefaultValues[productPropertyState.propertyId] is None:
							propertyDefaultValues[productPropertyState.propertyId] = []

			installationParameters = {
				'force': self.config['forceInstall'],
				'propertyDefaultValues': propertyDefaultValues,
				'tempDir': self.config['tempDir'],
			}
			if self.config['newProductId']:
				installationParameters['forceProductId'] = self.config['newProductId']
			if self.config['suppressPackageContentFileGeneration']:
				installationParameters['suppressPackageContentFileGeneration'] = self.config['suppressPackageContentFileGeneration']

			depotConnection = self.getDepotConnection(depotId)
			depotConnection.depot_installPackage(depotPackageFile, **installationParameters)

			if self.config['newProductId']:
				logger.notice(
					"Installation of package '%s' as %s on depot '%s' successful",
					packageFile,
					self.config['newProductId'],
					depotId
				)
				subject.setMessage(
					_("Installation of package {packageFile} as {forcedProductId} successful").format(
						packageFile=packageFile, forcedProductId=self.config['newProductId']
					), severity=4)

			else:
				set_product_cache_outdated(depotId, self.backend)
				logger.notice("Installation of package '%s' on depot '%s' successful", depotPackageFile, depotId)
				subject.setMessage(_("Installation of package %s successful") % packageFile, severity=4)

			if self.config['setupWhereInstalled']:
				if product.getSetupScript():
					self.setActionRequestWhereInstalled(productId=productId, depotId=depotId, actionRequest='setup')
				else:
					logger.warning("Cannot set action 'setup' for product '%s': setupScript not defined", productId)

			if self.config['setupWhereInstalledWithDependencies']:
				if product.getSetupScript():
					self.setActionRequestWhereInstalled(productId=productId, depotId=depotId, actionRequest='setup', dependency=True)
				else:
					logger.warning("Cannot set action 'setup' for product '%s': setupScript not defined", productId)

			if self.config['purgeClientProperties']:
				self.purgeProductPropertyStates(productId=productId, depotId=depotId)

			if self.config['updateWhereInstalled']:
				if product.getUpdateScript():
					self.setActionRequestWhereInstalled(productId=productId, depotId=depotId, actionRequest='update')
				else:
					logger.warning("Cannot set action 'update' for product '%s': updateScript not defined", productId)

		except Exception as installationError:
			logger.error(installationError)
			subject.setMessage(_("Error: %s") % installationError, severity=2)
			raise

	def uninstallPackages(self):
		for depotId in self.config['depotIds']:
			subject = self.getDepotSubject(depotId)
			packageNotInstalled = False
			productIds = []
			for product in self.config['productIds']:
				package = self.backend.productOnDepot_getObjects(depotId=depotId, productId=str(product))
				if not package:
					subject.setMessage(_("WARNING: Product {0} not installed on depot {1}.".format(product, depotId)), severity=3)  # pylint: disable=consider-using-f-string
					logger.warning("WARNING: Product %s not installed on depot %s.", product, depotId)
					packageNotInstalled = True

			for productOnDepot in self.backend.productOnDepot_getObjects(depotId=depotId, productId=self.config['productIds']):
				productIds.append(productOnDepot.productId)
			if not productIds:
				continue
			tq = TaskQueue(name=f"Uninstall of package(s) {', '.join(productIds)} on depot '{depotId}'")
			for productId in productIds:
				tq.addTask(
					UninstallTask(
						name=f"Uninstall of package '{productId}' on depot '{depotId}'",
						opsiPackageManager=self,
						method=self.uninstallPackage,
						params=[productId, depotId]
					)
				)
			self.taskQueues.append(tq)
			logger.info("Starting task queue '%s'", tq.name)
			tq.start()
		self.waitForTaskQueues()
		if packageNotInstalled:
			raise ValueError(
				f"At least one package failed to uninstall, please check {self.config['logFile']} for more information"
			)

	def uninstallPackage(self, productId, depotId):
		subject = self.getDepotSubject(depotId)

		try:
			logger.notice("Uninstalling package '%s' on depot '%s'", productId, depotId)
			subject.setMessage(_(f"Uninstalling package {productId}"))

			depot = self.backend.host_getObjects(type='OpsiDepotserver', id=depotId)[0]
			logger.info("Using '%s' as repository url", depot.getRepositoryRemoteUrl())
			repository = getRepository(url=depot.getRepositoryRemoteUrl(), username=depotId, password=depot.getOpsiHostKey())
			for destination in repository.listdir():
				fileInfo = parseFilename(destination)
				if not fileInfo:
					continue

				if not fileInfo.productId == productId:
					continue

				logger.info("Deleting destination '%s' on depot '%s'", destination, depotId)
				repository.delete(destination)

			depotConnection = self.getDepotConnection(depotId)
			depotConnection.depot_uninstallPackage(
				productId, force=self.config['forceUninstall'], deleteFiles=self.config['deleteFilesOnUninstall']
			)

			set_product_cache_outdated(depotId, self.backend)

			logger.notice("Uninstall of package '%s' on depot '%s' finished", productId, depotId)
			subject.setMessage(_("Uninstallation of package {0} successful").format(productId), severity=4)

		except Exception as err:
			logger.error(err)
			subject.setMessage(_("Error: %s") % err, severity=2)
			raise


class OpsiPackageManagerControl:
	def __init__(self):  # pylint: disable=too-many-branches,too-many-statements
		logger.debug("OpsiPackageManagerControl")
		# Set umask
		os.umask(0o077)
		self._pid = 0
		self._opm = None

		# Call signalHandler on signal SIGTERM, SIGINT
		signal(SIGTERM, self.signalHandler)
		signal(SIGINT, self.signalHandler)

		parser = ArgumentParser(add_help=False)

		parser.add_argument("-h", "--help", action="store_true", dest="help")
		parser.add_argument("-V", "--version", action="store_true", dest="version")
		parser.add_argument("-v", "--verbose", action="count", dest="verbose")
		parser.add_argument("-q", "--quiet", action="store_true", dest="quiet")
		parser.add_argument("-i", "--install", action="store_true", dest="COMMAND_INSTALL")
		parser.add_argument("-u", "--upload", action="store_true", dest="COMMAND_UPLOAD")
		parser.add_argument("-p", "--properties", action="store", dest="properties", default="keep", choices=['ask', 'package', 'keep'])
		parser.add_argument("--max-transfers", action="store", dest="maxTransfers", default=0, type=int)
		parser.add_argument("--max-bandwidth", action="store", dest="maxBandwidth", default=0, type=int)
		parser.add_argument("-l", "--list", action="store_true", dest="COMMAND_LIST")
		parser.add_argument("-D", "--differences", action="store_true", dest="COMMAND_DIFFERENCES")
		parser.add_argument("-r", "--remove", action="store_true", dest="COMMAND_REMOVE")
		parser.add_argument("-R", "--repo-remove", action="store_true", dest="COMMAND_REPOREMOVE")
		parser.add_argument("-x", "--extract", action="store_true", dest="COMMAND_EXTRACT")
		parser.add_argument("--new-product-id", action="store", dest="newProductId")
		parser.add_argument("-d", "--depots", action="store", dest="depots")
		parser.add_argument("-f", "--force", action="store_true", dest="force")
		parser.add_argument("-k", "--keep-files", action="store_true", dest="keepFiles")
		parser.add_argument("-t", "--temp-dir", action="store", dest="tempDir")
		parser.add_argument("-o", "--overwrite", action="store_true", dest="overwriteAlways")
		parser.add_argument("-n", "--no-delta", action="store_true", dest="noDelta")
		parser.add_argument("-S", "--setup", action="store_true", dest="setupWhereInstalled")
		parser.add_argument("-s", "--setup-with-dependencies", action="store_true", dest="setupWhereInstalledWithDependencies")
		parser.add_argument("-U", "--update", action="store_true", dest="updateWhereInstalled")
		parser.add_argument("--log-file", action="store", dest="logFile")
		parser.add_argument("--log-file-level", action="store", dest="fileLogLevel")
		parser.add_argument("--purge-client-properties", action="store_true", dest="purgeClientProperties")
		parser.add_argument("--suppress-pcf-generation", action="store_true", dest="suppressPackageContentFileGeneration")
		parser.add_argument("args", nargs="*")
		# Get commandline options and arguments
		try:
			self.opts = parser.parse_args()
			self.args = self.opts.args
		except Exception as err:  # pylint: disable=broad-except
			print(err, file=sys.stderr)
			self.usage()
			sys.exit(1)

		if self.opts.help:
			self.usage()
			sys.exit(0)

		if self.opts.version:
			print(f"{__version__} [python-opsi={python_opsi_version}]")
			sys.exit(0)

		need_opsi_server = (
			self.opts.COMMAND_INSTALL or
			self.opts.COMMAND_UPLOAD or
			self.opts.COMMAND_REMOVE or
			self.opts.COMMAND_LIST or
			self.opts.COMMAND_DIFFERENCES
		)

		self.setDefaultConfig(opsi_server=need_opsi_server)
		self.setCommandlineConfig()

		logging_config(
			log_file=self.config['logFile'],
			file_level=self.config['fileLogLevel'],
			stderr_level=self.config['consoleLogLevel'],
			stderr_format=DEFAULT_COLORED_FORMAT
		)

		self.backend = None
		if need_opsi_server:
			self.backend = BackendManager(
				backendConfigDir=self.config['backendConfigDir'],
				dispatchConfigFile=self.config['dispatchConfigFile'],
				extensionConfigDir=self.config['extendConfigDir'],
				extend=True
			)
			try:
				if not self.config['depotIds']:
					try:
						self.config['depotIds'] = [self.config['localDepotId']]
					except KeyError as err:
						raise RuntimeError(f"Failed to get local depot id: {err}") from err
				else:
					self.config['uploadToLocalDepot'] = True

				knownDepotIds = set(self.backend.host_getIdents(type='OpsiDepotserver', returnType='unicode'))  # pylint: disable=no-member

				if any(depotId.lower() == 'all' for depotId in self.config['depotIds']):
					self.config['depotIds'] = list(knownDepotIds)
				else:
					cleanedDepotIds = set()
					for depotId in self.config['depotIds']:
						depotId = forceHostId(depotId)
						if depotId not in knownDepotIds:
							raise RuntimeError(
								f"Depot '{depotId}' not in list of known depots: {','.join(knownDepotIds)}"
							)
						cleanedDepotIds.add(depotId)

					self.config['depotIds'] = list(cleanedDepotIds)

				self.config['depotIds'].sort()
			except Exception:
				if self.backend:
					self.backend.backend_exit()
				raise
		try:
			if self.config['command'] in ('install', 'upload', 'extract'):
				if len(self.config['packageFiles']) < 1:
					raise ValueError("No opsi package given")
				if self.config['command'] in ('install', 'upload', 'extract'):
					for i in range(len(self.config['packageFiles'])):
						self.config['packageFiles'][i] = os.path.abspath(self.config['packageFiles'][i])
						if not os.path.exists(self.config['packageFiles'][i]):
							raise OSError(f"Package file '{self.config['packageFiles'][i]}' does not exist or access denied")
				if self.config['command'] == 'extract' and self.config['newProductId'] and len(self.config['packageFiles']) > 1:
					raise ValueError("Cannot use new product id with multiple package files")

				if self.config['command'] == 'install' and self.config['newProductId']:
					if len(self.config['packageFiles']) > 1:
						raise ValueError(
							"Too many opsi packages given. "
							"Please supply only one package if forcing "
							"a product ID."
						)
			elif self.config['command'] in ('list', 'differences'):
				if not self.config['productIds']:
					self.config['productIds'] = ['*']
				if self.config['command'] == 'differences' and len(self.config['depotIds']) <= 1:
					raise ValueError("More than one depot id needed to display differences")

			elif self.config['command'] in ('remove', 'repo_remove'):
				if not self.config['productIds']:
					raise ValueError("No opsi product id given")
		except Exception:
			if self.backend:
				self.backend.backend_exit()
			raise

		try:
			self.processCommand()
		except Exception as err:
			logger.error(err, exc_info=True)
			raise RuntimeError(f"Failed to process command '{self.config['command']}': {err}") from err

	def processCommand(self):  # pylint: disable=too-many-branches
		try:
			command = self.config['command']
			if command == 'list':
				self.processListCommand()
			elif command == 'differences':
				self.processDifferencesCommand()
			elif command == 'upload':
				self.processUploadCommand()
			elif command == 'install':
				self.processInstallCommand()
			elif command == 'remove':
				self.processRemoveCommand()
			elif command == 'repo_remove':
				self.processRepoRemoveCommand()
			elif command == 'extract':
				self.processExtractCommand()
		finally:
			if self.backend:
				self.backend.backend_exit()

			for thread in threading.enumerate():
				try:
					thread.join(5)
				except Exception:  # pylint: disable=broad-except
					pass

		if self._opm:
			errors = self._opm.getTaskQueueErrors()
			if errors:
				print(_("Errors occurred: "), file=sys.stderr)
				for (name, errs) in errors.items():
					logger.error("Failure while processing %s:", name)
					print("   " + (_("Failure while processing %s:") % name), file=sys.stderr)
					for err in errs:
						logger.error("      %s", err)
						print(f"      {err}", file=sys.stderr)

				raise TaskError(f"{len(errors)} errors during the processing of tasks.")

	def processExtractCommand(self):
		progressSubject = ProgressSubject(id='extract', title='extracting')

		class ProgressNotifier(ProgressObserver):
			def __init__(self):  # pylint: disable=super-init-not-called
				self.usedWidth = 60
				try:
					tty = os.popen('tty').readline().strip()
					with open(tty, encoding="utf-8") as fd:
						terminalWidth = struct.unpack('hh', fcntl.ioctl(fd, termios.TIOCGWINSZ, '1234'))[1]
						self.usedWidth = min(self.usedWidth, terminalWidth)
				except Exception:  # pylint: disable=broad-except
					pass

			def progressChanged(self, subject, state, percent, timeSpend, timeLeft, speed):  # pylint: disable=too-many-arguments
				barlen = self.usedWidth - 10
				filledlen = int(round((barlen * percent / 100)))
				barstr = '=' * filledlen + ' ' * (barlen - filledlen)
				percent = f'{percent:0.2f}%'
				sys.stderr.write(f'\r {percent:>8} [{barstr}]\r')
				sys.stderr.flush()

			def messageChanged(self, subject, message):
				sys.stderr.write(f'\n{message}\n')
				sys.stderr.flush()

		if not self.config['quiet']:
			progressSubject.attachObserver(ProgressNotifier())

		extractTempDir = None
		if self.opts.tempDir:
			extractTempDir = os.path.abspath(self.config['tempDir'])

		destinationDir = os.path.abspath(os.getcwd())
		for packageFile in self.config['packageFiles']:
			if extractTempDir is None:
				ppf = ProductPackageFile(packageFile)
			else:
				ppf = ProductPackageFile(packageFile, tempDir=extractTempDir)

			productId = ppf.getMetaData().getProduct().getId()
			if not productId:
				raise ValueError(
					f"Failed to extract source from package '{packageFile}': product id not found in meta data"
				)
			newProductId = None
			if self.config['newProductId']:
				productId = forceProductId(self.config['newProductId'])
				newProductId = productId
			packageDestinationDir = os.path.join(destinationDir, productId)
			if os.path.exists(packageDestinationDir):
				raise OSError(f"Destination directory '{packageDestinationDir}' already exists")
			os.mkdir(packageDestinationDir)
			ppf.unpackSource(destinationDir=packageDestinationDir, newProductId=newProductId, progressSubject=progressSubject)
			if not self.config['quiet']:
				sys.stderr.write('\n\n')
			ppf.cleanup()

	def processListCommand(self):  # pylint: disable=too-many-locals
		terminalWidth = 60
		try:
			with os.popen('tty') as fd:
				tty = fd.readline().strip()
			with open(tty, encoding="utf-8") as fd:
				terminalWidth = struct.unpack('hh', fcntl.ioctl(fd, termios.TIOCGWINSZ, '1234'))[1]
		except Exception:  # pylint: disable=broad-except
			pass

		indent = "   "
		idWidth = versionWidth = int((terminalWidth - len(indent)) / 3)
		idWidth = min(idWidth, 25)
		versionWidth = min(versionWidth, 25)
		productOnDepots = self.backend.productOnDepot_getObjects(  # pylint: disable=no-member
			depotId=self.config['depotIds'], productId=self.config['productIds']
		)
		products = self.backend.product_getObjects(id=self.config['productIds'])  # pylint: disable=no-member

		productInfo = {}
		for product in products:
			if product.id not in productInfo:
				productInfo[product.id] = {}
			if product.productVersion not in productInfo[product.id]:
				productInfo[product.id][product.productVersion] = {}

			productInfo[product.id][product.productVersion][product.packageVersion] = product

			if len(product.id) > idWidth:
				idWidth = len(product.id)

		nameWidth = terminalWidth - len(indent) - idWidth - versionWidth - 4

		productOnDepotInfo = {}
		for depotId in self.config['depotIds']:
			productOnDepotInfo[depotId] = {}
		for productOnDepot in productOnDepots:
			productOnDepotInfo[productOnDepot.depotId][productOnDepot.productId] = productOnDepot

		if self.config['quiet']:
			return

		for (depotId, values) in productOnDepotInfo.items():
			print("-" * (len(depotId) + 4))
			print(f"- {depotId} -")
			print("-" * (len(depotId) + 4))
			print("%s%*s %*s %*s" % (  # pylint: disable=consider-using-f-string
				indent, -1 * idWidth,
				'Product ID',
				-1 * versionWidth, 'Version',
				-1 * nameWidth, 'Name'
			))
			print(f"{indent}{'=' * (terminalWidth - len(indent) - 2)}")
			productIds = list(values.keys())
			productIds.sort()

			for productId in productIds:
				productOnDepot = values[productId]
				product = productInfo[productOnDepot.productId][productOnDepot.productVersion][productOnDepot.packageVersion]
				print(
					"%s%*s %*s %*s" % (
						indent, -1 * idWidth,
						productId,
						-1 * versionWidth,
						product.version,
						-1 * nameWidth,
						product.name.replace('\n', '')[:nameWidth]
					)
				)
			print("")

	def processDifferencesCommand(self):  # pylint: disable=too-many-locals
		if self.config['quiet']:
			return

		depotIds = self.config['depotIds']
		productOnDepots = self.backend.productOnDepot_getObjects(  # pylint: disable=no-member
			depotId=depotIds,
			productId=self.config['productIds']
		)

		productIds = set()
		productOnDepotInfo = {depotId: {} for depotId in depotIds}
		for productOnDepot in productOnDepots:
			productIds.add(productOnDepot.productId)
			productOnDepotInfo[productOnDepot.depotId][productOnDepot.productId] = productOnDepot

		maxWidth = max(len(depotId) for depotId in depotIds)

		depotsInSync = True
		notInstalledText = _("not installed")
		for productId in sorted(productIds):
			differs = False
			lines = [productId]
			productVersion = None
			packageVersion = None
			for depotId in depotIds:
				try:
					productOnDepot = productOnDepotInfo[depotId][productId]
				except KeyError:
					lines.append(f"    {depotId:<{maxWidth}}: {notInstalledText}")
					differs = True
					continue

				if not productVersion:
					productVersion = productOnDepot.productVersion
				elif productVersion != productOnDepot.productVersion:
					differs = True

				if not packageVersion:
					packageVersion = productOnDepot.packageVersion
				elif packageVersion != productOnDepot.packageVersion:
					differs = True

				lines.append(f"    {depotId:<{maxWidth}}: {productOnDepot.version}")

			if differs:
				depotsInSync = False
				for line in lines:
					print(line)
				print("")

		if depotsInSync:
			syncMessage = _("There are no differences between the depots")
			print(syncMessage)

	def processUploadCommand(self):
		self._opm = OpsiPackageManager(self.config, self.backend)
		try:
			self._opm.uploadToRepositories()
		finally:
			self._opm.cleanup()

	def processInstallCommand(self):
		self._opm = OpsiPackageManager(self.config, self.backend)
		try:
			self._opm.installOnDepots()
		finally:
			self._opm.cleanup()

	def processRemoveCommand(self):
		self._opm = OpsiPackageManager(self.config, self.backend)
		try:
			self._opm.uninstallPackages()
		finally:
			self._opm.cleanup()

	def processRepoRemoveCommand(self):
		BASE_PATH = "/var/lib/opsi/repository"
		for product in self.config['productIds']:
			path = os.path.join(BASE_PATH, f"{product}_*")
			matches = glob.glob(path)
			if not matches:
				logger.error("Did not find product %s in %s", product, BASE_PATH)
				continue
			for filename in matches:
				logger.notice("Deleting %s", filename)
				os.remove(filename)

	def setDefaultConfig(self, opsi_server=True):
		self.config = {
			'fileLogLevel': LOG_WARNING,
			'consoleLogLevel': LOG_NONE,
			'logFile': None,
			'quiet': False,
			'tempDir': '/tmp',
			'backendConfigDir': None,
			'dispatchConfigFile': None,
			'extendConfigDir': None,
			'command': None,
			'packageFiles': [],
			'productIds': [],
			'properties': 'keep',
			'maxTransfers': 20,
			'maxBandwidth': 0,  # Kbyte/s
			'deltaUpload': False,
			'newProductId': None,
			'depotIds': [],
			'uploadToLocalDepot': False,
			'localDepotId': None,
			'forceInstall': False,
			'forceUninstall': False,
			'deleteFilesOnUninstall': True,
			'overwriteAlways': False,
			'setupWhereInstalled': False,
			'setupWhereInstalledWithDependencies': False,
			'updateWhereInstalled': False,
			'purgeClientProperties': False,
			'suppressPackageContentFileGeneration': False,
		}
		if opsi_server:
			self.config['logFile'] = '/var/log/opsi/opsi-package-manager.log'
			self.config['deltaUpload'] = librsyncDeltaFile is not None
			self.config['backendConfigDir'] = '/etc/opsi/backends'
			self.config['dispatchConfigFile'] = '/etc/opsi/backendManager/dispatch.conf'
			self.config['extendConfigDir'] = "/etc/opsi/backendManager/extend.d"
			self.config['localDepotId'] = forceHostId(getfqdn(conf='/etc/opsi/global.conf'))
			self.config['depotIds'] = None

	def setCommandlineConfig(self):  # pylint: disable=too-many-branches, too-many-statements
		if self.opts.properties == 'ask' and self.opts.quiet:
			raise ValueError("You cannot use properties=ask in quiet mode")

		if self.opts.quiet:
			self.config['quiet'] = True
		if self.opts.verbose:
			self.config['consoleLogLevel'] = 3 + self.opts.verbose
			if self.opts.properties != 'ask':
				self.config['quiet'] = True
		if self.opts.logFile:
			self.config['logFile'] = self.opts.logFile
		if self.opts.fileLogLevel:
			self.config['fileLogLevel'] = forceInt(self.opts.fileLogLevel)
		if self.opts.tempDir:
			self.config['tempDir'] = self.opts.tempDir
		if self.opts.depots:
			self.config['depotIds'] = self.opts.depots.split(',')
		if self.opts.newProductId:
			self.config['newProductId'] = forceProductId(self.opts.newProductId)
		if self.opts.maxBandwidth:
			self.config['maxBandwidth'] = self.opts.maxBandwidth
		if self.opts.maxTransfers:
			self.config['maxTransfers'] = self.opts.maxTransfers
		if self.opts.overwriteAlways:
			self.config['overwriteAlways'] = True
		if self.opts.noDelta:
			self.config['deltaUpload'] = False
		if self.opts.keepFiles:
			self.config['deleteFilesOnUninstall'] = False
		if self.opts.properties:
			self.config['properties'] = self.opts.properties
		if self.opts.force:
			self.config['forceInstall'] = self.config['forceUninstall'] = True
		if self.opts.setupWhereInstalled:
			self.config['setupWhereInstalled'] = True
		if self.opts.setupWhereInstalledWithDependencies:
			self.config['setupWhereInstalledWithDependencies'] = True
		if self.opts.updateWhereInstalled:
			self.config['updateWhereInstalled'] = True
		if self.opts.purgeClientProperties:
			self.config['purgeClientProperties'] = True
		if self.opts.suppressPackageContentFileGeneration:
			self.config['suppressPackageContentFileGeneration'] = True

		# Get command
		if self.opts.COMMAND_INSTALL:
			if self.config['command']:
				raise ValueError("More than one command specified")
			self.config['command'] = 'install'
		if self.opts.COMMAND_UPLOAD:
			if self.config['command']:
				raise ValueError("More than one command specified")
			self.config['command'] = 'upload'
		if self.opts.COMMAND_LIST:
			if self.config['command']:
				raise ValueError("More than one command specified")
			self.config['command'] = 'list'
		if self.opts.COMMAND_REMOVE:
			if self.config['command']:
				raise ValueError("More than one command specified")
			self.config['command'] = 'remove'
		if self.opts.COMMAND_REPOREMOVE:
			if self.config['command']:
				raise ValueError("More than one command specified")
			self.config['command'] = 'repo_remove'
		if self.opts.COMMAND_EXTRACT:
			if self.config['command']:
				raise ValueError("More than one command specified")
			self.config['command'] = 'extract'
		if self.opts.COMMAND_DIFFERENCES:
			if self.config['command']:
				raise ValueError("More than one command specified")
			self.config['command'] = 'differences'

		if not self.config['command']:
			raise ValueError("No command specified")

		if self.config['command'] in ('install', 'upload', 'extract'):
			self.config['packageFiles'] = self.args

		elif self.config['command'] in ('remove', 'repo_remove', 'list', 'differences'):
			self.config['productIds'] = self.args

	def signalHandler(self, signo, stackFrame):  # pylint: disable=unused-argument
		for thread in threading.enumerate():
			logger.debug("Running thread before signal: %s", thread)

		if signo in (SIGTERM, SIGINT):
			if self._opm:
				self._opm.abort()

		if self.backend:
			self.backend.backend_exit()

		for thread in threading.enumerate():
			logger.debug("Running thread after signal: %s", thread)

	def usage(self):  # pylint: disable=no-self-use
		print(f"\nUsage: {os.path.basename(sys.argv[0])} [options] <command>")
		print("")
		print("Manage opsi packages")
		print("")
		print("Commands:")
		print("  -i, --install      <opsi-package> ...      install opsi packages")
		print("  -u, --upload       <opsi-package> ...      upload opsi packages to repositories")
		print("  -l, --list         <regex>                 list opsi packages matching regex")
		print("  -D, --differences  <regex>                 show depot differences of opsi packages matching regex")
		print("  -r, --remove       <opsi-product-id> ...   uninstall opsi packages")
		print("  -R, --repo-remove  <opsi-product-id> ...   remove opsi packages from local repository")
		print("  -x, --extract      <opsi-package> ...      extract opsi packages to local directory")
		print("  -V, --version                              show program's version info and exit")
		print("  -h, --help                                 show this help message and exit")
		print("")
		print("Options:")
		print("  -v, --verbose                           increase verbosity (can be used multiple times)")
		print("  -q, --quiet                             do not display any messages")
		print("  --log-file         <log-file>           path to debug log file")
		print("  --log-file-level   <log-file-level>     log file level (default 4)")
		print("  -d, --depots       <depots>             comma separated list of depot ids to process")
		print("                                      all = all known depots")
		print("  -p, --properties   <mode>               mode for default product property values")
		print("                                  ask     = display dialog")
		print("                                  package = use defaults from package")
		print("	                                 keep    = keep depot defaults (default)")
		print("  --purge-client-properties               remove product property states of the installed product(s)")
		print("  -f, --force                             force install/uninstall (use with extreme caution)")
		print("  -U, --update                            set action \"update\" on hosts where installation status is \"installed\"")
		print("  -S, --setup                             set action \"setup\" on hosts where installation status is \"installed\"")
		print("  -s, --setup-with-dependencies           set action \"setup\" on hosts where installation status is \"installed\" with dependencies")  # pylint: disable=line-too-long
		print("  -o, --overwrite                         overwrite existing package on upload even if size matches")
		print("  -n, --no-delta                          full package transfers on uploads (do not use librsync)")
		print("  -k, --keep-files                        do not delete client data dir on uninstall")
		print("  -t, --temp-dir     <path>               tempory directory for package install")
		print("  --max-transfers    <num>                maximum number of simultaneous uploads")
		print("                                             0 = unlimited (default = 20)")
		print("  --max-bandwidth    <kbps>               maximum transfer rate for each transfer (in kilobytes per second)")
		print("                                             0 = unlimited (default = 0)")
		print("  --new-product-id   <product-id>         Set a new product id when extracting opsi package or")
		print("                                          set a specific product ID during installation.")
		print("  --suppress-pcf-generation               Suppress the generation of a package content file during package")
		print("                                          installation. Do not use with WAN extension!")
		print("")

def main():
	@contextmanager
	def keepOriginalTerminalSettings():
		try:
			fileno = sys.stdin.fileno()
			originalTerminalSettings = termios.tcgetattr(fileno)
		except Exception as err:  # pylint: disable=broad-except
			# Exception (25, 'Inappropriate ioctl for device') can happen on ssh connections
			logger.debug(err)
			originalTerminalSettings = None

		try:
			yield
		finally:
			if originalTerminalSettings:  # Restore terminal settings
				termios.tcsetattr(fileno, termios.TCSANOW, originalTerminalSettings)

	try:
		with keepOriginalTerminalSettings():
			OpsiPackageManagerControl()
	except SystemExit as err:
		sys.exit(err.code)
	except Exception as err:  # pylint: disable=broad-except
		logger.error(err, exc_info=True)
		print(f"\nERROR: {err}\n", file=sys.stderr)
		sys.exit(1)

def set_product_cache_outdated(depotId, backend):
	logger.debug("mark redis product cache as dirty for depot: %s", depotId)
	config_id = f"opsiconfd.{depotId}.product.cache.outdated"
	backend.config_createBool(id=config_id, description="", defaultValues=[True])
