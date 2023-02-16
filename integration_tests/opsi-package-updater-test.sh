set -ex
cd opsi-utils-amd64/

./opsi-package-updater -l 6 list
./opsi-package-updater -l 6 download hwaudit
./opsi-package-updater -l 6 update hwaudit
./opsi-package-manager -vv --quiet -r hwaudit
./opsi-package-updater -l 6 install hwaudit
./opsi-package-updater -l 6 list --repos
./opsi-package-updater -l 6 list --active-repos
./opsi-package-updater -l 6 list --packages
./opsi-package-updater -l 6 list --packages-unique
./opsi-package-updater -l 6 list --packages-and-installationstatus
./opsi-package-updater -l 6 list --updatable-packages
./opsi-package-updater -l 6 list --search-package hwaudit
