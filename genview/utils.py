# genview/utils.py
import os
import io
from django.core.files.base import ContentFile
from PIL import Image, ImageDraw, ImageFont

def detect_and_tag_faces(media_obj):
    """
    1. Laden des Original‑Images.
    2. Gesichter mit `face_recognition` erkennen.
    3. Für jedes gefundene Gesicht ein FaceTag‑Eintrag erzeugen.
    4. Optional: ein neues Bild mit Rechtecken anlegen (zur Anzeige).
    Rückgabe: Liste von FaceTag‑Instanzen (noch nicht gespeichert) und das annotierte Bild (PIL‑Image).
    """
    # -------------------------------------------------
    # 1️⃣ Bild laden (als numpy‑Array für face_recognition)
    # -------------------------------------------------
    img_path = media_obj.file.path
    pil_image = Image.open(img_path).convert('RGB')
    image_np = face_recognition.load_image_file(img_path)

    # -------------------------------------------------
    # 2️⃣ Gesichter erkennen
    # -------------------------------------------------
    face_locations = face_recognition.face_locations(image_np)   # gibt (top, right, bottom, left)

    tags = []
    draw = ImageDraw.Draw(pil_image)
    font = ImageFont.load_default()

    for i, (top, right, bottom, left) in enumerate(face_locations):
        # ---- 2a. Rechteck einzeichnen
        draw.rectangle(((left, top), (right, bottom)), outline="red", width=3)

        # ---- 2b. Text‐Overlay (temporär „Face #i“)
        label = f"Face {i+1}"
        text_w, text_h = draw.textsize(label, font=font)
        draw.rectangle(((left, top - text_h - 2), (left + text_w, top)), fill="red")
        draw.text((left, top - text_h - 2), label, fill="white", font=font)

        # ---- 2c. FaceTag‑Objekt vorbereiten (noch nicht gespeichert)
        tag = FaceTag(
            media=media_obj,
            x=left,
            y=top,
            width=right - left,
            height=bottom - top,
            tag_label=label,
        )
        tags.append(tag)

    # -------------------------------------------------
    # 3️⃣ Annotiertes Bild in einen In‑Memory‑File legen (für das Template)
    # -------------------------------------------------
    buffer = io.BytesIO()
    pil_image.save(buffer, format="JPEG")
    annotated_image_content = ContentFile(buffer.getvalue(), name=f"{media_obj.id}_annotated.jpg")
    return tags, annotated_image_content