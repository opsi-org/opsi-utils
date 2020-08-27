# -*- coding: utf-8 -*-

# opsi-package-manager is part of the client management solution opsi
# (open pc server integration) http://www.opsi.org
# Copyright (C) 2010-2019 uib GmbH <info@uib.de>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License version 3
# as published by the Free Software Foundation.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
opsi-package-manager

:author: Jan Schneider <j.schneider@uib.de>
:author: Niko Wenselowski <n.wenselowski@uib.de>
:license: GNU Affero General Public License version 3
"""

import base64
import curses
import fcntl
import gettext
import locale
import os
import random
import stat
import struct
import sys
import termios
import threading
import time
from contextlib import contextmanager
from signal import SIGWINCH, SIGTERM, SIGINT, signal
from optparse import OptionParser

from opsicommon.logging import logger, init_logging, logging_config, secret_filter, LOG_INFO, LOG_NONE, LOG_WARNING
from OPSI import __version__ as python_opsi_version
from OPSI.Backend.BackendManager import BackendManager
from OPSI.Backend.JSONRPC import JSONRPCBackend
from OPSI.Types import (
	forceActionRequest, forceBool, forceHostId, forceInt,
	forceList, forceProductId, forceUnicode, forceUnicodeList)
from OPSI.UI import SnackUI
from OPSI.Util import md5sum, getfqdn
from OPSI.Util.File.Opsi import parseFilename
from OPSI.Util.Message import (
	MessageSubject, ProgressObserver, ProgressSubject, SubjectsObserver)
from OPSI.Util.Repository import getRepository
from OPSI.Util.Product import ProductPackageFile
try:
	from OPSI.Util.Sync import librsyncDeltaFile
except ImportError:
	librsyncDeltaFile = None

from opsiutils import __version__

USER_AGENT = "opsi-package-manager/%s" % __version__

try:
	sp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	if os.path.exists(os.path.join(sp, "site-packages")):
		sp = os.path.join(sp, "site-packages")
	sp = os.path.join(sp, 'opsi-utils_data', 'locale')
	translation = gettext.translation('opsi-utils', sp)
	_ = translation.gettext
except Exception as error:
	logger.error("Failed to load locale from %s: %s", sp, error, exc_info=True)

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
		except Exception as error:
			logger.error(error, exc_info=True)
			self.exception = error
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


class CursesWindow:
	def __init__(self, height, width, y, x, title=u'', border=False):
		self.height = forceInt(height)
		self.width = forceInt(width)
		self.y = forceInt(y)
		self.x = forceInt(x)
		self.title = forceUnicode(title)
		self.border = forceBool(border)
		self.color = None
		self.win = curses.newwin(self.height, self.width, self.y, self.x)
		if self.border:
			self.win.border()
		self.setTitle(self.title)
		self.refresh()

	def resize(self, height, width, y, x):
		self.height = forceInt(height)
		self.width = forceInt(width)
		self.y = forceInt(y)
		self.x = forceInt(x)
		try:
			self.win.resize(height, width)
			self.win.mvwin(y, x)
			self.win.redrawwin()
			self.win.refresh()
		except Exception:
			pass

	def setTitle(self, title):
		self.title = forceUnicode(title)
		if not self.title:
			return
		if len(self.title) > self.width - 4:
			self.title = self.title[:self.width - 4]
		self.title = u'| %s |' % self.title
		attr = curses.A_NORMAL
		if self.color:
			attr |= self.color
		try:
			self.win.addstr(
				0,
				int((self.width - len(self.title)) / 2),
				self.title,
				attr)
		except Exception:
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
			for i in range(len(attr)):
				if i == 0:
					newAttr.append(forceUnicode(attr[i]))
				else:
					newAttr.append(attr[i])
			newAttr = tuple(newAttr)
			self.win.addstr(*newAttr)
		except Exception:
			pass

	def clrtoeol(self):
		try:
			self.win.clrtoeol()
		except Exception as error:
			logger.trace(error)

	def move(self, y, x):
		try:
			self.win.move(y, x)
		except Exception as error:
			logger.trace(error)

	def clear(self):
		try:
			self.win.clear()
		except Exception as error:
			logger.trace(error)

	def refresh(self):
		try:
			self.win.refresh()
		except Exception as error:
			logger.trace(error)

	def redraw(self):
		try:
			self.win.redrawwin()
			self.win.refresh()
		except Exception as error:
			logger.trace(error)


class CursesMainWindow(CursesWindow):
	def __init__(self):
		self.initScreen()

	def __del__(self):
		self.exitScreen()

	def initScreen(self):
		try:
			self.win = curses.initscr()
		except:
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

	def resize(self):
		return


class CursesTextWindow(CursesWindow):
	def __init__(self, height, width, y, x, title=u'', border=False):
		CursesWindow.__init__(self, height, width, y, x, title, border)
		self.lines = []
		self._lock = threading.Lock()

	def addLine(self, line, *params):
		line = forceUnicode(line)
		self._lock.acquire()
		if len(line) > self.width:
			line = line[:self.width - 1]
			self.lines.append((line, params))
		self.build()
		self._lock.release()

	def addLines(self, lines, *params):
		lines = forceUnicodeList(lines)
		self._lock.acquire()
		for line in lines:
			if len(line) > self.width:
				line = line[:self.width - 1]
			self.lines.append((line, params))
		self.build()
		self._lock.release()

	def setLines(self, lines, *params):
		lines = forceUnicodeList(lines)
		self._lock.acquire()
		self.lines = []
		for line in lines:
			if len(line) > self.width:
				line = line[:self.width - 1]
			self.lines.append((line, params))
		self.build()
		self._lock.release()

	def getLines(self):
		return self.lines

	def build(self):
		if len(self.lines) > self.height:
			self.lines = self.lines[-1 * self.height:]

		for i in range(len(self.lines)):
			if i >= len(self.lines) or i >= self.height:
				return
			self.move(i, 0)
			self.clrtoeol()

			(line, params) = self.lines[i]
			if params:
				self.addstr(line, *params)
			else:
				self.addstr(line)

	def resize(self, height, width, y, x):
		CursesWindow.resize(self, height, width, y, x)
		newLines = []
		for i in range(len(self.lines)):
			(line, params) = self.lines[i]
			if len(line) > self.width:
				line = line[:self.width - 1]
			newLines.append((line, params))
		self.lines = newLines


class UserInterface(SubjectsObserver):
	def __init__(self, config={}, subjects=[]):
		SubjectsObserver.__init__(self)
		self.config = config
		self.opmSubjects = subjects
		self.initScreen()

	def initScreen(self):
		# Important for ncurses to use the right encoding!?
		try:
			locale.setlocale(locale.LC_ALL, '')
		except Exception as error:
			raise RuntimeError(
				u"Setting locale failed - do you have $LC_ALL set? "
				u"Error: {0}".format(error)
			)

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
			self.loggerHeaderWindow.setLines([_(u'Log messages')])
			self.loggerHeaderWindow.refresh()

		self.mainWindow.refresh()

		self.setSubjects(self.opmSubjects)
		if self.loggerWindow:
			self.addSubject(logger.getMessageSubject())
			logger.setMessageSubjectLevel(self.config['consoleLogLevel'])

		signal(SIGWINCH, self.resized)
		logger.info("UserInterface initialized")

	def resized(self, signo, stackFrame):
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
		except Exception as error:
			logger.trace(error)

		try:
			self.subjectsChanged(self.getSubjects())
		except Exception:
			pass

	def subjectsChanged(self, subjects):
		for subject in subjects:
			if subject.getMessage():
				self.messageChanged(subject, subject.getMessage())

	def progressChanged(self, subject, state, percent, timeSpend, timeLeft, speed):
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
				self.loggerWindow.addLines(message.split(u'\n'), *params)
				self.loggerWindow.refresh()

		elif subject.getId() in ('info', 'transfers'):
			with self.__lock:
				info = u''
				transfers = u''
				for subject in self.getSubjects():
					if subject.getId() == u'info':
						info = subject.getMessage()
					elif subject.getId() == u'transfers':
						transfers = subject.getMessage()

				free = self.infoWindow.width - len(info) - len(transfers) - 1
				if free < 0:
					free = 0

				self.infoWindow.setLines([info + u' ' * free + transfers])
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

	def showProgress(self):
		if self.__lock.locked():
			return

		with self.__lock:
			subjects = {}
			for subject in self.getSubjects():
				if subject.getType() == u'depot':
					subjects[subject.getId()] = subject

			for subject in self.getSubjects():
				if subject.getType() == u'upload':
					subjects[subject.getId()] = subject

			ids = list(subjects.keys())
			ids.sort()
			maxIdLength = max([len(currentID) for currentID in ids] or [0])

			y = 0
			for currentID in ids:
				subject = subjects[currentID]
				if y >= self.progressWindow.height:
					# Screen full
					logger.info("Screen to small to display all progresses")
					break

				x = 0
				self.progressWindow.move(y, x)

				idString = u'%-*s | ' % (maxIdLength, subject.getId())
				if len(idString) > self.progressWindow.width:
					idString = idString[:self.progressWindow.width]
				self.progressWindow.addstr(idString, curses.A_BOLD)

				if len(idString) < self.progressWindow.width:
					color = None
					x += len(idString)
					self.progressWindow.move(y, x)
					maxSize = self.progressWindow.width - len(idString)
					message = subject.getMessage()
					severity = subject.getSeverity()
					if severity and severity in self._colors:
						color = self._colors[severity]

					if subject.getClass() is 'ProgressSubject':
						minutesLeft = str(int(subject.getTimeLeft() / 60))
						secondsLeft = str(int(subject.getTimeLeft() % 60))
						if len(minutesLeft) < 2:
							minutesLeft = '0' + minutesLeft
						if len(secondsLeft) < 2:
							secondsLeft = '0' + secondsLeft

						progress = u' %6s%% %8s KB%6s KB/s%6s:%s ETA' % (
							"%.2f" % subject.getPercent(),
							(subject.getState() / 1000),
							int(subject.getSpeed() / 1000),
							minutesLeft,
							secondsLeft
						)

						free = maxSize - len(message) - len(progress)
						if free < 0:
							free = 0
						message = message + u' ' * free + progress

					if len(message) > maxSize:
						message = message[:maxSize]

					if color:
						self.progressWindow.addstr(message, color)
					else:
						self.progressWindow.addstr(message)
					x += len(message)
					self.progressWindow.move(y, x)
					self.progressWindow.clrtoeol()
				y += 1

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
			raise RuntimeError(u"No tasks in queue")
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
			except Exception as error:
				logger.error("Task '%s' failed: %s", task.name, error)
				self.errors.append(error)
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
			raise ValueError(u"Task wanted, '%s' passed" % task)
		self.tasks.append(task)


class OpsiPackageManager(object):

	def __init__(self, config, backend):
		self.config = config
		self.backend = backend

		self.aborted = False
		self.userInterface = None
		self.taskQueues = []
		self.productPackageFiles = {}
		self.productPackageFileMd5sums = {}
		self.runningTransfers = 0

		self.infoSubject = MessageSubject(u'info')
		self.transferSubject = MessageSubject(u'transfers')
		self.depotSubjects = {}

		self.productPackageFilesLock = threading.Lock()
		self.productPackageFilesMd5sumLock = threading.Lock()
		self.runningTransfersLock = threading.Lock()

		self.infoSubject.setMessage(u'opsi-package-manager')

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
			self.transferSubject.setMessage(_("%d/%d transfers running")
				% (self.runningTransfers, self.config['maxTransfers']))
		else:
			self.transferSubject.setMessage(_("%d transfers running")
				% self.runningTransfers)

	def maxTransfersReached(self):
		if self.config['maxTransfers'] and (self.getRunningTransfers() >= self.config['maxTransfers']):
			return True
		return False

	def createDepotSubjects(self):
		if self.depotSubjects and self.userInterface:
			for subject in self.depotSubjects.values():
				self.userInterface.removeSubject(subject)

		for depotId in self.config['depotIds']:
			self.depotSubjects[depotId] = MessageSubject(id=depotId, type=u'depot')
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
				self.infoSubject.setMessage(_(u'Opening package file %s') % filename)
				self.productPackageFiles[filename] = ProductPackageFile(packageFile)
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
		self.infoSubject.setMessage(_(u'Waiting for task queues to finish up'))
		running = 1
		while running:
			running = 0
			for tq in self.taskQueues:
				if not tq.ended:
					running += 1
			self.infoSubject.setMessage(
				_(u'%d/%d task queues running') % (running, len(self.taskQueues))
			)
			time.sleep(1)

	def getTaskQueueErrors(self):
		errors = {}
		for tq in self.taskQueues:
			if not tq.errors:
				continue
			errors[tq.name] = tq.errors
		return errors

	def setActionRequestWhereInstalled(self, productId, depotId, actionRequest=u'setup', dependency=False):
		try:
			subject = self.getDepotSubject(depotId)
			subject.setMessage(_(u"Setting action setup for product %s where installed") % productId)
			actionRequest = forceActionRequest(actionRequest)
			clientIds = []
			for clientToDepot in self.backend.configState_getClientToDepotserver(depotIds=[depotId]):
				clientIds.append(clientToDepot['clientId'])

			if not clientIds:
				return

			productOnClients = self.backend.productOnClient_getObjects(clientId=clientIds, productId=productId, installationStatus=u'installed')
			if not productOnClients:
				return

			if dependency:
				for client in [x.clientId for x in productOnClients]:
					logger.notice(
						"Setting action '%s' with Dependencies for product '%s' on client: %s",
						actionRequest, productId, client
					)
					subject.setMessage(
						_(u"Setting action %s with Dependencies for product %s on client: %s")
						% (actionRequest, productId, client)
					)
					self.backend.setProductActionRequestWithDependencies(clientId=client, productId=productId, actionRequest=actionRequest)
				return

			clientIds = []
			for i in range(len(productOnClients)):
				productOnClients[i].setActionRequest(actionRequest)
				clientIds.append(productOnClients[i].clientId)

			clientIds.sort()
			logger.notice("Setting action '%s' for product '%s' on client(s): %s", actionRequest, productId, ', '.join(clientIds))
			subject.setMessage(_(u"Setting action %s for product %s on client(s): %s") % (actionRequest, productId, ', '.join(clientIds)))
			self.backend.productOnClient_updateObjects(productOnClients)
		except Exception as e:
			logger.error(e)
			subject.setMessage(_("Error: %s") % e, severity=2)
			raise

	def purgeProductPropertyStates(self, productId, depotId):
		try:
			subject = self.getDepotSubject(depotId)
			subject.setMessage(_(u"Purging product property states for product %s") % productId)
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
			subject.setMessage(_(u"Purging product property states for product '%s' on client(s): %s") % (productId, ', '.join(clientIds)))

			self.backend.productPropertyState_deleteObjects(productPropertyStates)
		except Exception as e:
			logger.error(e)
			subject.setMessage(_("Error: %s") % e, severity=2)
			raise

	def uploadToRepositories(self):
		for packageFile in self.config['packageFiles']:
			self.openProductPackageFile(packageFile)

		for depotId in self.config['depotIds']:
			tq = TaskQueue(name=u"Upload of package(s) %s to repository '%s'" % (', '.join(self.config['packageFiles']), depotId))
			for packageFile in self.config['packageFiles']:
				tq.addTask(
					UploadTask(
						name=u"Upload of package '%s' to repository '%s'" % (packageFile, depotId),
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

	def uploadToRepository(self, packageFile, depotId):
		subject = self.getDepotSubject(depotId)

		try:
			# Process upload
			if self.maxTransfersReached():
				logger.notice("Waiting for free upload slot for upload of '%s' to depot '%s'", os.path.basename(packageFile), depotId)
				subject.setMessage(_(u"Waiting for free upload slot for %s") % os.path.basename(packageFile))
				while self.maxTransfersReached():
					time.sleep(0.1 * random.randint(1, 20))
			self.addRunningTransfer()

			logger.notice(
				"Processing upload of '%s' to depot '%s'",
				os.path.basename(packageFile), depotId
			)
			subject.setMessage(_(u"Processing upload of %s") % os.path.basename(packageFile))

			packageSize = os.stat(packageFile)[stat.ST_SIZE]
			localChecksum = self.getPackageMd5Sum(packageFile)
			destination = os.path.basename(packageFile)

			if u"~" in destination:
				logger.notice("Custom-package detected, try to fix that.")
				destination = "%s%s" % (destination.split("~")[0], ".opsi")

			productId = self.getPackageControlFile(packageFile).getProduct().getId()

			depot = self.backend.host_getObjects(type='OpsiDepotserver', id=depotId)[0]
			if not depot.repositoryLocalUrl.startswith('file://'):
				raise ValueError(u"Repository local url '%s' not supported" % depot.repositoryLocalUrl)
			depotRepositoryPath = depot.repositoryLocalUrl[7:]
			if depotRepositoryPath.endswith('/'):
				depotRepositoryPath = depotRepositoryPath[:-1]
			logger.info("Depot repository path is '%s'", depotRepositoryPath)
			logger.info("Using '%s' as repository url", depot.repositoryRemoteUrl)

			maxBandwidth = depot.maxBandwidth
			if maxBandwidth < 0:
				maxBandwidth = 0
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
							remoteChecksum = depotConnection.depot_getMD5Sum(depotRepositoryPath + u'/' + destination)
							if localChecksum == remoteChecksum:
								# md5sum match => do not overwrite
								logger.info("MD5sum of source and destination matches on depot '%s'", depotId)
								logger.notice("No need to upload, '%s' is up to date on '%s'", os.path.basename(packageFile), depotId)
								subject.setMessage(_(u"No need to upload, %s is up to date") % os.path.basename(packageFile), severity=4)
								self.removeRunningTransfer()
								return

							# md5sums differ => overwrite
							logger.info("MD5sum of source and destination differs on depot '%s'", depotId)

					logger.info("Overwriting destination '%s' on depot '%s'", destination, depotId)
					subject.setMessage(_(u"Overwriting destination %s") % destination)
					break

			depotConnection = self.getDepotConnection(depotId)
			info = depotConnection.depot_getDiskSpaceUsage(depotRepositoryPath)
			if info['available'] < packageSize:
				subject.setMessage(
					_(u"Not enough disk space: %dMB needed, %dMB available")
					% ((packageSize / (1024 * 1024)), (info['available'] / (1024 * 1024)))
				)

				raise OSError(
					u"Not enough disk space on depot '%s': %dMB needed, %dMB available"
					% (depotId, (packageSize / (1024 * 1024)), (info['available'] / (1024 * 1024)))
				)

			oldPackages = []
			for dest in repository.content():
				fileInfo = parseFilename(dest['name'])
				if not fileInfo:
					continue

				if fileInfo.productId == productId and dest['name'] != destination:
					# same product, other version
					oldPackages.append(dest['name'])

			subject.setMessage(_(u"Starting upload"))
			try:
				if self.config['deltaUpload'] and oldPackages:
					deltaFile = None
					try:
						oldPackage = oldPackages[0]
						depotConnection = self.getDepotConnection(depotId)

						logger.notice("Getting librsync signature of '%s' on depot '%s'", oldPackage, depotId)
						subject.setMessage(_(u"Getting librsync signature of %s") % oldPackage)

						sig = depotConnection.depot_librsyncSignature(depotRepositoryPath + '/' + oldPackage)
						if type(sig) is not bytes:
							sig = sig.encode("ascii")
						sig = base64.decodestring(sig)
						
						logger.notice("Calculating delta for depot '%s'", depotId)
						subject.setMessage(_(u"Calculating delta"))

						deltaFilename = '%s_%s.delta' % (productId, depotId)

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
						speedup = (float(packageSize) / float(deltaSize)) - 1
						if speedup < 0:
							speedup = 0
						logger.notice("Delta calculated, upload speedup is %.3f", speedup)
						logger.notice("Starting delta upload of '%s' to depot '%s'", deltaFilename, depotId)
						subject.setMessage(
							_(u"Starting delta upload of %s")
							% os.path.basename(packageFile)
						)

						progressSubject = ProgressSubject(id=depotId, type='upload')
						progressSubject.setMessage(
							u"Uploading %s (delta upload, speedup %.1f%%)"
							% (os.path.basename(packageFile), speedup * 100)
						)
						if self.userInterface:
							self.userInterface.addSubject(progressSubject)

						try:
							repository.upload(deltaFile, deltaFilename, progressSubject)
						finally:
							if self.userInterface:
								self.userInterface.removeSubject(progressSubject)

						logger.notice("Patching '%s'", oldPackage)
						subject.setMessage(_(u"Patching %s") % oldPackage)

						depotConnection.depot_librsyncPatchFile(depotRepositoryPath + u'/' + oldPackage, depotRepositoryPath + u'/' + deltaFilename, depotRepositoryPath + u'/' + destination)

						repository.delete(deltaFilename)
					finally:
						if deltaFile and os.path.exists(deltaFile):
							os.unlink(deltaFile)
				else:
					logger.notice("Starting upload of '%s' to depot '%s'", os.path.basename(packageFile), depotId)
					subject.setMessage(_(u"Starting upload of %s") % os.path.basename(packageFile))

					progressSubject = ProgressSubject(id=depotId, type=u'upload')
					progressSubject.setMessage(u"Uploading %s" % os.path.basename(packageFile))
					if self.userInterface:
						self.userInterface.addSubject(progressSubject)
					try:
						repository.upload(packageFile, destination, progressSubject)
					finally:
						if self.userInterface:
							self.userInterface.removeSubject(progressSubject)

				logger.notice("Upload of '%s' to depot '%s' finished", os.path.basename(packageFile), depotId)
				subject.setMessage(_(u"Upload of %s finished") % os.path.basename(packageFile))

				for oldPackage in oldPackages:
					if oldPackage == destination:
						continue

					try:
						logger.notice("Deleting '%s' from depot '%s'", oldPackage, depotId)
						repository.delete(oldPackage)
					except Exception as deleteError:
						logger.error("Failed to delete '%s' from depot '%s': %s", oldPackage, depotId, deleteError)

				logger.notice("Verifying upload")
				subject.setMessage(_(u"Verifying upload"))

				remotePackageFile = depotRepositoryPath + u'/' + destination
				depotConnection = self.getDepotConnection(depotId)
				remoteChecksum = depotConnection.depot_getMD5Sum(remotePackageFile)
				info = depotConnection.depot_getDiskSpaceUsage(depotRepositoryPath)
				if localChecksum != remoteChecksum:
					raise ValueError(u"MD5sum of source '%s' and destination '%s' differ after upload to depot '%s'" % (localChecksum, remoteChecksum, depotId))

				if info['usage'] >= 0.9:
					logger.warning("Warning: %d%% filesystem usage at repository on depot '%s'", int(100 * info['usage']), depotId)
					subject.setMessage(_(u"Warning: %d%% filesystem usage") % int(100 * info['usage']), severity=3)

				logger.notice("Upload of '%s' to depot '%s' successful", os.path.basename(packageFile), depotId)
				subject.setMessage(_(u"Upload of %s successful") % os.path.basename(packageFile), severity=4)

				remotePackageMd5sumFile = remotePackageFile + u'.md5'
				try:
					depotConnection.depot_createMd5SumFile(remotePackageFile, remotePackageMd5sumFile)
				except Exception as checksumError:
					logger.warning("Failed to create md5sum file '%s': %s", remotePackageMd5sumFile, checksumError)

				remotePackageZsyncFile = remotePackageFile + u'.zsync'
				try:
					depotConnection.depot_createZsyncFile(remotePackageFile, remotePackageZsyncFile)
				except Exception as zsyncCreationError:
					logger.warning("Failed to create zsync file '%s': %s", remotePackageZsyncFile, zsyncCreationError)
			finally:
				self.removeRunningTransfer()
		except Exception as uploadError:
			logger.info(uploadError, exc_info=True)
			logger.error(uploadError)
			subject.setMessage(_("Error: %s") % uploadError, severity=2)
			raise

	def installOnDepots(self):
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
				except Exception as sequenceError:
					logger.debug("While processing package '%s', dependency '%s': %s", packageFile, dependency['package'], sequenceError)

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
					u"Product '%s' currently locked on depot '%s'" % (productOnDepot.productId, productOnDepot.depotId)
					for productOnDepot in lockedProductsOnDepot
				]
				raise RuntimeError(u'\n' + u'\n'.join(errors) + u'\nUse --force to force installation')

		if self.userInterface and (self.config['properties'] == 'ask'):
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
					title = _(u'Please select product property defaults')
					text = u'%s: %s\n   %s\n\n%s: %s\n   %s' % (_(u'Product'), product.id, product.name, _(u'Property'), productProperty.propertyId, productProperty.description)
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
									'name': _(u'<other value>'),
									'value': '<other value>',
									'selected': False
								}
							)

						selection = ui.getSelection(entries, radio=radio, width=65, height=10, title=title, text=text, cancelLabel=cancelLabel)
						if selection is None:
							# back
							i -= 1
							if i < 0:
								i = 0
							continue

						if _(u'<other value>') in selection:
							addNewValue = True

						productProperties[i].setDefaultValues(selection)
					else:
						addNewValue = True

					if addNewValue:
						default = u''
						if productProperty.defaultValues:
							default = productProperty.defaultValues[0]
						value = ui.getValue(width=65, height=13, title=title, default=default, password=False, text=text, cancelLabel=cancelLabel)
						if value is None:
							# back
							i -= 1
							if i < 0:
								i = 0
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
			tq = TaskQueue(name=u"Install of package(s) %s on depot '%s'" % (', '.join(self.config['packageFiles']), depotId))
			for packageFile in self.config['packageFiles']:
				if self.config['uploadToLocalDepot'] or (depotId != self.config['localDepotId']):
					tq.addTask(
						UploadTask(
							name=u"Upload of package '%s' to repository '%s'" % (packageFile, depotId),
							opsiPackageManager=self,
							method=self.uploadToRepository,
							params=[packageFile, depotId]
						)
					)
				tq.addTask(
					InstallTask(
						name=u"Install of package '%s' on depot '%s'" % (os.path.basename(packageFile), depotId),
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

	def installPackage(self, packageFile, depotId):
		subject = self.getDepotSubject(depotId)
		depotPackageFile = packageFile

		try:
			depot = self.backend.host_getObjects(type='OpsiDepotserver', id=depotId)[0]
			if self.config['uploadToLocalDepot'] or (depotId != self.config['localDepotId']):
				if not depot.repositoryLocalUrl.startswith('file://'):
					raise ValueError(u"Repository local url '%s' not supported" % depot.repositoryLocalUrl)
				depotPackageFile = depot.repositoryLocalUrl[7:]
				if depotPackageFile.endswith('/'):
					depotPackageFile = depotPackageFile[:-1]
				depotPackageFile += u'/' + os.path.basename(packageFile)

			if u"~" in depotPackageFile and not os.path.exists(depotPackageFile):
				depotPackageFile = depotPackageFile.split(u"~")[0] + u".opsi"

			logger.info("Path to package file on depot '%s' is '%s'", depotId, depotPackageFile)

			packageFile = os.path.basename(packageFile)
			if self.config['newProductId']:
				logger.notice(
					"Installing package '%s' as '%s' on depot '%s'",
					packageFile,
					self.config['newProductId'],
					depotId
				)
				subject.setMessage(_(u"Installing package '%s' as '%s'") % (packageFile, self.config['newProductId']))
			else:
				logger.notice("Installing package '%s' on depot '%s'", packageFile, depotId)
				subject.setMessage(_(u"Installing package %s") % packageFile)

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
						if propertyDefaultValues[productProperty.propertyId] is None:
							propertyDefaultValues[productProperty.propertyId] = []

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
				subject.setMessage(_(u"Installation of package {packageFile} as {forcedProductId} successful").format(packageFile=packageFile, forcedProductId=self.config['newProductId']), severity=4)
			else:
				logger.notice("Installation of package '%s' on depot '%s' successful", depotPackageFile, depotId)
				subject.setMessage(_(u"Installation of package %s successful") % packageFile, severity=4)

			if self.config['setupWhereInstalled']:
				if product.getSetupScript():
					self.setActionRequestWhereInstalled(productId=productId, depotId=depotId, actionRequest=u'setup')
				else:
					logger.warning("Cannot set action 'setup' for product '%s': setupScript not defined", productId)

			if self.config['setupWhereInstalledWithDependencies']:
				if product.getSetupScript():
					self.setActionRequestWhereInstalled(productId=productId, depotId=depotId, actionRequest=u'setup', dependency=True)
				else:
					logger.warning("Cannot set action 'setup' for product '%s': setupScript not defined", productId)

			if self.config['purgeClientProperties']:
				self.purgeProductPropertyStates(productId=productId, depotId=depotId)

			if self.config['updateWhereInstalled']:
				if product.getUpdateScript():
					self.setActionRequestWhereInstalled(productId=productId, depotId=depotId, actionRequest=u'update')
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
				package = self.backend.productOnDepot_getObjects(depotId=depotId, productId='%s' % product)
				if not package:
					subject.setMessage(_(u"WARNING: Product {0} not installed on depot {1}.".format(product, depotId)), severity=3)
					logger.warning("WARNING: Product %s not installed on depot %s.", product, depotId)
					packageNotInstalled = True

			for productOnDepot in self.backend.productOnDepot_getObjects(depotId=depotId, productId=self.config['productIds']):
				productIds.append(productOnDepot.productId)
			if not productIds:
				continue
			tq = TaskQueue(name=u"Uninstall of package(s) {0} on depot {1!r}".format(', '.join(productIds), depotId))
			for productId in productIds:
				tq.addTask(
					UninstallTask(
						name=u"Uninstall of package {0!r} on depot {1!r}".format(productId, depotId),
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
				'At least one package failed to uninstall, please check '
				'{} for more information'.format(self.config['logFile'])
			)

	def uninstallPackage(self, productId, depotId):
		subject = self.getDepotSubject(depotId)

		try:
			logger.notice("Uninstalling package '%s' on depot '%s'", productId, depotId)
			subject.setMessage(_(u"Uninstalling package {0}".format(productId)))

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
			depotConnection.depot_uninstallPackage(productId, force=self.config['forceUninstall'], deleteFiles=self.config['deleteFilesOnUninstall'])
			logger.notice("Uninstall of package '%s' on depot '%s' finished", productId, depotId)
			subject.setMessage(_(u"Uninstallation of package {0} successful").format(productId), severity=4)

		except Exception as uninstallError:
			logger.error(uninstallError)
			subject.setMessage(_("Error: %s") % uninstallError, severity=2)
			raise


class OpsiPackageManagerControl(object):
	def __init__(self):
		logger.debug("OpsiPackageManagerControl")
		# Set umask
		os.umask(0o077)
		self._pid = 0
		self._opm = None

		# Call signalHandler on signal SIGTERM, SIGINT
		signal(SIGTERM, self.signalHandler)
		signal(SIGINT, self.signalHandler)

		parser = OptionParser(add_help_option=False)

		parser.add_option("-h", "--help", action="store_true", dest="help")
		parser.add_option("-V", "--version", action="store_true", dest="version")
		parser.add_option("-v", "--verbose", action="count", dest="verbose")
		parser.add_option("-q", "--quiet", action="store_true", dest="quiet")
		parser.add_option("-i", "--install", action="store_true", dest="COMMAND_INSTALL")
		parser.add_option("-u", "--upload", action="store_true", dest="COMMAND_UPLOAD")
		parser.add_option("-p", "--properties", action="store", dest="properties", default="keep", choices=['ask', 'package', 'keep'])
		parser.add_option("--max-transfers", action="store", dest="maxTransfers", default=0, type="int")
		parser.add_option("--max-bandwidth", action="store", dest="maxBandwidth", default=0, type="int")
		parser.add_option("-l", "--list", action="store_true", dest="COMMAND_LIST")
		parser.add_option("-D", "--differences", action="store_true", dest="COMMAND_DIFFERENCES")
		parser.add_option("-r", "--remove", action="store_true", dest="COMMAND_REMOVE")
		parser.add_option("-x", "--extract", action="store_true", dest="COMMAND_EXTRACT")
		parser.add_option("--new-product-id", action="store", dest="newProductId")
		parser.add_option("-d", "--depots", action="store", dest="depots")
		parser.add_option("-f", "--force", action="store_true", dest="force")
		parser.add_option("-k", "--keep-files", action="store_true", dest="keepFiles")
		parser.add_option("-t", "--temp-dir", action="store", dest="tempDir")
		parser.add_option("-o", "--overwrite", action="store_true", dest="overwriteAlways")
		parser.add_option("-n", "--no-delta", action="store_true", dest="noDelta")
		parser.add_option("-S", "--setup", action="store_true", dest="setupWhereInstalled")
		parser.add_option("-s", "--setup-with-dependencies", action="store_true", dest="setupWhereInstalledWithDependencies")
		parser.add_option("-U", "--update", action="store_true", dest="updateWhereInstalled")
		parser.add_option("--log-file", action="store", dest="logFile")
		parser.add_option("--log-file-level", action="store", dest="fileLogLevel")
		parser.add_option("--purge-client-properties", action="store_true", dest="purgeClientProperties")
		parser.add_option("--suppress-pcf-generation", action="store_true", dest="suppressPackageContentFileGeneration")

		# Get commandline options and arguments
		try:
			(self.opts, self.args) = parser.parse_args()
		except Exception:
			self.usage()
			sys.exit(1)

		if self.opts.help:
			self.usage()
			sys.exit(0)

		if self.opts.version:
			print(f"{__version__} [python-opsi={python_opsi_version}]")
			sys.exit(0)

		self.setDefaultConfig()
		self.setCommandlineConfig()

		if self.config['logFile']:
			logging_config(log_file=self.config['logFile'], file_level=self.config['fileLogLevel'])

		logging_config(stderr_level=self.config['consoleLogLevel'])
		
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
				except KeyError as e:
					raise RuntimeError(u"Failed to get local depot id: %s" % e)
			else:
				self.config['uploadToLocalDepot'] = True

			knownDepotIds = set(self.backend.host_getIdents(type='OpsiDepotserver', returnType='unicode'))

			if any(depotId.lower() == 'all' for depotId in self.config['depotIds']):
				self.config['depotIds'] = list(knownDepotIds)
			else:
				cleanedDepotIds = set()
				for depotId in self.config['depotIds']:
					depotId = forceHostId(depotId)
					if depotId not in knownDepotIds:
						raise RuntimeError(u"Depot '%s' not in list of known depots: %s" % (depotId, u', '.join(knownDepotIds)))
					cleanedDepotIds.add(depotId)

				self.config['depotIds'] = list(cleanedDepotIds)

			self.config['depotIds'].sort()

			if self.config['command'] in (u'install', u'upload', u'extract'):
				if len(self.config['packageFiles']) < 1:
					raise ValueError(u"No opsi package given")
				if self.config['command'] in (u'install', u'upload', u'extract'):
					for i in range(len(self.config['packageFiles'])):
						self.config['packageFiles'][i] = os.path.abspath(self.config['packageFiles'][i])
						if not os.path.exists(self.config['packageFiles'][i]):
							raise OSError(u"Package file '%s' does not exist or access denied" % self.config['packageFiles'][i])
				if self.config['command'] == u'extract' and self.config['newProductId'] and len(self.config['packageFiles']) > 1:
					raise ValueError(u"Cannot use new product id with multiple package files")

				if self.config['command'] == u'install' and self.config['newProductId']:
					if len(self.config['packageFiles']) > 1:
						raise ValueError(
							u"Too many opsi packages given. "
							u"Please supply only one package if forcing "
							u"a product ID."
						)
			elif self.config['command'] in (u'list', u'differences'):
				if not self.config['productIds']:
					self.config['productIds'] = ['*']
				if self.config['command'] == u'differences' and len(self.config['depotIds']) <= 1:
					raise ValueError(u"More than one depot id needed to display differences")

			elif self.config['command'] == u'remove':
				if not self.config['productIds']:
					raise ValueError(u"No opsi product id given")
		except Exception:
			if self.backend:
				self.backend.backend_exit()
			raise

		try:
			self.processCommand()
		except Exception as processingError:
			logger.error(processingError, exc_info=True)
			raise RuntimeError(u"Failed to process command '%s': %s" % (self.config['command'], processingError))

	def processCommand(self):
		try:
			command = self.config['command']
			if command == u'list':
				self.processListCommand()
			elif command == u'differences':
				self.processDifferencesCommand()
			elif command == u'upload':
				self.processUploadCommand()
			elif command == u'install':
				self.processInstallCommand()
			elif command == u'remove':
				self.processRemoveCommand()
			elif command == u'extract':
				self.processExtractCommand()
		finally:
			if self.backend:
				self.backend.backend_exit()

			for thread in threading.enumerate():
				try:
					thread.join(5)
				except Exception:
					pass

		if self._opm:
			errors = self._opm.getTaskQueueErrors()
			if errors:
				print(_(u"Errors occurred: "), file=sys.stderr)
				for (name, errs) in errors.items():
					logger.error("Failure while processing %s:", name)
					print("   " + (_(u"Failure while processing %s:") % name), file=sys.stderr)
					for err in errs:
						logger.error(u"      %s", err)
						print((u"      %s" % err), file=sys.stderr)

				raise TaskError("{} errors during the processing of tasks.".format(len(errors)))

	def processExtractCommand(self):
		progressSubject = ProgressSubject(id='extract', title=u'extracting')

		class ProgressNotifier(ProgressObserver):
			def __init__(self):
				self.usedWidth = 60
				try:
					tty = os.popen('tty').readline().strip()
					fd = open(tty)
					terminalWidth = struct.unpack('hh', fcntl.ioctl(fd, termios.TIOCGWINSZ, '1234'))[1]
					if self.usedWidth > terminalWidth:
						self.usedWidth = terminalWidth
					fd.close()
				except Exception:
					pass

			def progressChanged(self, subject, state, percent, timeSpend, timeLeft, speed):
				barlen = self.usedWidth - 10
				filledlen = int("%0.0f" % (barlen * percent / 100))
				bar = u'=' * filledlen + u' ' * (barlen - filledlen)
				percent = '%0.2f%%' % percent
				sys.stderr.write('\r %8s [%s]\r' % (percent, bar))
				sys.stderr.flush()

			def messageChanged(self, subject, message):
				sys.stderr.write('\n%s\n' % message)
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
				raise ValueError(u"Failed to extract source from package '%s': product id not found in meta data" % (packageFile))
			newProductId = None
			if self.config['newProductId']:
				productId = forceProductId(self.config['newProductId'])
				newProductId = productId
			packageDestinationDir = os.path.join(destinationDir, productId)
			if os.path.exists(packageDestinationDir):
				raise OSError(u"Destination directory '%s' already exists" % packageDestinationDir)
			os.mkdir(packageDestinationDir)
			ppf.unpackSource(destinationDir=packageDestinationDir, newProductId=newProductId, progressSubject=progressSubject)
			if not self.config['quiet']:
				sys.stderr.write('\n\n')
			ppf.cleanup()

	def processListCommand(self):
		terminalWidth = 60
		try:
			tty = os.popen('tty').readline().strip()
			fd = open(tty)
			terminalWidth = struct.unpack('hh', fcntl.ioctl(fd, termios.TIOCGWINSZ, '1234'))[1]
			fd.close()
		except Exception:
			pass

		indent = u"   "
		idWidth = versionWidth = int((terminalWidth - len(indent)) / 3)
		if idWidth > 25:
			idWidth = 25
		if versionWidth > 25:
			versionWidth = 25

		productOnDepots = self.backend.productOnDepot_getObjects(depotId=self.config['depotIds'], productId=self.config['productIds'])
		products = self.backend.product_getObjects(id=self.config['productIds'])

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
			print(u"-" * (len(depotId) + 4))
			print(u"- %s -" % depotId)
			print(u"-" * (len(depotId) + 4))
			print(u"%s%*s %*s %*s" % (
				indent, -1 * idWidth,
				u'Product ID',
				-1 * versionWidth, u'Version',
				-1 * nameWidth, u'Name'))
			print(u"%s%s" % (indent, "=" * (terminalWidth - len(indent) - 2)))
			productIds = list(values.keys())
			productIds.sort()

			for productId in productIds:
				productOnDepot = productOnDepotInfo[depotId][productId]
				product = productInfo[productOnDepot.productId][productOnDepot.productVersion][productOnDepot.packageVersion]
				print(
					u"%s%*s %*s %*s" % (
						indent, -1 * idWidth,
						productId,
						-1 * versionWidth,
						product.version,
						-1 * nameWidth,
						product.name.replace(u'\n', u'')[:nameWidth]
					)
				)
			print("")

	def processDifferencesCommand(self):
		if self.config['quiet']:
			return

		depotIds = self.config['depotIds']
		productOnDepots = self.backend.productOnDepot_getObjects(
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
					lines.append(
						u"    {depotId:<{width}}: {text}".format(
							depotId=depotId,
							width=maxWidth,
							text=notInstalledText
						)
					)
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

				lines.append(
					u"    {depotId:<{width}}: {product.version}".format(
						depotId=depotId,
						width=maxWidth,
						product=productOnDepot
					)
				)

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

	def setDefaultConfig(self):
		self.config = {
			'fileLogLevel': LOG_WARNING,
			'consoleLogLevel': LOG_NONE,
			'logFile': '/var/log/opsi/opsi-package-manager.log',
			'quiet': False,
			'tempDir': u'/tmp',
			'backendConfigDir': u'/etc/opsi/backends',
			'dispatchConfigFile': u'/etc/opsi/backendManager/dispatch.conf',
			'extendConfigDir': u"/etc/opsi/backendManager/extend.d",
			'command': None,
			'packageFiles': [],
			'productIds': [],
			'properties': u'keep',
			'maxTransfers': 0,
			'maxBandwidth': 0,  # Kbyte/s
			'deltaUpload': True if librsyncDeltaFile is not None else False,
			'newProductId': None,
			'depotIds': None,
			'uploadToLocalDepot': False,
			'localDepotId': forceHostId(getfqdn(conf='/etc/opsi/global.conf')),
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

	def setCommandlineConfig(self):
		if self.opts.properties == 'ask' and self.opts.quiet:
			raise ValueError(u"You cannot use properties=ask in quiet mode")
		
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
				raise ValueError(u"More than one command specified")
			self.config['command'] = u'install'
		if self.opts.COMMAND_UPLOAD:
			if self.config['command']:
				raise ValueError(u"More than one command specified")
			self.config['command'] = u'upload'
		if self.opts.COMMAND_LIST:
			if self.config['command']:
				raise ValueError(u"More than one command specified")
			self.config['command'] = u'list'
		if self.opts.COMMAND_REMOVE:
			if self.config['command']:
				raise ValueError(u"More than one command specified")
			self.config['command'] = u'remove'
		if self.opts.COMMAND_EXTRACT:
			if self.config['command']:
				raise ValueError(u"More than one command specified")
			self.config['command'] = u'extract'
		if self.opts.COMMAND_DIFFERENCES:
			if self.config['command']:
				raise ValueError(u"More than one command specified")
			self.config['command'] = u'differences'

		if not self.config['command']:
			raise ValueError(u"No command specified")

		if self.config['command'] in (u'install', u'upload', u'extract'):
			self.config['packageFiles'] = self.args

		elif self.config['command'] in (u'remove', u'list', u'differences'):
			self.config['productIds'] = self.args

	def signalHandler(self, signo, stackFrame):
		for thread in threading.enumerate():
			logger.debug("Running thread before signal: %s", thread)

		if signo in (SIGTERM, SIGINT):
			if self._opm:
				self._opm.abort()

		if self.backend:
			self.backend.backend_exit()

		for thread in threading.enumerate():
			logger.debug("Running thread after signal: %s", thread)

	def usage(self):
		print(u"\nUsage: %s [options] <command>" % os.path.basename(sys.argv[0]))
		print(u"")
		print(u"Manage opsi packages")
		print(u"")
		print(u"Commands:")
		print(u"  -i, --install      <opsi-package> ...      install opsi packages")
		print(u"  -u, --upload       <opsi-package> ...      upload opsi packages to repositories")
		print(u"  -l, --list         <regex>                 list opsi packages matching regex")
		print(u"  -D, --differences  <regex>                 show depot differences of opsi packages matching regex")
		print(u"  -r, --remove       <opsi-product-id> ...   uninstall opsi packages")
		print(u"  -x, --extract      <opsi-package> ...      extract opsi packages to local directory")
		print(u"  -V, --version                              show program's version info and exit")
		print(u"  -h, --help                                 show this help message and exit")
		print(u"")
		print(u"Options:")
		print(u"  -v, --verbose                           increase verbosity (can be used multiple times)")
		print(u"  -q, --quiet                             do not display any messages")
		print(u"  --log-file         <log-file>           path to debug log file")
		print(u"  --log-file-level   <log-file-level>     log file level (default 4)")
		print(u"  -d, --depots       <depots>             comma separated list of depot ids to process")
		print(u"			 	             all = all known depots")
		print(u"  -p, --properties   <mode>               mode for default product property values")
		print(u"		                             ask     = display dialog")
		print(u"		                             package = use defaults from package")
		print(u"		                             keep    = keep depot defaults (default)")
		print(u"  --purge-client-properties               remove product property states of the installed product(s)")
		print(u"  -f, --force                             force install/uninstall (use with extreme caution)")
		print(u"  -U, --update                            set action \"update\" on hosts where installation status is \"installed\"")
		print(u"  -S, --setup                             set action \"setup\" on hosts where installation status is \"installed\"")
		print(u"  -s, --setup-with-dependencies           set action \"setup\" on hosts where installation status is \"installed\" with dependencies")
		print(u"  -o, --overwrite                         overwrite existing package on upload even if size matches")
		print(u"  -n, --no-delta                          full package transfers on uploads (do not use librsync)")
		print(u"  -k, --keep-files                        do not delete client data dir on uninstall")
		print(u"  -t, --temp-dir     <path>               tempory directory for package install")
		print(u"  --max-transfers    <num>                maximum number of simultaneous uploads")
		print(u"                                             0 = unlimited (default)")
		print(u"  --max-bandwidth    <kbps>               maximum transfer rate for each transfer (in kilobytes per second)")
		print(u"                                             0 = unlimited (default)")
		print(u"  --new-product-id   <product-id>         Set a new product id when extracting opsi package or")
		print(u"                                          set a specific product ID during installation.")
		print(u"  --suppress-pcf-generation               Suppress the generation of a package content file during package")
		print(u"                                          installation. Do not use with WAN extension!")
		print(u"")

def main():
	@contextmanager
	def keepOriginalTerminalSettings():
		try:
			fileno = sys.stdin.fileno()
			originalTerminalSettings = termios.tcgetattr(fileno)
		except Exception as readSettingsException:
			logger.warning(readSettingsException)
			originalTerminalSettings = None

		try:
			yield
		finally:
			if originalTerminalSettings:  # Restore terminal settings
				termios.tcsetattr(fileno, termios.TCSANOW, originalTerminalSettings)

	try:
		with keepOriginalTerminalSettings():
			OpsiPackageManagerControl()
	except SystemExit:
		pass
	except Exception as exception:
		logger.error(exception, exc_info=True)
		print(u"\nERROR: %s\n" % exception, file=sys.stderr)
		sys.exit(1)

