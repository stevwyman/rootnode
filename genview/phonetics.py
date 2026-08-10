"""Phonetic helpers for genealogy name search."""

from __future__ import annotations

import re

import jellyfish

_UMLAUT_MAP = str.maketrans(
    {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
        "ß": "ss",
    }
)

# Common particles in genealogy names — never use these for phonetic hits.
_STOP_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "aka",
        "de",
        "del",
        "della",
        "der",
        "di",
        "do",
        "dos",
        "du",
        "la",
        "le",
        "mac",
        "mc",
        "of",
        "or",
        "st",
        "the",
        "und",
        "van",
        "von",
        "y",
    }
)

_MIN_TOKEN_LEN = 3


def normalize_name(text: str) -> str:
    """Lowercase, expand umlauts, keep letters/spaces only."""
    if not text:
        return ""
    text = text.translate(_UMLAUT_MAP).lower()
    text = re.sub(r"[^a-z\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> list[str]:
    """Meaningful name tokens (drop particles and very short fragments)."""
    return [
        part
        for part in normalize_name(text).split()
        if len(part) >= _MIN_TOKEN_LEN and part not in _STOP_TOKENS
    ]


def names_match_phonetic(name: str, term: str) -> bool:
    """True if *term* is phonetically close to any token in *name*."""
    term_n = normalize_name(term)
    if len(term_n) < _MIN_TOKEN_LEN:
        return False
    return any(_codes_close(candidate, term_n) for candidate in tokens(name))


def _codes_close(a: str, b: str) -> bool:
    """
    Conservative phonetic equality for name tokens.

    Prefer Metaphone; allow Soundex only when lengths are similar so short
    / unrelated names do not collide. Match Rating is not used — it is far
    too loose against long multi-word genealogy strings.
    """
    if not a or not b:
        return False
    if a == b:
        return True

    if jellyfish.metaphone(a) == jellyfish.metaphone(b):
        return True

    # Soundex helps German variants (Schmidt/Schmitt, Meier/Meyer) but collides
    # easily when lengths differ a lot (Edna vs Elisabeth).
    if abs(len(a) - len(b)) <= 2 and jellyfish.soundex(a) == jellyfish.soundex(b):
        return True

    return False


def individual_name_tokens(individual) -> list[str]:
    """All searchable phonetic tokens from primary + alternative names."""
    parts: list[str] = []
    for value in (individual.given_name, individual.surname):
        if value:
            parts.extend(tokens(value))
    for alt in individual.alternative_names.all():
        if alt.given_name:
            parts.extend(tokens(alt.given_name))
        if alt.surname:
            parts.extend(tokens(alt.surname))
    # Preserve order but drop duplicates
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        if part not in seen:
            seen.add(part)
            unique.append(part)
    return unique


def individual_matches_phonetic_term(individual, term: str) -> bool:
    """Match *term* against any name token of *individual*."""
    term_n = normalize_name(term)
    if len(term_n) < _MIN_TOKEN_LEN:
        return False
    return any(_codes_close(tok, term_n) for tok in individual_name_tokens(individual))


def individual_matches_all_phonetic(individual, terms: list[str]) -> bool:
    """
    Each search term must phonetically match a *distinct* name token.

    Prevents one weak/long field from satisfying every term in an AND query.
    """
    remaining = individual_name_tokens(individual)
    for term in terms:
        term_n = normalize_name(term)
        if len(term_n) < _MIN_TOKEN_LEN:
            return False
        found_idx = None
        for idx, tok in enumerate(remaining):
            if _codes_close(tok, term_n):
                found_idx = idx
                break
        if found_idx is None:
            return False
        remaining.pop(found_idx)
    return True


def individual_matches_any_phonetic(individual, terms: list[str]) -> bool:
    return any(individual_matches_phonetic_term(individual, term) for term in terms)
