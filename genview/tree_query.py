"""
Deterministic family-tree query executor (Phase 1).

Natural language is *not* parsed here. Callers pass a structured plan;
this module walks kinship on Individual/Family and returns facts plus a
template-rendered answer. Privacy is applied the same way as the rest of
the app (Individual.is_confidential).
"""

from __future__ import annotations

import re
from collections import deque
from datetime import date
from typing import Any, Iterable

from django.utils.translation import gettext as _

from .models import Individual, Tree

ALLOWED_INTENTS = frozenset(
    {
        "resolve_kinship",
        "count_children",
        "list_children",
        "list_kinship",
        "list_relatives",
        "person_facts",
        "person_age",
        "relation_between",
    }
)

ALLOWED_STEPS = frozenset({"father", "mother", "spouse", "child", "sibling"})

STEP_ALIASES = {
    "father": "father",
    "vater": "father",
    "papa": "father",
    "dad": "father",
    "daddy": "father",
    "mother": "mother",
    "mutter": "mother",
    "mama": "mother",
    "mom": "mother",
    "mum": "mother",
    "spouse": "spouse",
    "partner": "spouse",
    "ehepartner": "spouse",
    "ehemann": "spouse",
    "ehefrau": "spouse",
    "husband": "spouse",
    "wife": "spouse",
    "child": "child",
    "kind": "child",
    "sohn": "child",
    "tochter": "child",
    "son": "child",
    "daughter": "child",
    "sibling": "sibling",
    "geschwister": "sibling",
    "bruder": "sibling",
    "schwester": "sibling",
    "brother": "sibling",
    "sister": "sibling",
}

MAX_PATH_LENGTH = 8
MAX_RELATION_DEPTH = 10

STEP_LABELS = {
    "father": lambda: _("Vater"),
    "mother": lambda: _("Mutter"),
    "spouse": lambda: _("Ehepartner/in"),
    "child": lambda: _("Kind"),
    "sibling": lambda: _("Geschwister"),
    "parent": lambda: _("Elternteil"),
}

# Compact German/English labels for common ancestor paths from the anchor.
_PATH_PHRASES = {
    ("father",): lambda: _("der Vater"),
    ("mother",): lambda: _("die Mutter"),
    ("spouse",): lambda: _("der/die Ehepartner/in"),
    ("child",): lambda: _("ein Kind"),
    ("sibling",): lambda: _("ein Geschwisterteil"),
    ("father", "father"): lambda: _("der Großvater väterlicherseits"),
    ("father", "mother"): lambda: _("die Großmutter väterlicherseits"),
    ("mother", "father"): lambda: _("der Großvater mütterlicherseits"),
    ("mother", "mother"): lambda: _("die Großmutter mütterlicherseits"),
    ("father", "father", "father"): lambda: _("der Urgroßvater väterlicherseits"),
    ("mother", "mother", "mother"): lambda: _("die Urgroßmutter mütterlicherseits"),
    ("child", "child"): lambda: _("ein Enkelkind"),
}

# Composable relative groups for list_relatives(kind).
# Paths fan out: "child" / "sibling" mean *all* matches, not the first one.
RELATIVE_KINDS: dict[str, dict[str, Any]] = {
    "grandfathers": {
        "paths": [("father", "father"), ("mother", "father")],
        "label": lambda: _("Großväter"),
        "show_side": True,
        "report_missing": True,
    },
    "grandmothers": {
        "paths": [("father", "mother"), ("mother", "mother")],
        "label": lambda: _("Großmütter"),
        "show_side": True,
        "report_missing": True,
    },
    "grandparents": {
        "paths": [
            ("father", "father"),
            ("father", "mother"),
            ("mother", "father"),
            ("mother", "mother"),
        ],
        "label": lambda: _("Großeltern"),
        "show_side": True,
        "report_missing": True,
    },
    "parents": {
        "paths": [("father",), ("mother",)],
        "label": lambda: _("Eltern"),
        "show_side": True,
        "report_missing": True,
    },
    "children": {
        "paths": [("child",)],
        "label": lambda: _("Kinder"),
        "show_side": False,
        "report_missing": False,
    },
    "siblings": {
        "paths": [("sibling",)],
        "label": lambda: _("Geschwister"),
        "show_side": False,
        "report_missing": False,
    },
    "brothers": {
        "paths": [("sibling",)],
        "sex": "M",
        "label": lambda: _("Brüder"),
        "show_side": False,
        "report_missing": False,
    },
    "sisters": {
        "paths": [("sibling",)],
        "sex": "F",
        "label": lambda: _("Schwestern"),
        "show_side": False,
        "report_missing": False,
    },
    "spouses": {
        "paths": [("spouse",)],
        "label": lambda: _("Ehepartner"),
        "show_side": False,
        "report_missing": False,
    },
    "uncles": {
        "paths": [("father", "sibling"), ("mother", "sibling")],
        "sex": "M",
        "label": lambda: _("Onkel"),
        "show_side": True,
        "report_missing": False,
    },
    "aunts": {
        "paths": [("father", "sibling"), ("mother", "sibling")],
        "sex": "F",
        "label": lambda: _("Tanten"),
        "show_side": True,
        "report_missing": False,
    },
    "grandchildren": {
        "paths": [("child", "child")],
        "label": lambda: _("Enkelkinder"),
        "show_side": False,
        "report_missing": False,
    },
}

KIND_ALIASES = {
    "grandfathers": "grandfathers",
    "grandmothers": "grandmothers",
    "grandparents": "grandparents",
    "parents": "parents",
    "children": "children",
    "siblings": "siblings",
    "brothers": "brothers",
    "sisters": "sisters",
    "spouses": "spouses",
    "uncles": "uncles",
    "aunts": "aunts",
    "grandchildren": "grandchildren",
    "grossvaeter": "grandfathers",
    "grossmuetter": "grandmothers",
    "grosseltern": "grandparents",
    "opas": "grandfathers",
    "omas": "grandmothers",
    "kinder": "children",
    "geschwister": "siblings",
    "brueder": "brothers",
    "schwestern": "sisters",
    "eltern": "parents",
    "onkel": "uncles",
    "tanten": "aunts",
    "enkel": "grandchildren",
    "enkelkinder": "grandchildren",
    "ehepartner": "spouses",
}

# Backward-compatible names used by earlier list_kinship plans.
KINSHIP_SETS = {key: spec["paths"] for key, spec in RELATIVE_KINDS.items()}
SET_ALIASES = KIND_ALIASES
SET_LABELS = {key: spec["label"] for key, spec in RELATIVE_KINDS.items()}


class TreeQueryError(ValueError):
    """Invalid plan or unresolved kinship; message is safe to show the user."""


def children_of(person: Individual) -> list[Individual]:
    """All children from families where *person* is husband or wife."""
    found: list[Individual] = []
    seen: set[int] = set()
    for fam in person.spousal_families:
        for child in fam.children_links():
            if child.pk not in seen:
                seen.add(child.pk)
                found.append(child)
    return found


def first_spouse(person: Individual) -> Individual | None:
    for fam in person.spousal_families:
        partner = fam.spouse_of(person)
        if partner:
            return partner
    return None


def walk_kinship(person: Individual, path: Iterable[str]) -> Individual:
    """Walk *path* steps from *person*. Raises TreeQueryError if a step is missing."""
    current = person
    for raw_step in path:
        step = (raw_step or "").strip().lower()
        if step not in ALLOWED_STEPS:
            raise TreeQueryError(
                _("Unbekannter Verwandtschaftsschritt: %(step)s") % {"step": raw_step}
            )
        nxt = _apply_step(current, step)
        if nxt is None:
            raise TreeQueryError(
                _("Kein „%(step)s“ auf diesem Pfad gefunden.")
                % {"step": STEP_LABELS.get(step, lambda: step)()}
            )
        current = nxt
    return current


def walk_kinship_or_none(person: Individual, path: Iterable[str]) -> Individual | None:
    try:
        return walk_kinship(person, path)
    except TreeQueryError:
        return None


def _people_for_step(person: Individual, step: str) -> list[Individual]:
    """All neighbors for *step* (fan-out for child/sibling/spouse)."""
    if step == "father":
        return [person.father] if person.father else []
    if step == "mother":
        return [person.mother] if person.mother else []
    if step == "spouse":
        found: list[Individual] = []
        seen: set[int] = set()
        for fam in person.spousal_families:
            partner = fam.spouse_of(person)
            if partner and partner.pk not in seen:
                seen.add(partner.pk)
                found.append(partner)
        return found
    if step == "child":
        return children_of(person)
    if step == "sibling":
        return list(person.siblings)
    return []


def expand_kinship_path(start: Individual, path: Iterable[str]) -> list[Individual]:
    """Walk *path* from *start*, collecting every match at child/sibling/spouse steps."""
    current = [start]
    for raw_step in path:
        step = (raw_step or "").strip().lower()
        nxt: list[Individual] = []
        seen: set[int] = set()
        for node in current:
            for person in _people_for_step(node, step):
                if person.pk not in seen:
                    seen.add(person.pk)
                    nxt.append(person)
        current = nxt
        if not current:
            return []
    return current


def collect_relatives(anchor: Individual, kind: str) -> list[dict[str, Any]]:
    """People of *kind* relative to *anchor*, with optional side labels."""
    spec = RELATIVE_KINDS[kind]
    sex = spec.get("sex")
    show_side = bool(spec.get("show_side"))
    report_missing = bool(spec.get("report_missing"))
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for path in spec["paths"]:
        people = expand_kinship_path(anchor, path)
        matched: list[Individual] = []
        for person in people:
            if person.pk == anchor.pk:
                continue
            if sex and person.sex not in {sex, Individual.Sex.UNKNOWN, ""}:
                continue
            matched.append(person)
        if report_missing and not matched:
            out.append(
                {
                    "path": list(path),
                    "side_label": _side_label(path) if show_side else "",
                    "person": None,
                }
            )
            continue
        for person in matched:
            if person.pk in seen:
                continue
            seen.add(person.pk)
            out.append(
                {
                    "path": list(path),
                    "side_label": _side_label(path) if show_side else "",
                    "person": person,
                }
            )
    return out


def _apply_step(person: Individual, step: str) -> Individual | None:
    if step == "father":
        return person.father
    if step == "mother":
        return person.mother
    if step == "spouse":
        return first_spouse(person)
    if step == "child":
        kids = children_of(person)
        return kids[0] if kids else None
    if step == "sibling":
        sibs = list(person.siblings[:1])
        return sibs[0] if sibs else None
    return None


def serialize_person(person: Individual | None, apply_privacy: bool) -> dict[str, Any] | None:
    if person is None:
        return None
    if apply_privacy and person.is_confidential:
        return {
            "id": None,
            "display_name": _("Vertrauliche Person"),
            "redacted": True,
            "url": None,
        }
    return {
        "id": person.pk,
        "display_name": person.full_name(),
        "redacted": False,
        "url": person.get_absolute_url(),
    }


def kinship_path_label(path: list[str]) -> str:
    key = tuple(s.strip().lower() for s in path if s)
    if not key:
        return _("die Person")
    phrase = _PATH_PHRASES.get(key)
    if phrase:
        return phrase()
    parts = [STEP_LABELS[s]() if s in STEP_LABELS else s for s in key]
    return " → ".join(parts)


def _as_year(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 3000 else None
    match = re.search(r"(\d{4})", str(value))
    if not match:
        return None
    year = int(match.group(1))
    return year if 1 <= year <= 3000 else None


def _years_between(start: date, end: date) -> int:
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return max(0, years)


def _age_facts(person: Individual) -> dict[str, Any]:
    """Age in whole years from parsed dates, with a year-only fallback."""
    birth = person.birth_date
    death = person.death_date
    deceased = bool(person.is_deceased)

    if birth and death:
        return {
            "age_years": _years_between(birth, death),
            "age_approximate": False,
            "age_at_death": True,
        }
    if birth and not deceased:
        return {
            "age_years": _years_between(birth, date.today()),
            "age_approximate": False,
            "age_at_death": False,
        }

    birth_year = _as_year(getattr(person, "birth_year", None))
    death_year = _as_year(getattr(person, "death_year", None))
    if birth_year and death_year:
        return {
            "age_years": max(0, death_year - birth_year),
            "age_approximate": True,
            "age_at_death": True,
        }
    if birth_year and not deceased:
        return {
            "age_years": max(0, date.today().year - birth_year),
            "age_approximate": True,
            "age_at_death": False,
        }
    return {
        "age_years": None,
        "age_approximate": False,
        "age_at_death": deceased,
    }


def _person_facts(person: Individual) -> dict[str, Any]:
    birth = person.birth_event
    death = person.death_event
    marriage = None
    for fam in person.spousal_families:
        marriage = fam.marriage_event
        if marriage:
            break
    facts = {
        "birth_date": (
            person.birth_date.isoformat()
            if person.birth_date
            else (person.birth_date_raw or "")
        ),
        "birth_place": birth.place.name if birth and birth.place else "",
        "death_date": (
            person.death_date.isoformat()
            if person.death_date
            else (person.death_date_raw or "")
        ),
        "death_place": death.place.name if death and death.place else "",
        "marriage_date": (
            marriage.parsed_date.isoformat()
            if marriage and marriage.parsed_date
            else ((marriage.raw_date if marriage else "") or "")
        ),
        "is_deceased": person.is_deceased,
    }
    facts.update(_age_facts(person))
    return facts


def _age_phrase(facts: dict[str, Any]) -> str | None:
    years = facts.get("age_years")
    if years is None:
        return None
    if facts.get("age_approximate"):
        span = _("etwa %(years)s Jahre") % {"years": years}
    else:
        span = _("%(years)s Jahre") % {"years": years}
    if facts.get("age_at_death"):
        return _("wurde %(age)s alt") % {"age": span}
    return _("ist %(age)s alt") % {"age": span}


def _neighbors(person: Individual) -> list[tuple[Individual, str]]:
    edges: list[tuple[Individual, str]] = []
    if person.father:
        edges.append((person.father, "father"))
    if person.mother:
        edges.append((person.mother, "mother"))
    for fam in person.spousal_families:
        partner = fam.spouse_of(person)
        if partner:
            edges.append((partner, "spouse"))
        for child in fam.children_links():
            if child.pk != person.pk:
                edges.append((child, "child"))
    for sib in person.siblings:
        edges.append((sib, "sibling"))
    return edges


def find_relation_path(
    start: Individual, goal: Individual, max_depth: int = MAX_RELATION_DEPTH
) -> list[str] | None:
    """
    Shortest path of kinship edge labels from *start* to *goal*.
    Each label describes the neighbor relative to the current node
    (father, mother, spouse, child).
    """
    if start.pk == goal.pk:
        return []
    visited = {start.pk}
    queue: deque[tuple[Individual, list[str]]] = deque([(start, [])])
    while queue:
        current, path = queue.popleft()
        if len(path) >= max_depth:
            continue
        for neighbor, label in _neighbors(current):
            if neighbor.pk in visited:
                continue
            nxt_path = path + [label]
            if neighbor.pk == goal.pk:
                return nxt_path
            visited.add(neighbor.pk)
            queue.append((neighbor, nxt_path))
    return None


def describe_relation(edge_path: list[str]) -> str:
    """Turn an edge-label path into a short relationship phrase (who the goal is to the start)."""
    if not edge_path:
        return _("dieselbe Person")
    key = tuple(edge_path)
    mapping = {
        ("father",): _("Vater"),
        ("mother",): _("Mutter"),
        ("child",): _("Kind"),
        ("spouse",): _("Ehepartner/in"),
        ("sibling",): _("Geschwister"),
        ("father", "child"): _("Geschwister (väterlicherseits)"),
        ("mother", "child"): _("Geschwister (mütterlicherseits)"),
        ("father", "father"): _("Großvater väterlicherseits"),
        ("father", "mother"): _("Großmutter väterlicherseits"),
        ("mother", "father"): _("Großvater mütterlicherseits"),
        ("mother", "mother"): _("Großmutter mütterlicherseits"),
        ("child", "child"): _("Enkelkind"),
        ("father", "spouse"): _("Ehepartner/in des Vaters"),
        ("mother", "spouse"): _("Ehepartner/in der Mutter"),
        ("spouse", "father"): _("Schwiegervater"),
        ("spouse", "mother"): _("Schwiegermutter"),
        ("child", "spouse"): _("Schwiegerkind"),
        ("sibling", "spouse"): _("Schwager/Schwägerin"),
        ("spouse", "sibling"): _("Schwager/Schwägerin"),
        ("father", "sibling"): _("Onkel/Tante väterlicherseits"),
        ("mother", "sibling"): _("Onkel/Tante mütterlicherseits"),
        ("sibling", "child"): _("Neffe/Nichte"),
        ("father", "father", "child"): _("Onkel/Tante väterlicherseits"),
        ("father", "mother", "child"): _("Onkel/Tante väterlicherseits"),
        ("mother", "father", "child"): _("Onkel/Tante mütterlicherseits"),
        ("mother", "mother", "child"): _("Onkel/Tante mütterlicherseits"),
        ("father", "child", "child"): _("Neffe/Nichte väterlicherseits"),
        ("mother", "child", "child"): _("Neffe/Nichte mütterlicherseits"),
        ("father", "sibling", "child"): _("Cousin/Cousine väterlicherseits"),
        ("mother", "sibling", "child"): _("Cousin/Cousine mütterlicherseits"),
        ("father", "father", "father"): _("Urgroßvater väterlicherseits"),
        ("mother", "mother", "mother"): _("Urgroßmutter mütterlicherseits"),
        ("father", "father", "mother"): _("Urgroßmutter väterlicherseits"),
        ## TODO: complete the list
    }
    if key in mapping:
        return mapping[key]
    return " → ".join(STEP_LABELS[s]() if s in STEP_LABELS else s for s in edge_path)


def _sex_of(person: Individual | None) -> str:
    return (getattr(person, "sex", None) or "").upper()


def _role_with_article(kind: str, sex: str) -> str:
    """Grammatical German role with article, gendered when known."""
    male = sex == "M"
    female = sex == "F"
    roles = {
        "child": (_("das Kind"), _("der Sohn"), _("die Tochter")),
        "parent": (_("ein Elternteil"), _("der Vater"), _("die Mutter")),
        "spouse": (_("der/die Ehepartner/in"), _("der Ehemann"), _("die Ehefrau")),
        "sibling": (_("ein Geschwisterteil"), _("der Bruder"), _("die Schwester")),
        "nibling": (_("der/die Neffe/Nichte"), _("der Neffe"), _("die Nichte")),
        "pibling": (_("der/die Onkel/Tante"), _("der Onkel"), _("die Tante")),
        "grandchild": (_("das Enkelkind"), _("der Enkel"), _("die Enkelin")),
        "grandparent": (_("ein Großelternteil"), _("der Großvater"), _("die Großmutter")),
        "child_in_law": (
            _("das Schwiegerkind"),
            _("der Schwiegersohn"),
            _("die Schwiegertochter"),
        ),
        "parent_in_law": (
            _("ein Schwiegerelternteil"),
            _("der Schwiegervater"),
            _("die Schwiegermutter"),
        ),
        "sibling_in_law": (
            _("der/die Schwager/Schwägerin"),
            _("der Schwager"),
            _("die Schwägerin"),
        ),
        "cousin": (_("ein Cousin/eine Cousine"), _("der Cousin"), _("die Cousine")),
    }
    neutral, der, die = roles[kind]
    if male:
        return der
    if female:
        return die
    return neutral


# Path start→goal: who *start* is to *goal* (August→Beatrice = nibling).
_START_ROLE_TO_GOAL: dict[tuple[str, ...], str] = {
    ("father",): "child",
    ("mother",): "child",
    ("child",): "parent",
    ("spouse",): "spouse",
    ("sibling",): "sibling",
    ("father", "child"): "sibling",
    ("mother", "child"): "sibling",
    ("father", "spouse"): "stepchild",
    ("mother", "spouse"): "stepchild",
    ("spouse", "father"): "child_in_law",
    ("spouse", "mother"): "child_in_law",
    ("child", "spouse"): "parent_in_law",
    ("sibling", "spouse"): "sibling_in_law",
    ("spouse", "sibling"): "sibling_in_law",
    ("father", "sibling"): "nibling",
    ("mother", "sibling"): "nibling",
    ("sibling", "child"): "pibling",
    ("father", "father"): "grandchild",
    ("father", "mother"): "grandchild",
    ("mother", "father"): "grandchild",
    ("mother", "mother"): "grandchild",
    ("child", "child"): "grandparent",
    ("father", "father", "child"): "nibling",
    ("father", "mother", "child"): "nibling",
    ("mother", "father", "child"): "nibling",
    ("mother", "mother", "child"): "nibling",
    ("father", "child", "child"): "pibling",
    ("mother", "child", "child"): "pibling",
    ("father", "sibling", "child"): "cousin",
    ("mother", "sibling", "child"): "cousin",
}


def describe_start_to_goal(edge_path: list[str], start: Individual) -> str:
    """Who *start* is to the person at the end of *edge_path*, with article."""
    kind = _START_ROLE_TO_GOAL.get(tuple(edge_path))
    if not kind:
        return describe_relation(edge_path)
    if kind == "stepchild":
        return _role_with_article("child", _sex_of(start))
    return _role_with_article(kind, _sex_of(start))


def _capitalize(text: str) -> str:
    if not text:
        return text
    return text[0].upper() + text[1:]


def render_answer(intent: str, facts: dict[str, Any]) -> str:
    """Human-readable sentence from structured facts. No model involved."""
    subject = facts.get("subject") or {}
    name = subject.get("display_name") or _("Unbekannt")
    redacted = bool(subject.get("redacted"))
    path = facts.get("kinship_path") or []
    label = _capitalize(kinship_path_label(path))

    if intent == "resolve_kinship":
        if redacted:
            return _("%(label)s unterliegt dem Datenschutz.") % {"label": label}
        return _("%(label)s heißt %(name)s.") % {"label": label, "name": name}

    if intent in {"count_children", "list_children"}:
        if redacted:
            return _("Die Kinder dieser Person können aus Datenschutzgründen nicht angezeigt werden.")
        count = facts.get("children_count", 0)
        if count == 0:
            return _("%(name)s hat keine bekannten Kinder.") % {"name": name}
        if intent == "count_children":
            return _("%(name)s hat %(count)s Kind(er).") % {"name": name, "count": count}
        child_names = [
            c.get("display_name")
            for c in facts.get("children") or []
            if c.get("display_name")
        ]
        return _("%(name)s hat %(count)s Kind(er): %(children)s.") % {
            "name": name,
            "count": count,
            "children": ", ".join(child_names),
        }

    if intent in {"person_facts", "person_age"}:
        if redacted:
            return _("Die Daten dieser Person unterliegen dem Datenschutz.")
        age_bit = _age_phrase(facts)
        if intent == "person_age":
            if age_bit:
                return _("%(name)s %(age)s.") % {"name": name, "age": age_bit}
            return _(
                "%(name)s: Alter unbekannt (kein ausreichendes Geburts- oder Sterbedatum)."
            ) % {"name": name}
        bits = [name]
        birth = facts.get("birth_date") or ""
        bplace = facts.get("birth_place") or ""
        if birth and bplace:
            bits.append(_("geboren %(date)s in %(place)s") % {"date": birth, "place": bplace})
        elif birth:
            bits.append(_("geboren %(date)s") % {"date": birth})
        death = facts.get("death_date") or ""
        dplace = facts.get("death_place") or ""
        if death and dplace:
            bits.append(_("gestorben %(date)s in %(place)s") % {"date": death, "place": dplace})
        elif death:
            bits.append(_("gestorben %(date)s") % {"date": death})
        if age_bit:
            bits.append(age_bit)
        if len(bits) == 1:
            return _("%(name)s: keine Datumsangaben vorhanden.") % {"name": name}
        return ", ".join(bits) + "."

    if intent in {"list_kinship", "list_relatives"}:
        kind = facts.get("kind") or facts.get("kinship_set")
        spec = RELATIVE_KINDS.get(kind) if kind else None
        group = spec["label"]() if spec else _("Verwandte")
        bits: list[str] = []
        found = False
        for rel in facts.get("relatives") or []:
            side = rel.get("side_label") or ""
            person = rel.get("person")
            if not person:
                if side:
                    bits.append(_("%(side)s unbekannt") % {"side": side})
                continue
            found = True
            who = person.get("display_name") or _("Unbekannt")
            if side:
                bits.append(_("%(who)s (%(side)s)") % {"who": who, "side": side})
            else:
                bits.append(who)
        if not found:
            return _("Keine %(group)s von %(name)s gefunden.") % {
                "group": group,
                "name": name,
            }
        return _("Die %(group)s von %(name)s: %(list)s.") % {
            "group": group,
            "name": name,
            "list": ", ".join(bits),
        }

    if intent == "relation_between":
        if facts.get("relation_hidden"):
            return _("Die Beziehung kann aus Datenschutzgründen nicht angezeigt werden.")
        other = facts.get("target") or {}
        relation = facts.get("relation_label") or _("unbekannt")
        if facts.get("no_relation"):
            return _("Keine Verwandtschaft zwischen %(a)s und %(b)s gefunden.") % {
                "a": name,
                "b": other.get("display_name") or _("Unbekannt"),
            }
        kind = facts.get("relation_kind")
        if kind == "sibling":
            return _("%(a)s und %(b)s sind Geschwister.") % {
                "a": name,
                "b": other.get("display_name") or _("Unbekannt"),
            }
        if kind == "spouse":
            return _("%(a)s und %(b)s sind Ehepartner.") % {
                "a": name,
                "b": other.get("display_name") or _("Unbekannt"),
            }
        return _("%(a)s ist %(relation)s von %(b)s.") % {
            "a": name,
            "b": other.get("display_name") or _("Unbekannt"),
            "relation": relation,
        }

    return _("Keine Antwort erzeugt.")


def _side_label(path: tuple[str, ...] | list[str]) -> str:
    if path and path[0] == "father":
        return _("väterlicherseits")
    if path and path[0] == "mother":
        return _("mütterlicherseits")
    return ""


def _path_tokens(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.replace(";", ",").split(",")]
        return [p for p in parts if p]
    if isinstance(raw, (list, tuple)):
        return [str(item).strip().lower() for item in raw if str(item).strip()]
    raise TreeQueryError(_("kinship_path muss eine Liste oder ein Text sein."))


def _normalize_path(raw: Any) -> list[str]:
    out: list[str] = []
    for step in _path_tokens(raw):
        canonical = STEP_ALIASES.get(step)
        if not canonical:
            raise TreeQueryError(
                _("Unbekannter Verwandtschaftsschritt: %(step)s") % {"step": step}
            )
        out.append(canonical)
    return out


def _resolve_kind(payload: dict[str, Any], tokens: list[str], intent: str) -> str | None:
    for key in ("kind", "kinship_set"):
        raw = str(payload.get(key) or "").strip().lower()
        if not raw:
            continue
        canonical = KIND_ALIASES.get(raw)
        if not canonical:
            raise TreeQueryError(
                _("Unbekannte Verwandtengruppe: %(name)s") % {"name": raw}
            )
        return canonical
    if len(tokens) == 1 and tokens[0] in KIND_ALIASES:
        token = tokens[0]
        if intent in {"list_relatives", "list_kinship"} or token not in STEP_ALIASES:
            return KIND_ALIASES[token]
    return None


def _optional_int(value: Any, field: str) -> int | None:
    if value in (None, "", "starting_individual"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TreeQueryError(_("%(field)s ist keine gültige ID.") % {"field": field}) from exc


def parse_plan(payload: dict[str, Any]) -> dict[str, Any]:
    intent = str(payload.get("intent") or "").strip()
    if intent == "list_kinship":
        intent = "list_relatives"
    tokens = _path_tokens(payload.get("kinship_path"))
    kind = _resolve_kind(payload, tokens, intent)
    if kind:
        intent = "list_relatives"
        if len(tokens) == 1 and tokens[0] in KIND_ALIASES:
            path: list[str] = []
        else:
            path = _normalize_path(payload.get("kinship_path")) if tokens else []
            if len(path) > MAX_PATH_LENGTH:
                raise TreeQueryError(_("Verwandtschaftspfad ist zu lang."))
    else:
        if intent not in ALLOWED_INTENTS:
            raise TreeQueryError(
                _("Unbekannte Anfrageart. Erlaubt: %(intents)s")
                % {"intents": ", ".join(sorted(ALLOWED_INTENTS))}
            )
        path = _normalize_path(payload.get("kinship_path"))
        if len(path) > MAX_PATH_LENGTH:
            raise TreeQueryError(_("Verwandtschaftspfad ist zu lang."))
        for step in path:
            if step not in ALLOWED_STEPS:
                raise TreeQueryError(
                    _("Unbekannter Verwandtschaftsschritt: %(step)s") % {"step": step}
                )

    if intent not in ALLOWED_INTENTS:
        raise TreeQueryError(
            _("Unbekannte Anfrageart. Erlaubt: %(intents)s")
            % {"intents": ", ".join(sorted(ALLOWED_INTENTS))}
        )
    if intent == "list_relatives" and not kind:
        raise TreeQueryError(
            _(
                "Bitte eine Verwandtengruppe angeben "
                "(Großväter, Großmütter, Kinder, Geschwister, Onkel, …)."
            )
        )
    return {
        "intent": intent,
        "anchor": str(payload.get("anchor") or "starting_individual").strip()
        or "starting_individual",
        "person_id": _optional_int(payload.get("person_id"), "person_id"),
        "target_id": _optional_int(payload.get("target_id"), "target_id"),
        "person_name": str(payload.get("person_name") or "").strip(),
        "target_name": str(payload.get("target_name") or "").strip(),
        "kinship_path": path,
        "kind": kind,
        "kinship_set": kind,
    }


def resolve_person_by_name(
    tree_id: int, name: str, apply_privacy: bool
) -> Individual:
    """Resolve a display name to one Individual in *tree_id* (privacy-filtered)."""
    from django.db.models import Q

    from .mixins import apply_privacy_to_individual_qs

    name = " ".join((name or "").split())
    if not name:
        raise TreeQueryError(_("Kein Personenname angegeben."))

    terms = name.replace(",", " ").split()
    qs = apply_privacy_to_individual_qs(
        Individual.objects.filter(gedcom_tree_id=tree_id),
        apply_privacy,
    )
    combined = Q()
    for term in terms:
        combined &= (
            Q(given_name__icontains=term)
            | Q(surname__icontains=term)
            | Q(alternative_names__given_name__icontains=term)
            | Q(alternative_names__surname__icontains=term)
        )
    matches = list(qs.filter(combined).distinct()[:6])
    if not matches:
        raise TreeQueryError(
            _("Keine Person „%(name)s“ gefunden.") % {"name": name}
        )
    if len(matches) > 1:
        labels = ", ".join(person.full_name() for person in matches)
        raise TreeQueryError(
            _("Mehrere Treffer für „%(name)s“: %(matches)s. Bitte den Namen genauer angeben.")
            % {"name": name, "matches": labels}
        )
    return matches[0]


def _resolve_anchor(tree: Tree, plan: dict[str, Any], apply_privacy: bool) -> Individual:
    tree_id = tree.pk
    if plan["person_id"]:
        person = Individual.objects.filter(
            pk=plan["person_id"], gedcom_tree_id=tree_id
        ).first()
        if not person:
            raise TreeQueryError(_("Person nicht in diesem Stammbaum gefunden."))
        return person
    if plan.get("person_name"):
        return resolve_person_by_name(tree.pk, plan["person_name"], apply_privacy)
    if tree.starting_individual_id:
        start = tree.starting_individual
        if start is None:
            start = Individual.objects.filter(pk=tree.starting_individual_id).first()
        if start and start.gedcom_tree_id == tree_id:
            return start
    raise TreeQueryError(
        _("Keine Startperson gesetzt. Bitte eine Person wählen oder eine Startperson im Baum festlegen.")
    )


def execute_tree_query(
    tree_id: int,
    payload: dict[str, Any],
    apply_privacy: bool,
) -> dict[str, Any]:
    """
    Run a structured plan against *tree_id*.

    Returns ``{ok, error, intent, answer, facts}``. ``ok`` is False for
    validation / missing-kinship errors (HTTP 200 with error is fine for the UI).
    """
    try:
        plan = parse_plan(payload)
        tree = Tree.objects.select_related("starting_individual").get(pk=tree_id)
        anchor = _resolve_anchor(tree, plan, apply_privacy)
        subject = walk_kinship(anchor, plan["kinship_path"])
        intent = plan["intent"]
        facts: dict[str, Any] = {
            "intent": intent,
            "kinship_path": plan["kinship_path"],
            "kinship_set": plan.get("kind"),
            "kind": plan.get("kind"),
            "path_label": kinship_path_label(plan["kinship_path"]),
            "subject": serialize_person(subject, apply_privacy),
        }

        if intent == "resolve_kinship":
            return _ok(intent, facts)

        if intent == "list_relatives":
            kind = plan["kind"]
            relatives: list[dict[str, Any]] = []
            for rel in collect_relatives(subject, kind):
                person = rel["person"]
                relatives.append(
                    {
                        "path": rel["path"],
                        "side_label": rel["side_label"],
                        "person": serialize_person(person, apply_privacy) if person else None,
                    }
                )
            facts["relatives"] = relatives
            facts["kind"] = kind
            facts["kinship_set"] = kind
            facts["path_label"] = RELATIVE_KINDS[kind]["label"]()
            return _ok(intent, facts)

        if intent in {"count_children", "list_children"}:
            if facts["subject"]["redacted"]:
                facts["children_count"] = None
                facts["children"] = []
                return _ok(intent, facts)
            kids = children_of(subject)
            facts["children_count"] = len(kids)
            facts["children"] = [
                serialize_person(child, apply_privacy) for child in kids
            ]
            return _ok(intent, facts)

        if intent in {"person_facts", "person_age"}:
            if not facts["subject"]["redacted"]:
                facts.update(_person_facts(subject))
            return _ok(intent, facts)

        if intent == "relation_between":
            if plan["target_id"]:
                target = Individual.objects.filter(
                    pk=plan["target_id"], gedcom_tree_id=tree_id
                ).first()
                if not target:
                    raise TreeQueryError(_("Zielperson nicht in diesem Stammbaum gefunden."))
            elif plan.get("target_name"):
                target = resolve_person_by_name(tree_id, plan["target_name"], apply_privacy)
            else:
                raise TreeQueryError(_("Bitte eine zweite Person für den Vergleich wählen."))
            facts["target"] = serialize_person(target, apply_privacy)
            if facts["subject"]["redacted"] or facts["target"]["redacted"]:
                facts["relation_hidden"] = True
                facts["relation_path"] = []
                facts["relation_label"] = ""
                return _ok(intent, facts)
            edge_path = find_relation_path(subject, target)
            if edge_path is None:
                facts["no_relation"] = True
                facts["relation_path"] = []
                facts["relation_label"] = ""
                facts["relation_kind"] = None
            else:
                facts["no_relation"] = False
                facts["relation_path"] = edge_path
                facts["relation_kind"] = _START_ROLE_TO_GOAL.get(tuple(edge_path))
                facts["relation_label"] = describe_start_to_goal(edge_path, subject)
            return _ok(intent, facts)

        raise TreeQueryError(_("Unbekannte Anfrageart."))
    except Tree.DoesNotExist:
        return _fail("", _("Stammbaum nicht gefunden."))
    except TreeQueryError as exc:
        intent = str(payload.get("intent") or "")
        return _fail(intent, str(exc))


def _ok(intent: str, facts: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "error": None,
        "intent": intent,
        "answer": render_answer(intent, facts),
        "facts": facts,
    }


def _fail(intent: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "error": message,
        "intent": intent,
        "answer": message,
        "facts": {},
    }
