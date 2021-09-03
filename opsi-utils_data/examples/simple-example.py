#! /usr/bin/opsi-python
# -*- coding: utf-8 -*-

# Copyright (C) 2019 uib GmbH <info@uib.de>

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

from OPSI.Backend.BackendManager import BackendManager

with BackendManager() as backend:
	print(backend.backend_info())

	# Create Opsi clients
	for i in range(0,4):
		backend.host_createOpsiClient(id=f"test-client-{i}.domain.local", description=f"Test client {i}")

	clients = backend.host_getObjects(type="OpsiClient")
	for client in clients:
		print(client.id)
