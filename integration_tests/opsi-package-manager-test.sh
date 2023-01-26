set -ex
cd opsi-utils-amd64/

wget -q http://opsipackages.uib.gmbh/development/hwaudit/all/all/hwaudit_all_all_4.2.0.2-1.tar.gz
tar -xvf hwaudit_all_all_4.2.0.2-1.tar.gz
./opsi-package-manager -vvv -i hwaudit-package/hwaudit_4.2.0.2-1.opsi
./opsi-package-manager -vvv -r hwaudit
./opsi-package-manager -vvv -i hwaudit-package/hwaudit_4.2.0.2-1.opsi -d configserver.mycompany.de
./opsi-package-manager -vvv -x hwaudit-package/hwaudit_4.2.0.2-1.opsi
