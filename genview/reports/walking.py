"""Tree walking and vital-fact helpers for reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from django.db.models import Count, Exists, OuterRef, Prefetch, Q

from genview.mixins import apply_privacy_to_individual_qs
from genview.models import ChildFamilyLink, Event, Family, Individual

BIRT = "BIRT"
DEAT = "DEAT"
MARR = "MARR"
LIKELY_LIVING_YEARS = 110


def _event_qs():
    return Event.objects.select_related("event_type", "place").prefetch_related("sources")


def prefetch_people(qs):
    """Attach parents, spouses, children, and vital events in a few queries."""
    family_qs = Family.objects.select_related("husband", "wife").prefetch_related(
        Prefetch("events", queryset=_event_qs()),
        Prefetch(
            "children",
            queryset=ChildFamilyLink.objects.select_related("child"),
        ),
    )
    return qs.prefetch_related(
        Prefetch(
            "parental_families",
            queryset=ChildFamilyLink.objects.select_related(
                "family__husband",
                "family__wife",
            ),
        ),
        Prefetch("events", queryset=_event_qs()),
        Prefetch("families_as_husband", queryset=family_qs),
        Prefetch("families_as_wife", queryset=family_qs),
        "sources",
    )


def load_people(tree_id: int, pks: Iterable[int], *, apply_privacy: bool):
    ids = list({pk for pk in pks if pk})
    if not ids:
        return {}
    qs = prefetch_people(
        apply_privacy_to_individual_qs(
            Individual.objects.filter(gedcom_tree_id=tree_id, pk__in=ids),
            apply_privacy,
        )
    )
    return {person.pk: person for person in qs}


def parents_of(person: Individual) -> tuple[Individual | None, Individual | None]:
    links = list(person.parental_families.all())
    if not links:
        return None, None
    family = links[0].family
    if not family:
        return None, None
    return family.husband, family.wife


def children_of(person: Individual) -> list[Individual]:
    found: list[Individual] = []
    seen: set[int] = set()
    for family in list(person.families_as_husband.all()) + list(person.families_as_wife.all()):
        for link in family.children.all():
            child = link.child
            if child and child.pk not in seen:
                seen.add(child.pk)
                found.append(child)
    return found


def spouses_of(person: Individual) -> list[Individual]:
    found: list[Individual] = []
    seen: set[int] = set()
    for family in list(person.families_as_husband.all()) + list(person.families_as_wife.all()):
        partner = family.spouse_of(person)
        if partner and partner.pk not in seen:
            seen.add(partner.pk)
            found.append(partner)
    return found


def families_of(person: Individual) -> list[Family]:
    return list(person.families_as_husband.all()) + list(person.families_as_wife.all())


@dataclass
class LineageNode:
    person: Individual
    generation: int
    ahnentafel: int | None = None
    parent: Individual | None = None


def collect_ancestor_ids(root_id: int, max_generations: int) -> list[tuple[int, int, int]]:
    """Return (person_id, generation, ahnentafel). Generation 0 is the root."""
    rows: list[tuple[int, int, int]] = [(root_id, 0, 1)]
    frontier: dict[int, int] = {1: root_id}
    for gen in range(max_generations):
        child_ids = list(frontier.values())
        links = ChildFamilyLink.objects.filter(child_id__in=child_ids).select_related("family")
        by_child = {link.child_id: link.family for link in links}
        next_frontier: dict[int, int] = {}
        for number, person_id in frontier.items():
            family = by_child.get(person_id)
            if not family:
                continue
            if family.husband_id:
                ahn = number * 2
                next_frontier[ahn] = family.husband_id
                rows.append((family.husband_id, gen + 1, ahn))
            if family.wife_id:
                ahn = number * 2 + 1
                next_frontier[ahn] = family.wife_id
                rows.append((family.wife_id, gen + 1, ahn))
        frontier = next_frontier
        if not frontier:
            break
    return rows


def collect_descendant_ids(root_id: int, max_generations: int) -> list[tuple[int, int, int | None]]:
    """Return (person_id, generation, parent_id). Each person appears once."""
    rows: list[tuple[int, int, int | None]] = [(root_id, 0, None)]
    seen = {root_id}
    frontier = [root_id]
    for gen in range(1, max_generations + 1):
        family_ids = Family.objects.filter(
            Q(husband_id__in=frontier) | Q(wife_id__in=frontier)
        ).values_list("pk", flat=True)
        links = ChildFamilyLink.objects.filter(family_id__in=family_ids).select_related("family")
        next_frontier: list[int] = []
        for link in links:
            child_id = link.child_id
            if not child_id or child_id in seen:
                continue
            parent_id = link.family.husband_id or link.family.wife_id
            if parent_id not in frontier:
                parent_id = link.family.wife_id or link.family.husband_id
            seen.add(child_id)
            rows.append((child_id, gen, parent_id))
            next_frontier.append(child_id)
        frontier = next_frontier
        if not frontier:
            break
    return rows


def collect_ancestors(
    tree_id: int, root_id: int, max_generations: int, *, apply_privacy: bool
) -> list[LineageNode]:
    slots = collect_ancestor_ids(root_id, max_generations)
    people = load_people(tree_id, [pk for pk, _, _ in slots], apply_privacy=False)
    nodes: list[LineageNode] = []
    for person_id, generation, ahnentafel in slots:
        person = people.get(person_id)
        if person is None:
            continue
        if apply_privacy and person.is_confidential:
            continue
        nodes.append(LineageNode(person=person, generation=generation, ahnentafel=ahnentafel))
    return nodes


def collect_descendants(
    tree_id: int, root_id: int, max_generations: int, *, apply_privacy: bool
) -> list[LineageNode]:
    slots = collect_descendant_ids(root_id, max_generations)
    people = load_people(tree_id, [pk for pk, _, _ in slots], apply_privacy=False)
    nodes: list[LineageNode] = []
    for person_id, generation, parent_id in slots:
        person = people.get(person_id)
        if person is None:
            continue
        if apply_privacy and person.is_confidential:
            continue
        parent = people.get(parent_id) if parent_id else None
        if parent and apply_privacy and parent.is_confidential:
            parent = None
        nodes.append(LineageNode(person=person, generation=generation, parent=parent))
    return nodes


def event_for(person: Individual, tag: str) -> Event | None:
    for event in person.events.all():
        if getattr(event.event_type, "tag", None) == tag:
            return event
    return None


def marriage_event(family: Family) -> Event | None:
    for event in family.events.all():
        if getattr(event.event_type, "tag", None) == MARR:
            return event
    return None


def event_date_text(event: Event | None) -> str:
    if event is None:
        return ""
    if event.parsed_date:
        return event.parsed_date.strftime("%d.%m.%Y")
    return (event.raw_date or "").strip()


def event_has_date(event: Event | None) -> bool:
    if event is None:
        return False
    return bool(event.parsed_date or (event.raw_date or "").strip())


def event_has_place(event: Event | None) -> bool:
    return bool(event and event.place_id)


def event_has_source(event: Event | None) -> bool:
    if event is None:
        return False
    sources = getattr(event, "_prefetched_objects_cache", {}).get("sources")
    if sources is not None:
        return bool(sources)
    return event.sources.exists()


def recent_birth_cutoff() -> date:
    today = date.today()
    try:
        return today.replace(year=today.year - LIKELY_LIVING_YEARS)
    except ValueError:
        return today - timedelta(days=LIKELY_LIVING_YEARS * 365)


def is_likely_living(person: Individual) -> bool:
    """True when a parsed birth is recent and there is no death event."""
    if event_for(person, DEAT) is not None:
        return False
    birth = event_for(person, BIRT)
    if birth and birth.parsed_date:
        return birth.parsed_date > recent_birth_cutoff()
    return False


def annotate_vital_gaps(qs):
    """Boolean annotations for birth / death / marriage completeness."""
    birth = Event.objects.filter(individual_id=OuterRef("pk"), event_type__tag=BIRT)
    death = Event.objects.filter(individual_id=OuterRef("pk"), event_type__tag=DEAT)
    marriage = Event.objects.filter(
        event_type__tag=MARR,
    ).filter(Q(family__husband_id=OuterRef("pk")) | Q(family__wife_id=OuterRef("pk")))
    has_family = Family.objects.filter(
        Q(husband_id=OuterRef("pk")) | Q(wife_id=OuterRef("pk"))
    )
    dated = Q(parsed_date__isnull=False) | ~Q(raw_date="")
    return qs.annotate(
        has_birth=Exists(birth),
        has_birth_date=Exists(birth.filter(dated)),
        has_birth_place=Exists(birth.filter(place_id__isnull=False)),
        has_birth_source=Exists(birth.filter(sources__isnull=False)),
        has_death=Exists(death),
        has_death_date=Exists(death.filter(dated)),
        has_death_place=Exists(death.filter(place_id__isnull=False)),
        has_death_source=Exists(death.filter(sources__isnull=False)),
        has_family=Exists(has_family),
        has_marriage=Exists(marriage),
        has_marriage_date=Exists(marriage.filter(dated)),
        has_marriage_place=Exists(marriage.filter(place_id__isnull=False)),
        has_marriage_source=Exists(marriage.filter(sources__isnull=False)),
        born_recently=Exists(birth.filter(parsed_date__gt=recent_birth_cutoff())),
        source_count=Count("sources", distinct=True),
        event_source_count=Count("events__sources", distinct=True),
    )
