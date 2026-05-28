#! /usr/bin/opsi-python

import argparse

from opsi.logging import DEFAULT_COLORED_FORMAT, LOG_WARNING, logger, logging_config
from opsi.opsi.service.client import get_service_client
from opsi.opsi.service.model.object import OpsiClient

__version__ = "1.0.0"


def main():
	options = parse_options()
	if options.log_level:
		logging_config(stderr_level=options.log_level, stderr_format=DEFAULT_COLORED_FORMAT)

	with get_service_client() as service_client:
		do_something(service_client)


def parse_options():
	parser = argparse.ArgumentParser(description="Some opsi script.")
	parser.add_argument("--version", action="version", version=__version__)
	parser.add_argument(
		"--log-level",
		"-l",
		dest="log_level",
		type=int,
		default=LOG_WARNING,
		choices=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
		help="Set the desired loglevel.",
	)
	parser.add_argument("--filename", "-f", help="Required file.")
	args = parser.parse_args()

	return args


def do_something(service_client):
	logger.info("logging with level 'info'")
	print(service_client.backend_info())

	# create opsi clients 0-3
	clients_to_create = []
	for i in range(0, 4):
		client_config = {"id": f"test-{i}.domain.local", "description": f"Test client {i}"}
		clients_to_create.append(OpsiClient(**client_config))
	service_client.host_createObjects(clients_to_create)

	# Create Opsi clients 4 and 5
	for i in range(4, 6):
		service_client.host_createObjects([{"id": f"test-{i}.domain.local", "description": f"Test client {i}", "type": "OpsiClient"}])

	# list all opsi clients
	clients = service_client.host_getObjects(type="OpsiClient")
	for client in clients:
		print(client.id)
		print(client.lastSeen)


if __name__ == "__main__":
	main()
