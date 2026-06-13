#!/bin/sh
set -e

# ------------------------------------------------------------------
# UID/GID des momentan laufenden Users (appuser)
# ------------------------------------------------------------------
APP_UID=$(id -u)   # z. B. 1000
APP_GID=$(id -g)   # z. B. 1000

echo "🚀 Container startet als UID=${APP_UID} GID=${APP_GID}"

# ------------------------------------------------------------------
# Migrations (optional – du kannst das auch separat ausführen)
# ------------------------------------------------------------------
echo "🔁 Run migrations …"
python manage.py migrate
echo "🔁 Run compilemessages …"
python manage.py compilemessages

# ------------------------------------------------------------------
# Starte den eigentlichen Django‑Befehl (z. B. runserver, gunicorn, …)
# ------------------------------------------------------------------
exec "$@"