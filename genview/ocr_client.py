# genview/ocr_client.py
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
API_BASE = os.getenv('OCR_RECOGNITION_URL', 'http://localhost:8000/api/v1')
API_KEY  = os.getenv('OCR_RECOGNITION_API_KEY', '')        # leer → kein Header nötig

HEADERS = {'X-API-Key': API_KEY} if API_KEY else {}

# -----------------------------------------------------------------
# 1️⃣ Detect‑Funktion – gibt Bounding‑Boxes zurück
# -----------------------------------------------------------------
def extract_text_via_api(image_path: str) -> List[Dict[str, Any]]:
    
    url = f'{API_BASE}/extract'

    try:
        with open(image_path, 'rb') as f:
            response = requests.post(url, files={'file': f}, timeout=120)
        response.raise_for_status()
        return response.json().get('text', '')
    except Exception as e:
        print(f"Fehler bei der OCR Erkennung: {e}")
        return ""