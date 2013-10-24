#! /bin/bash

rm -rf doc/compiled/ || true
mkdir -p doc/compiled

# Compiling manpages
a2x --doctype manpage --format manpage doc/opsi-admin.asciidoc
a2x --doctype manpage --format manpage doc/opsi-backup.asciidoc
a2x --doctype manpage --format manpage doc/opsi-convert.asciidoc
a2x --doctype manpage --format manpage doc/opsi-makeproductfile.asciidoc
a2x --doctype manpage --format manpage doc/opsi-newprod.asciidoc
a2x --doctype manpage --format manpage doc/opsi-package-manager.asciidoc
a2x --doctype manpage --format manpage doc/opsi-product-updater.asciidoc

# gzip'ing as requested by http://www.debian.org/doc/debian-policy/ch-docs.html#s12.1
gzip -9 --stdout doc/opsi-admin.1 > doc/opsi-admin.1.gz
gzip -9 --stdout doc/opsi-backup.1 > doc/opsi-backup.1.gz
gzip -9 --stdout doc/opsi-convert.1 > doc/opsi-convert.1.gz
gzip -9 --stdout doc/opsi-makeproductfile.1 > doc/opsi-makeproductfile.1.gz
gzip -9 --stdout doc/opsi-newprod.1 > doc/opsi-newprod.1.gz
gzip -9 --stdout doc/opsi-package-manager.1 > doc/opsi-package-manager.1.gz
gzip -9 --stdout doc/opsi-product-updater.1 > doc/opsi-product-updater.1.gz

mv doc/*.1 doc/compiled/
mv doc/*.1.gz doc/compiled/
