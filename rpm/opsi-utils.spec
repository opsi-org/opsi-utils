#
# spec file for package opsi-utils
#
# Copyright (c) 2008 uib GmbH.
# This file and all modifications and additions to the pristine
# package are under the same license as the package itself.
#

Name:           opsi-utils
Requires:       python-opsi
Url:            http://www.opsi.org
License:        GPL v2 or later
Group:          Productivity/Networking/Opsi
AutoReqProv:    on
Version:        3.3.0.11
Release:        1
Summary:        opsi python library
%define tarname opsi-utils
Source:         %{tarname}-%{version}.tar.bz2
BuildRoot:      %{_tmppath}/%{name}-%{version}-build
BuildArch:      noarch
%{py_requires}

# ===[ description ]================================
%description
This package contains the opsi util collection.

# ===[ debug_package ]==============================
%debug_package

# ===[ prep ]=======================================
%prep

# ===[ setup ]======================================
%setup -n %{tarname}-%{version}

# ===[ build ]======================================
%build
msgfmt -o gettext/opsi_newprod_de.mo gettext/opsi_newprod_de.po

# ===[ install ]==================================== 
%install
mkdir -p $RPM_BUILD_ROOT/usr/bin
mkdir -p $RPM_BUILD_ROOT/usr/share/locale/de/LC_MESSAGES
install -m 0640 gettext/opsi_newprod_de.mo $RPM_BUILD_ROOT/usr/share/locale/de/LC_MESSAGES/opsi_newprod.mo
install -m 0750 opsi-admin $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-newprod $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-makeproductfile $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsiinst $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsiuninst $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-package-manager $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-convert $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-makeproductfilev2 $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsiinstv2 $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-winipatch $RPM_BUILD_ROOT/usr/bin/
install -m 0755 sysbackup $RPM_BUILD_ROOT/usr/bin/
ln -sf /usr/bin/opsi-makeproductfile $RPM_BUILD_ROOT/usr/bin/makeproductfile
ln -sf /usr/bin/opsi-makeproductfilev2 $RPM_BUILD_ROOT/usr/bin/makeproductfilev2
ln -sf /usr/bin/opsi-newprod $RPM_BUILD_ROOT/usr/bin/newprod
ln -sf /usr/bin/opsi-winipatch $RPM_BUILD_ROOT/usr/bin/winipatch


# ===[ clean ]======================================
%clean
rm -rf $RPM_BUILD_ROOT

# ===[ post ]=======================================
%post

# ===[ postun ]=====================================
%postun

# ===[ files ]======================================
%files
# default attributes
%defattr(755,root,root)

# documentation
#%doc LICENSE README RELNOTES doc

# configfiles
%attr(644,root,root) %config /usr/share/locale/de/LC_MESSAGES/opsi_newprod.mo

# other files
%attr(750,root,opsiadmin) /usr/bin/opsi-admin
/usr/bin/opsi-newprod
/usr/bin/opsi-makeproductfile
/usr/bin/opsiinst
/usr/bin/opsiuninst
/usr/bin/opsi-package-manager
/usr/bin/opsi-convert
/usr/bin/opsi-makeproductfilev2
/usr/bin/opsiinstv2
/usr/bin/opsi-winipatch
/usr/bin/sysbackup
/usr/bin/makeproductfile
/usr/bin/makeproductfilev2
/usr/bin/newprod
/usr/bin/winipatch

# directories
%dir /usr/share/locale/de/LC_MESSAGES

# ===[ changelog ]==================================
%changelog
* Wed Sep 17 2008 - j.schneider@uib.de
- created new package









