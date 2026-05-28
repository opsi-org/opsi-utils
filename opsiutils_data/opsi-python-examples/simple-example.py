#! /usr/bin/opsi-python


from opsi.opsi.service.client import get_service_client

with get_service_client() as client:
	print(client.backend_info())

	# Create Opsi clients
	for i in range(0, 4):
		client.host_createOpsiClient(id=f"test-client-{i}.domain.local", description=f"Test client {i}")

	clients = client.host_getObjects(type="OpsiClient")
	for client in clients:
		print(client.id)
