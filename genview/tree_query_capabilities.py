"""Catalog of tree-query capabilities shared by the executor and the LLM planner.

The executor only runs plans that fit this catalog. The LLM does not invent
kinship; it fills a JSON template whose slots map onto these capabilities.
"""

from __future__ import annotations

import json
from typing import Any

INTENT_CATALOG: dict[str, str] = {
    "resolve_kinship": (
        "Name the unique person at kinship_path from the anchor "
        "(father, mother, a specified grandmother). Not for groups like uncles."
    ),
    "list_relatives": (
        "List names of every person of one kind (uncles, children, …). "
        "Do not use this when the question asks for a date, age, birth, or death."
    ),
    "count_children": "How many children the anchor/person_name has.",
    "list_children": "List children (legacy; prefer list_relatives with kind=children).",
    "person_facts": (
        "Birth/death/marriage dates. For a group (uncle, aunt, siblings) set kind "
        "and optional place_filter; the executor picks or lists matches. "
        "Set fact_focus to birth or death when the question is specific."
    ),
    "person_age": "Age of the person or of every match in kind (executor calculates).",
    "relation_between": "How person_name is related to target_name. Two personal names required.",
    "unsupported": "Question is not about kinship or person dates in the tree.",
}

FACT_FOCUS_VALUES = ("", "birth", "death", "marriage")

PLAN_TEMPLATE: dict[str, Any] = {
    "intent": "",
    "kind": "",
    "kinship_path": [],
    "person_name": "",
    "target_name": "",
    "place_filter": "",
    "fact_focus": "",
}

FANOUT_KINDS = frozenset(
    {
        "uncles",
        "aunts",
        "siblings",
        "brothers",
        "sisters",
        "children",
        "grandchildren",
        "grandparents",
        "grandfathers",
        "grandmothers",
        "parents",
        "spouses",
    }
)


def relative_kind_lines() -> list[str]:
    from .tree_query import RELATIVE_KINDS

    lines = []
    for key, spec in RELATIVE_KINDS.items():
        paths = " OR ".join("→".join(path) for path in spec["paths"])
        sex = spec.get("sex")
        extra = f" (sex={sex} only)" if sex else ""
        lines.append(f"- {key}: {paths}{extra}")
    return lines


def build_parse_system_prompt() -> str:
    """System prompt: capabilities + empty template the model must fill."""
    kinds = "\n".join(relative_kind_lines())
    intents = "\n".join(
        f"- {name}: {desc}" for name, desc in INTENT_CATALOG.items()
    )
    template = json.dumps(PLAN_TEMPLATE, ensure_ascii=False, indent=2)
    return f"""You fill a genealogy query template. Output JSON only, no markdown.

Empty template (copy and fill):
{template}

Allowed intent values and what the executor will do:
{intents}

Relation groups (kind). The executor expands these from the tree; do not pick one person with kinship_path when several matches are possible:
{kinds}

kinship_path steps (only for a unique relative): father, mother, spouse, child, sibling.
Examples: maternal grandmother = ["mother","mother"]; father of a named person = ["father"] plus person_name.

Slots:
- person_name: a real personal name from the question, else empty (my/ich/mein = starting person).
- target_name: only for relation_between.
- place_filter: place used to choose among several relatives ("from Berlin", "aus Berlin", "in Berlin"). Never put a place in person_name.
- fact_focus: "birth" | "death" | "marriage" | "" (empty = all known dates).

Hard rules:
- Date/death/birth/age of a group (uncle, aunt, siblings, children) → intent=person_facts or person_age, kind=uncles/aunts/…, empty kinship_path, place_filter if a place is named.
- "When did my uncle from Berlin die" → {{"intent":"person_facts","kind":"uncles","place_filter":"Berlin","fact_focus":"death","kinship_path":[],"person_name":""}}
- Do not use resolve_kinship for Onkel/uncle/Tante/aunt/Geschwister; that would return only the first match.
- Do not use list_relatives when the user asked when/where someone was born or died.
- If a draft plan is supplied, start from it and correct wrong intent/kind/place_filter/fact_focus.
- Never invent numeric ids.
- If the question is not about kinship or dates in the family tree, return {{"intent":"unsupported"}}.
"""
