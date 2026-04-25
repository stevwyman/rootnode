# Dockerfile (oder Containerfile)
FROM python:3.11-slim

# System‑Abhängigkeiten (falls du lokale Bootstrap‑Dateien hast)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml poetry.lock ./
RUN pip install poetry && poetry config virtualenvs.create false && poetry install --no-dev

COPY . /app/

# Collect static files (Bootstrap‑CSS/JS wird mitkopiert)
RUN python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["gunicorn", "rootnode.wsgi:application", "--bind", "0.0.0.0:8000"]