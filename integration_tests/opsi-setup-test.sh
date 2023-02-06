set -ex
cd opsi-utils-amd64/

./opsi-setup --help
./opsi-setup --register-depot  # nothing
./opsi-setup --set-rights
./opsi-setup --init-current-config  # nothing
./opsi-setup --update-mysql  # nothing
./opsi-setup --configure-mysql  # nothing
#./opsi-setup --file-to-mysql  # requires filebackend
#./opsi-setup --edit-config-defaults  # is interactive
./opsi-setup --cleanup-backend  # nothing
#./opsi-setup --auto-configure-samba  # not needed in container (webdav)
./opsi-setup --auto-configure-dhcpd
./opsi-setup --patch-sudoers-file
