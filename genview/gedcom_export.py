"""Build GEDCOM 5.5.1 text from Rootnode tree data."""

from __future__ import annotations

from datetime import date

from django.db.models import Prefetch, Q
from django.utils.text import slugify

from .gedcom_text import format_gedcom_date
from .models import ChildFamilyLink, Event, Family, Individual, Tree


def _pointer(obj) -> str:
    value = getattr(obj, "gedcom_id", "") or ""
    if value:
        return value
    prefix = getattr(obj, "gedcom_prefix", "X")
    return f"@{prefix}-M{obj.pk}@"


def _emit_text(level: int, tag: str, value: str | None) -> list[str]:
    if value is None:
        value = ""
    value = str(value).replace("\r\n", "\n").replace("\r", "\n")
    if not value:
        return [f"{level} {tag}"]
    lines = value.split("\n")
    out = [f"{level} {tag} {lines[0]}"]
    for extra in lines[1:]:
        out.append(f"{level + 1} CONT {extra}")
    return out


def _event_lines(level: int, event: Event) -> list[str]:
    tag = getattr(getattr(event, "event_type", None), "tag", "") or "EVEN"
    desc = (event.description or "").strip()
    lines = _emit_text(level, tag, desc)
    raw = (event.raw_date or "").strip()
    if not raw and event.parsed_date:
        raw = format_gedcom_date(event.parsed_date)
    if raw:
        lines.extend(_emit_text(level + 1, "DATE", raw))
    place_name = ""
    if event.place_id:
        place_name = event.place.name if getattr(event, "place", None) else ""
    if place_name:
        lines.extend(_emit_text(level + 1, "PLAC", place_name))
    return lines


def _name_lines(person: Individual) -> list[str]:
    given = person.given_name or ""
    surname = person.surname or ""
    lines = [f"1 NAME {given} /{surname}/"]
    if given:
        lines.extend(_emit_text(2, "GIVN", given))
    if surname:
        lines.extend(_emit_text(2, "SURN", surname))
    if person.name_prefix:
        lines.extend(_emit_text(2, "NPFX", person.name_prefix))
    if person.name_suffix:
        lines.extend(_emit_text(2, "NSFX", person.name_suffix))
    for alt in person.alternative_names.all():
        alt_given = alt.given_name or ""
        alt_surname = alt.surname or ""
        lines.append(f"1 NAME {alt_given} /{alt_surname}/")
        if alt_given:
            lines.extend(_emit_text(2, "GIVN", alt_given))
        if alt_surname:
            lines.extend(_emit_text(2, "SURN", alt_surname))
        if alt.name_type:
            lines.extend(_emit_text(2, "TYPE", alt.name_type))
    return lines


def _individual_lines(person: Individual, family_ids: set[int]) -> list[str]:
    lines = [f"0 {_pointer(person)} INDI"]
    lines.extend(_name_lines(person))
    sex = person.sex or "U"
    lines.append(f"1 SEX {sex}")
    for event in person.events.all():
        lines.extend(_event_lines(1, event))
    if person.notes:
        lines.extend(_emit_text(1, "NOTE", person.notes))
    for link in person.parental_families.all():
        if link.family_id in family_ids:
            lines.append(f"1 FAMC {_pointer(link.family)}")
    for family in list(person.families_as_husband.all()) + list(
        person.families_as_wife.all()
    ):
        if family.pk in family_ids:
            lines.append(f"1 FAMS {_pointer(family)}")
    return lines


def _family_lines(family: Family, person_ids: set[int]) -> list[str]:
    lines = [f"0 {_pointer(family)} FAM"]
    if family.husband_id and family.husband_id in person_ids:
        lines.append(f"1 HUSB {_pointer(family.husband)}")
    if family.wife_id and family.wife_id in person_ids:
        lines.append(f"1 WIFE {_pointer(family.wife)}")
    for link in family.children.all():
        if link.child_id in person_ids:
            lines.append(f"1 CHIL {_pointer(link.child)}")
    for event in family.events.all():
        lines.extend(_event_lines(1, event))
    if family.notes:
        lines.extend(_emit_text(1, "NOTE", family.notes))
    return lines


def build_gedcom_text(tree: Tree, individuals, families) -> str:
    people = list(individuals)
    fams = list(families)
    person_ids = {p.pk for p in people}
    family_ids = {f.pk for f in fams}
    today = date.today()
    header = [
        "0 HEAD",
        "1 SOUR Rootnode",
        "2 NAME Rootnode",
        "1 DEST ANY",
        f"1 DATE {format_gedcom_date(today)}",
        "1 GEDC",
        "2 VERS 5.5.1",
        "2 FORM LINEAGE-LINKED",
        "1 CHAR UTF-8",
        f"1 NOTE Export of {tree.name}",
    ]
    body: list[str] = []
    for person in sorted(people, key=lambda p: (_pointer(p), p.pk)):
        body.extend(_individual_lines(person, family_ids))
    for family in sorted(fams, key=lambda f: (_pointer(f), f.pk)):
        body.extend(_family_lines(family, person_ids))
    body.append("0 TRLR")
    return "\r\n".join(header + body) + "\r\n"


def _prefetch_people(qs):
    events = Event.objects.select_related("event_type", "place")
    return qs.select_related().prefetch_related(
        Prefetch("events", queryset=events),
        "alternative_names",
        "parental_families__family",
        "families_as_husband",
        "families_as_wife",
    )


def _prefetch_families(qs):
    events = Event.objects.select_related("event_type", "place")
    return qs.select_related("husband", "wife").prefetch_related(
        Prefetch("events", queryset=events),
        Prefetch(
            "children",
            queryset=ChildFamilyLink.objects.select_related("child"),
        ),
    )


def families_for_people(tree: Tree, person_ids: set[int]):
    if not person_ids:
        return Family.objects.none()
    return _prefetch_families(
        Family.objects.filter(gedcom_tree=tree)
        .filter(
            Q(husband_id__in=person_ids)
            | Q(wife_id__in=person_ids)
            | Q(children__child_id__in=person_ids)
        )
        .distinct()
    )


def collect_ancestor_ids(person: Individual) -> set[int]:
    ids = {person.pk}
    queue = [person.pk]
    seen_families: set[int] = set()
    while queue:
        current_id = queue.pop()
        links = ChildFamilyLink.objects.filter(child_id=current_id).select_related(
            "family"
        )
        for link in links:
            family = link.family
            if family.pk in seen_families:
                continue
            seen_families.add(family.pk)
            for parent_id in (family.husband_id, family.wife_id):
                if parent_id and parent_id not in ids:
                    ids.add(parent_id)
                    queue.append(parent_id)
    return ids


def collect_descendant_ids(person: Individual, *, include_spouses: bool = True) -> set[int]:
    ids = {person.pk}
    queue = [person.pk]
    seen_families: set[int] = set()
    while queue:
        current_id = queue.pop()
        families = Family.objects.filter(
            Q(husband_id=current_id) | Q(wife_id=current_id)
        )
        for family in families:
            if family.pk in seen_families:
                continue
            seen_families.add(family.pk)
            if include_spouses:
                for spouse_id in (family.husband_id, family.wife_id):
                    if spouse_id:
                        ids.add(spouse_id)
            for link in family.children.all():
                child_id = link.child_id
                if child_id not in ids:
                    ids.add(child_id)
                    queue.append(child_id)
    return ids


def people_for_ids(tree: Tree, person_ids: set[int]):
    return _prefetch_people(
        Individual.objects.filter(gedcom_tree=tree, pk__in=person_ids)
    )


def all_tree_people(tree: Tree):
    return _prefetch_people(Individual.objects.filter(gedcom_tree=tree))


def all_tree_families(tree: Tree):
    return _prefetch_families(Family.objects.filter(gedcom_tree=tree))


def export_filename(tree: Tree, scope: str, person: Individual | None = None) -> str:
    tree_slug = slugify(tree.name) or f"tree-{tree.pk}"
    if person is not None:
        person_slug = slugify(person.full_name()) or f"person-{person.pk}"
        return f"{tree_slug}-{person_slug}-{scope}.ged"
    return f"{tree_slug}-{scope}.ged"
