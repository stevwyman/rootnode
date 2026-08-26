# genview/colorize_client.py
import json
import logging
import os
from typing import TypedDict

import requests
from django.conf import settings
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

API_BASE = os.getenv("COLORIZE_URL", "http://localhost:8000")
API_KEY = os.getenv("COLORIZE_API_KEY", "API_KEY")
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

REQUEST_TIMEOUT = 180
_LOG_BODY_MAX_LEN = 500


class ColorizeResult(TypedDict):
    image: bytes
    content_type: str
    error: str | None


def _truncate_for_log(text: str, max_len: int = _LOG_BODY_MAX_LEN) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}…"


def _colorize_url() -> str:
    base = (API_BASE or "").rstrip("/")
    if base.endswith("/colorize"):
        return base
    return f"{base}/colorize"


def _error_from_response(response: requests.Response) -> str | None:
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if content_type.startswith("image/"):
        return None
    try:
        payload = response.json()
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("success") is False:
        return str(payload.get("message") or payload.get("detail") or "ColorNode meldete einen Fehler.")
    detail = payload.get("detail") or payload.get("error")
    if detail:
        return str(detail)
    return None


def colorize_via_api(image_path: str) -> ColorizeResult:
    """
    Send an image to ColorNode and return JPEG bytes:

    {
        "image": b"...",
        "content_type": "image/jpeg",
        "error": None | "Fehlermeldung"
    }

    Callers must check `error` first.
    """
    result: ColorizeResult = {"image": b"", "content_type": "image/jpeg", "error": None}

    if not API_KEY and not settings.DEBUG and not getattr(settings, "TESTING", False):
        result["error"] = "ColorNode-API-Schlüssel ist nicht konfiguriert."
        logger.error(result["error"])
        return result

    if not image_path or not str(image_path).strip():
        result["error"] = "Kein Bildpfad angegeben."
        logger.error(result["error"])
        return result

    url = _colorize_url()

    try:
        with open(image_path, "rb") as image_file:
            file_content = image_file.read()
    except FileNotFoundError:
        result["error"] = f"Datei nicht gefunden: {image_path}"
        logger.error(result["error"])
        return result
    except OSError as exc:
        result["error"] = f"Datei kann nicht gelesen werden: {image_path}"
        logger.error("%s (%s)", result["error"], exc)
        return result

    filename = os.path.basename(image_path) or "image.jpg"

    try:
        response = requests.post(
            url,
            files={"file": (filename, file_content, "application/octet-stream")},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        api_error = _error_from_response(response)
        if api_error:
            result["error"] = api_error
            logger.error("ColorNode-Antwortfehler: %s", api_error)
            return result

        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not content_type.startswith("image/") or not response.content:
            result["error"] = "Ungültige Bild-Antwort vom ColorNode-Server."
            logger.error(
                "%s Body: %s",
                result["error"],
                _truncate_for_log(response.text if content_type.startswith("text/") else ""),
            )
            return result

        result["image"] = response.content
        result["content_type"] = content_type
        return result

    except requests.exceptions.HTTPError as exc:
        resp = exc.response
        status = resp.status_code if resp is not None else "?"
        api_error = _error_from_response(resp) if resp is not None else None
        result["error"] = api_error or f"ColorNode-Server antwortete mit HTTP {status}."
        body = _truncate_for_log(resp.text if resp is not None else str(exc))
        logger.error("ColorNode HTTP %s: %s", status, body)
    except requests.exceptions.ConnectionError:
        result["error"] = "Verbindung zum ColorNode-Server fehlgeschlagen."
        logger.error(result["error"])
    except requests.exceptions.Timeout:
        result["error"] = "Anfrage an ColorNode hat das Zeitlimit überschritten."
        logger.error(result["error"])
    except requests.exceptions.RequestException as exc:
        result["error"] = "Kommunikation mit ColorNode fehlgeschlagen."
        logger.exception("ColorNode RequestException: %s", exc)
    except Exception as exc:
        result["error"] = "Unerwarteter Fehler bei der Kolorisierung."
        logger.exception("ColorNode unerwarteter Fehler: %s", exc)

    return result
