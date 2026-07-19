import numpy as np
import requests
from urllib.parse import urlencode
from difflib import SequenceMatcher
from django.db import transaction
from .models import FaceTag, Place, Individual

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
        params["countrycodes"] = country_codes   # z. B. "de" für Deutschland

    # Nominatim verlangt einen User-Agent-Header – sonst 403
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


def build_individual_tree(
    individual: Individual,
    depth: int = 0,
    max_depth: int = 3,
) -> dict[str, any]:
    """
    Build a hierarchical node for *individual* that matches the
    flat‑array structure you posted:

    {
        "id":   <pk>,
        "data": {                # contains first name, last name, birthday, avatar, gender
            "first name": "...",
            "last name":  "...",
            "birthday":   "...",
            "avatar":     "...",
            "gender":     "M|F"
        },
        "rels": {
            "spouses":  [ <node>, … ],
            "children": [ <node>, … ],
            "parents":  [ <node>, … ]
        }
    }
    """
    # -----------------------------------------------------------------
    # 1️⃣  Base payload (always present)
    # -----------------------------------------------------------------
    node: Dict[str, Any] = {
        "id": individual.id,
        "data": {
            "first name": individual.given_name,
            "last name":  individual.surname,
            "birthday":   getattr(individual, "birth_year", ""),
            "avatar":     getattr(individual, "avatar", ""),
            "gender":     getattr(individual, "gender", "")
        },
        "rels": {
            "spouses":  [],
            "children": [],
            "parents":  []
        }
    }

    # -----------------------------------------------------------------
    # 2️⃣  Stop recursion when depth limit is reached
    # -----------------------------------------------------------------
    if depth >= max_depth:
        return node

    # -----------------------------------------------------------------
    # 3️⃣  Spouses  – families where the person is husband or wife
    # -----------------------------------------------------------------
    partner_families = (
        list(individual.families_as_husband.all()) +
        list(individual.families_as_wife.all())
    )
    for fam in partner_families:
        partner = fam.spouse_of(individual)          # defined in Family model
        if partner:
            node["rels"]["spouses"].append(
                build_individual_tree(partner, depth + 1, max_depth)
            )

    # -----------------------------------------------------------------
    # 4️⃣  Children  – families in which the person is a parent
    # -----------------------------------------------------------------
    for link in individual.parental_families.all():   # ChildFamilyLink objects
        family: Family = link.family
        for child in family.children_links():         # all Individual children
            if child.id == individual.id:
                continue                               # avoid self‑reference
            node["rels"]["children"].append(
                build_individual_tree(child, depth + 1, max_depth)
            )

    # -----------------------------------------------------------------
    # 5️⃣  Parents  – families where the person is listed as a child
    # -----------------------------------------------------------------
    for link in individual.parental_families.all():      # Family objects (child side)
        fam: Family = link.family
        # mother (wife) if present
        if fam.wife:
            node["rels"]["parents"].append(
                build_individual_tree(fam.wife, depth + 1, max_depth)
            )
        # father (husband) if present
        if fam.husband:
            node["rels"]["parents"].append(
                build_individual_tree(fam.husband, depth + 1, max_depth)
            )

    return node
