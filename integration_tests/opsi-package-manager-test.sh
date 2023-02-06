set -ex
cd opsi-utils-amd64/

wget -q http://opsipackages.uib.gmbh/development/hwaudit/all/all/hwaudit_all_all_4.2.0.2-1.tar.gz
tar -xvf hwaudit_all_all_4.2.0.2-1.tar.gz
./opsi-package-manager -vvvv -i hwaudit-package/hwaudit_4.2.0.2-1.opsi

# setActionRequestWithDependencies requires POD object. Test moved to opsi-package-manager
./opsi-admin method productOnDepot_getObjects
client=$(./opsi-admin method host_getObjects '[]' '{"id": "*1*"}' | grep '"id"' | head -n 1 | sed -e 's/^.*: "\([^"]*\)".*$/\1/')
./opsi-admin task setActionRequestWithDependencies "setup" "hwaudit" $client

echo patching repositoryRemoteUrl to webdavs://test.uib.gmbh:4447/repository
./opsi-admin -r method host_getObjects [] '{"type": "OpsiConfigserver"}' | sed -e 's#"repositoryRemoteUrl":"[^"]*"#"repositoryRemoteUrl":"webdavs://test.uib.gmbh:4447/repository"#' | ./opsi-admin method host_updateObjects
./opsi-admin method host_getObjects [] '{"type": "OpsiConfigserver"}'

./opsi-package-manager -vvvv -r hwaudit
./opsi-package-manager -vvvv -i hwaudit-package/hwaudit_4.2.0.2-1.opsi -d test.uib.gmbh
./opsi-package-manager -vvvv -x hwaudit-package/hwaudit_4.2.0.2-1.opsi
rm -rf ./hwaudit*
