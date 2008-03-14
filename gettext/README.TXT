# Verzeichnis erstellen
mkdir -p locale/de/LC_MESSAGES

# .pot Datei erstellen
pygettext -a newprod.py

# template .po Datei erstellen
msginit -l de_DE

# .po Datei bearbeiten
edit de.po

# .mo Datei erstellen
msgfmt -o locale/de/LC_MESSAGES/opsi_newprod.mo de.po
