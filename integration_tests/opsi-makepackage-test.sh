set -ex
cd opsi-utils-amd64/

wget -q http://opsipackages.uib.gmbh/development/hwaudit/all/all/hwaudit_all_all_4.2.0.2-1.tar.gz
tar -xvf hwaudit_all_all_4.2.0.2-1.tar.gz
./opsi-package-manager -vv -x hwaudit-package/hwaudit_4.2.0.2-1.opsi
rm -r hwaudit-package

./opsi-makepackage -vv hwaudit
ls hwaudit_4.2.0.2-1.opsi
rm hwaudit*.opsi*

# pack CLIENT_DATA.custom alongside CLIENT_DATA
mkdir -p hwaudit/CLIENT_DATA.custom
echo "testcontent" > hwaudit/CLIENT_DATA.custom/testfile
./opsi-makepackage -vv -i custom hwaudit
tar -tf hwaudit_4.2.0.2-1~custom.opsi
rm hwaudit*.opsi*

# pack CLIENT_DATA.custom instead of CLIENT_DATA
rm -rf hwaudit/CLIENT_DATA
./opsi-makepackage -vv -c custom hwaudit
tar -tf hwaudit_4.2.0.2-1~custom.opsi
rm hwaudit*.opsi*

rm -rf hwaudit*
