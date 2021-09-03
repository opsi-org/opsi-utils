#! /usr/bin/opsi-python
# -*- coding: utf-8 -*-

# Copyright (C) 2021 uib GmbH <info@uib.de>

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
One sentence description here.

You should write what it does here.
You can use multiple lines.

:license: GNU Affero General Public License version 3
"""

import argparse

from OPSI.Backend.BackendManager import BackendManager
from OPSI.Object import OpsiClient
from opsicommon.logging import logger, logging_config, DEFAULT_COLORED_FORMAT, LOG_WARNING

__version__ = '1'


def main():
	options = parse_options()
	if options.log_level:
		logging_config(stderr_level=options.log_level, stderr_format=DEFAULT_COLORED_FORMAT)

	backend_config = {
		"dispatchConfigFile": "/etc/opsi/backendManager/dispatch.conf",
		"backendConfigDir": "/etc/opsi/backends",
		"extensionConfigDir": "/etc/opsi/backendManager/extend.d",
		"depotBackend": False,
		"hostControlBackend": True,
		"hostControlSafeBackend": True
	}

	with BackendManager(**backend_config) as backend:
		do_something(backend)


def parse_options():
	parser = argparse.ArgumentParser(description="Some opsi script.")
	parser.add_argument("--version", action='version', version=__version__)

	log_group = parser.add_mutually_exclusive_group()
	log_group.add_argument("--verbose", "-v", dest="log_level",
						default=LOG_WARNING, action="count",
						help="increase verbosity (can be used multiple times)")
	log_group.add_argument("--log-level", "-l", dest="log_level", type=int,
						choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
						help="Set the desired loglevel.")

	parser.add_argument("--filename", "-f",  help='Required file.')

	args = parser.parse_args()

	return args


def do_something(backend):
	logger.info("logging with level 'info'")
	print(backend.backend_info())

	# create opsi clients 0-3
	clients_to_create = []
	for i in range(0,4):
		client_config = {
			"id": f"test-{i}.domain.local",
			"description": f"Test client {i}"
		}
		clients_to_create.append(
			OpsiClient(**client_config)
		)
	backend.host_createObjects(clients_to_create)

	# Create Opsi clients 4 and 5
	for i in range(4,6):
		backend.host_createObjects([
			{
				"id": f"test-{i}.domain.local",
				"description": f"Test client {i}",
				"type": "OpsiClient"
			}
		])

	# list all opsi clients
	clients = backend.host_getObjects(type="OpsiClient")
	for client in clients:
		print(client.id)
		print(client.lastSeen)


if __name__ == '__main__':
	logger.setConsoleColor(True)
	main()
