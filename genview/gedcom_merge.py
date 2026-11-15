"""Store a pending GEDCOM merge and apply match decisions to a tree."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import date
from typing import Any

from django.db import transaction
from django.db.models import Prefetch

from .gedcom_text import parse_gedcom_date, xref_form_key
from .models import (
    AlternativeName,
    ChildFamilyLink,
    Event,
    EventType,
    Family,
    Individual,
    Place,
    Tree,
)

SESSION_PREFIX = "gedcom_merge_"
ALT_TYPE_MAP = {
    "married": AlternativeName.NameType.MARRIED,
    "maiden": AlternativeName.NameType.MAIDEN,
    "aka": AlternativeName.NameType.AKA,
    "immigrant": AlternativeName.NameType.IMMIGRANT,
}


def session_key(tree_id: int) -> str:
    return f"{SESSION_PREFIX}{tree_id}"


def store_merge_payload(request, tree: Tree, payload: dict[str, Any], filename: str) -> None:
    clear_merge_payload(request, tree.pk)
    token = uuid.uuid4().hex
    path = os.path.join(tempfile.gettempdir(), f"rootnode_gedcom_merge_{token}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    request.session[session_key(tree.pk)] = {
        "path": path,
        "filename": filename,
        "token": token,
        "tree_id": tree.pk,
    }
    request.session.modified = True


def load_merge_payload(request, tree: Tree) -> tuple[dict[str, Any], dict[str, Any]] | None:
    meta = request.session.get(session_key(tree.pk))
    if not meta:
        return None
    path = meta.get("path")
    if not path or not os.path.isfile(path):
        clear_merge_payload(request, tree.pk)
        return None
    if meta.get("tree_id") != tree.pk:
        clear_merge_payload(request, tree.pk)
        return None
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload, meta


def clear_merge_payload(request, tree_id: int) -> None:
    key = session_key(tree_id)
    meta = request.session.pop(key, None)
    request.session.modified = True
    path = (meta or {}).get("path")
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _event_year(event: Event) -> int | None:
    if event.parsed_date:
        return event.parsed_date.year
    if event.raw_date:
        parsed = parse_gedcom_date(event.raw_date)
        if parsed:
            return parsed.year
    return None


def _display_years(birth_year: int | None, death_year: int | None) -> str:
    if birth_year and death_year:
        return f"{birth_year}–{death_year}"
    if birth_year:
        return f"*{birth_year}"
    if death_year:
        return f"†{death_year}"
    return ""


def load_existing_match_people(tree: Tree):
    from .gedcom_match import MatchPerson

    people = (
        Individual.objects.filter(gedcom_tree=tree)
        .prefetch_related(
            Prefetch(
                "events",
                queryset=Event.objects.filter(event_type__tag__in=["BIRT", "DEAT"]).select_related(
                    "event_type"
                ),
            ),
            "alternative_names",
        )
        .order_by("surname", "given_name")
    )
    rows = []
    for person in people:
        birth_year = None
        death_year = None
        for event in person.events.all():
            tag = getattr(getattr(event, "event_type", None), "tag", "")
            year = _event_year(event)
            if tag == "BIRT":
                birth_year = year
            elif tag == "DEAT":
                death_year = year
        years = _display_years(birth_year, death_year)
        label = person.full_name()
        if years:
            label = f"{label} ({years})"
        if person.gedcom_id:
            label = f"{label} [{person.gedcom_id}]"
        alts = [
            (alt.given_name or "", alt.surname or "")
            for alt in person.alternative_names.all()
        ]
        rows.append(
            MatchPerson(
                pk=person.pk,
                given_name=person.given_name or "",
                surname=person.surname or "",
                sex=person.sex or "U",
                birth_year=birth_year,
                death_year=death_year,
                label=label,
                alt_names=alts,
            )
        )
    return rows


def _get_place(tree: Tree, name: str) -> Place | None:
    name = (name or "").strip()
    if not name:
        return None
    place, _created = Place.objects.get_or_create(
        gedcom_tree=tree, name=name[:255]
    )
    return place


def _event_type(tag: str) -> EventType:
    tag = (tag or "EVEN")[:4].upper()
    event_type, _created = EventType.objects.get_or_create(
        tag=tag, defaults={"name": tag}
    )
    return event_type


def _event_exists(qs, payload_event: dict[str, Any]) -> bool:
    tag = payload_event.get("tag")
    raw = (payload_event.get("raw_date") or "").strip()
    parsed_iso = payload_event.get("parsed_date")
    parsed = date.fromisoformat(parsed_iso) if parsed_iso else None
    for existing in qs:
        existing_tag = getattr(getattr(existing, "event_type", None), "tag", "")
        if existing_tag != tag:
            continue
        if parsed and existing.parsed_date == parsed:
            return True
        if raw and (existing.raw_date or "").strip() == raw:
            return True
        if not raw and not parsed and not existing.raw_date and not existing.parsed_date:
            return True
    return False


def _create_events(tree: Tree, payload_events: list[dict[str, Any]], *, individual=None, family=None):
    created = 0
    if individual is not None:
        existing = list(individual.events.select_related("event_type").all())
    elif family is not None:
        existing = list(family.events.select_related("event_type").all())
    else:
        existing = []
    for payload_event in payload_events or []:
        tag = (payload_event.get("tag") or "").strip()
        if not tag:
            continue
        if _event_exists(existing, payload_event):
            continue
        parsed_iso = payload_event.get("parsed_date")
        parsed = date.fromisoformat(parsed_iso) if parsed_iso else None
        event = Event.objects.create(
            gedcom_tree=tree,
            individual=individual,
            family=family,
            event_type=_event_type(tag),
            raw_date=(payload_event.get("raw_date") or "")[:100],
            parsed_date=parsed,
            place=_get_place(tree, payload_event.get("place") or ""),
            description=payload_event.get("description") or "",
        )
        existing.append(event)
        created += 1
    return created


def _create_individual(tree: Tree, incoming: dict[str, Any]) -> Individual:
    sex = (incoming.get("sex") or "U").upper()
    if sex not in ("M", "F", "U"):
        sex = "U"
    person = Individual.objects.create(
        gedcom_tree=tree,
        given_name=(incoming.get("given_name") or "")[:150],
        surname=(incoming.get("surname") or "")[:150],
        sex=sex,
        notes=incoming.get("notes") or "",
    )
    for alt in incoming.get("names") or []:
        name_type = ALT_TYPE_MAP.get((alt.get("type") or "").lower(), AlternativeName.NameType.UNKNOWN)
        AlternativeName.objects.create(
            individual=person,
            given_name=alt.get("given_name") or "",
            surname=alt.get("surname") or "",
            name_type=name_type,
        )
    _create_events(tree, incoming.get("events") or [], individual=person)
    return person


def _find_family(tree: Tree, husband: Individual | None, wife: Individual | None) -> Family | None:
    qs = Family.objects.filter(gedcom_tree=tree)
    if husband and wife:
        found = qs.filter(husband=husband, wife=wife).first()
        if found:
            return found
        return qs.filter(husband=wife, wife=husband).first()
    if husband and not wife:
        return qs.filter(husband=husband, wife__isnull=True).first()
    if wife and not husband:
        return qs.filter(wife=wife, husband__isnull=True).first()
    return None


def decisions_from_post(payload: dict[str, Any], post, tree: Tree) -> dict[str, int | None]:
    """Map incoming xref -> existing pk or None (create new)."""
    valid_ids = set(
        Individual.objects.filter(gedcom_tree=tree).values_list("pk", flat=True)
    )
    mapping: dict[str, int | None] = {}
    for incoming in payload.get("individuals") or []:
        xref = incoming.get("xref") or ""
        key = xref_form_key(xref)
        manual = (post.get(f"manual_{key}") or "").strip()
        choice = (post.get(f"map_{key}") or "").strip()
        raw = manual or choice
        if not raw:
            mapping[xref] = None
            continue
        try:
            pk = int(raw)
        except (TypeError, ValueError):
            mapping[xref] = None
            continue
        if pk not in valid_ids:
            raise ValueError(xref)
        mapping[xref] = pk
    return mapping


@transaction.atomic
def apply_merge(tree: Tree, payload: dict[str, Any], mapping: dict[str, int | None]) -> dict[str, int]:
    person_map: dict[str, Individual] = {}
    created_people = 0
    matched_people = 0
    created_events = 0
    created_families = 0
    linked_children = 0

    existing_by_id = {
        person.pk: person
        for person in Individual.objects.filter(
            gedcom_tree=tree, pk__in=[pk for pk in mapping.values() if pk]
        )
    }

    for incoming in payload.get("individuals") or []:
        xref = incoming.get("xref") or ""
        match_pk = mapping.get(xref)
        if match_pk:
            person = existing_by_id[match_pk]
            created_events += _create_events(
                tree, incoming.get("events") or [], individual=person
            )
            matched_people += 1
        else:
            person = _create_individual(tree, incoming)
            created_people += 1
        person_map[xref] = person

    for family_payload in payload.get("families") or []:
        husband = person_map.get(family_payload.get("husb") or "")
        wife = person_map.get(family_payload.get("wife") or "")
        children = [
            person_map[xref]
            for xref in family_payload.get("children") or []
            if xref in person_map
        ]
        if not husband and not wife and not children:
            continue
        family = _find_family(tree, husband, wife)
        if family is None:
            family = Family.objects.create(
                gedcom_tree=tree,
                husband=husband,
                wife=wife,
                notes=family_payload.get("notes") or "",
            )
            created_families += 1
        else:
            changed = False
            if husband and family.husband_id is None:
                family.husband = husband
                changed = True
            if wife and family.wife_id is None:
                family.wife = wife
                changed = True
            if changed:
                family.save()
        created_events += _create_events(
            tree, family_payload.get("events") or [], family=family
        )
        for child in children:
            _link, was_created = ChildFamilyLink.objects.get_or_create(
                child=child,
                family=family,
                defaults={
                    "relationship_type": ChildFamilyLink.Relationship.BIOLOGICAL,
                },
            )
            if was_created:
                linked_children += 1

    return {
        "created_people": created_people,
        "matched_people": matched_people,
        "created_families": created_families,
        "created_events": created_events,
        "linked_children": linked_children,
    }
