import numpy as np
from difflib import SequenceMatcher
from django.db import transaction
from .models import FaceTag, Place

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


def find_similar_places(tree_id, threshold=0.80):
    """
    Findet ähnliche Orte innerhalb eines Stammbaums.
    Gibt eine Liste von Dictionaries zurück: 
    [{'place1': p1, 'place2': p2, 'similarity': 0.85}]
    """
    # Alle Orte des Baums holen. Wir sortieren nach Alphabet, 
    # damit wir beim Paare-Vergleichen keine Duplikate (A-B und B-A) haben.
    places = list(Place.objects.filter(gedcom_tree_id=tree_id).order_index('name'))
    
    similar_pairs = []
    
    # Verschachtelte Schleife zum Paar-Vergleich (O(N²) - bei gigantischen 
    # Ortslisten evtl. später optimieren, für normale Bäume völlig okay)
    for i in range(len(places)):
        for j in range(i + 1, len(places)):
            p1 = places[i]
            p2 = places[j]
            
            # Schnell-Check: Wenn die Anfangsbuchstaben völlig verschieden sind, 
            # überspringen (spart Rechenzeit)
            if p1.name[0].lower() != p2.name[0].lower():
                continue
                
            # Ähnlichkeit berechnen (Wert zwischen 0.0 und 1.0)
            similarity = SequenceMatcher(None, p1.name.lower(), p2.name.lower()).ratio()
            
            if similarity >= threshold:
                similar_pairs.append({
                    'place1': p1,
                    'place2': p2,
                    'similarity': round(similarity * 100, 1) # In Prozent, z.B. 85.4
                })
                
    # Sortieren nach höchster Ähnlichkeit zuerst
    similar_pairs.sort(key=lambda x: x['similarity'], reverse=True)
    return similar_pairs
