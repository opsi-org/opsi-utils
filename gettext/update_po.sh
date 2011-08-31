#!/bin/bash

package_name="opsi-utils"

cwd=$(pwd)
dir=${cwd}/$(dirname $0)

cd $dir
pygettext --extract-all --default-domain=${package_name} ../opsi-admin ../opsi-convert ../opsi-makeproductfile ../opsi-newprod ../opsi-package-manager
for lang in de fr; do
	if [ -e ${package_name}_${lang}.po ]; then
		msgmerge -U ${package_name}_${lang}.po ${package_name}.pot
	else
		msginit --no-translator --locale $lang --output-file ${package_name}_${lang}.po --input ${package_name}.pot
		sed -i 's#"Content-Type: text/plain.*#"Content-Type: text/plain; charset=UTF-8\\n"#' ${package_name}_${lang}.po
	fi
done
