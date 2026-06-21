import numpy as np
from .models import FaceTag

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