# genview/facenode_client.py
import os
import requests

from dotenv import load_dotenv

load_dotenv()

# -----------------------------------------------------------------
# Konfiguration – URL & API‑Key (falls du in Compreface auth nutzt)
# -----------------------------------------------------------------
API_BASE = os.getenv('FACE_RECOGNITION_URL', 'http://localhost:8000/api/v1')
API_KEY  = os.getenv('COMPREFACE_API_KEY', '')        # leer → kein Header nötig

HEADERS = {'X-API-Key': API_KEY} if API_KEY else {}

# -----------------------------------------------------------------
# 1️⃣ Detect‑Funktion – gibt Bounding‑Boxes zurück
# -----------------------------------------------------------------
def detect_faces_via_api(image_path):
    """
    Schickt das Bild an den DeepFace‑(Compreface‑ähnlichen) Service
    und liefert eine Liste von Dicts:
    [
        {"x": ..., "y": ..., "width": ..., "height": ..., "confidence": ...},
        …
    ]
    """
    url = f'{API_BASE}/detect'          # Endpunkt laut Service‑Spec
    with open(image_path, 'rb') as f:
        files = {'file': f}
        response = requests.post(url, files=files, headers=HEADERS, timeout=30)
    response.raise_for_status()
    payload = response.json()

    faces = []
    for entry in payload.get('faces', []):
        faces.append({
            'x':          int(entry.get('x', 0)),
            'y':          int(entry.get('y', 0)),
            'width':      int(entry.get('width', 0)),
            'height':     int(entry.get('height', 0)),
            'confidence': float(entry.get('confidence', 1.0)),
        })
    return faces