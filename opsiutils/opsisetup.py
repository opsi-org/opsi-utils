#! /usr/bin/python3
# -*- coding: utf-8 -*-

# This module is part of the desktop management solution opsi
# (open pc server integration) http://www.opsi.org

# Copyright (C) 2011-2019 uib GmbH
# http://www.uib.de/
# All rights reserved.

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
opsi-setup - swiss army knife for opsi administration.

:copyright: uib GmbH <info@uib.de>
:author: Jan Schneider <j.schneider@uib.de>
:author: Erol Ueluekmen <e.ueluekmen@uib.de>
:author: Niko Wenselowski <n.wenselowski@uib.de>
:license: GNU Affero General Public License version 3
"""

import codecs
import getopt
import json
import os
import pwd
import re
import shutil
import sys
import time

from opsicommon.logging import logger, init_logging, logging_config, secret_filter
from opsicommon.logging.constants import DEFAULT_COLORED_FORMAT, LOG_DEBUG, LOG_NONE, LOG_INFO, LOG_NOTICE

import OPSI.System.Posix as Posix
import OPSI.Util.Task.ConfigureBackend as backendUtils
from OPSI.Backend.BackendManager import BackendManager
from OPSI.Backend.JSONRPC import JSONRPCBackend
from OPSI.Config import DEFAULT_DEPOT_USER as CLIENT_USER
from OPSI.Object import OpsiDepotserver
from OPSI.System.Posix import (
	execute, getLocalFqdn, getNetworkConfiguration, which, Distribution)
from OPSI.Types import (
	forceBool, forceFilename, forceInt, forceIpAddress, forceUnicodeList)
from OPSI.UI import UIFactory
from OPSI.Util import blowfishDecrypt, randomString
from OPSI.Util.File.Opsi import BackendDispatchConfigFile
from OPSI.Util.Task.Certificate import (
	DEFAULT_CERTIFICATE_PARAMETERS, OPSICONFD_CERTFILE,
	NoCertificateError, UnreadableCertificateError,
	createCertificate, loadConfigurationFromCertificate, renewCertificate)
from OPSI.Util.Task.CleanupBackend import cleanupBackend
from OPSI.Util.Task.ConfigureBackend.ConfigDefaults import editConfigDefaults
from OPSI.Util.Task.ConfigureBackend.ConfigurationData import (
	initializeConfigs, readWindowsDomainFromSambaConfig)
from OPSI.Util.Task.ConfigureBackend.DHCPD import configureDHCPD
from OPSI.Util.Task.ConfigureBackend.MySQL import (
	DatabaseConnectionFailedException,
	configureMySQLBackend as configureMySQLBackendWithoutGUI
)
from OPSI.Util.Task.InitializeBackend import (
	_getServerConfig as getServerConfig, initializeBackends)
from OPSI.Util.Task.Rights import setRights, setPasswdRights
from OPSI.Util.Task.Sudoers import patchSudoersFileForOpsi
from OPSI.Util.Task.UpdateBackend.ConfigurationData import (
	getServerAddress, updateBackendData)
from OPSI.Util.Task.UpdateBackend.File import updateFileBackend
from OPSI.Util.Task.UpdateBackend.MySQL import updateMySQLBackend
from OPSI.Util.Task.Samba import SMB_CONF, configureSamba

from OPSI import __version__ as python_opsi_version
from opsiutils import __version__

init_logging(stderr_level=LOG_NOTICE, stderr_format=DEFAULT_COLORED_FORMAT)

LOG_FILE = '/tmp/opsi-setup.log'
DHCPD_CONF = Posix.locateDHCPDConfig('/etc/dhcp3/dhcpd.conf')

backendConfig = {}
ipAddress = None
sysConfig = {}


class CancelledByUserError(Exception):
	pass


def getDistribution():
	distribution = ''
	try:
		f = os.popen('lsb_release -d 2>/dev/null')
		distribution = f.read().split(':')[1].strip()
		f.close()
	except:
		pass
	return distribution


# TODO: use OPSI.System.Posix.Sysconfig for a more standardized approach
def getSysConfig():
	global sysConfig
	if sysConfig:
		return sysConfig

	logger.notice("Getting current system config")

	distri = Distribution()
	sysConfig['distributor'] = distri.distributor
	sysConfig['distribution'] = getDistribution()

	if not sysConfig['distributor'] or not sysConfig['distribution']:
		logger.warning("Failed to get distributor/distribution")

	sysConfig.update(getNetworkConfiguration(ipAddress))

	sysConfig['fqdn'] = getLocalFqdn()
	sysConfig['hostname'] = sysConfig['fqdn'].split(u'.')[0]
	sysConfig['domain'] = u'.'.join(sysConfig['fqdn'].split(u'.')[1:])
	sysConfig['winDomain'] = readWindowsDomainFromSambaConfig(SMB_CONF)

	logger.notice("System information:")
	logger.notice("   distributor  : %s", sysConfig['distributor'])
	logger.notice("   distribution : %s", sysConfig['distribution'])
	logger.notice("   ip address   : %s", sysConfig['ipAddress'])
	logger.notice("   netmask      : %s", sysConfig['netmask'])
	logger.notice("   subnet       : %s", sysConfig['subnet'])
	logger.notice("   broadcast    : %s", sysConfig['broadcast'])
	logger.notice("   fqdn         : %s", sysConfig['fqdn'])
	logger.notice("   hostname     : %s", sysConfig['hostname'])
	logger.notice("   domain       : %s", sysConfig['domain'])
	logger.notice("   win domain   : %s", sysConfig['winDomain'])

	return sysConfig


def configureClientUser():
	logger.notice("Configuring client user %s", CLIENT_USER)

	clientUserHome = pwd.getpwnam(CLIENT_USER)[5]

	sshDir = os.path.join(clientUserHome, '.ssh')

	if os.path.exists(sshDir):
		shutil.rmtree(sshDir)

	idRsa = os.path.join(sshDir, u'id_rsa')
	idRsaPub = os.path.join(sshDir, u'id_rsa.pub')
	authorizedKeys = os.path.join(sshDir, u'authorized_keys')
	if not os.path.exists(sshDir):
		os.mkdir(sshDir)
	if not os.path.exists(idRsa):
		logger.notice("   Creating RSA private key for user %s in '%s'", CLIENT_USER, idRsa)
		execute(u"%s -N '' -t rsa -f %s" % (which('ssh-keygen'), idRsa))

	if not os.path.exists(authorizedKeys):
		with codecs.open(idRsaPub, 'r', 'utf-8') as f:
			with codecs.open(authorizedKeys, 'w', 'utf-8') as f2:
				f2.write(f.read())

	setRights(sshDir)
	setPasswordForClientUser()


def setPasswordForClientUser():
	fqdn = getSysConfig()['fqdn']

	password = None
	backendConfig = {
		"dispatchConfigFile": u'/etc/opsi/backendManager/dispatch.conf',
		"backendConfigDir": u'/etc/opsi/backends',
		"extensionConfigDir": u'/etc/opsi/backendManager/extend.d',
		"depotBackend": True
	}

	try:
		with BackendManager(**backendConfig) as backend:
			depot = backend.host_getObjects(type='OpsiDepotserver', id=fqdn)[0]

			for configserver in backend.host_getObjects(type='OpsiConfigserver'):
				if configserver.id == fqdn:
					break  # we are on the configserver - nothing to do

				try:
					with JSONRPCBackend(address=configserver.id, username=depot.id, password=depot.opsiHostKey) as jsonrpcBackend:
						password = blowfishDecrypt(
							depot.opsiHostKey,
							jsonrpcBackend.user_getCredentials(username=CLIENT_USER, hostId=depot.id)['password']
						)
				except Exception as error:
					logger.info("Failed to get client user (%s) password from configserver: %s", CLIENT_USER, error)

			if not password:
				password = blowfishDecrypt(
					depot.opsiHostKey,
					backend.user_getCredentials(username=CLIENT_USER, hostId=depot.id)['password']
				)
	except Exception as error:
		logger.info("Failed to get client user (%s) password: %s", CLIENT_USER, error)

	if not password:
		logger.warning("No password for %s found. Generating random password.", CLIENT_USER)
		password = randomString(12)

	secret_filter.add_secrets(password)
	execute('opsi-admin -d task setPcpatchPassword "%s"' % password)


def update(fromVersion=None):
	isConfigServer = False
	try:
		bdc = BackendDispatchConfigFile(u'/etc/opsi/backendManager/dispatch.conf')
		for (regex, backends) in bdc.parse():
			if not re.search(regex, 'backend_createBase'):
				continue
			if 'jsonrpc' not in backends:
				isConfigServer = True
			break
	except Exception as error:
		logger.warning(error)

	configServerBackendConfig = {
		"dispatchConfigFile": u'/etc/opsi/backendManager/dispatch.conf',
		"backendConfigDir": u'/etc/opsi/backends',
		"extensionConfigDir": u'/etc/opsi/backendManager/extend.d',
		"depotbackend": False
	}

	if isConfigServer:
		try:
			with BackendManager(**configServerBackendConfig) as backend:
				backend.backend_createBase()
		except Exception as error:
			logger.warning(error)

	if isConfigServer:
		initializeConfigs()

		with BackendManager(**configServerBackendConfig) as backend:
			updateBackendData(backend)  # opsi 4.0 -> 4.1

	configureSamba()


def configureMySQLBackend(unattendedConfiguration=None):
	def notifyFunction(message):
		logger.notice(message)
		messageBox.addText(u"{0}\n".format(message))

	def errorFunction(message):
		logger.error(message)
		ui.showError(
			text=message, width=70, height=6,
			title=u'Problem configuring MySQL backend'
		)

	dbAdminUser = u'root'
	dbAdminPass = u''
	config = backendUtils.getBackendConfiguration(u'/etc/opsi/backends/mysql.conf')
	messageBox = None

	if unattendedConfiguration is not None:
		errorTemplate = u"Missing '{key}' in unattended configuration."
		for key in ('dbAdminUser', 'dbAdminPass'):
			if key not in unattendedConfiguration:
				raise Exception(errorTemplate.format(key=key))

		dbAdminUser = unattendedConfiguration['dbAdminUser']
		dbAdminPass = unattendedConfiguration['dbAdminPass']
		# User / PW must not show in config file -> delete from config.
		for key in ('dbAdminUser', 'dbAdminPass'):
			del unattendedConfiguration[key]

		config.update(unattendedConfiguration)

		logger.debug("Configuration for unattended mysql configuration: %s", config)
		configureMySQLBackendWithoutGUI(
			dbAdminUser, dbAdminPass, config, getSysConfig(),
			additionalBackendConfig=backendConfig,
		)
		return

	log_level = logger.level
	logging_config(stderr_level=LOG_NONE)
	ui = UIFactory(type='snack')
	try:
		while True:
			values = [
				{"name": u"Database host", "value": config['address']},
				{"name": u"Database admin user", "value": dbAdminUser},
				{"name": u"Database admin password", "value": dbAdminPass, "password": True},
				{"name": u"Opsi database name", "value": config['database']},
				{"name": u"Opsi database user", "value": config['username']},
				{"name": u"Opsi database password", "value": config['password'], "password": True}
			]
			values = ui.getValues(title=u'MySQL config', width=70, height=15, entries=values)
			if values is None:
				raise Exception(u"Canceled")

			config['address'] = values[0]["value"]
			dbAdminUser = values[1]["value"]
			dbAdminPass = values[2]["value"]
			config['database'] = values[3]["value"]
			config['username'] = values[4]["value"]
			config['password'] = values[5]["value"]

			messageBox = ui.createMessageBox(
				width=70, height=20, title=u'MySQL config', text=u''
			)

			try:
				configureMySQLBackendWithoutGUI(
					dbAdminUser, dbAdminPass,
					config, getSysConfig(),
					additionalBackendConfig=backendConfig,
					notificationFunction=notifyFunction,
					errorFunction=errorFunction
				)
				break
			except DatabaseConnectionFailedException:
				messageBox.hide()

		time.sleep(2)
		ui.showMessage(
			width=70, height=4,
			title=u'Success', text=u"MySQL Backend configuration done"
		)
	finally:
		if messageBox is not None:
			messageBox.hide()

		ui.exit()
		logging_config(stderr_level=log_level)


def registerDepot(unattendedConfiguration=None):
	"""
	Register a depot.

	The registration is done manually through a graphical commandline
	interface unless `unattendedConfiguration` supplies required data.

	For an unattended registration `unattendedConfiguration` should be
	of dict-type that must contain the following data:

		{
			"address": "fqdn.of.master",
			"username": "admin user for registration",
			"password": "the password of the user",
			"depot": {} //see below for config
		}


	An example configuration for `depot` can be:

		{
			"masterDepotId" : "id of master depot",
			"networkAddress" : "12.34.5.6/255.255.255.0",
			"description" : "Description of depot.",
			"inventoryNumber" : "inventory number",
			"ipAddress" : "12.34.5.6",
			"repositoryRemoteUrl" : "webdavs://depot.address:4447/repository",
			"depotLocalUrl" : "file:///var/lib/opsi/depot",
			"isMasterDepot" : true, // or false
			"notes" : "Put some notes here",
			"hardwareAddress" : "01:02:03:0a:0b:0c",
			"maxBandwidth" : 0,
			"repositoryLocalUrl" : "file:///var/lib/opsi/repository",
			"depotWebdavUrl" : "webdavs://depot.address:4447/depot",
			"depotRemoteUrl" : "smb://12.34.5.6/opsi_depot"
		}

	None of the entries in `depot` are mandatory.
	Machine-specific defaults will be used if no values are given.
	If a re-registration of a system is done the values of the existing
	depot will be used and any values given in `depot` will override
	the existing values.

	The `id` of the new depot will be the FQDN of the current system.
	"""
	backendConfigFile = u'/etc/opsi/backends/jsonrpc.conf'
	dispatchConfigFile = u'/etc/opsi/backendManager/dispatch.conf'

	getSysConfig()
	config = backendUtils.getBackendConfiguration(backendConfigFile)
	config.update(backendConfig)
	logger.info("Current jsonrpc backend config: %s", config)

	if unattendedConfiguration:
		depotConfig = unattendedConfiguration.pop('depot', {})
		config.update(unattendedConfiguration)

		jsonrpcBackend = _getJSONRPCBackendFromConfig(unattendedConfiguration)
		depot = _getConfiguredDepot(jsonrpcBackend, depotConfig)
	else:
		jsonrpcBackend, depot = _getBackendConfigViaGUI(config)

	logger.notice("Creating depot '%s'", depot.id)
	jsonrpcBackend.host_createObjects([depot])

	logger.notice("Getting depot '%s'", depot.id)
	depots = jsonrpcBackend.host_getObjects(id=depot.id)
	if not depots:
		raise Exception("Failed to create depot")
	depot = depots[0]
	config['username'] = depot.id
	config['password'] = depot.opsiHostKey
	jsonrpcBackend.backend_exit()

	logger.notice("Testing connection to config server as user '%s'", config['username'])
	try:
		jsonrpcBackend = JSONRPCBackend(address=config['address'], username=config['username'], password=config['password'])
	except Exception as e:
		raise Exception(u"Failed to connect to config server as user '%s': %s" % (config['username'], e))
	logger.notice("Successfully connected to config server as user '%s'", config['username'])

	logger.debug("Updating config file %s", backendConfigFile)
	backendUtils.updateConfigFile(backendConfigFile, config)

	logger.notice("Updating dispatch config '%s'", dispatchConfigFile)

	# We want to keep lines that are currently commented out and only
	# replace the currently active backend configuration
	with codecs.open(dispatchConfigFile, 'r', 'utf-8') as originalDispatchConfig:
		lines = [
			line for line in originalDispatchConfig
			if line.strip().startswith((';', '#'))
		]

	with codecs.open(dispatchConfigFile, 'w', 'utf-8') as newDispatchConfig:
		newDispatchConfig.writelines(lines)
		newDispatchConfig.write("backend_.* : jsonrpc, opsipxeconfd, dhcpd\n")
		newDispatchConfig.write(".*         : jsonrpc\n")
	logger.notice("Dispatch config '%s' updated", dispatchConfigFile)

	setRights()
	restartServices()


def _getJSONRPCBackendFromConfig(config):
	"""
	Connect to a depot using the given `config`.

	`config` must be a dict like the following:
		{
			"address": "fqdn.of.master",
			"username": "admin user for registration",
			"password": "the password of the user",
		}

	:type config: dict
	:raises OpsiAuthenticationError: If username / password is wrong.
	:raises Exception: If the supplied user is no opsi administrator.
	:return: A connected backend.
	:returntype: JSONRPCBackend
	"""
	connectionConfig = {
		"address": config['address'],
		"username": config['username'],
		"password": config['password']
	}
	logger.notice("Connecting to config server '%s' as user '%s'", connectionConfig.get("address"), connectionConfig.get("username"))
	jsonrpcBackend = JSONRPCBackend(**connectionConfig)
	if not jsonrpcBackend.accessControl_userIsAdmin():
		raise Exception(u"User {username!r} is not an admin user".format(**connectionConfig))

	logger.notice("Successfully connected to config server '%s' as user '%s'", connectionConfig.get("address"), connectionConfig.get("username"))
	return jsonrpcBackend


def _getConfiguredDepot(jsonrpcBackend, depotConfig=None):
	"""
	Get an configured depot.

	An example configuration for `depot` can be:
		{
			"masterDepotId" : "id of master depot",
			"networkAddress" : "12.34.5.6/255.255.255.0",
			"description" : "Description of depot.",
			"inventoryNumber" : "inventory number",
			"ipAddress" : "12.34.5.6",
			"repositoryRemoteUrl" : "webdavs://depot.address:4447/repository",
			"depotLocalUrl" : "file:///var/lib/opsi/depot",
			"isMasterDepot" : true, // or false
			"notes" : "Put some notes here",
			"hardwareAddress" : "01:02:03:0a:0b:0c",
			"maxBandwidth" : 0,
			"repositoryLocalUrl" : "file:///var/lib/opsi/repository",
			"depotWebdavUrl" : "webdavs://depot.address:4447/depot",
			"depotRemoteUrl" : "smb://depot.address/opsi_depot"
		}

	None of the entries are mandatory.
	We try to set sensible defaults

	The `id` of the new depot will be the FQDN of the current system.
	"""
	depotId = getLocalFqdn()
	depots = jsonrpcBackend.host_getObjects(id=depotId)
	try:
		depot = depots[0]
	except IndexError:  # no depot found
		serverConfig = getServerConfig(depotId, getSysConfig())

		if not serverConfig['hardwareAddress']:
			serverConfig['hardwareAddress'] = u''

		depot = OpsiDepotserver(**serverConfig)

	depotConfig = depotConfig or {}
	try:
		depot.setDescription(depotConfig['description'])
	except KeyError:
		logger.debug("Depot config holds no 'description'.")

	try:
		depot.setInventoryNumber(depotConfig['inventoryNumber'])
	except KeyError:
		logger.debug("Depot config holds no 'inventoryNumber'.")

	try:
		depot.setNotes(depotConfig["notes"])
	except KeyError:
		logger.debug("Depot config holds no 'notes'.")

	try:
		depot.setIpAddress(depotConfig["ipAddress"])
	except KeyError:
		logger.debug("Depot config holds no 'ipAddress'.")

	try:
		depot.setHardwareAddress(depotConfig["hardwareAddress"])
	except KeyError:
		logger.debug("Depot config holds no 'hardwareAddress'.")

	try:
		depot.setNetworkAddress(depotConfig['networkAddress'])
	except KeyError:
		logger.debug("Depot config holds no 'networkAddress'.")

	try:
		depot.setMaxBandwidth(forceInt(depotConfig['maxBandwidth']))
	except KeyError:
		logger.debug("Depot config holds no 'maxBandwidth'.")

	try:
		depot.setDepotLocalUrl(depotConfig["depotLocalUrl"])
	except KeyError:
		logger.debug("Depot config holds no 'depotLocalUrl'.")

	try:
		depot.setDepotRemoteUrl(depotConfig["depotRemoteUrl"])
	except KeyError:
		logger.debug("Depot config holds no 'depotRemoteUrl'.")

	try:
		depot.setDepotWebdavUrl(depotConfig["depotWebdavUrl"])
	except KeyError:
		logger.debug("Depot config holds no 'depotWebdavUrl'.")
		depot.depotWebdavUrl = None

	try:
		depot.setRepositoryLocalUrl(depotConfig["repositoryLocalUrl"])
	except KeyError:
		logger.debug("Depot config holds no 'repositoryLocalUrl'.")

	try:
		depot.setRepositoryRemoteUrl(depotConfig["repositoryRemoteUrl"])
	except KeyError:
		logger.debug("Depot config holds no 'repositoryRemoteUrl'.")

	try:
		depot.setWorkbenchLocalUrl(depotConfig["workbenchLocalUrl"])
	except KeyError:
		logger.debug("Depot config holds no 'workbenchLocalUrl'.")

	try:
		depot.setWorkbenchRemoteUrl(depotConfig["workbenchRemoteUrl"])
	except KeyError:
		logger.debug("Depot config holds no 'workbenchRemoteUrl'.")

	try:
		depot.setIsMasterDepot(forceBool(depotConfig["isMasterDepot"]))
	except KeyError:
		logger.debug("Depot config holds no 'isMasterDepot'.")

	try:
		depot.setMasterDepotId(depotConfig["masterDepotId"])
	except KeyError:
		logger.debug("Depot config holds no 'masterDepotId'.")
		depot.masterDepotId = None

	if depot.maxBandwidth < 0:
		depot.maxBandwidth = 0

	return depot


def _getBackendConfigViaGUI(config):
	log_level = logger.level
	logging_config(stderr_level=LOG_NONE)

	ui = UIFactory(type='snack')
	try:
		adminUser = u'root'
		adminPass = u''
		messageBox = None
		while True:
			values = [
				{"name": u"Config server", "value": config['address']},
				{"name": u"Opsi admin user", "value": adminUser},
				{"name": u"Opsi admin password", "value": adminPass, "password": True}
			]
			values = ui.getValues(title=u'Config server connection', width=70, height=10, entries=values)
			if values is None:
				raise Exception(u"Canceled")

			config['address'] = values[0]["value"]
			adminUser = values[1]["value"]
			adminPass = values[2]["value"]

			messageBox = ui.createMessageBox(width=70, height=20, title=u'Register depot', text=u'')
			# Connect to config server
			logger.notice("Connecting to config server '%s' as user '%s'", config['address'], adminUser)
			messageBox.addText(u"Connecting to config server '%s' as user '%s'\n" % (config['address'], adminUser))

			try:
				jsonrpcBackend = _getJSONRPCBackendFromConfig({
					"address": config['address'],
					"username": adminUser,
					"password": adminPass
				})
			except Exception as e:
				messageBox.hide()
				logger.error("Failed to connect to config server '%s' as user '%s': %s", config['address'], adminUser, e)
				ui.showError(
					title=u'Failed to connect', width=70, height=6, seconds=0,
					text=u"Failed to connect to config server '%s' as user '%s': %s" % (config['address'], adminUser, e)
				)
				continue

			messageBox.addText(u"Successfully connected to config server '%s' as user '%s'\n" % (config['address'], adminUser))
			break

		fqdn = getLocalFqdn()
		depots = jsonrpcBackend.host_getObjects(id=fqdn)
		try:
			depot = depots[0]

			if depot.getType() == 'OpsiClient':
				deleteClient = ui.yesno(
					title=u'ID already in use',
					text=u'''We already have an client with the ID %s.
We can not register a new depot with the same ID.
Should the client be deleted to free the ID?
This will remove the client and it's settings from opsi.''' % depot.id,
					okLabel=u'Delete client',
					cancelLabel=u'Cancel'
				)

				if not deleteClient:
					raise CancelledByUserError(u'Cancelled')

				jsonrpcBackend.host_delete(id=depot.id)

				raise ValueError("We want defaults.")

			if not depot.depotWebdavUrl:
				depot.depotWebdavUrl = u''
			if not depot.masterDepotId:
				depot.masterDepotId = u''
			if not depot.hardwareAddress:
				depot.hardwareAddress = getSysConfig()['hardwareAddress'] or u''
			if not depot.ipAddress:
				depot.ipAddress = getSysConfig()['ipAddress'] or u''
			if not depot.networkAddress:
				depot.ipAddress = u'%s/%s' % (getSysConfig()['subnet'], getSysConfig()['netmask'])
			if not depot.depotWebdavUrl:
				depot.depotWebdavUrl = u'webdavs://%s:4447/depot' % fqdn

			if not depot.workbenchLocalUrl:
				depot.workbenchLocalUrl = u'file:///var/lib/opsi/workbench'

			if not depot.workbenchRemoteUrl:
				depotAddress = getServerAddress(depot.depotRemoteUrl)
				remoteWorkbenchPath = u'smb://{}/opsi_workbench'.format(depotAddress)
				depot.workbenchRemoteUrl = remoteWorkbenchPath
		except (IndexError, ValueError):
			serverConfig = getServerConfig(fqdn, getSysConfig())

			for key in ('description', 'notes', 'hardwareAddress', 'ipAddress', 'inventoryNumber'):
				if not serverConfig[key]:
					# We want to make sure this is set to an empty
					# string for the UI
					serverConfig[key] = u''

			depot = OpsiDepotserver(**serverConfig)

		while True:
			if depot.maxBandwidth < 0:
				depot.maxBandwidth = 0
			if depot.maxBandwidth > 0:
				depot.maxBandwidth = int(depot.maxBandwidth / 1000)

			values = [
				{"name": u"Description", "value": depot.description},
				{"name": u"Inventory number", "value": depot.inventoryNumber},
				{"name": u"Notes", "value": depot.notes},
				{"name": u"Ip address", "value": depot.ipAddress},
				{"name": u"Hardware address", "value": depot.hardwareAddress},
				{"name": u"Network address", "value": depot.networkAddress},
				{"name": u"Maximum bandwidth (kbyte/s)", "value": depot.maxBandwidth},
				{"name": u"Local depot url", "value": depot.depotLocalUrl},
				{"name": u"Remote depot url", "value": depot.depotRemoteUrl},
				{"name": u"Depot webdav url", "value": depot.depotWebdavUrl},
				{"name": u"Local repository url", "value": depot.repositoryLocalUrl},
				{"name": u"Remote repository url", "value": depot.repositoryRemoteUrl},
				{"name": u"Local workbench url", "value": depot.workbenchLocalUrl},
				{"name": u"Remote workbench url", "value": depot.workbenchRemoteUrl},
				{"name": u"Is master depot", "value": depot.isMasterDepot},
				{"name": u"Master depot id", "value": depot.masterDepotId or u''},
			]
			values = ui.getValues(title=u'Depot server settings', width=70, height=16, entries=values)
			if values is None:
				raise Exception(u"Canceled")

			error = None
			try:
				depot.setDescription(values[0].get('value'))
			except Exception as e:
				if not error:
					error = u'Invalid description'

			try:
				depot.setInventoryNumber(values[1].get('value'))
			except Exception as e:
				if not error:
					error = u'Inventory number invalid'

			try:
				depot.setNotes(values[2].get('value'))
			except Exception as e:
				if not error:
					error = u'Invalid notes'

			try:
				depot.setIpAddress(values[3].get('value'))
			except Exception as e:
				if not error:
					error = u'Invalid ip address'

			try:
				depot.setHardwareAddress(values[4].get('value'))
			except Exception as e:
				if not error:
					error = u'Invalid hardware address'

			try:
				depot.setNetworkAddress(values[5].get('value'))
			except Exception as e:
				if not error:
					error = u'Invalid network address'

			try:
				depot.setMaxBandwidth(forceInt(values[6].get('value')) * 1000)
			except Exception as e:
				if not error:
					error = u'Invalid maximum bandwidth'

			try:
				depot.setDepotLocalUrl(values[7].get('value'))
			except Exception as e:
				if not error:
					error = u'Depot local url invalid'

			try:
				depot.setDepotRemoteUrl(values[8].get('value'))
			except Exception as e:
				if not error:
					error = u'Depot remote url invalid'

			try:
				if values[9].get('value'):
					depot.setDepotWebdavUrl(values[9].get('value'))
				else:
					depot.depotWebdavUrl = None
			except Exception as e:
				if not error:
					error = u'Depot webdav url invalid'

			try:
				depot.setRepositoryLocalUrl(values[10].get('value'))
			except Exception as e:
				if not error:
					error = u'Repository local url invalid'

			try:
				depot.setRepositoryRemoteUrl(values[11].get('value'))
			except Exception as e:
				if not error:
					error = u'Repository remote url invalid'

			try:
				depot.setWorkbenchLocalUrl(values[12].get('value'))
			except Exception as e:
				if not error:
					error = u'Workbench local url invalid'

			try:
				depot.setWorkbenchRemoteUrl(values[13].get('value'))
			except Exception as e:
				if not error:
					error = u'Workbench remote url invalid'

			try:
				depot.setIsMasterDepot(values[14].get('value'))
			except Exception as e:
				if not error:
					error = u'Invalid value for is master depot'

			try:
				if values[15].get('value'):
					depot.setMasterDepotId(values[15].get('value'))
				else:
					depot.masterDepotId = None
			except Exception as e:
				if not error:
					error = u'Master depot id invalid'

			if error:
				ui.showError(title=u'Bad value', text=error, width=50, height=5)
				continue

			break
	finally:
		ui.exit()
		logging_config(stderr_level=log_level)

	return jsonrpcBackend, depot


def restartServices():
	""" Restart *opsiconfd* and *opsipxeconfd* """
	logger.notice("Restarting opsi webservice")
	execute("service opsiconfd restart")
	logger.notice("Restarting PXE service")
	execute("service opsipxeconfd restart")


def renewOpsiconfdCert(unattendedConfiguration=None):
	def makeCert():
		if certificateExisted:
			renewCertificate(
				yearsUntilExpiration=certparams['expires'],
				config=certparams
			)
		else:
			createCertificate(config=certparams)

	try:
		which("ucr")
		logger.notice("Don't use recreate method on UCS-Systems")
		return
	except Exception:
		pass

	certificateExisted = True
	try:
		certparams = loadConfigurationFromCertificate()
	except UnreadableCertificateError as err:
		logger.notice(
			'Using default values because reading old certificate '
			'failed: %s', err
		)
		certparams = DEFAULT_CERTIFICATE_PARAMETERS
		certparams["commonName"] = getLocalFqdn()
	except NoCertificateError:
		certificateExisted = False
		certparams = DEFAULT_CERTIFICATE_PARAMETERS
		certparams["commonName"] = getLocalFqdn()

	if 'expires' not in certparams:
		certparams['expires'] = "2"  # Not included in existing cert

	if unattendedConfiguration is not None:
		logger.debug("Unattended certificate config: %s", unattendedConfiguration)
		certparams.update(unattendedConfiguration)
		logger.debug("Configuration for unattended certificate renewal: %s", certparams)

		makeCert()
		setPasswdRights()
		setRights(OPSICONFD_CERTFILE)
		restartServices()
		return

	log_level = logger.level
	logging_config(stderr_level=LOG_NONE)
	ui = UIFactory(type='snack')

	try:
		while True:
			values = [
				{"name": u"Country", "value": certparams["country"] or ''},
				{"name": u"State", "value": certparams["state"] or ''},
				{"name": u"Locality", "value": certparams["locality"] or ''},
				{"name": u"Organization", "value": certparams["organization"] or ''},
				{"name": u"OrganizationUnit", "value": certparams["organizationalUnit"] or ''},
				{"name": u"Hostname", "value": certparams["commonName"] or ''},
				{"name": u"Emailaddress", "value": certparams["emailAddress"] or ''},
				{"name": u"Expires (Years)", "value": certparams["expires"] or ''},
			]
			values = ui.getValues(title=u'Renew opsiconfd Certificate', width=70, height=15, entries=values)

			if values is None:
				raise RuntimeError(u"Canceled")

			certparams["country"] = values[0]["value"]
			certparams["state"] = values[1]["value"]
			certparams["locality"] = values[2]["value"]
			certparams["organization"] = values[3]["value"]
			certparams["organizationalUnit"] = values[4]["value"]
			certparams["commonName"] = values[5]["value"]
			certparams["emailAddress"] = values[6]["value"]

			error = None

			if error is None:
				if not certparams["commonName"] == getLocalFqdn():
					error = "Hostname must be the FQDN from Server"

			if error is None:
				try:
					certparams["expires"] = forceInt(values[7]["value"])
				except Exception:
					error = u'No valid years for expiredate given, must be an integer'

			if error:
				ui.showError(title=u'Bad value', text=error, width=50, height=5)
				continue

			break
	finally:
		ui.exit()
		logging_config(stderr_level=log_level)

	makeCert()
	setPasswdRights()
	setRights(OPSICONFD_CERTFILE)
	restartServices()


def usage():
	print(u"\nUsage: %s [options]" % os.path.basename(sys.argv[0]))
	print(u"")
	print(u"Options:")
	print(u"   -h, --help     show this help")
	print(u"   -l             log-level 0..9")
	print(u"   -V, --version  Show version info and exit.")
	print(u"")
	print(u"   --log-file <path>             path to log file")
	print(u"   --backend-config <json hash>  overwrite backend config hash values")
	print(u"   --ip-address <ip>             force to this ip address (do not lookup by name)")
	print(u"   --register-depot              register depot at config server")
	print(u"   --set-rights [path]           set default rights on opsi files (in [path] only)")
	print(u"   --init-current-config         init current backend configuration")
	print(u"   --update-from=<version>       update from opsi version <version>")
	print(u"   --update-mysql                update mysql backend")
	print(u"   --update-file                 update file backend")
	print(u"   --configure-mysql             configure mysql backend")
	print(u"   --edit-config-defaults        edit global config defaults")
	print(u"   --cleanup-backend             cleanup backend")
	print(u"   --auto-configure-samba        patch smb.conf")
	print(u"   --auto-configure-dhcpd        patch dhcpd.conf")
	print(u"   --renew-opsiconfd-cert        renew opsiconfd-cert")
	print(u"   --patch-sudoers-file	         patching sudoers file for tasks in opsiadmin context.")
	print(u"")


def opsisetup_main():
	try:
		(opts, args) = getopt.getopt(sys.argv[1:], "hVl:",
			[
				'help', 'version', 'log-file=', 'ip-address=', 'backend-config=',
				'init-current-config', 'set-rights', 'auto-configure-samba',
				'auto-configure-dhcpd', 'register-depot', 'configure-mysql',
				'update-mysql', 'update-file', 'edit-config-defaults',
				'cleanup-backend', 'update-from=', 'renew-opsiconfd-cert',
				'patch-sudoers-file', 'unattended='
			]
		)

	except Exception:
		usage()
		raise

	global backendConfig
	task = None
	updateFrom = None
	autoConfigureSamba = False
	autoConfigureDhcpd = False
	unattended = None

	for (opt, arg) in opts:
		if opt in ("-h", "--help"):
			usage()
			return
		elif opt in ("-V", "--version"):
			print(f"{__version__} [python-opsi={python_opsi_version}]")
			return

	if os.geteuid() != 0:
		raise Exception(u"This script must be startet as root")

	for (opt, arg) in opts:
		if (opt == "--log-file"):
			logging_config(log_file=arg, file_level=LOG_DEBUG)
		elif (opt == "-l"):
			logging_config(stderr_level=int(arg))
		elif (opt == "--ip-address"):
			global ipAddress
			ipAddress = forceIpAddress(arg)
		elif (opt == "--backend-config"):
			backendConfig = json.loads(arg)
		elif (opt == "--init-current-config"):
			task = 'init-current-config'
		elif (opt == "--set-rights"):
			task = 'set-rights'
		elif (opt == "--register-depot"):
			task = 'register-depot'
		elif (opt == "--configure-mysql"):
			task = 'configure-mysql'
		elif (opt == "--update-mysql"):
			task = 'update-mysql'
		elif (opt == "--update-file"):
			task = 'update-file'
		elif (opt == "--edit-config-defaults"):
			task = 'edit-config-defaults'
		elif (opt == "--cleanup-backend"):
			task = 'cleanup-backend'
		elif (opt == "--update-from"):
			updateFrom = arg
		elif (opt == "--auto-configure-samba"):
			autoConfigureSamba = True
		elif (opt == "--auto-configure-dhcpd"):
			autoConfigureDhcpd = True
		elif (opt == "--renew-opsiconfd-cert"):
			task = "renew-opsiconfd-cert"
		elif (opt == "--patch-sudoers-file"):
			task = "patch-sudoers-file"
		elif opt == '--unattended':
			logger.debug('Got unattended argument: %s', arg)

			if args and not arg.strip().endswith('}'):
				logger.debug("Probably wrong reading of arguments by getopt.")

				tempArgs = [arg]
				while args and not tempArgs[-1].strip().endswith('}'):
					tempArgs.append(args.pop(0))
					logger.debug("temp arguments are: %s", tempArgs)

				arg = ' '.join(tempArgs)
				del tempArgs

			unattended = json.loads(arg)

	path = u'/'
	if len(args) > 0:
		logger.debug("Additional arguments are: %s", args)
		if task == 'set-rights' and len(args) == 1:
			path = os.path.abspath(forceFilename(args[0]))
		else:
			usage()
			raise Exception(u"Too many arguments")

	if autoConfigureSamba:
		configureSamba()

	if autoConfigureDhcpd:
		configureDHCPD()

	if (task == 'set-rights'):
		setRights(path)

	elif (task == 'init-current-config'):
		initializeBackends(ipAddress)
		configureClientUser()

	elif task == 'configure-mysql':
		configureMySQLBackend(unattended)

	elif (task == 'update-mysql'):
		updateMySQLBackend(additionalBackendConfiguration=backendConfig)
		update()

	elif (task == 'update-file'):
		updateFileBackend(additionalBackendConfiguration=backendConfig)
		update()

	elif (task == 'register-depot'):
		registerDepot(unattended)
		configureClientUser()

	elif (task == 'edit-config-defaults'):
		editConfigDefaults()

	elif (task == 'cleanup-backend'):
		cleanupBackend()

	elif (task == "renew-opsiconfd-cert"):
		renewOpsiconfdCert(unattended)

	elif (task == "patch-sudoers-file"):
		patchSudoersFileForOpsi()

	elif (updateFrom):
		update(updateFrom)

	elif not autoConfigureSamba and not autoConfigureDhcpd:
		usage()
		sys.exit(1)


def main():
	logging_config(log_file=LOG_FILE, file_level=LOG_INFO, stderr_format=DEFAULT_COLORED_FORMAT)

	try:
		opsisetup_main()
	except SystemExit:
		pass
	except Exception as exception:
		logger.error(exception, exc_info=True)
		print("\nERROR: %s\n" % exception, file=sys.stderr)
		sys.exit(1)
