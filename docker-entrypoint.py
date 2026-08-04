import os
import sys
import subprocess
import logging

# Setup basic logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("entrypoint")

def run_command(cmd):
    """Führt einen Befehl aus und bricht bei Fehlern ab."""
    logger.info(f"Running: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Command failed with exit code {e.returncode}")
        sys.exit(e.returncode)

def main():
    # ------------------------------------------------------------------
    # 1️⃣ UID/GID auslesen
    # ------------------------------------------------------------------
    uid = os.getuid()
    gid = os.getgid()
    logger.info(f"🚀 Container startet als UID={uid} GID={gid}")

    # ------------------------------------------------------------------
    # 2️⃣ Statisches Sammeln & Verzeichnisse anlegen
    # ------------------------------------------------------------------
    static_dir = "/data/genview/staticfiles"
    if not os.path.exists(static_dir):
        logger.info(f"🗂️ Erstelle Verzeichnis {static_dir}")
        try:
            os.makedirs(static_dir, exist_ok=True)
        except PermissionError:
            logger.error(
                f"❌ Keine Berechtigung zum Erstellen von {static_dir}. "
                "Stimmen die Volume-Rechte auf dem Host (chown 1001:0)?"
            )
            sys.exit(1)
    else:
        logger.info("✅ staticfiles-Verzeichnis existiert bereits.")

    logger.info("🧹 Collectstatic wird ausgeführt …")
    run_command(["python", "manage.py", "collectstatic", "--noinput"])

    # ------------------------------------------------------------------
    # 3️⃣ Datenbank-Migrationen
    # ------------------------------------------------------------------
    logger.info("🔁 Run migrations …")
    run_command(["python", "manage.py", "migrate"])

    # ------------------------------------------------------------------
    # 4️⃣ Befehl aus CMD ausführen (ersetzt 'exec "$@"')
    # ------------------------------------------------------------------
    # sys.argv enthält den ENTRYPOINT plus das, was in CMD steht.
    # sys.argv[0] ist "docker-entrypoint.py".
    # sys.argv[1:] ist z.B. ["python", "manage.py", "runserver", "0.0.0.0:8003"]
    args = sys.argv[1:]
    
    if not args:
        logger.error("❌ Kein Startbefehl übergeben!")
        sys.exit(1)

    logger.info(f"🚀 Starte App: {' '.join(args)}")
    
    # os.execvp ersetzt den aktuellen Python-Prozess komplett mit dem neuen Befehl.
    # Parameter 1: Die Executable (z.B. "python")
    # Parameter 2: Die Liste der Argumente inkl. Executable-Name
    os.execvp(args[0], args)

if __name__ == "__main__":
    main()