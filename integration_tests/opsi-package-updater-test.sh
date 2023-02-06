set -ex
cd opsi-utils-amd64/

./opsi-package-updater -l 6 list
./opsi-package-updater -l 6 download hwaudit
./opsi-package-updater -l 6 update hwaudit
./opsi-package-updater -l 6 install hwaudit
./opsi-package-updater -l 6 list
