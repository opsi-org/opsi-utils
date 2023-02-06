set -ex
cd opsi-utils-amd64/

./opsi-admin -d method host_getObjects '[]' '{"type": "OpsiDepotserver"}'
./opsi-admin method product_getIdents
./opsi-admin task setupWhereInstalled "hwaudit"
./opsi-admin task setupWhereNotInstalled "hwaudit"
./opsi-admin task updateWhereInstalled "hwaudit"
./opsi-admin task uninstallWhereInstalled "hwaudit"
./opsi-admin task setActionRequestWhereOutdated "setup" "hwaudit"
./opsi-admin task setActionRequestWhereOutdatedWithDependencies "setup" "hwaudit"

# setActionRequestWithDependencies requires POD object. Test moved to opsi-package-manager
#./opsi-admin method productOnDepot_getObjects
#client=$(./opsi-admin -d method host_getObjects '[]' '{"id": "*1*"}' | grep '"id"' | head -n 1 | sed -e 's/^.*: "\([^"]*\)".*$/\1/')
#./opsi-admin task setActionRequestWithDependencies "setup" "hwaudit" $client

#./opsi-admin task decodePcpatchPassword "$encoded_password" "$host_key"
#./opsi-admin task setPcpatchPassword "some_test_password"
