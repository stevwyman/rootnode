# genview/facenode_client.py
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
API_BASE = os.getenv('FACE_RECOGNITION_URL', 'http://localhost:8000/api/v1')
API_KEY  = os.getenv('FACE_RECOGNITION_API_KEY', '')        # leer → kein Header nötig
HEADERS = {'X-API-Key': API_KEY} if API_KEY else {}

# -----------------------------------------------------------------
# 1️⃣ Detect-Funktion – gibt Bounding-Boxes zurück
# -----------------------------------------------------------------
def detect_faces_via_api(image_path: str) -> Dict[str, Any]:
    """
    Sendet ein Bild an die DeepFace-API und liefert ein strukturiertes Ergebnis-Dict:

    {
        "faces": [               # Liste von Face-Dictionaries (kann leer sein)
            {
                "x": int,
                "y": int,
                "width": int,
                "height": int,
                "confidence": float,
                "embedding": Any
            },
            ...
        ],
        "error": None | "Fehlermeldung"
    }

    Der Aufrufer prüft zuerst `result["error"]`. Ist dieser `None`,
    kann `result["faces"]` verarbeitet werden.
    """
    url = f'{API_BASE}/detect'

    # Grundgerüst des Rückgabe-Objekts
    result: Dict[str, Any] = {
        "faces": [],   # Standard-Leerwert
        "error": None
    }

    try:
        with open(image_path, "rb") as f:
            response = requests.post(
                url,
                files={"file": f},
                headers=HEADERS,
                timeout=30,
            )
        response.raise_for_status()

        payload = response.json()

        # Normalisiere die Antwort in das gewünschte Format
        result["faces"] = [
            {
                "x": int(entry.get("x", 0)),
                "y": int(entry.get("y", 0)),
                "width": int(entry.get("width", 0)),
                "height": int(entry.get("height", 0)),
                "confidence": float(entry.get("confidence", 1.0)),
                "embedding": entry.get("embedding"),
            }
            for entry in payload.get("faces", [])
        ]

    # ------------------- Fehler-Behandlung -------------------
    except FileNotFoundError:
        result["error"] = f"Datei nicht gefunden: {image_path}"
        logger.error(result["error"])

    except requests.exceptions.HTTPError:
        result["error"] = (
            f"HTTP-Fehler {response.status_code}: {response.text}"
        )
        logger.error(result["error"])

    except requests.exceptions.ConnectionError:
        result["error"] = "Verbindung zum DeepFace-Server fehlgeschlagen"
        logger.error(result["error"])

    except requests.exceptions.Timeout:
        result["error"] = "Anfrage an DeepFace-API hat das Zeitlimit überschritten"
        logger.error(result["error"])

    except ValueError:
        # JSON-Decode-Fehler
        result["error"] = "Ungültige JSON-Antwort von der DeepFace-API"
        logger.error(result["error"])

    except Exception as e:
        result["error"] = f"Unerwarteter Fehler: {e}"
        logger.exception(result["error"])

    return result