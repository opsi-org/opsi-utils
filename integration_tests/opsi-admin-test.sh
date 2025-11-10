set -ex
cd opsi-utils-amd64/

cat /etc/opsi/opsi.conf
./opsi-admin -d method host_getObjects '[]' '{"type": "OpsiDepotserver"}'
./opsi-admin method product_getIdents
./opsi-admin task setupWhereInstalled "hwaudit"
./opsi-admin task setupWhereNotInstalled "hwaudit"
./opsi-admin task updateWhereInstalled "hwaudit"
./opsi-admin task uninstallWhereInstalled "hwaudit"
# removed most task tests -> use opsi-cli!
