#! /usr/bin/opsi-python


from opsi.opsi.service.client import get_service_client

with get_service_client() as client:
	print(client.backend_info())  # ty: ignore[unresolved-attribute]

	# Create Opsi clients
	for i in range(4):
		client.host_createOpsiClient(id=f"test-client-{i}.domain.local", description=f"Test client {i}")  # ty: ignore[unresolved-attribute]

	clients = client.host_getObjects(type="OpsiClient")  # ty: ignore[unresolved-attribute]
	for client in clients:
		print(client.id)
