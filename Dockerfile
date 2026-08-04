# ==========================================
# Stage 1️⃣ Builder
# ==========================================
FROM registry.access.redhat.com/hi/python:3.14-builder AS builder
USER root
WORKDIR /app

# Install gettext ONLY in the builder for compilemessages
RUN dnf install -y gettext && dnf clean all

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install pip requirements directly into the venv
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Compile Django translations (this requires gettext)
# We do this here so the final image only gets the compiled .mo files
RUN SECRET_KEY=build-time-only-not-for-runtime \
    ALLOWED_HOSTS=localhost \
    python manage.py compilemessages

# ==========================================
# Stage 2️⃣ Final (Rootless & Hardened)
# ==========================================
FROM registry.access.redhat.com/hi/python:3.14 AS final

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER root
WORKDIR /usr/src/app

# Copy the venv from builder
COPY --from=builder --chown=1001:0 /opt/venv /opt/venv

# Copy the application code (now containing compiled translations)
COPY --from=builder --chown=1001:0 /app /usr/src/app

# Set up the volume mount point with correct permissions
RUN mkdir -p /data/genview && chown -R 1001:0 /data/genview

# Explicitly make the Python entrypoint executable
RUN chmod +x docker-entrypoint.py

# Switch to the default rootless user provided by Red Hat
USER 1001

VOLUME /data/genview

# Execute the entrypoint via the venv's Python binary
ENTRYPOINT ["python", "docker-entrypoint.py"]
#CMD ["python", "manage.py", "runserver", "0.0.0.0:8003"]
CMD ["gunicorn", "rootnode.wsgi:application", "--bind", "0.0.0.0:8003", "--workers", "3", "--access-logfile", "-", "--error-logfile", "-"]