# genview/facenode_client.py
import json
import logging
import os
from typing import Any, TypedDict

import requests
from django.conf import settings
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

API_BASE = os.getenv("FACE_RECOGNITION_URL", "http://localhost:8000/api/v1")
API_KEY = os.getenv("FACE_RECOGNITION_API_KEY", "API_KEY")
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

REQUEST_TIMEOUT = 30
_LOG_BODY_MAX_LEN = 500


class FaceDetection(TypedDict):
    x: int
    y: int
    width: int
    height: int
    confidence: float
    embedding: Any


class FaceDetectResult(TypedDict):
    faces: list[FaceDetection]
    error: str | None


def _truncate_for_log(text: str, max_len: int = _LOG_BODY_MAX_LEN) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}…"


def _extract_api_error(payload: Any) -> str | None:
    """Return a user-facing error if the API reports failure in a 200 body."""
    if not isinstance(payload, dict):
        return None

    if payload.get("success") is False:
        return str(payload.get("message") or payload.get("detail") or "FaceNode meldete einen Fehler.")

    api_error = payload.get("error")
    if api_error:
        return str(api_error)

    return None


def _coerce_int(value: Any, field: str, index: int) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("FaceNode: Gesicht #%s – ungültiger Wert für %s: %r", index, field, value)
        return None


def _coerce_float(value: Any, field: str, index: int) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("FaceNode: Gesicht #%s – ungültiger Wert für %s: %r", index, field, value)
        return None


def _parse_face_entry(entry: Any, index: int) -> FaceDetection | None:
    if not isinstance(entry, dict):
        logger.warning("FaceNode: Eintrag #%s ist kein Objekt: %r", index, entry)
        return None

    x = _coerce_int(entry.get("x", 0), "x", index)
    y = _coerce_int(entry.get("y", 0), "y", index)
    width = _coerce_int(entry.get("width", 0), "width", index)
    height = _coerce_int(entry.get("height", 0), "height", index)
    confidence = _coerce_float(entry.get("confidence", 1.0), "confidence", index)

    if None in (x, y, width, height, confidence):
        return None
    if width <= 0 or height <= 0:
        logger.warning(
            "FaceNode: Gesicht #%s übersprungen – ungültige Bounding-Box (%sx%s)", index, width, height
        )
        return None

    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "confidence": confidence,
        "embedding": entry.get("embedding"),
    }


def _normalize_faces_payload(payload: Any) -> tuple[list[FaceDetection], str | None]:
    if not isinstance(payload, dict):
        return [], "Ungültiges Antwortformat vom FaceNode-Server."

    api_error = _extract_api_error(payload)
    if api_error:
        return [], api_error

    raw_faces = payload.get("faces", [])
    if raw_faces is None:
        return [], None
    if not isinstance(raw_faces, list):
        return [], "Ungültiges Antwortformat: 'faces' muss eine Liste sein."

    faces: list[FaceDetection] = []
    for index, entry in enumerate(raw_faces, start=1):
        parsed = _parse_face_entry(entry, index)
        if parsed is not None:
            faces.append(parsed)

    return faces, None


def detect_faces_via_api(image_path: str) -> FaceDetectResult:
    """
    Send image to FaceNode and return a stable result dict:

    {
        "faces": [...],
        "error": None | "Fehlermeldung"
    }

    Callers must check `error` first. An empty `faces` list with `error is None`
    means the request succeeded but no valid faces were detected.
    """
    result: FaceDetectResult = {"faces": [], "error": None}

    if not API_KEY and not settings.DEBUG and not getattr(settings, "TESTING", False):
        result["error"] = "FaceNode-API-Schlüssel ist nicht konfiguriert."
        logger.error(result["error"])
        return result

    if not image_path or not str(image_path).strip():
        result["error"] = "Kein Bildpfad angegeben."
        logger.error(result["error"])
        return result

    url = f"{API_BASE}/detect"

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

    filename = os.path.basename(image_path) or "image"

    try:
        response = requests.post(
            url,
            files={"file": (filename, file_content, "application/octet-stream")},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        try:
            payload = response.json()
        except json.JSONDecodeError:
            result["error"] = "Ungültige JSON-Antwort vom FaceNode-Server."
            logger.error(
                "%s Body: %s",
                result["error"],
                _truncate_for_log(response.text),
            )
            return result

        faces, parse_error = _normalize_faces_payload(payload)
        if parse_error:
            result["error"] = parse_error
            logger.error("FaceNode-Antwortfehler: %s", parse_error)
            return result

        result["faces"] = faces
        return result

    except requests.exceptions.HTTPError as exc:
        resp = exc.response
        status = resp.status_code if resp is not None else "?"
        body = _truncate_for_log(resp.text if resp is not None else str(exc))
        result["error"] = f"FaceNode-Server antwortete mit HTTP {status}."
        logger.error("FaceNode HTTP %s: %s", status, body)
    except requests.exceptions.ConnectionError:
        result["error"] = "Verbindung zum FaceNode-Server fehlgeschlagen."
        logger.error(result["error"])
    except requests.exceptions.Timeout:
        result["error"] = "Anfrage an FaceNode hat das Zeitlimit überschritten."
        logger.error(result["error"])
    except requests.exceptions.RequestException as exc:
        result["error"] = "Kommunikation mit FaceNode fehlgeschlagen."
        logger.exception("FaceNode RequestException: %s", exc)
    except Exception as exc:
        result["error"] = "Unerwarteter Fehler bei der Gesichtserkennung."
        logger.exception("FaceNode unerwarteter Fehler: %s", exc)

    return result
