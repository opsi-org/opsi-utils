#
# spec file for package opsi-utils
#
# Copyright (c) 2010 uib GmbH.
# This file and all modifications and additions to the pristine
# package are under the same license as the package itself.
#

Name:           opsi-utils
BuildRequires:  python >= 2.6
Requires:       python-opsi >= 4.0.3.4 zsync python >= 2.6
Url:            http://www.opsi.org
License:        GPLv2+
Group:          Productivity/Networking/Opsi
AutoReqProv:    on
Version:        4.0.5.2
Release:        1
Summary:        opsi utils
Source:         opsi-utils_4.0.5.2-1.tar.gz
BuildRoot:      %{_tmppath}/%{name}-%{version}-build
BuildArch:      noarch
%if 0%{?suse_version}
Requires:       python-curses
%{py_requires}
%endif
%if 0%{?centos_version} || 0%{?rhel_version} || 0%{?fedora_version}
BuildRequires:  gettext
%else
BuildRequires:  gettext-runtime
%endif

%if 0%{?suse_version} == 1110 || 0%{?suse_version} == 1315
# SLES
BuildRequires:  logrotate
%else
%if 0%{?suse_version}
Suggests: logrotate
BuildRequires:  logrotate
%endif
%endif

%define toplevel_dir %{name}-%{version}

# ===[ description ]================================
%description
This package contains the opsi util collection.

# ===[ debug_package ]==============================
%debug_package

# ===[ prep ]=======================================
%prep

# ===[ setup ]======================================
%setup -n %{name}-%{version}

# ===[ build ]======================================
%build

# ===[ install ]====================================
%install
mkdir -p $RPM_BUILD_ROOT/usr/share/locale/de/LC_MESSAGES
msgfmt -o $RPM_BUILD_ROOT/usr/share/locale/de/LC_MESSAGES/opsi-utils.mo gettext/opsi-utils_de.po
chmod 644 $RPM_BUILD_ROOT/usr/share/locale/de/LC_MESSAGES/opsi-utils.mo
mkdir -p $RPM_BUILD_ROOT/usr/share/locale/fr/LC_MESSAGES
msgfmt -o $RPM_BUILD_ROOT/usr/share/locale/fr/LC_MESSAGES/opsi-utils.mo gettext/opsi-utils_fr.po
chmod 644 $RPM_BUILD_ROOT/usr/share/locale/fr/LC_MESSAGES/opsi-utils.mo

mkdir -p $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 doc/compiled/opsi-admin.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 doc/compiled/opsi-backup.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 doc/compiled/opsi-convert.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 doc/compiled/opsi-makeproductfile.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 doc/compiled/opsi-newprod.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 doc/compiled/opsi-package-manager.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 doc/compiled/opsi-product-updater.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/

mkdir -p $RPM_BUILD_ROOT/usr/bin
install -m 0755 opsi-admin $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-newprod $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-makeproductfile $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-package-manager $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-product-updater $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-convert $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-backup $RPM_BUILD_ROOT/usr/bin/

mkdir -p $RPM_BUILD_ROOT/etc/opsi
install -m 0644 data/opsi-product-updater.conf $RPM_BUILD_ROOT/etc/opsi/

mkdir -p $RPM_BUILD_ROOT/etc/logrotate.d/
%if 0%{?suse_version} > 1110
echo "Detected openSuse / SLES"
LOGROTATE_VERSION="$(zypper info logrotate | grep -i "version" | awk '{print $2}' | cut -d '-' -f 1)"
if [ "$(zypper --terse versioncmp $LOGROTATE_VERSION 3.8)" == "-1" ]; then
	echo "Fixing logrotate configuration for logrotate version older than 3.8"
	LOGROTATE_TEMP=$RPM_BUILD_ROOT/opsi-package-log.temp
	LOGROTATE_CONFIG=$RPM_BUILD_ROOT/etc/logrotate.d/opsi-package-log
	grep -v "su opsiconfd opsiadmin" $LOGROTATE_CONFIG > $LOGROTATE_TEMP
	mv $LOGROTATE_TEMP $LOGROTATE_CONFIG
else
	echo "Logrotate version $LOGROTATE_VERSION is 3.8 or newer. Nothing to do."
fi
%else
	%if 0%{?rhel_version} || 0%{?centos_version} || 0%{?fedora_version}
		echo "Detected RHEL / CentOS / Fedora"
		%if 0%{?rhel_version} == 600 || 0%{?centos_version} == 600
			echo "Fixing logrotate configuration"
			LOGROTATE_TEMP=$RPM_BUILD_ROOT/opsi-package-log.temp
			LOGROTATE_CONFIG=$RPM_BUILD_ROOT/etc/logrotate.d/opsi-package-log
			grep -v "su opsiconfd opsiadmin" $LOGROTATE_CONFIG > $LOGROTATE_TEMP
			mv $LOGROTATE_TEMP $LOGROTATE_CONFIG
		%endif
	%endif
%endif
install -m 0644 data/etc/logrotate.d/opsi-package-log $RPM_BUILD_ROOT/etc/logrotate.d/

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
%doc /usr/share/man/man1/opsi-admin.1.gz
%doc /usr/share/man/man1/opsi-backup.1.gz
%doc /usr/share/man/man1/opsi-convert.1.gz
%doc /usr/share/man/man1/opsi-makeproductfile.1.gz
%doc /usr/share/man/man1/opsi-newprod.1.gz
%doc /usr/share/man/man1/opsi-package-manager.1.gz
%doc /usr/share/man/man1/opsi-product-updater.1.gz

# configfiles
%attr(660,root,opsiadmin) %config(noreplace) /etc/opsi/opsi-product-updater.conf
%config /etc/logrotate.d/opsi-package-log

# other files
/usr/bin/opsi-admin
/usr/bin/opsi-newprod
/usr/bin/opsi-makeproductfile
/usr/bin/opsi-package-manager
/usr/bin/opsi-convert
/usr/bin/opsi-product-updater
/usr/bin/opsi-backup

%attr(644,root,root) /usr/share/locale/de/LC_MESSAGES/opsi-utils.mo
%attr(644,root,root) /usr/share/locale/fr/LC_MESSAGES/opsi-utils.mo

# directories
#%dir /usr/share/locale/de/LC_MESSAGES
%if 0%{?suse_version}
%dir /etc/opsi
%endif

# ===[ changelog ]==================================
%changelog
