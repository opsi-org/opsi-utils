#!/bin/bash

cwd=$(pwd)
dir=${cwd}/$(dirname $0)
cd $dir

for po in *.po; do
    bn=$(basename $po ".po")
    msgfmt -o ${bn}.mo $po
done
