# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
Configuration.

Attention: socket.defaulttimeout may be changed per config file setting.
"""

import os
import os.path
import re
import socket
from configparser import ConfigParser
from typing import Any, Generator

from opsi.logging import get_logger, logging_config, secret_filter
from opsi.opsi.service.client import ServiceClient
from opsi.opsi.service.model.type import (
	to_bool,
	to_email_address,
	to_filename,
	to_host_address,
	to_host_id,
	to_int,
	to_list,
	to_product_id,
	to_string,
	to_url,
)
from opsi_legacy import __version__
from opsi_legacy.Util.File import IniFile

from .Exceptions import ConfigurationError, MissingConfigurationValueError, RequiringBackendError
from .Repository import ProductRepositoryInfo

__all__ = ("DEFAULT_CONFIG", "DEFAULT_USER_AGENT", "ConfigurationParser")

DEFAULT_USER_AGENT = f"opsi-package-updater/{__version__}"
DEFAULT_CONFIG: dict[str, str | int | bool | list[ProductRepositoryInfo] | None] = {
	"userAgent": DEFAULT_USER_AGENT,
	"packageDir": "/var/lib/opsi/products",
	"configFile": "/etc/opsi/opsi-package-updater.conf",
	"repositoryConfigDir": "/etc/opsi/package-updater.repos.d",
	"notification": False,
	"smtphost": "localhost",
	"smtpport": 25,
	"smtpuser": None,
	"smtppassword": None,
	"subject": "opsi-package-updater",
	"use_starttls": False,
	"sender": "opsi@localhost",
	"receivers": [],
	"wolAction": False,
	"wolActionExcludeProductIds": [],
	"wolShutdownWanted": False,
	"wolStartGap": 0,
	"installationWindowStartTime": None,
	"installationWindowEndTime": None,
	"installationWindowExceptions": None,
	"repositories": [],
	"repositoryName": None,
	"forceRepositoryActivation": False,
	"installAllAvailable": False,
	"useZsync": True,
	"processProductIds": None,
	"forceChecksumCalculation": False,
	"forceDownload": False,
	"proxy": None,
	"ignoreErrors": False,
}

logger = get_logger("opsi.general")


def getRepoConfigs(repoDir: str) -> Generator[str, None, None]:
	try:
		for entry in os.listdir(repoDir):
			filePath = os.path.join(repoDir, entry)
			if entry.endswith(".repo") and os.path.isfile(filePath):
				yield filePath
	except OSError as oserr:
		logger.warning("Problem listing %s: %s", repoDir, oserr)


def splitAndStrip(string: str, sep: str) -> Generator[str, None, None]:
	for singleValue in string.split(sep):
		singleValue = singleValue.strip()
		if singleValue:
			yield singleValue


class ConfigurationParser:
	TIME_REGEX = re.compile(r"^\d{1,2}:\d{1,2}$")

	def __init__(self, configFile: str, backend: ServiceClient, depotId: str, depotKey: str) -> None:
		self.configFile = configFile
		self.backend = backend
		self.depotId = depotId
		self.depotKey = depotKey

	@staticmethod
	def get_proxy(configFile: str) -> str | None:
		iniFile = IniFile(filename=configFile, raw=True)
		configIni = iniFile.parse()
		return configIni.get(section="general", option="proxy", fallback=None) or None

	def parse(self, configuration: dict | None = None) -> dict[str, Any]:
		"""
		Parse the configuration file.

		:param confiuration: Predefined configuration. Contents may be overriden based on values in configuration file.
		:rtype: dict
		"""
		logger.info("Reading config file '%s'", self.configFile)
		if not os.path.isfile(self.configFile):
			raise OSError(f"Configuration file '{self.configFile}' not found")

		config = DEFAULT_CONFIG.copy()
		if configuration:
			config.update(configuration)

		config["repositories"] = []

		try:
			iniFile = IniFile(filename=self.configFile, raw=True)
			configIni = iniFile.parse()
			for section in configIni.sections():
				if section.lower() == "general":
					for option, value in configIni.items(section):
						if option.lower() == "packagedir":
							config["packageDir"] = to_filename(value.strip())
						elif option.lower() == "logfile":
							value = to_filename(value.strip())
							logging_config(log_file=value)
						elif option.lower() == "loglevel":
							logging_config(file_level=to_int(value.strip()))
						elif option.lower() == "timeout":
							# TODO: find a better way!
							socket.setdefaulttimeout(float(value.strip()))
						elif option.lower() == "tempdir":
							config["tempdir"] = value.strip()
						elif option.lower() == "repositoryconfigdir":
							config["repositoryConfigDir"] = value.strip()
						elif option.lower() == "proxy" and value.strip():
							config["proxy"] = value.strip()
							if config["proxy"] != "system":
								config["proxy"] = to_url(value.strip())
						elif option.lower() == "ignoreerrors" and value.strip():
							config["ignoreErrors"] = to_bool(value.strip())

				elif section.lower() == "notification":
					for option, value in configIni.items(section):
						if option.lower() == "active":
							config["notification"] = to_bool(value)
						elif option.lower() == "smtphost":
							config["smtphost"] = to_host_address(value.strip())
						elif option.lower() == "smtpport":
							config["smtpport"] = to_int(value.strip())
						elif option.lower() == "smtpuser":
							config["smtpuser"] = to_string(value.strip())
						elif option.lower() == "smtppassword":
							config["smtppassword"] = to_string(value.strip())
							secret_filter.add_secrets(str(config["smtppassword"]))
						elif option.lower() == "subject":
							config["subject"] = to_string(value.strip())
						elif option.lower() == "use_starttls":
							config["use_starttls"] = to_bool(value.strip())
						elif option.lower() == "sender":
							config["sender"] = to_email_address(value.strip())
						elif option.lower() == "receivers":
							config["receivers"] = [to_email_address(receiver) for receiver in splitAndStrip(str(value), ",")]  # ty: ignore[invalid-assignment]

				elif section.lower() == "wol":
					for option, value in configIni.items(section):
						if option.lower() == "active":
							config["wolAction"] = to_bool(value.strip())
						elif option.lower() == "excludeproductids":
							config["wolActionExcludeProductIds"] = [to_product_id(productId) for productId in splitAndStrip(value, ",")]  # ty: ignore[invalid-assignment]
						elif option.lower() == "shutdownwanted":
							config["wolShutdownWanted"] = to_bool(value.strip())
						elif option.lower() == "startgap":
							config["wolStartGap"] = max(0, to_int(value.strip()))

				elif section.lower() == "installation":
					for option, value in configIni.items(section):
						if option.lower() == "windowstart":
							if not value.strip():
								continue
							if not self.TIME_REGEX.search(value.strip()):
								raise ValueError(f"Start time '{value.strip()}' not in needed format 'HH:MM'")
							config["installationWindowStartTime"] = value.strip()
						elif option.lower() == "windowend":
							if not value.strip():
								continue
							if not self.TIME_REGEX.search(value.strip()):
								raise ValueError(f"End time '{value.strip()}' not in needed format 'HH:MM'")
							config["installationWindowEndTime"] = value.strip()
						elif option.lower() == "exceptproductids":
							config["installationWindowExceptions"] = [to_product_id(productId) for productId in splitAndStrip(value, ",")]  # ty: ignore[invalid-assignment]
				elif section.lower().startswith("repository"):
					try:
						repository = self._getRepository(
							config=configIni,
							section=section,
							forceRepositoryActivation=bool(config["forceRepositoryActivation"]),
							repositoryName=str(config["repositoryName"]),
							installAllAvailable=bool(config["installAllAvailable"]),
							proxy=str(config["proxy"]) if config["proxy"] else None,
						)
						config["repositories"] = to_list(config["repositories"]) + [repository]
					except MissingConfigurationValueError as mcverr:
						logger.debug("Configuration for %s incomplete: %s", section, mcverr)
					except ConfigurationError as cerr:
						logger.error("Configuration problem in %s: %s", section, cerr)
					except Exception as err:
						logger.error("Can't load repository from %s: %s", section, err)
				else:
					logger.error("Unhandled section '%s'", section)
		except Exception as err:
			raise RuntimeError(f"Failed to read config file '{self.configFile}': {err}") from err

		for configFile in getRepoConfigs(str(config["repositoryConfigDir"])):
			iniFile = IniFile(filename=configFile, raw=True)

			try:
				repoConfig = iniFile.parse()
				for section in repoConfig.sections():
					if not section.lower().startswith("repository"):
						continue

					try:
						repository = self._getRepository(
							config=repoConfig,
							section=section,
							forceRepositoryActivation=bool(config["forceRepositoryActivation"]),
							repositoryName=str(config["repositoryName"]),
							installAllAvailable=bool(config["installAllAvailable"]),
							proxy=str(config["proxy"]) if config["proxy"] else None,
						)
						config["repositories"] = to_list(config["repositories"]) + [repository]
					except MissingConfigurationValueError as err:
						logger.debug("Configuration for %s in %s incomplete: %s", section, configFile, err)
					except ConfigurationError as err:
						logger.error("Configuration problem in %s in %s: %s", section, configFile, err)
					except Exception as err:
						logger.error("Can't load repository from %s in %s: %s", section, configFile, err)
			except Exception as err:
				logger.error("Unable to load repositories from %s: %s", configFile, err)

		return config

	def _getRepository(
		self,
		config: ConfigParser,
		section: str,
		forceRepositoryActivation: bool = False,
		repositoryName: str | None = None,
		installAllAvailable: bool = False,
		proxy: str | None = None,
	) -> ProductRepositoryInfo:
		active = False
		verifyCert = False
		baseUrl = None
		opsiDepotId = None
		for option, value in config.items(section):
			option = option.lower()
			value = value.strip()
			if option == "active":
				active = to_bool(value)
			elif option == "baseurl":
				if value:
					baseUrl = to_url(value)
			elif option == "opsidepotid":
				if value:
					opsiDepotId = to_host_id(value)
			elif option == "proxy" and value:
				proxy = value
				if value != "system":
					proxy = to_url(value)
			elif option == "verifycert":
				verifyCert = to_bool(value)

		repoName = section.replace("repository_", "", 1)

		if forceRepositoryActivation:
			if repoName == repositoryName:
				logger.debug("Activation for repository %s forced.", repoName)
				active = True
			else:
				active = False

		repository = None
		if opsiDepotId:
			if not self.backend:
				raise RequiringBackendError(f"Repository section '{section}' supplied an depot ID but we have no backend to check.")

			depots = self.backend.host_getObjects(type="OpsiDepotserver", id=opsiDepotId)  # ty: ignore[unresolved-attribute]
			if not depots:
				raise ConfigurationError(f"Depot '{opsiDepotId}' not found in backend")
			if not depots[0].repositoryRemoteUrl:
				raise ConfigurationError(f"Repository remote url for depot '{opsiDepotId}' not found in backend")

			repository = ProductRepositoryInfo(
				name=repoName,
				baseUrl=depots[0].repositoryRemoteUrl,
				dirs=["/"],
				username=self.depotId,
				password=self.depotKey,
				opsiDepotId=opsiDepotId,
				active=active,
				verifyCert=verifyCert,
			)

		elif baseUrl:
			if proxy:
				logger.info("Repository %s is using proxy %s", repoName, proxy)

			repository = ProductRepositoryInfo(name=repoName, baseUrl=baseUrl, proxy=proxy, active=active, verifyCert=verifyCert)
		else:
			raise MissingConfigurationValueError(f"Repository section '{section}': neither baseUrl nor opsiDepotId set")

		for option, value in config.items(section):
			if option.lower() == "username":
				repository.username = to_string(value.strip())
			elif option.lower() == "password":
				repository.password = to_string(value.strip())
				if repository.password:
					secret_filter.add_secrets(repository.password)
			elif option.lower() == "authcertfile":
				repository.authcertfile = to_filename(value.strip())
			elif option.lower() == "authkeyfile":
				repository.authkeyfile = to_filename(value.strip())
			elif option.lower() == "autoinstall":
				repository.autoInstall = to_bool(value.strip())
			elif option.lower() == "autoupdate":
				repository.autoUpdate = to_bool(value.strip())
			elif option.lower() == "autosetup":
				repository.autoSetup = to_bool(value.strip())
			elif option.lower() == "onlydownload":
				repository.onlyDownload = to_bool(value.strip())
			elif option.lower() == "inheritproductproperties":
				if not opsiDepotId:
					logger.warning("InheritProductProperties not possible with normal http ressource.")
					repository.inheritProductProperties = False
				else:
					repository.inheritProductProperties = to_bool(value.strip())
			elif option.lower() == "dirs":
				repository.dirs = [to_filename(directory) for directory in splitAndStrip(value, ",")]
			elif option.lower() == "excludes":
				repository.excludes = [re.compile(exclude) for exclude in splitAndStrip(value, ",")]
			elif option.lower() == "customversions":
				customVersions: dict[re.Pattern, str] = {}
				for item in splitAndStrip(value, ","):
					try:
						patternStr, versionStr = item.split("~", 1)
						pattern = re.compile(patternStr.strip())
						version = versionStr.strip()
						customVersions[pattern] = version
					except ValueError:
						logger.error("Invalid custom version entry '%s' in repository '%s'", item, repository.name)
				repository.customVersions = customVersions
			elif option.lower() == "includeproductids":
				repository.includes = [re.compile(include) for include in splitAndStrip(value, ",")]
			elif option.lower() == "autosetupexcludes":
				repository.autoSetupExcludes = [re.compile(exclude) for exclude in splitAndStrip(value, ",")]
			elif option.lower() == "description":
				repository.description = to_string(value)

		if installAllAvailable:
			repository.autoInstall = True
			repository.autoUpdate = True
			repository.excludes = []

		return repository
