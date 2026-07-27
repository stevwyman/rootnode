import numpy as np
import requests
from urllib.parse import urlencode
from difflib import SequenceMatcher
from django.db import transaction
from django.urls import reverse
from .models import (
    FaceTag,
    Place,
    tree_thumbs_mini_directory_path,
    tree_thumbs_small_directory_path,
)

import io
import os
from pathlib import Path
from typing import Tuple

from django.core.files.base import ContentFile
from django.conf import settings

from PIL import Image, ImageOps

# ---------- PDF handling – PyMuPDF (fitz) ----------
# pip install "PyMuPDF>=1.22"
import fitz  # noqa: E402

def find_best_match_for_face(new_embedding, tree_id, threshold=0.30):
    """
    Vergleicht ein neues Gesicht-Embedding mit allen bereits Personen
    zugeordneten Embeddings im selben Stammbaum.
    Gibt das Individual-Objekt zurück, wenn ein Match gefunden wurde.
    """
    if not new_embedding:
        return None

    # 1. Hole alle bereits erfolgreich verknüpften Gesichter aus diesem Baum
    known_tags = FaceTag.objects.filter(
        media__gedcom_tree_id=tree_id,
        individual__isnull=False,
        embedding__isnull=False
    ).select_related('individual')

    if not known_tags.exists():
        return None

    best_individual = None
    lowest_distance = 1.0  # Startwert (Kosinus-Abstand liegt zwischen 0 und 1)

    # Vektor in ein Numpy-Array umwandeln für schnellere Berechnung
    A = np.array(new_embedding)

    for tag in known_tags:
        B = np.array(tag.embedding)
        
        # Mathematische Formel für den Kosinus-Abstand
        cosine_distance = 1 - (np.dot(A, B) / (np.linalg.norm(A) * np.linalg.norm(B)))

        # Wenn der Abstand kleiner ist als alles bisherige UND unter dem Threshold liegt
        if cosine_distance < lowest_distance and cosine_distance < threshold:
            lowest_distance = cosine_distance
            best_individual = tag.individual

    return best_individual


def get_similar_place_clusters(tree_id, threshold=0.80):
    """
    Gruppiert ähnliche Orte.
    Gibt eine Liste von Listen zurück: [[Place1, Place2, Place3], [Place4, Place5]]
    """
    # Alphabetisch sortieren, um sinnvolle Basis-Wörter als Cluster-Start zu haben
    places = list(Place.objects.filter(gedcom_tree_id=tree_id).order_by('name'))
    clusters = []

    for place in places:
        found_cluster = False
        
        # Prüfen, ob der Ort in einen der bestehenden Cluster passt
        for cluster in clusters:
            representative = cluster[0] # Wir vergleichen immer mit dem ersten Ort im Cluster
            
            # Quick-Check: Wenn der erste Buchstabe anders ist, überspringen wir (Performance)
            if place.name[0].lower() != representative.name[0].lower():
                continue
                
            similarity = SequenceMatcher(None, place.name.lower(), representative.name.lower()).ratio()
            
            if similarity >= threshold:
                cluster.append(place)
                found_cluster = True
                break
        
        # Wenn er nirgends reinpasst, macht er seinen eigenen, neuen Cluster auf
        if not found_cluster:
            clusters.append([place])
            
    # Wir werfen alle Cluster weg, die nur aus 1 Ort bestehen (da gibt es ja keine Duplikate)
    return [c for c in clusters if len(c) > 1]


def merge_multiple_places(master_place, duplicate_places):
    """
    Verschiebt alle Referenzen von MEHREREN Duplikaten auf den Master-Ort.
    """
    with transaction.atomic():
        related_objects = master_place._meta.related_objects
        
        for duplicate in duplicate_places:
            for related in related_objects:
                filter_kwargs = {related.remote_field.name: duplicate}
                update_kwargs = {related.remote_field.name: master_place}
                
                related.related_model.objects.filter(**filter_kwargs).update(**update_kwargs)
            
            # Nach dem Umbiegen aller Referenzen wird dieses eine Duplikat gelöscht
            duplicate.delete()

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

def geocode_place(query, limit=1, country_codes=None):
    """
    Sendet eine Anfrage an Nominatim und gibt die erste (oder limit) Treffer zurück.
    Rückgabe: Liste von Dicts mit at least ['lat', 'lon', 'display_name'].
    """
    params = {
        "q": query,
        "format": "json",
        "addressdetails": 1,
        "limit": limit,
    }
    if country_codes:
        params["countrycodes"] = country_codes   # z. B. "de" für Deutschland

    # Nominatim verlangt einen User-Agent-Header – sonst 403
    headers = {
        "User-Agent": "YourAppName/1.0 (+https://stevwyman.com)",
        "Accept-Language": "de",   # Ergebnis in Deutsch (optional)
    }

    url = f"{NOMINATIM_URL}?{urlencode(params)}"
    response = requests.get(url, headers=headers, timeout=10)
    
    response.raise_for_status()

    results = response.json()
    print(results)
    # Normalisiere das Ergebnis – Liste von dicts
    normalized = []
    for r in results:
        normalized.append({
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "display_name": r["display_name"],
            "type": r.get("type", ""),
            "importance": float(r.get("importance", 0)),
        })
    return normalized


def build_flat_family_tree(tree_id, root_individual, max_depth=4):
    """
    Erstellt ein flaches Array aller Personen im Baum bis zur max_depth.
    Stellt sicher, dass keine "Geister-IDs" (fehlende Personen) in den Relationen landen.
    """
    # PHASE 1: Alle Personen sammeln (bis max_depth)
    collected_individuals = {}  # Dictionary (ID -> Personen-Objekt)

    def traverse_and_collect(ind, current_depth):
        str_id = str(ind.id)
        if str_id in collected_individuals:
            return
        
        collected_individuals[str_id] = ind

        # Wenn wir noch tiefer dürfen, auch die Verwandten sammeln
        if current_depth < max_depth:
            # Eltern sammeln
            for link in ind.parental_families.all():
                fam = link.family
                if getattr(fam, 'husband', None): 
                    traverse_and_collect(fam.husband, current_depth + 1)
                if getattr(fam, 'wife', None): 
                    traverse_and_collect(fam.wife, current_depth + 1)
            
            # Partner und Kinder sammeln
            partner_families = list(ind.families_as_husband.all()) + list(ind.families_as_wife.all())
            for fam in partner_families:
                partner = fam.spouse_of(ind)
                if partner: 
                    traverse_and_collect(partner, current_depth + 1)
                for child in fam.children_links():
                    traverse_and_collect(child, current_depth + 1)

    # Rekursion starten
    traverse_and_collect(root_individual, 0)

    # PHASE 2: Das flache f3-Format bauen (mit strikter referenzieller Integrität)
    nodes = []
    
    for str_id, ind in collected_individuals.items():
        # Geschlecht bereinigen (f3 zwingt zu "M" oder "F")
        gender_val = str(getattr(ind, "sex", "")).upper()
        f3_gender = "F" if gender_val.startswith("W") or gender_val == "F" else "M"

        avatar_url = ""

        # 1. Prüfen, ob ein Profilbild existiert (genau wie in deinem Template)
        if ind.profile_image and ind.profile_image.file:
            # {% url 'genview:media-file' tree_id=tree_id pk=ind.profile_image.pk %} in Python:
            avatar_url = reverse('genview:media-file', kwargs={
                'tree_id': tree_id, 
                'pk': ind.profile_image.pk
            })
        else:
            # 2. Fallback: Wir ahmen deinen "bg-secondary text-white" div als echtes Bild nach!
            # Bootstrap 'secondary' ist der Hex-Code 6c757d.
            initial = ind.given_name[0] if ind.given_name else "?"
            avatar_url = f"https://ui-avatars.com/api/?name={initial}&background=6c757d&color=ffffff"

        

        node = {
            "id": str_id,
            "data": {
                "first name": ind.given_name or "",
                "last name": ind.surname or "",
                "birthday": getattr(ind, "birth_year", ""),
                "avatar": avatar_url,
                "detail_url": reverse('genview:individual-detail', kwargs={'tree_id': tree_id, 'pk': ind.pk}),
                "gender": f3_gender
            },
            "rels": {}
        }

        parents = []
        spouses = []
        children = []

        # Eltern prüfen (NUR hinzufügen, wenn sie in collected_individuals sind!)
        for link in ind.parental_families.all():
            fam = link.family
            if getattr(fam, 'husband', None) and str(fam.husband.id) in collected_individuals:
                parents.append(str(fam.husband.id))
            if getattr(fam, 'wife', None) and str(fam.wife.id) in collected_individuals:
                parents.append(str(fam.wife.id))

        # Partner und Kinder prüfen
        partner_families = list(ind.families_as_husband.all()) + list(ind.families_as_wife.all())
        for fam in partner_families:
            partner = fam.spouse_of(ind)
            if partner and str(partner.id) in collected_individuals:
                spouses.append(str(partner.id))
            
            for child in fam.children_links():
                if str(child.id) in collected_individuals:
                    children.append(str(child.id))

        # Relationen in den Node schreiben (list(set(...)) verhindert versehentliche Duplikate)
        if parents: node["rels"]["parents"] = list(set(parents))
        if spouses: node["rels"]["spouses"] = list(set(spouses))
        if children: node["rels"]["children"] = list(set(children))

        nodes.append(node)

    return nodes


# ------------------------------------------------------------------
# Size definitions (width, height) – keep aspect ratio.
# ------------------------------------------------------------------
THUMB_SIZES = {
    "mini":  (86, 86),   # square, center-cropped
    "small": (200, 200), # max dimension, keep aspect ratio
}


def _open_image(file_path: Path) -> Image.Image:
    """Open an image with Pillow and convert to RGB (JPEG-safe)."""
    img = Image.open(file_path)
    # JPEG cannot store alpha; flatten onto white when needed.
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _render_pdf_first_page(pdf_path: Path) -> Image.Image:
    """Render first page of PDF to a Pillow Image (RGB)."""
    doc = fitz.open(str(pdf_path))
    if doc.page_count == 0:
        raise ValueError("PDF contains no pages")
    page = doc.load_page(0)               # 0-based index
    pix = page.get_pixmap(dpi=150)        # reasonable resolution
    img_bytes = pix.tobytes("png")
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    doc.close()
    return img


def _crop_center(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    """Center-crop the image to exactly *size* (used for mini thumbs)."""
    return ImageOps.fit(img, size, method=Image.LANCZOS, centering=(0.5, 0.5))


def _resize_max(img: Image.Image, max_size: Tuple[int, int]) -> Image.Image:
    """Resize image so that neither width nor height exceeds *max_size*."""
    img.thumbnail(max_size, Image.LANCZOS)
    return img


def _save_image(img: Image.Image, dest_path: Path, quality: int = 85) -> None:
    """Save Pillow image to *dest_path* (creates parent dirs)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(dest_path), format="JPEG", quality=quality, optimize=True)


_THUMB_UPLOAD_TO = {
    "mini": tree_thumbs_mini_directory_path,
    "small": tree_thumbs_small_directory_path,
}


def generate_thumbnail_for_instance(instance, size: str = "mini") -> None:
    """
    Generates and stores the requested thumbnail for a MediaObject instance.

    Important: persists the path with QuerySet.update() so post_save signals
    are NOT re-entered (instance.save() would recurse into thumbnail generation).
    """
    if size not in THUMB_SIZES:
        raise ValueError("size must be 'mini' or 'small'")

    if not instance.pk:
        raise ValueError("MediaObject must be saved before thumbnails can be generated")

    if not instance.file or not instance.file.name:
        return

    source_path = Path(instance.file.path)

    if instance.is_image:
        pil_img = _open_image(source_path)
    elif instance.is_pdf:
        pil_img = _render_pdf_first_page(source_path)
    else:
        return

    target_width, target_height = THUMB_SIZES[size]
    if size == "mini":
        thumb_img = _crop_center(pil_img, (target_width, target_height))
    else:
        thumb_img = _resize_max(pil_img, (target_width, target_height))

    buffer = io.BytesIO()
    thumb_img.save(buffer, format="JPEG", quality=85, optimize=True)
    buffer.seek(0)

    stem = Path(instance.file.name).stem
    thumb_name = f"{stem}_thumb_{size}.jpg"
    field_name = f"thumb_{size}"
    thumb_field = getattr(instance, field_name)
    storage = thumb_field.storage

    # Build destination via the same upload_to helper the ImageField uses.
    rel_path = _THUMB_UPLOAD_TO[size](instance, thumb_name)

    # Remove previous file only from storage (do NOT use FieldFile.delete(),
    # which clears the model field and invites save/signal recursion).
    old_name = thumb_field.name or ""
    if old_name and old_name != rel_path:
        try:
            storage.delete(old_name)
        except Exception:
            pass

    # Write bytes; allow overwrite of the same hashed path on regenerate.
    if storage.exists(rel_path):
        try:
            storage.delete(rel_path)
        except Exception:
            pass
    saved_name = storage.save(rel_path, ContentFile(buffer.read()))

    # Persist without firing post_save (avoids endless regenerate loops).
    type(instance).objects.filter(pk=instance.pk).update(**{field_name: saved_name})
    setattr(instance, field_name, saved_name)
