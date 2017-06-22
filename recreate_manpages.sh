#! /bin/bash

rm -rf manpages/compiled/ || true
mkdir -p manpages/compiled

# Compiling manpages
a2x --doctype manpage --format manpage manpages/opsi-admin.asciidoc
a2x --doctype manpage --format manpage manpages/opsi-backup.asciidoc
a2x --doctype manpage --format manpage manpages/opsi-convert.asciidoc
a2x --doctype manpage --format manpage manpages/opsi-makepackage.asciidoc
a2x --doctype manpage --format manpage manpages/opsi-newprod.asciidoc
a2x --doctype manpage --format manpage manpages/opsi-package-manager.asciidoc
a2x --doctype manpage --format manpage manpages/opsi-package-updater.asciidoc

# gzip'ing as requested by http://www.debian.org/manpages/debian-policy/ch-docs.html#s12.1
gzip -9 manpages/opsi-admin.1
gzip -9 manpages/opsi-backup.1
gzip -9 manpages/opsi-convert.1
gzip -9 manpages/opsi-makepackage.1
gzip -9 manpages/opsi-newprod.1
gzip -9 manpages/opsi-package-manager.1
gzip -9 manpages/opsi-package-updater.1

mv manpages/*.1.gz manpages/compiled/
