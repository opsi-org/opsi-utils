#
# spec file for package opsi-utils
#
# Copyright (c) 2010-2019 uib GmbH.
# This file and all modifications and additions to the pristine
# package are under the same license as the package itself.
#

Name:           opsi-utils
%if 0%{?rhel_version} >= 800 || 0%{?centos_version} >= 800
BuildRequires:  python27
%else
BuildRequires:  python >= 2.7
%endif
Requires:       python >= 2.7
Requires:       python-opsi >= 4.1.1.71
Requires:       zsync
Url:            http://www.opsi.org
License:        AGPL-3.0-only
Group:          Productivity/Networking/Opsi
AutoReqProv:    on
Version:        4.1.1.33
Release:        7
Summary:        Tools for working on a opsi server
Source:         opsi-utils_4.1.1.33-7.tar.gz
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
BuildRequires:  zypper
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
mkdir -p $RPM_BUILD_ROOT/usr/share/locale/da/LC_MESSAGES
msgfmt -o $RPM_BUILD_ROOT/usr/share/locale/da/LC_MESSAGES/opsi-utils.mo gettext/opsi-utils_da.po
chmod 644 $RPM_BUILD_ROOT/usr/share/locale/da/LC_MESSAGES/opsi-utils.mo
mkdir -p $RPM_BUILD_ROOT/usr/share/locale/de/LC_MESSAGES
msgfmt -o $RPM_BUILD_ROOT/usr/share/locale/de/LC_MESSAGES/opsi-utils.mo gettext/opsi-utils_de.po
chmod 644 $RPM_BUILD_ROOT/usr/share/locale/de/LC_MESSAGES/opsi-utils.mo
mkdir -p $RPM_BUILD_ROOT/usr/share/locale/es/LC_MESSAGES
msgfmt -o $RPM_BUILD_ROOT/usr/share/locale/es/LC_MESSAGES/opsi-utils.mo gettext/opsi-utils_es.po
chmod 644 $RPM_BUILD_ROOT/usr/share/locale/es/LC_MESSAGES/opsi-utils.mo
mkdir -p $RPM_BUILD_ROOT/usr/share/locale/fr/LC_MESSAGES
msgfmt -o $RPM_BUILD_ROOT/usr/share/locale/fr/LC_MESSAGES/opsi-utils.mo gettext/opsi-utils_fr.po
chmod 644 $RPM_BUILD_ROOT/usr/share/locale/fr/LC_MESSAGES/opsi-utils.mo
mkdir -p $RPM_BUILD_ROOT/usr/share/locale/nl/LC_MESSAGES
msgfmt -o $RPM_BUILD_ROOT/usr/share/locale/nl/LC_MESSAGES/opsi-utils.mo gettext/opsi-utils_nl.po
chmod 644 $RPM_BUILD_ROOT/usr/share/locale/nl/LC_MESSAGES/opsi-utils.mo
mkdir -p $RPM_BUILD_ROOT/usr/share/locale/ru/LC_MESSAGES
msgfmt -o $RPM_BUILD_ROOT/usr/share/locale/ru/LC_MESSAGES/opsi-utils.mo gettext/opsi-utils_ru.po
chmod 644 $RPM_BUILD_ROOT/usr/share/locale/ru/LC_MESSAGES/opsi-utils.mo

mkdir -p $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 manpages/compiled/opsi-admin.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 manpages/compiled/opsi-backup.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 manpages/compiled/opsi-convert.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 manpages/compiled/opsi-makepackage.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 manpages/compiled/opsi-newprod.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 manpages/compiled/opsi-package-manager.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/
install -m 644 manpages/compiled/opsi-package-updater.1.gz $RPM_BUILD_ROOT/usr/share/man/man1/

mkdir -p $RPM_BUILD_ROOT/usr/bin
install -m 0755 opsi-admin $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-newprod $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-makepackage $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-migrate-product-updater-configuration $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-package-manager $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-package-updater $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-convert $RPM_BUILD_ROOT/usr/bin/
install -m 0755 opsi-backup $RPM_BUILD_ROOT/usr/bin/

mkdir -p $RPM_BUILD_ROOT/etc/opsi
install -m 0644 data/opsi-package-updater.conf $RPM_BUILD_ROOT/etc/opsi/

mkdir -p $RPM_BUILD_ROOT/etc/opsi/package-updater.repos.d
install -m 0644 data/etc/opsi/package-updater.repos.d/example.repo.template $RPM_BUILD_ROOT/etc/opsi/package-updater.repos.d
install -m 0644 data/etc/opsi/package-updater.repos.d/experimental.repo $RPM_BUILD_ROOT/etc/opsi/package-updater.repos.d
install -m 0644 data/etc/opsi/package-updater.repos.d/opsi-server.repo $RPM_BUILD_ROOT/etc/opsi/package-updater.repos.d
install -m 0644 data/etc/opsi/package-updater.repos.d/testing.repo $RPM_BUILD_ROOT/etc/opsi/package-updater.repos.d
install -m 0644 data/etc/opsi/package-updater.repos.d/uib-linux.repo $RPM_BUILD_ROOT/etc/opsi/package-updater.repos.d
install -m 0644 data/etc/opsi/package-updater.repos.d/uib-local_image.repo $RPM_BUILD_ROOT/etc/opsi/package-updater.repos.d
install -m 0644 data/etc/opsi/package-updater.repos.d/uib-windows.repo $RPM_BUILD_ROOT/etc/opsi/package-updater.repos.d

mkdir -p $RPM_BUILD_ROOT/etc/logrotate.d/
install -m 0644 data/etc/logrotate.d/opsi-backup $RPM_BUILD_ROOT/etc/logrotate.d/
install -m 0644 data/etc/logrotate.d/opsi-package-manager $RPM_BUILD_ROOT/etc/logrotate.d/
install -m 0644 data/etc/logrotate.d/opsi-package-updater $RPM_BUILD_ROOT/etc/logrotate.d/

%if 0%{?suse_version} > 1110
echo "Detected openSuse / SLES"
LOGROTATE_VERSION="$(zypper info logrotate | grep -i "version" | awk '{print $2}' | cut -d '-' -f 1)"
if [ "$(zypper --terse versioncmp $LOGROTATE_VERSION 3.8)" == "-1" ]; then
	echo "Fixing logrotate configuration for logrotate version older than 3.8"
	LOGROTATE_TEMP=data/etc/logrotate.d/opsi-backup.temp
	LOGROTATE_CONFIG=data/etc/logrotate.d/opsi-backup
	grep -v "su opsiconfd opsiadmin" $LOGROTATE_CONFIG > $LOGROTATE_TEMP
	mv $LOGROTATE_TEMP $LOGROTATE_CONFIG

	LOGROTATE_TEMP=data/etc/logrotate.d/opsi-package-manager.temp
	LOGROTATE_CONFIG=data/etc/logrotate.d/opsi-package-manager
	grep -v "su opsiconfd opsiadmin" $LOGROTATE_CONFIG > $LOGROTATE_TEMP
	mv $LOGROTATE_TEMP $LOGROTATE_CONFIG

	LOGROTATE_TEMP=data/etc/logrotate.d/opsi-package-updater.temp
	LOGROTATE_CONFIG=data/etc/logrotate.d/opsi-package-updater
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
			LOGROTATE_TEMP=data/etc/logrotate.d/opsi-backup.temp
			LOGROTATE_CONFIG=data/etc/logrotate.d/opsi-backup
			grep -v "su opsiconfd opsiadmin" $LOGROTATE_CONFIG > $LOGROTATE_TEMP
			mv $LOGROTATE_TEMP $LOGROTATE_CONFIG

			LOGROTATE_TEMP=data/etc/logrotate.d/opsi-package-manager.temp
			LOGROTATE_CONFIG=data/etc/logrotate.d/opsi-package-manager
			grep -v "su opsiconfd opsiadmin" $LOGROTATE_CONFIG > $LOGROTATE_TEMP
			mv $LOGROTATE_TEMP $LOGROTATE_CONFIG

			LOGROTATE_TEMP=data/etc/logrotate.d/opsi-package-updater.temp
			LOGROTATE_CONFIG=data/etc/logrotate.d/opsi-package-updater
			grep -v "su opsiconfd opsiadmin" $LOGROTATE_CONFIG > $LOGROTATE_TEMP
			mv $LOGROTATE_TEMP $LOGROTATE_CONFIG
		%endif
	%endif
%endif
install -m 0644 data/etc/logrotate.d/opsi-backup $RPM_BUILD_ROOT/etc/logrotate.d/
install -m 0644 data/etc/logrotate.d/opsi-package-manager $RPM_BUILD_ROOT/etc/logrotate.d/
install -m 0644 data/etc/logrotate.d/opsi-package-updater $RPM_BUILD_ROOT/etc/logrotate.d/

mkdir -p $RPM_BUILD_ROOT/var/lib/opsi/repository

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
%doc /usr/share/man/man1/opsi-makepackage.1.gz
%doc /usr/share/man/man1/opsi-newprod.1.gz
%doc /usr/share/man/man1/opsi-package-manager.1.gz
%doc /usr/share/man/man1/opsi-package-updater.1.gz

# configfiles
%attr(660,root,opsiadmin) %config(noreplace) /etc/opsi/opsi-package-updater.conf
%attr(660,root,opsiadmin) %config(noreplace) /etc/opsi/package-updater.repos.d/example.repo.template
%attr(660,root,opsiadmin) %config(noreplace) /etc/opsi/package-updater.repos.d/experimental.repo
%attr(660,root,opsiadmin) %config(noreplace) /etc/opsi/package-updater.repos.d/opsi-server.repo
%attr(660,root,opsiadmin) %config(noreplace) /etc/opsi/package-updater.repos.d/testing.repo
%attr(660,root,opsiadmin) %config(noreplace) /etc/opsi/package-updater.repos.d/uib-linux.repo
%attr(660,root,opsiadmin) %config(noreplace) /etc/opsi/package-updater.repos.d/uib-local_image.repo
%attr(660,root,opsiadmin) %config(noreplace) /etc/opsi/package-updater.repos.d/uib-windows.repo
%attr(644,root,root) %config /etc/logrotate.d/opsi-backup
%attr(644,root,root) %config /etc/logrotate.d/opsi-package-manager
%attr(644,root,root) %config /etc/logrotate.d/opsi-package-updater

# other files
/usr/bin/opsi-admin
/usr/bin/opsi-backup
/usr/bin/opsi-convert
/usr/bin/opsi-makepackage
/usr/bin/opsi-migrate-product-updater-configuration
/usr/bin/opsi-newprod
/usr/bin/opsi-package-manager
/usr/bin/opsi-package-updater

%attr(644,root,root) /usr/share/locale/da/LC_MESSAGES/opsi-utils.mo
%attr(644,root,root) /usr/share/locale/de/LC_MESSAGES/opsi-utils.mo
%attr(644,root,root) /usr/share/locale/es/LC_MESSAGES/opsi-utils.mo
%attr(644,root,root) /usr/share/locale/fr/LC_MESSAGES/opsi-utils.mo
%attr(644,root,root) /usr/share/locale/nl/LC_MESSAGES/opsi-utils.mo
%attr(644,root,root) /usr/share/locale/ru/LC_MESSAGES/opsi-utils.mo

# directories
%if 0%{?suse_version}
%dir /etc/opsi
%dir /var/lib/opsi
%dir %attr(775, root, pcpatch) /var/lib/opsi/repository
%endif
%dir /etc/opsi/package-updater.repos.d

# ===[ changelog ]==================================
%changelog
