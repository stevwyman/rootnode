# genview/facenode_client.py
import logging
import os
import requests
from dotenv import load_dotenv
from typing import List, Dict, Any

logger = logging.getLogger(__name__)
load_dotenv()

# -----------------------------------------------------------------
# Konfiguration – URL & API‑Key (falls du in Compreface auth nutzt)
# -----------------------------------------------------------------
API_BASE = os.getenv('FACE_RECOGNITION_URL', 'http://localhost:8000/api/v1')
API_KEY  = os.getenv('FACE_RECOGNITION_API_KEY', '')        # leer → kein Header nötig

HEADERS = {'X-API-Key': API_KEY} if API_KEY else {}

# -----------------------------------------------------------------
# 1️⃣ Detect‑Funktion – gibt Bounding‑Boxes zurück
# -----------------------------------------------------------------
def detect_faces_via_api(image_path: str) -> List[Dict[str, Any]]:
    """
    Sendet ein Bild an die DeepFace-API und gibt eine Liste der erkannten Gesichter zurück.
    Gibt bei einem Verbindungs- oder API-Fehler eine leere Liste zurück.
    """
    url = f'{API_BASE}/detect'

    try:
        with open(image_path, 'rb') as f:
            # Das Dictionary direkt im Methodenaufruf spart eine Zeile
            response = requests.post(url, files={'file': f}, headers=HEADERS, timeout=30)
            
        response.raise_for_status()
        payload = response.json()
        
    except requests.exceptions.RequestException as e:
        # Fängt Timeouts, Connection Errors und 4xx/5xx HTTP-Fehler ab
        logger.error(f"Fehler bei der Kommunikation mit der DeepFace API: {e}")
        return []
    except ValueError:
        # Fängt ab, falls die API plötzlich kein valides JSON mehr liefert
        logger.error("Ungültige JSON-Antwort von der DeepFace API erhalten.")
        return []

    # Eine "List Comprehension" macht die Schleife wesentlich kompakter und schneller
    return [
        {
            'x': int(entry.get('x', 0)),
            'y': int(entry.get('y', 0)),
            'width': int(entry.get('width', 0)),
            'height': int(entry.get('height', 0)),
            'confidence': float(entry.get('confidence', 1.0)),
        }
        for entry in payload.get('faces', [])
    ]