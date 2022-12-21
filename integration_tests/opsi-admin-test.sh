set -ex
cd opsi-utils-amd64/

./opsi-admin -d method host_getObjects '[]' '{"id": "*1.*"}'
$client=$(./opsi-admin -d method host_getObjects '[]' '{"id": "*w*"}' | grep '"id"' | head -n 1 | sed -e 's/^.*: "\([^"]*\)".*$/\1/')
./opsi-admin -d task setupWhereInstalled "hwaudit"
./opsi-admin -d task setupWhereNotInstalled "hwaudit"
./opsi-admin -d task updateWhereInstalled "hwaudit"
./opsi-admin -d task uninstallWhereInstalled "hwaudit"
./opsi-admin -d task setActionRequestWhereOutdated "setup" "hwaudit"
./opsi-admin -d task setActionRequestWhereOutdatedWithDependencies "setup" "hwaudit"
./opsi-admin -d task setActionRequestWithDependencies "setup" "hwaudit" $client
#./opsi-admin -d task decodePcpatchPassword "$encoded_password" "$host_key"
#./opsi-admin -d task setPcpatchPassword "some_test_password"
