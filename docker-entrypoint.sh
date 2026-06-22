#!/bin/sh
set -e

# ------------------------------------------------------------------
# 1️⃣ UID/GID des momentan laufenden Users (appuser)
# ------------------------------------------------------------------
APP_UID=$(id -u)   # z. B. 1000
APP_GID=$(id -g)   # z. B. 1000

echo "🚀 Container startet als UID=${APP_UID} GID=${APP_GID}"

# ------------------------------------------------------------------
# 2️⃣ Statisches Sammeln (nur wenn STATIC_ROOT noch leer ist)
# ------------------------------------------------------------------
if [ ! -d "/data/genview/staticfiles" ]; then
    echo "🗂️ Erstelle Verzeichnis /data/genview/staticfiles"
    mkdir -p /data/genview/staticfiles
    chown -R $(id -u):$(id -g) /data/genview/staticfiles
else
    echo "✅ staticfiles-Verzeichnis existiert bereits."
fi

echo "🧹 Collectstatic wird ausgeführt …"
python manage.py collectstatic --noinput

# ------------------------------------------------------------------
# 3️⃣ Migrations (optional – du kannst das auch separat ausführen)
# ------------------------------------------------------------------
echo "🔁 Run migrations …"
python manage.py migrate

# ------------------------------------------------------------------
# 4️⃣ Starte den eigentlichen Django-Befehl (z. B. runserver, gunicorn, …)
# ------------------------------------------------------------------
exec "$@"