#
# spec file for package opsi-utils
#
# Copyright (c) 2010 uib GmbH.
# This file and all modifications and additions to the pristine
# package are under the same license as the package itself.
#

Name:           opsi-utils
BuildRequires:  python >= 2.4
Requires:       python-opsi >= 3.99
Url:            http://www.opsi.org
License:        GPL v2 or later
Group:          Productivity/Networking/Opsi
AutoReqProv:    on
Version:        3.99.0
Release:        1
Summary:        opsi utils
%define tarname opsi-utils
Source:         %{tarname}-%{version}.tar.bz2
BuildRoot:      %{_tmppath}/%{name}-%{version}-build
BuildArch:      noarch
%if 0%{?suse_version}
Requires:       python-curses
%{py_requires}
%endif

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
#msgfmt -o gettext/opsi_newprod_de.mo gettext/opsi_newprod_de.po
#msgfmt -o gettext/opsi_newprod_fr.mo gettext/opsi_newprod_fr.po

# ===[ install ]==================================== 
%install
mkdir -p $RPM_BUILD_ROOT/usr/bin
#mkdir -p $RPM_BUILD_ROOT/usr/share/locale/de/LC_MESSAGES
#mkdir -p $RPM_BUILD_ROOT/usr/share/locale/fr/LC_MESSAGES
#install -m 0640 gettext/opsi_newprod_de.mo $RPM_BUILD_ROOT/usr/share/locale/de/LC_MESSAGES/opsi_newprod.mo
#install -m 0640 gettext/opsi_newprod_fr.mo $RPM_BUILD_ROOT/usr/share/locale/fr/LC_MESSAGES/opsi_newprod.mo
install -m 0755 opsi-admin $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-newprod $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-makeproductfile $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-package-manager $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-product-updater $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-convert $RPM_BUILD_ROOT/usr/bin/

mkdir -p $RPM_BUILD_ROOT/etc/opsi
install -m 0644 data/opsi-product-updater.conf $RPM_BUILD_ROOT/etc/opsi/

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
#%attr(644,root,root) %config /usr/share/locale/de/LC_MESSAGES/opsi_newprod.mo
#%attr(644,root,root) %config /usr/share/locale/fr/LC_MESSAGES/opsi_newprod.mo

%attr(660,root,opsiadmin) %config(noreplace) /etc/opsi/opsi-product-updater.conf

# other files
/usr/bin/opsi-admin
/usr/bin/opsi-newprod
/usr/bin/opsi-makeproductfile
/usr/bin/opsi-package-manager
/usr/bin/opsi-convert
/usr/bin/opsi-product-updater

# directories
#%dir /usr/share/locale/de/LC_MESSAGES
#%dir /usr/share/locale/fr/LC_MESSAGES
%dir /etc/opsi

# ===[ changelog ]==================================
%changelog
