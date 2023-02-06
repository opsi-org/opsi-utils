set -ex
cd opsi-utils-amd64/

./opsi-admin method group_createHostGroup testgroup
./opsi-wakeup-clients -vv --host-group-id=testgroup  # exit code 0 even if no clients reached
