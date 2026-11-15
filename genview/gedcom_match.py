"""Score incoming GEDCOM people against individuals already in a tree."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import jellyfish

from .phonetics import names_match_phonetic, normalize_name, tokens

AUTO_MATCH_THRESHOLD = 0.88
SHOW_CANDIDATE_THRESHOLD = 0.48
MAX_CANDIDATES = 8


@dataclass
class MatchPerson:
    pk: int
    given_name: str
    surname: str
    sex: str
    birth_year: int | None
    death_year: int | None
    label: str
    alt_names: list[tuple[str, str]] = field(default_factory=list)


def _string_similarity(left: str, right: str) -> float:
    a = normalize_name(left)
    b = normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    jw = jellyfish.jaro_winkler_similarity(a, b)
    phonetic = 0.0
    if names_match_phonetic(left, right) or names_match_phonetic(right, left):
        phonetic = 0.92
    else:
        a_tokens = tokens(left)
        b_tokens = tokens(right)
        if a_tokens and b_tokens and set(a_tokens) & set(b_tokens):
            phonetic = 0.9
    return max(jw, phonetic)


def _name_score(incoming: dict[str, Any], existing: MatchPerson) -> float:
    incoming_names = [(incoming.get("given_name") or "", incoming.get("surname") or "")]
    for extra in incoming.get("names") or []:
        incoming_names.append(
            (extra.get("given_name") or "", extra.get("surname") or "")
        )
    existing_names = [(existing.given_name or "", existing.surname or "")]
    existing_names.extend(existing.alt_names)
    best = 0.0
    for given, surname in incoming_names:
        for other_given, other_surname in existing_names:
            score = 0.55 * _string_similarity(surname, other_surname) + 0.45 * _string_similarity(
                given, other_given
            )
            if score > best:
                best = score
    return best


def _year_proximity(left: int | None, right: int | None) -> float | None:
    if left is None or right is None:
        return None
    delta = abs(int(left) - int(right))
    if delta == 0:
        return 1.0
    if delta == 1:
        return 0.85
    if delta <= 2:
        return 0.7
    if delta <= 5:
        return 0.4
    if delta <= 10:
        return 0.15
    return 0.0


def score_pair(incoming: dict[str, Any], existing: MatchPerson) -> float:
    name = _name_score(incoming, existing)
    incoming_sex = (incoming.get("sex") or "U").upper()
    existing_sex = (existing.sex or "U").upper()
    if incoming_sex in ("M", "F") and existing_sex in ("M", "F"):
        sex_score = 1.0 if incoming_sex == existing_sex else 0.0
    else:
        sex_score = None

    birth = _year_proximity(incoming.get("birth_year"), existing.birth_year)
    death = _year_proximity(incoming.get("death_year"), existing.death_year)

    weights = {"name": 0.62, "birth": 0.22, "death": 0.08, "sex": 0.08}
    parts = {"name": name}
    if birth is not None:
        parts["birth"] = birth
    if death is not None:
        parts["death"] = death
    if sex_score is not None:
        parts["sex"] = sex_score

    total_weight = sum(weights[key] for key in parts)
    if total_weight <= 0:
        return 0.0
    score = sum(parts[key] * weights[key] for key in parts) / total_weight
    if sex_score == 0.0:
        score *= 0.55
    if birth == 0.0 and name < 0.97:
        score *= 0.7
    return round(min(max(score, 0.0), 1.0), 4)


def _bucket_key(surname: str, given_name: str) -> str:
    surn = normalize_name(surname)
    if surn:
        return f"s:{surn[:3]}"
    given = normalize_name(given_name)
    return f"g:{given[:3]}"


def _candidate_pool(
    incoming: dict[str, Any],
    existing: list[MatchPerson],
    buckets: dict[str, list[MatchPerson]],
) -> list[MatchPerson]:
    key = _bucket_key(incoming.get("surname") or "", incoming.get("given_name") or "")
    pool = list(buckets.get(key, []))
    given = normalize_name(incoming.get("given_name") or "")
    surn = normalize_name(incoming.get("surname") or "")
    if len(pool) < 12:
        for person in existing:
            other_surn = normalize_name(person.surname)
            if surn and other_surn and (
                surn == other_surn
                or names_match_phonetic(incoming.get("surname") or "", person.surname)
            ):
                pool.append(person)
            elif given and normalize_name(person.given_name) == given:
                pool.append(person)
    # Preserve order, drop duplicates
    seen: set[int] = set()
    unique: list[MatchPerson] = []
    for person in pool:
        if person.pk not in seen:
            seen.add(person.pk)
            unique.append(person)
    if unique:
        return unique
    return existing


def suggest_for_incoming(
    incoming: dict[str, Any],
    existing: list[MatchPerson],
    buckets: dict[str, list[MatchPerson]] | None = None,
) -> list[dict[str, Any]]:
    if not existing:
        return []
    if buckets is None:
        buckets = build_buckets(existing)
    pool = _candidate_pool(incoming, existing, buckets)
    ranked: list[dict[str, Any]] = []
    for person in pool:
        name = _name_score(incoming, person)
        birth = _year_proximity(incoming.get("birth_year"), person.birth_year)
        score = score_pair(incoming, person)
        plausible = name >= 0.72 or (
            name >= 0.55 and birth is not None and birth >= 0.7
        )
        if score >= SHOW_CANDIDATE_THRESHOLD and plausible:
            ranked.append(
                {
                    "pk": person.pk,
                    "label": person.label,
                    "score": score,
                    "percent": int(round(score * 100)),
                }
            )
    ranked.sort(key=lambda row: (-row["score"], row["label"]))
    return ranked[:MAX_CANDIDATES]


def build_buckets(existing: list[MatchPerson]) -> dict[str, list[MatchPerson]]:
    buckets: dict[str, list[MatchPerson]] = defaultdict(list)
    for person in existing:
        buckets[_bucket_key(person.surname, person.given_name)].append(person)
        for given, surname in person.alt_names:
            buckets[_bucket_key(surname, given)].append(person)
    return buckets


def suggest_matches(
    payload: dict[str, Any], existing: list[MatchPerson]
) -> list[dict[str, Any]]:
    buckets = build_buckets(existing)
    rows = []
    for incoming in payload.get("individuals") or []:
        candidates = suggest_for_incoming(incoming, existing, buckets)
        suggested = candidates[0] if candidates else None
        suggested_pk = None
        suggested_score = 0.0
        if suggested and suggested["score"] >= AUTO_MATCH_THRESHOLD:
            suggested_pk = suggested["pk"]
            suggested_score = suggested["score"]
        rows.append(
            {
                "xref": incoming.get("xref") or "",
                "key": (incoming.get("xref") or "").replace("@", "").replace("/", "_"),
                "given_name": incoming.get("given_name") or "",
                "surname": incoming.get("surname") or "",
                "sex": incoming.get("sex") or "U",
                "birth_date": incoming.get("birth_date") or "",
                "birth_place": incoming.get("birth_place") or "",
                "death_date": incoming.get("death_date") or "",
                "candidates": candidates,
                "suggested_pk": suggested_pk,
                "suggested_score": suggested_score,
            }
        )
    rows.sort(key=lambda row: (row["surname"].lower(), row["given_name"].lower()))
    return rows
