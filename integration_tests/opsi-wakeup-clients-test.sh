set -ex
cd opsi-utils-amd64/

group=$(./opsi-admin -d method group_getObjects '[]' '{"type": "HostGroup"}' | grep ident | head -n 1 | sed -e 's/^.*: "\([^"]*\)".*$/\1/')
./opsi-wakeup-clients -vv --host-group-id=clientdirectory  # exit code 0 even if no clients reached
