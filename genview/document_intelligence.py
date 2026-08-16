"""Parse OCR text from documents into structured event suggestions."""

from __future__ import annotations

import re
from datetime import date

from django.db.models import Q

from .models import DocumentExtractionSuggestion, EventType, Event, Individual, MediaObject, Place

# GEDCOM tags triggered by German (and common Latin) keywords in archival text.
_EVENT_KEYWORD_MAP: dict[str, list[str]] = {
    "BIRT": [
        r"geboren",
        r"geb\.",
        r"geburtsdatum",
        r"geburtsurkunde",
        r"birth",
    ],
    "DEAT": [
        r"gestorben",
        r"gest\.",
        r"verstorben",
        r"sterbedatum",
        r"todesdatum",
        r"death",
    ],
    "MARR": [
        r"heirat",
        r"verheirat",
        r"trauung",
        r"eheschließung",
        r"marriage",
    ],
    "CHR": [
        r"getauft",
        r"taufe",
        r"christening",
    ],
    "BAPM": [
        r"getauft",
        r"taufe",
    ],
}

_EVENT_TYPE_DEFAULTS: dict[str, tuple[str, str]] = {
    "BIRT": ("Geburt", EventType.Category.INDIVIDUAL),
    "DEAT": ("Tod", EventType.Category.INDIVIDUAL),
    "MARR": ("Heirat", EventType.Category.FAMILY),
    "CHR": ("Taufe", EventType.Category.INDIVIDUAL),
    "BAPM": ("Taufe", EventType.Category.INDIVIDUAL),
}

_DATE_PATTERNS = [
  # dd.mm.yyyy
    re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b"),
    # dd.mm.yy
    re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2})\b"),
    # yyyy-mm-dd
    re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"),
]

_YEAR_PATTERN = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")

_PLACE_PATTERN = re.compile(
    r"(?:in|zu|nach|from|in der|im)\s+([A-ZÄÖÜ][\wäöüß\-]+(?:[\s,][\wäöüß\-]+){0,4})",
    re.IGNORECASE,
)


def _parse_date_from_line(line: str) -> tuple[str, date | None]:
    for pat in _DATE_PATTERNS:
        m = pat.search(line)
        if not m:
            continue
        groups = m.groups()
        if len(groups) == 3 and len(groups[2]) == 4:
            d, mo, y = int(groups[0]), int(groups[1]), int(groups[2])
            try:
                return m.group(0), date(y, mo, d)
            except ValueError:
                return m.group(0), None
        if len(groups) == 3 and len(groups[2]) == 2:
            d, mo, y2 = int(groups[0]), int(groups[1]), int(groups[2])
            y = 1900 + y2 if y2 > 30 else 2000 + y2
            try:
                return m.group(0), date(y, mo, d)
            except ValueError:
                return m.group(0), None
        if len(groups) == 3 and len(groups[0]) == 4:
            y, mo, d = int(groups[0]), int(groups[1]), int(groups[2])
            try:
                return m.group(0), date(y, mo, d)
            except ValueError:
                return m.group(0), None
    ym = _YEAR_PATTERN.search(line)
    if ym:
        y = int(ym.group(1))
        return ym.group(1), date(y, 1, 1)
    return "", None


def _detect_event_tag(line: str) -> str | None:
    lower = line.lower()
    for tag, patterns in _EVENT_KEYWORD_MAP.items():
        for pat in patterns:
            if re.search(pat, lower):
                return tag
    return None


def _extract_place_name(line: str) -> str:
    m = _PLACE_PATTERN.search(line)
    if not m:
        return ""
    place = m.group(1).strip(" ,.;")
    return place[:255]


def _match_individual(tree_id: int, person_name: str, media: MediaObject) -> Individual | None:
    if not person_name.strip():
        linked = media.individuals.first()
        return linked

    tokens = [t for t in re.split(r"\s+", person_name.strip()) if t]
    if not tokens:
        return media.individuals.first()

    qs = Individual.objects.filter(gedcom_tree_id=tree_id)
    # Prefer persons already linked to this document.
    linked_ids = list(media.individuals.values_list("pk", flat=True))
    if linked_ids:
        linked_match = qs.filter(pk__in=linked_ids).filter(
            Q(surname__icontains=tokens[-1]) | Q(given_name__icontains=tokens[0])
        ).first()
        if linked_match:
            return linked_match

    if len(tokens) >= 2:
        surname = tokens[-1]
        given = " ".join(tokens[:-1])
        hit = qs.filter(surname__icontains=surname, given_name__icontains=given).first()
        if hit:
            return hit

    return qs.filter(
        Q(surname__icontains=tokens[-1]) | Q(given_name__icontains=tokens[0])
    ).first()


def _match_place(tree_id: int, place_name: str) -> Place | None:
    if not place_name:
        return None
    return Place.objects.filter(gedcom_tree_id=tree_id, name__iexact=place_name).first()


def _guess_person_name(line: str, event_tag: str | None) -> str:
    """Heuristic: strip keywords and dates to leave a name-like remainder."""
    cleaned = line
    for patterns in _EVENT_KEYWORD_MAP.values():
        for pat in patterns:
            cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE)
    cleaned = _DATE_PATTERNS[0].sub("", cleaned)
    cleaned = _YEAR_PATTERN.sub("", cleaned)
    cleaned = _PLACE_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"[^\wäöüÄÖÜß\s\-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) < 3:
        return ""
  # Drop single generic words
    if cleaned.lower() in {"am", "den", "der", "die", "das", "und"}:
        return ""
    return cleaned[:255]


def extract_document_suggestions(media: MediaObject, tree_id: int) -> list[DocumentExtractionSuggestion]:
    """
    Parse ``media.extracted_text`` and replace pending suggestions for this media.
    Returns newly created suggestion rows.
    """
    text = (media.extracted_text or "").strip()
    if not text:
        return []

    media.document_suggestions.filter(
        status=DocumentExtractionSuggestion.Status.PENDING
    ).delete()

    created: list[DocumentExtractionSuggestion] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if len(line) < 4:
            continue
        event_tag = _detect_event_tag(line)
        if not event_tag:
            continue

        raw_date, parsed_date = _parse_date_from_line(line)
        place_name = _extract_place_name(line)
        person_name = _guess_person_name(line, event_tag)
        individual = _match_individual(tree_id, person_name, media)
        place = _match_place(tree_id, place_name)

        dedupe_key = (
            event_tag,
            person_name or str(individual.pk if individual else ""),
            raw_date,
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        created.append(
            DocumentExtractionSuggestion.objects.create(
                media=media,
                event_type_tag=event_tag,
                person_name=person_name,
                individual=individual,
                raw_date=raw_date,
                parsed_date=parsed_date,
                place_name=place_name,
                place=place,
                context_line=line[:500],
            )
        )

    return created


def apply_document_suggestion(suggestion: DocumentExtractionSuggestion, tree_id: int) -> Event:
    """Create an Event from a suggestion and link it to the source media."""
    if suggestion.status != DocumentExtractionSuggestion.Status.PENDING:
        raise ValueError("Suggestion is not pending.")

    tag = suggestion.event_type_tag.upper()
    label, category = _EVENT_TYPE_DEFAULTS.get(tag, (tag, EventType.Category.INDIVIDUAL))
    event_type, _ = EventType.objects.get_or_create(
        tag=tag,
        defaults={"name": label, "category": category},
    )

    individual = suggestion.individual
    if not individual and suggestion.person_name:
        individual = _match_individual(tree_id, suggestion.person_name, suggestion.media)

    family = None
    if category == EventType.Category.FAMILY:
        family = suggestion.media.families.first()
        if not family:
            raise ValueError("Heirat-Vorschlag: Dokument ist mit keiner Familie verknüpft.")
        individual = None

    place = suggestion.place
    if not place and suggestion.place_name:
        place = Place.objects.filter(
            gedcom_tree_id=tree_id, name__iexact=suggestion.place_name
        ).first()
        if not place:
            place = Place.objects.create(
                gedcom_tree_id=tree_id,
                name=suggestion.place_name,
            )

    event = Event.objects.create(
        gedcom_tree_id=tree_id,
        event_type=event_type,
        individual=individual,
        family=family,
        raw_date=suggestion.raw_date,
        parsed_date=suggestion.parsed_date,
        place=place,
        description=f"Extrahiert aus Dokument: {suggestion.media.title or suggestion.media.pk}",
    )

    suggestion.media.events.add(event)
    if individual:
        suggestion.media.individuals.add(individual)

    suggestion.status = DocumentExtractionSuggestion.Status.ACCEPTED
    suggestion.created_event = event
    suggestion.individual = individual
    suggestion.place = place
    suggestion.save(
        update_fields=[
            "status",
            "created_event",
            "individual",
            "place",
            "updated_at",
        ]
    )
    return event
