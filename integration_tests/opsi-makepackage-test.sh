set -ex
cd opsi-utils-amd64/

wget -q http://opsipackages.uib.gmbh/development/hwaudit/all/all/hwaudit_all_all_4.2.0.2-1.tar.gz
tar -xvf hwaudit_all_all_4.2.0.2-1.tar.gz
./opsi-package-manager -vv -x hwaudit-package/hwaudit_4.2.0.2-1.opsi
rm -r hwaudit-package
./opsi-makepackage -vv hwaudit
ls hwaudit_4.2.0.2-1.opsi
rm -rf hwaudit*
