# genview/ocr_client.py
import logging
import os
import requests
from dotenv import load_dotenv
from typing import Dict, Any

logger = logging.getLogger(__name__)
load_dotenv()

# -----------------------------------------------------------------
# Konfiguration – URL & API-Key (falls du in Compreface auth nutzt)
# -----------------------------------------------------------------
API_BASE = os.getenv('OCR_RECOGNITION_URL', 'http://localhost:8000/api/v1')
API_KEY  = os.getenv('OCR_RECOGNITION_API_KEY', '')        # leer → kein Header nötig

HEADERS = {'X-API-Key': API_KEY} if API_KEY else {}

# -----------------------------------------------------------------
# 1️⃣ Detect-Funktion – gibt Bounding-Boxes zurück
# -----------------------------------------------------------------
def extract_text_via_api(image_path: str) -> Dict[str, Any]:
    """
    Ruft den OCR-Service auf und liefert ein JSON-ähnliches Dict zurück:

    {
        "text":   [...],   # Ergebnis-Daten (kann leer sein)
        "error":  None | "Fehlermeldung"
    }

    So hat der Aufrufer immer dieselbe Datenstruktur und kann
    bequem prüfen, ob ein Fehler aufgetreten ist.
    """
    url = f'{API_BASE}/extract'

    # Grundgerüst des Rückgabe-Objekts
    result: Dict[str, Any] = {
        "text": [],      # Standard-Leerwert (Typ-kompatibel zu API-Antwort)
        "error": None
    }

    try:
        with open(image_path, 'rb') as f:
            response = requests.post(url, files={'file': f}, timeout=120)
        response.raise_for_status()

        result["text"]=response.json().get('text', '')
    except FileNotFoundError:
        result["error"] = f"Datei nicht gefunden: {image_path}"
        logger.error(result["error"])

    except requests.exceptions.HTTPError as http_err:
        result["error"] = (
            f"HTTP-Fehler {response.status_code}: {http_err}"
        )
        logger.error(result["error"])

    except requests.exceptions.ConnectionError:
        result["error"] = "Verbindung zum OCR-Server fehlgeschlagen"
        logger.error(result["error"])

    except requests.exceptions.Timeout:
        result["error"] = "Anfrage an den OCR-Server hat das Zeitlimit überschritten"
        logger.error(result["error"])

    except Exception as e:
        result["error"] = f"Unerwarteter Fehler: {e}"
        logger.exception(result["error"])

    return result