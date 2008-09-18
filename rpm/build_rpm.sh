#!/bin/bash

topdir=/usr/src/packages
builddir=${topdir}/BUILD
rpmdir=${topdir}/RPMS
sourcedir=${topdir}/SOURCES
specdir=${topdir}/SPECS
srcrpmdir=${topdir}/SRPMS
packagename=opsi-utils
version=`grep -i ^Version  rpm/${packagename}.spec | awk '{ print $2 }'`
tmpdir=/tmp/${packagename}-${version}
cwd=`pwd`
dir=${cwd}/`dirname $0`

test -e $specdir || mkdir -p $specdir
test -e $sourcedir || mkdir -p $sourcedir
test -e $buildroot && rm -rf $buildroot

cd $dir/..
test -e $tmpdir && rm -rf $tmpdir
mkdir $tmpdir
cp opsi-admin opsi-convert opsiinst opsiinstv2 opsi-makeproductfile opsi-makeproductfilev2 opsi-newprod opsi-package-manager opsiuninst opsi-winipatch sysbackup ${tmpdir}/
cd ${tmpdir}/..
tar cjvf ${sourcedir}/${packagename}-${version}.tar.bz2 ${packagename}-${version}
rm -rf $tmpdir
cd $dir

cp ${packagename}.spec $specdir/
rpmbuild -ba $specdir/${packagename}.spec

cd $cwd
