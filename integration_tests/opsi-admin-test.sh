set -ex

poetry run opsi-admin -d method host_getObjects
poetry run opsi-admin -d method does_not_exist
