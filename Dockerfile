# --------------------------------------------------------------
# 1️⃣ Builder-Stage – Dependencies, venv und Wheels bauen
# --------------------------------------------------------------
FROM python:3.13-slim AS builder
WORKDIR /app

# ---- System-Pakete (gettext + optional locales) ----------------
RUN apt-get update && \
    apt-get install -y --no-install-recommends gettext && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/*

# ---- Python-Umgebung (virtualenv) ------------------------------
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ---- Pip-Requirements (Wheels generieren) --------------------
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

# ---- Nicht-root-User für spätere Nutzung (nur Definitions-Zeit) -
ARG APP_UID=1001
ARG APP_GID=1001
RUN groupadd -g ${APP_GID} appgroup && \
    useradd -m -u ${APP_UID} -g ${APP_GID} -s /bin/bash appuser

# --------------------------------------------------------------
# 2️⃣ Runtime-Stage – Minimal-Image, venv und App-Code
# --------------------------------------------------------------
FROM python:3.13-slim
WORKDIR /app

# ---- System-Pakete, die zur Laufzeit gebraucht werden ---------
RUN apt-get update && \
    apt-get install -y --no-install-recommends gettext && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/*

# ---- Kopiere das vorbereitete venv und die Wheels ------------
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/wheels /wheels

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ---- Installiere die Wheels (keine zweite pip-install-Runde) ---
RUN pip install --no-cache /wheels/*

# ---- Kopiere den non-root-User aus der Builder-Stage -----------
COPY --from=builder /etc/passwd /etc/passwd
COPY --from=builder /etc/group  /etc/group

# ---- Anwendungscode -------------------------------------------
COPY . /usr/src/app
WORKDIR /usr/src/app

# ---- Volume (nur im finalen Image) ---------------------------
VOLUME /data/genview

# ---- Entry-point-Script (muss ausführbar sein) --------------
RUN chmod +x docker-entrypoint.sh

# ---- Setze den non-root-User (aus Builder-Stage) ------------
USER appuser:appgroup

# ---- Container-Start ------------------------------------------
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["./manage.py", "runserver", "0.0.0.0:8003"]