set -ex
cd opsi-utils-amd64/

./opsi-admin -d method host_getObjects
./opsi-admin -d method does_not_exist
