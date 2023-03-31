# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
opsi-setup - swiss army knife for opsi administration.
"""

# pylint: disable=too-many-lines

import codecs
import getopt
import json
import logging
import os
import pwd
import re
import shutil
import sys
import time

import OPSI.Util.Task.ConfigureBackend as backendUtils
from OPSI import __version__ as python_opsi_version
from OPSI.Backend.BackendManager import BackendManager
from OPSI.Backend.JSONRPC import JSONRPCBackend
from OPSI.Config import DEFAULT_DEPOT_USER as CLIENT_USER
from OPSI.Object import OpsiDepotserver
from OPSI.System import Posix
from OPSI.System.Posix import (
	Distribution,
	execute,
	getLocalFqdn,
	getNetworkConfiguration,
	which,
)
from OPSI.Types import forceBool, forceFilename, forceInt, forceIpAddress
from OPSI.UI import UIFactory
from OPSI.Util import blowfishDecrypt, randomString
from OPSI.Util.File.Opsi import BackendDispatchConfigFile
from OPSI.Util.Task.BackendMigration import migrate_file_to_mysql
from OPSI.Util.Task.CleanupBackend import cleanupBackend
from OPSI.Util.Task.ConfigureBackend.ConfigDefaults import editConfigDefaults
from OPSI.Util.Task.ConfigureBackend.ConfigurationData import (
	initializeConfigs,
	readWindowsDomainFromSambaConfig,
)
from OPSI.Util.Task.ConfigureBackend.DHCPD import configureDHCPD
from OPSI.Util.Task.ConfigureBackend.MySQL import DatabaseConnectionFailedException
from OPSI.Util.Task.ConfigureBackend.MySQL import (
	configureMySQLBackend as configureMySQLBackendWithoutGUI,
)
from OPSI.Util.Task.ConfigureBootimage import patchServiceUrlInDefaultConfigs
from OPSI.Util.Task.InitializeBackend import _getServerConfig as getServerConfig
from OPSI.Util.Task.InitializeBackend import initializeBackends
from OPSI.Util.Task.Rights import setRights
from OPSI.Util.Task.Samba import SMB_CONF, configureSamba
from OPSI.Util.Task.Sudoers import patchSudoersFileForOpsi
from OPSI.Util.Task.UpdateBackend.ConfigurationData import (
	getServerAddress,
	updateBackendData,
)
from OPSI.Util.Task.UpdateBackend.File import updateFileBackend
from OPSI.Util.Task.UpdateBackend.MySQL import updateMySQLBackend
from opsicommon.logging import (
	DEFAULT_COLORED_FORMAT,
	LOG_CONFIDENTIAL,
	LOG_CRITICAL,
	LOG_DEBUG,
	LOG_NOTICE,
	OPSI_LEVEL_TO_LEVEL,
	init_logging,
	logger,
	logging_config,
	secret_filter,
)

from opsiutils import __version__

init_logging(stderr_level=LOG_NOTICE, stderr_format=DEFAULT_COLORED_FORMAT)

DHCPD_CONF = Posix.locateDHCPDConfig('/etc/dhcp3/dhcpd.conf')

backendConfig = {}  # pylint: disable=invalid-name
ipAddress = None  # pylint: disable=invalid-name
sysConfig = {}  # pylint: disable=invalid-name


class CancelledByUserError(Exception):
	pass

# TODO: use OPSI.System.Posix.Sysconfig for a more standardized approach
def getSysConfig():
	"""Get the current system config"""
	if sysConfig:
		return sysConfig

	logger.notice("Getting current system config")

	distri = Distribution()
	sysConfig['distributor'] = distri.distributor
	sysConfig['distribution'] = f"{distri.distribution} {distri.version[0]}"

	if not sysConfig['distributor'] or not sysConfig['distribution']:
		logger.warning("Failed to get distributor/distribution")

	sysConfig.update(getNetworkConfiguration(ipAddress))

	sysConfig['fqdn'] = getLocalFqdn()
	sysConfig['hostname'] = sysConfig['fqdn'].split('.')[0]
	sysConfig['domain'] = '.'.join(sysConfig['fqdn'].split('.')[1:])
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
	"""Configure the client user"""
	logger.notice("Configuring client user %s", CLIENT_USER)

	clientUserHome = pwd.getpwnam(CLIENT_USER)[5]

	sshDir = os.path.join(clientUserHome, '.ssh')

	if os.path.exists(sshDir):
		shutil.rmtree(sshDir)

	idRsa = os.path.join(sshDir, 'id_rsa')
	idRsaPub = os.path.join(sshDir, 'id_rsa.pub')
	authorizedKeys = os.path.join(sshDir, 'authorized_keys')
	if not os.path.exists(sshDir):
		os.makedirs(sshDir)
	if not os.path.exists(idRsa):
		logger.notice(
			"   Creating RSA private key for user %s in '%s'",
			CLIENT_USER, idRsa
		)
		execute(f"{which('ssh-keygen')} -N '' -t rsa -f {idRsa}")

	if not os.path.exists(authorizedKeys):
		with codecs.open(idRsaPub, 'r', 'utf-8') as file:
			with codecs.open(authorizedKeys, 'w', 'utf-8') as file2:
				file2.write(file.read())

	setRights(sshDir)
	setPasswordForClientUser()


def setPasswordForClientUser():
	fqdn = getSysConfig()['fqdn']

	password = None
	backend_config = {
		"dispatchConfigFile": '/etc/opsi/backendManager/dispatch.conf',
		"backendConfigDir": '/etc/opsi/backends',
		"extensionConfigDir": '/etc/opsi/backendManager/extend.d',
		"depotBackend": True
	}

	try:
		with BackendManager(**backend_config) as backend:
			depot = backend.host_getObjects(type='OpsiDepotserver', id=fqdn)[0]  # pylint: disable=no-member

			for configserver in backend.host_getObjects(type='OpsiConfigserver'):  # pylint: disable=no-member
				if configserver.id == fqdn:
					break  # we are on the configserver - nothing to do

				try:
					with JSONRPCBackend(address=configserver.id, username=depot.id, password=depot.opsiHostKey) as jsonrpcBackend:
						password = blowfishDecrypt(
							depot.opsiHostKey,
							jsonrpcBackend.user_getCredentials(username=CLIENT_USER, hostId=depot.id)['password']  # pylint: disable=no-member
						)
				except Exception as err:  # pylint: disable=broad-except
					logger.info("Failed to get client user (%s) password from configserver: %s", CLIENT_USER, err)

			if not password:
				password = blowfishDecrypt(
					depot.opsiHostKey,
					backend.user_getCredentials(username=CLIENT_USER, hostId=depot.id)['password']  # pylint: disable=no-member
				)
	except Exception as err:  # pylint: disable=broad-except
		logger.info("Failed to get client user (%s) password: %s", CLIENT_USER, err)

	if not password:
		logger.warning("No password for %s found. Generating random password.", CLIENT_USER)
		password = randomString(12)

	secret_filter.add_secrets(password)
	execute(f'opsi-admin -d task setPcpatchPassword "{password}"')


def update(fromVersion=None):  # pylint: disable=unused-argument
	isConfigServer = False
	try:
		bdc = BackendDispatchConfigFile('/etc/opsi/backendManager/dispatch.conf')
		for (regex, backends) in bdc.parse():
			if not re.search(regex, 'backend_createBase'):
				continue
			if 'jsonrpc' not in backends:
				isConfigServer = True
			break
	except Exception as err:  # pylint: disable=broad-except
		logger.warning(err)

	configServerBackendConfig = {
		"dispatchConfigFile": '/etc/opsi/backendManager/dispatch.conf',
		"backendConfigDir": '/etc/opsi/backends',
		"extensionConfigDir": '/etc/opsi/backendManager/extend.d',
		"depotbackend": False
	}

	if isConfigServer:
		try:
			with BackendManager(**configServerBackendConfig) as backend:
				backend.backend_createBase()
		except Exception as err:  # pylint: disable=broad-except
			logger.warning(err)

	if isConfigServer:
		initializeConfigs()

		with BackendManager(**configServerBackendConfig) as backend:
			updateBackendData(backend)  # opsi 4.0 -> 4.1

	if os.path.exists(SMB_CONF):
		configureSamba()


def configureMySQLBackend(unattendedConfiguration=None):
	def notifyFunction(message):
		logger.notice(message)
		messageBox.addText(f"{message}\n")

	def errorFunction(*args):
		logger.error(*args)
		message = args[0]
		if len(args) > 1:
			message = message % args[1:]
		ui.showError(
			text=message, width=70, height=6,
			title='Problem configuring MySQL backend'
		)

	dbAdminUser = 'root'
	dbAdminPass = ''
	config = backendUtils.getBackendConfiguration('/etc/opsi/backends/mysql.conf')
	messageBox = None

	if unattendedConfiguration is not None:
		errorTemplate = "Missing '{key}' in unattended configuration."
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

	logging.disable(OPSI_LEVEL_TO_LEVEL[LOG_CRITICAL])
	ui = UIFactory(type='snack')
	try:
		while True:
			values = [
				{"name": "Database host", "value": config['address']},
				{"name": "Database admin user", "value": dbAdminUser},
				{"name": "Database admin password", "value": dbAdminPass, "password": True},
				{"name": "Opsi database name", "value": config['database']},
				{"name": "Opsi database user", "value": config['username']},
				{"name": "Opsi database password", "value": config['password'], "password": True}
			]
			values = ui.getValues(title='MySQL config', width=70, height=15, entries=values)
			if values is None:
				raise Exception("Canceled")

			config['address'] = values[0]["value"]
			dbAdminUser = values[1]["value"]
			dbAdminPass = values[2]["value"]
			config['database'] = values[3]["value"]
			config['username'] = values[4]["value"]
			config['password'] = values[5]["value"]

			messageBox = ui.createMessageBox(
				width=70, height=20, title='MySQL config', text=''
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
			title='Success', text="MySQL Backend configuration done"
		)
	finally:
		if messageBox is not None:
			messageBox.hide()

		ui.exit()
		logging.disable(OPSI_LEVEL_TO_LEVEL[LOG_CONFIDENTIAL])


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
	backendConfigFile = '/etc/opsi/backends/jsonrpc.conf'
	dispatchConfigFile = '/etc/opsi/backendManager/dispatch.conf'

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

	if config["address"] in [depot.id, depot.ipAddress]:
		raise ValueError("Cannot register depot to itself. This should not be executed on the confserver.")
	logger.notice("Creating depot '%s'", depot.id)
	jsonrpcBackend.host_createObjects([depot])  # pylint: disable=no-member

	logger.notice("Getting depot '%s'", depot.id)
	depots = jsonrpcBackend.host_getObjects(id=depot.id)  # pylint: disable=no-member
	if not depots:
		raise Exception("Failed to create depot")
	depot = depots[0]
	config['username'] = depot.id
	config['password'] = depot.opsiHostKey
	jsonrpcBackend.backend_exit()

	logger.notice("Testing connection to config server as user '%s'", config['username'])
	try:
		jsonrpcBackend = JSONRPCBackend(address=config['address'], username=config['username'], password=config['password'])
	except Exception as err:  # pylint: disable=broad-except
		raise Exception(
			f"Failed to connect to config server as user '{config['username']}': {err}"
		) from err
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
	logger.notice(
		"Connecting to config server '%s' as user '%s'",
		connectionConfig.get("address"), connectionConfig.get("username")
	)
	jsonrpcBackend = JSONRPCBackend(**connectionConfig)
	if not jsonrpcBackend.accessControl_userIsAdmin():  # pylint: disable=no-member
		raise Exception(f"User {connectionConfig['username']} is not an admin user")

	logger.notice(
		"Successfully connected to config server '%s' as user '%s'",
		connectionConfig.get("address"), connectionConfig.get("username")
	)
	return jsonrpcBackend


def _getConfiguredDepot(jsonrpcBackend, depotConfig=None):  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
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
			serverConfig['hardwareAddress'] = ''

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

	depot.maxBandwidth = max(depot.maxBandwidth, 0)

	return depot


def _getBackendConfigViaGUI(config):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
	logging.disable(OPSI_LEVEL_TO_LEVEL[LOG_CRITICAL])

	ui = UIFactory(type='snack')
	try:
		adminUser = 'root'
		adminPass = ''
		messageBox = None
		while True:
			values = [
				{"name": "Config server", "value": config['address']},
				{"name": "Opsi admin user", "value": adminUser},
				{"name": "Opsi admin password", "value": adminPass, "password": True}
			]
			values = ui.getValues(title='Config server connection', width=70, height=10, entries=values)
			if values is None:
				raise Exception("Canceled")

			config['address'] = values[0]["value"]
			adminUser = values[1]["value"]
			adminPass = values[2]["value"]

			messageBox = ui.createMessageBox(width=70, height=20, title='Register depot', text='')
			# Connect to config server
			logger.notice("Connecting to config server '%s' as user '%s'", config['address'], adminUser)
			messageBox.addText(f"Connecting to config server '{config['address']}' as user '{adminUser}'\n")

			try:
				jsonrpcBackend = _getJSONRPCBackendFromConfig({
					"address": config['address'],
					"username": adminUser,
					"password": adminPass
				})
			except Exception as err:  # pylint: disable=broad-except
				messageBox.hide()
				err_text = f"Failed to connect to config server '{config['address']}' as user '{adminUser}': {err}"
				logger.error(err_text)
				ui.showError(
					title='Failed to connect', width=70, height=6, seconds=0,
					text=err_text
				)
				continue

			messageBox.addText(
				f"Successfully connected to config server '{config['address']}' as user '{adminUser}'\n"
			)
			break

		fqdn = getLocalFqdn()
		depots = jsonrpcBackend.host_getObjects(id=fqdn)  # pylint: disable=no-member
		try:
			depot = depots[0]

			if depot.getType() == 'OpsiClient':
				deleteClient = ui.yesno(
					title='ID already in use',
					text=(
						f"A client with the ID {depot.id} exists."
						" We can not register a new depot with the same ID."
						" Should the client be deleted to free the ID?"
						" This will remove the client and it's settings from opsi."
					),
					okLabel='Delete client',
					cancelLabel='Cancel'
				)

				if not deleteClient:
					raise CancelledByUserError('Cancelled')

				jsonrpcBackend.host_delete(id=depot.id)  # pylint: disable=no-member

				raise ValueError("We want defaults.")

			if not depot.depotWebdavUrl:
				depot.depotWebdavUrl = ''
			if not depot.masterDepotId:
				depot.masterDepotId = ''
			if not depot.hardwareAddress:
				depot.hardwareAddress = getSysConfig()['hardwareAddress'] or ''
			if not depot.ipAddress:
				depot.ipAddress = getSysConfig()['ipAddress'] or ''
			if not depot.networkAddress:
				depot.ipAddress = f"{getSysConfig()['subnet']}/{getSysConfig()['netmask']}"
			if not depot.depotWebdavUrl:
				depot.depotWebdavUrl = f"webdavs://{fqdn}:4447/depot"

			if not depot.workbenchLocalUrl:
				depot.workbenchLocalUrl = 'file:///var/lib/opsi/workbench'

			if not depot.workbenchRemoteUrl:
				depotAddress = getServerAddress(depot.depotRemoteUrl)
				remoteWorkbenchPath = f'smb://{depotAddress}/opsi_workbench'
				depot.workbenchRemoteUrl = remoteWorkbenchPath
		except (IndexError, ValueError):
			serverConfig = getServerConfig(fqdn, getSysConfig())

			for key in ('description', 'notes', 'hardwareAddress', 'ipAddress', 'inventoryNumber'):
				if not serverConfig[key]:
					# We want to make sure this is set to an empty
					# string for the UI
					serverConfig[key] = ''

			depot = OpsiDepotserver(**serverConfig)

		while True:
			depot.maxBandwidth = max(depot.maxBandwidth, 0)
			if depot.maxBandwidth > 0:
				depot.maxBandwidth = int(depot.maxBandwidth / 1000)

			values = [
				{"name": "Description", "value": depot.description},
				{"name": "Inventory number", "value": depot.inventoryNumber},
				{"name": "Notes", "value": depot.notes},
				{"name": "Ip address", "value": depot.ipAddress},
				{"name": "Hardware address", "value": depot.hardwareAddress},
				{"name": "Network address", "value": depot.networkAddress},
				{"name": "Maximum bandwidth (kbyte/s)", "value": depot.maxBandwidth},
				{"name": "Local depot url", "value": depot.depotLocalUrl},
				{"name": "Remote depot url", "value": depot.depotRemoteUrl},
				{"name": "Depot webdav url", "value": depot.depotWebdavUrl},
				{"name": "Local repository url", "value": depot.repositoryLocalUrl},
				{"name": "Remote repository url", "value": depot.repositoryRemoteUrl},
				{"name": "Local workbench url", "value": depot.workbenchLocalUrl},
				{"name": "Remote workbench url", "value": depot.workbenchRemoteUrl},
				{"name": "Is master depot", "value": depot.isMasterDepot},
				{"name": "Master depot id", "value": depot.masterDepotId or ''},
			]
			values = ui.getValues(title='Depot server settings', width=70, height=16, entries=values)
			if values is None:
				raise Exception("Canceled")

			error = None
			try:
				depot.setDescription(values[0].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Invalid description'

			try:
				depot.setInventoryNumber(values[1].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Inventory number invalid'

			try:
				depot.setNotes(values[2].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Invalid notes'

			try:
				depot.setIpAddress(values[3].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Invalid ip address'

			try:
				depot.setHardwareAddress(values[4].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Invalid hardware address'

			try:
				depot.setNetworkAddress(values[5].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Invalid network address'

			try:
				depot.setMaxBandwidth(forceInt(values[6].get('value')) * 1000)
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Invalid maximum bandwidth'

			try:
				depot.setDepotLocalUrl(values[7].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Depot local url invalid'

			try:
				depot.setDepotRemoteUrl(values[8].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Depot remote url invalid'

			try:
				if values[9].get('value'):
					depot.setDepotWebdavUrl(values[9].get('value'))
				else:
					depot.depotWebdavUrl = None
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Depot webdav url invalid'

			try:
				depot.setRepositoryLocalUrl(values[10].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Repository local url invalid'

			try:
				depot.setRepositoryRemoteUrl(values[11].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Repository remote url invalid'

			try:
				depot.setWorkbenchLocalUrl(values[12].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Workbench local url invalid'

			try:
				depot.setWorkbenchRemoteUrl(values[13].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Workbench remote url invalid'

			try:
				depot.setIsMasterDepot(values[14].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Invalid value for is master depot'

			try:
				if values[15].get('value'):
					depot.setMasterDepotId(values[15].get('value'))
				else:
					depot.masterDepotId = None
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = 'Master depot id invalid'

			if error:
				ui.showError(title='Bad value', text=error, width=50, height=5)
				continue

			break
	finally:
		ui.exit()
		logging.disable(OPSI_LEVEL_TO_LEVEL[LOG_CONFIDENTIAL])

	return jsonrpcBackend, depot


def restartServices():
	""" Restart *opsiconfd* and *opsipxeconfd* """
	logger.notice("Restarting opsi webservice")
	execute("service opsiconfd restart")
	logger.notice("Restarting PXE service")
	execute("service opsipxeconfd restart")


def usage():
	print(f"\nUsage: {os.path.basename(sys.argv[0])} [options]")
	print("")
	print("Options:")
	print("   -h, --help     show this help")
	print("   -l             log-level 0..9")
	print("   -V, --version  Show version info and exit.")
	print("")
	print("   --log-file <path>             path to log file")
	print("   --backend-config <json hash>  overwrite backend config hash values")
	print("   --ip-address <ip>             force to this ip address (do not lookup by name)")
	print("   --register-depot              register depot at config server")
	print("   --set-rights [path]           set default rights on opsi files (in [path] only)")
	print("   --init-current-config         init current backend configuration")
	print("   --update-from=<version>       update from opsi version <version>")
	print("   --update-mysql                update mysql backend")
	print("   --update-file                 update file backend")
	print("   --configure-mysql             configure mysql backend")
	print("   --file-to-mysql               migrate file to mysql backend and adjust dispatch.conf")
	print("     --no-backup                 do not run a backup before migration")
	print("     --no-restart                do not restart services on migration")
	print("   --edit-config-defaults        edit global config defaults")
	print("   --cleanup-backend             cleanup backend")
	print("   --auto-configure-samba        patch smb.conf")
	print("   --auto-configure-dhcpd        patch dhcpd.conf")
	print("   --patch-sudoers-file	        patching sudoers file for tasks in opsiadmin context.")
	print("")


def opsisetup_main():  # pylint: disable=too-many-branches.too-many-statements
	try:
		(opts, args) = getopt.getopt(sys.argv[1:], "hVl:",
			[
				'help', 'version', 'log-file=', 'ip-address=', 'backend-config=',
				'init-current-config', 'set-rights', 'auto-configure-samba',
				'auto-configure-dhcpd', 'register-depot', 'configure-mysql',
				'update-mysql', 'update-file', 'file-to-mysql',
				'edit-config-defaults', 'cleanup-backend', 'update-from=',
				'patch-sudoers-file', 'unattended=', 'no-backup', 'no-restart'
			]
		)

	except Exception:
		usage()
		raise

	global backendConfig  # pylint: disable=global-statement,invalid-name
	task = None
	updateFrom = None
	autoConfigureSamba = False
	autoConfigureDhcpd = False
	unattended = None
	noBackup = False
	noRestart = False

	for (opt, arg) in opts:
		if opt in ("-h", "--help"):
			usage()
			return
		if opt in ("-V", "--version"):
			print(f"{__version__} [python-opsi={python_opsi_version}]")
			return

	if os.geteuid() != 0:
		raise Exception("This script must be startet as root")

	for (opt, arg) in opts:
		if opt == "--log-file":
			logging_config(log_file=arg, file_level=LOG_DEBUG)
		elif opt == "-l":
			logging_config(stderr_level=int(arg))
		elif opt == "--ip-address":
			global ipAddress  # pylint: disable=global-statement,invalid-name
			ipAddress = forceIpAddress(arg)
		elif opt == "--backend-config":
			backendConfig = json.loads(arg)
		elif opt == "--init-current-config":
			task = 'init-current-config'
		elif opt == "--set-rights":
			task = 'set-rights'
		elif opt == "--register-depot":
			task = 'register-depot'
		elif opt == "--configure-mysql":
			task = 'configure-mysql'
		elif opt == "--update-mysql":
			task = 'update-mysql'
		elif opt == "--update-file":
			task = 'update-file'
		elif opt == "--file-to-mysql":
			task = 'file-to-mysql'
		elif opt == "--edit-config-defaults":
			task = 'edit-config-defaults'
		elif opt == "--cleanup-backend":
			task = 'cleanup-backend'
		elif opt == "--no-backup":
			noBackup = True
		elif opt == "--no-restart":
			noRestart = True
		elif opt == "--update-from":
			updateFrom = arg
		elif opt == "--auto-configure-samba":
			autoConfigureSamba = True
		elif opt == "--auto-configure-dhcpd":
			autoConfigureDhcpd = True
		elif opt == "--patch-sudoers-file":
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

	path = '/'
	if len(args) > 0:
		logger.debug("Additional arguments are: %s", args)
		if task == 'set-rights' and len(args) == 1:
			path = os.path.abspath(forceFilename(args[0]))
		else:
			usage()
			raise Exception("Too many arguments")

	if noBackup and task != 'file-to-mysql':
		raise Exception("--no-backup only valid with --file-to-mysql")
	if noRestart and task != 'file-to-mysql':
		raise Exception("--no-restart only valid with --file-to-mysql")

	if autoConfigureSamba:
		configureSamba()

	if autoConfigureDhcpd:
		configureDHCPD()

	if task == 'set-rights':
		setRights(path)

	elif task == 'init-current-config':
		initializeBackends(ipAddress)
		configureClientUser()
		with BackendManager() as backend:
			patchServiceUrlInDefaultConfigs(backend)

	elif task == 'configure-mysql':
		configureMySQLBackend(unattended)

	elif task == 'update-mysql':
		updateMySQLBackend(additionalBackendConfiguration=backendConfig)
		update()

	elif task == 'update-file':
		updateFileBackend(additionalBackendConfiguration=backendConfig)
		update()

	elif task == 'file-to-mysql':
		if not migrate_file_to_mysql(create_backup=not noBackup, restart_services=not noRestart):
			# Nothing to do
			sys.exit(2)

	elif task == 'register-depot':
		registerDepot(unattended)
		configureClientUser()
		with BackendManager() as backend:
			patchServiceUrlInDefaultConfigs(backend)

	elif task == 'edit-config-defaults':
		editConfigDefaults()

	elif task == 'cleanup-backend':
		cleanupBackend()

	elif task == "patch-sudoers-file":
		patchSudoersFileForOpsi()

	elif updateFrom:
		update(updateFrom)

	elif not autoConfigureSamba and not autoConfigureDhcpd:
		usage()
		sys.exit(1)


def main():
	try:
		opsisetup_main()
	except SystemExit as err:
		sys.exit(err.code)
	except Exception as err:  # pylint: disable=broad-except
		logger.error(err, exc_info=True)
		print(f"\nERROR: {err}\n", file=sys.stderr)
		sys.exit(1)
