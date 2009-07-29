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
Version:        3.4
Release:        1
Summary:        opsi utils
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
msgfmt -o gettext/opsi_newprod_fr.mo gettext/opsi_newprod_fr.po

# ===[ install ]==================================== 
%install
mkdir -p $RPM_BUILD_ROOT/usr/bin
mkdir -p $RPM_BUILD_ROOT/usr/share/locale/de/LC_MESSAGES
mkdir -p $RPM_BUILD_ROOT/usr/share/locale/fr/LC_MESSAGES
install -m 0640 gettext/opsi_newprod_de.mo $RPM_BUILD_ROOT/usr/share/locale/de/LC_MESSAGES/opsi_newprod.mo
install -m 0640 gettext/opsi_newprod_fr.mo $RPM_BUILD_ROOT/usr/share/locale/fr/LC_MESSAGES/opsi_newprod.mo
install -m 0750 opsi-admin $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-newprod $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-makeproductfile $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsiinst $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsiuninst $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-package-manager $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-convert $RPM_BUILD_ROOT/usr/bin/
install -m 0755 sysbackup $RPM_BUILD_ROOT/usr/bin/


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
%attr(644,root,root) %config /usr/share/locale/fr/LC_MESSAGES/opsi_newprod.mo

# other files
%attr(750,root,opsiadmin) /usr/bin/opsi-admin
/usr/bin/opsi-newprod
/usr/bin/opsi-makeproductfile
/usr/bin/opsiinst
/usr/bin/opsiuninst
/usr/bin/opsi-package-manager
/usr/bin/opsi-convert
/usr/bin/sysbackup

# directories
%dir /usr/share/locale/de/LC_MESSAGES
%dir /usr/share/locale/fr/LC_MESSAGES

# ===[ changelog ]==================================
%changelog
* Wed Sep 17 2008 - j.schneider@uib.de
- created new package









