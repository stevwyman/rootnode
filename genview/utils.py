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


def build_flat_family_tree(root_individual, max_depth=4):
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
        gender_val = str(getattr(ind, "gender", "")).upper()
        f3_gender = "F" if gender_val.startswith("W") or gender_val == "F" else "M"

        node = {
            "id": str_id,
            "data": {
                "first name": ind.given_name or "",
                "last name": ind.surname or "",
                "birthday": getattr(ind, "birth_year", ""),
                "avatar": getattr(ind, "avatar", ""),
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

