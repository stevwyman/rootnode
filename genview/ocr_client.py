# genview/ocr_client.py
import json
import logging
import os
from typing import Any, TypedDict

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()

API_BASE = os.getenv("OCR_RECOGNITION_URL", "http://localhost:8000/api/v1")
API_KEY = os.getenv("OCR_RECOGNITION_API_KEY", "")
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

REQUEST_TIMEOUT = 120
_LOG_BODY_MAX_LEN = 500


class OcrExtractResult(TypedDict):
    text: str
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
        return str(payload.get("message") or payload.get("detail") or "OCR-Server meldete einen Fehler.")

    api_error = payload.get("error")
    if api_error:
        return str(api_error)

    return None


def _normalize_text_value(raw: Any) -> tuple[str, str | None]:
    if raw is None:
        return "", None
    if isinstance(raw, str):
        return raw, None
    if isinstance(raw, list):
        lines: list[str] = []
        for index, item in enumerate(raw, start=1):
            if isinstance(item, str):
                lines.append(item)
            elif isinstance(item, dict):
                line = item.get("text") or item.get("content") or item.get("value")
                if line is None:
                    logger.warning("OCR: Zeile #%s ohne Textfeld: %r", index, item)
                    continue
                lines.append(str(line))
            elif item is not None:
                lines.append(str(item))
        return "\n".join(lines), None
    if isinstance(raw, (int, float, bool)):
        return str(raw), None

    return "", "Ungültiges Antwortformat: 'text' hat einen unerwarteten Typ."


def _normalize_text_payload(payload: Any) -> tuple[str, str | None]:
    if not isinstance(payload, dict):
        return "", "Ungültiges Antwortformat vom OCR-Server."

    api_error = _extract_api_error(payload)
    if api_error:
        return "", api_error

    if "text" not in payload:
        return "", None

    return _normalize_text_value(payload.get("text"))


def extract_text_via_api(image_path: str) -> OcrExtractResult:
    """
    Call the OCR service and return a stable result dict:

    {
        "text": "",
        "error": None | "Fehlermeldung"
    }

    Callers must check `error` first. Empty `text` with `error is None`
    means the request succeeded but no text was detected.
    """
    result: OcrExtractResult = {"text": "", "error": None}

    if not image_path or not str(image_path).strip():
        result["error"] = "Kein Bildpfad angegeben."
        logger.error(result["error"])
        return result

    url = f"{API_BASE}/extract"

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
            result["error"] = "Ungültige JSON-Antwort vom OCR-Server."
            logger.error(
                "%s Body: %s",
                result["error"],
                _truncate_for_log(response.text),
            )
            return result

        text, parse_error = _normalize_text_payload(payload)
        if parse_error:
            result["error"] = parse_error
            logger.error("OCR-Antwortfehler: %s", parse_error)
            return result

        result["text"] = text
        return result

    except requests.exceptions.HTTPError as exc:
        resp = exc.response
        status = resp.status_code if resp is not None else "?"
        body = _truncate_for_log(resp.text if resp is not None else str(exc))
        result["error"] = f"OCR-Server antwortete mit HTTP {status}."
        logger.error("OCR HTTP %s: %s", status, body)
    except requests.exceptions.ConnectionError:
        result["error"] = "Verbindung zum OCR-Server fehlgeschlagen."
        logger.error(result["error"])
    except requests.exceptions.Timeout:
        result["error"] = "Anfrage an den OCR-Server hat das Zeitlimit überschritten."
        logger.error(result["error"])
    except requests.exceptions.RequestException as exc:
        result["error"] = "Kommunikation mit dem OCR-Server fehlgeschlagen."
        logger.exception("OCR RequestException: %s", exc)
    except Exception as exc:
        result["error"] = "Unerwarteter Fehler bei der Texterkennung."
        logger.exception("OCR unerwarteter Fehler: %s", exc)

    return result
