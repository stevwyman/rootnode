"""Natural-language → tree_query plan (Phase 2).

Rules handle common German/English kinship phrases without a model.
Anything else is sent to the LLM client; the executor still validates the plan.
"""

from __future__ import annotations

import re
from typing import Any

from django.utils.translation import gettext as _

from .llm_client import llm_parser_enabled, parse_question_via_llm
from .tree_query import ALLOWED_INTENTS, TreeQueryError, parse_plan

_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

# Longest phrases first.
_PATH_PHRASES: list[tuple[str, list[str]]] = [
    ("grossmutter muetterlicherseits", ["mother", "mother"]),
    ("grandmother on my mothers side", ["mother", "mother"]),
    ("grandmother on mothers side", ["mother", "mother"]),
    ("grandmother on my mother's side", ["mother", "mother"]),
    ("muetterliche grossmutter", ["mother", "mother"]),
    ("mutter meiner mutter", ["mother", "mother"]),
    ("mutter der mutter", ["mother", "mother"]),
    ("grossmutter vaeterlicherseits", ["father", "mother"]),
    ("grandmother on my fathers side", ["father", "mother"]),
    ("grandmother on fathers side", ["father", "mother"]),
    ("vaeterliche grossmutter", ["father", "mother"]),
    ("mutter meines vaters", ["father", "mother"]),
    ("mutter des vaters", ["father", "mother"]),
    ("grossvater muetterlicherseits", ["mother", "father"]),
    ("grandfather on my mothers side", ["mother", "father"]),
    ("grandfather on mothers side", ["mother", "father"]),
    ("muetterlicher grossvater", ["mother", "father"]),
    ("vater meiner mutter", ["mother", "father"]),
    ("vater der mutter", ["mother", "father"]),
    ("grossvater vaeterlicherseits", ["father", "father"]),
    ("grandfather on my fathers side", ["father", "father"]),
    ("grandfather on fathers side", ["father", "father"]),
    ("fathers father", ["father", "father"]),
    ("father's father", ["father", "father"]),
    ("vaeterlicher grossvater", ["father", "father"]),
    ("vater meines vaters", ["father", "father"]),
    ("vater des vaters", ["father", "father"]),
    ("my father's father", ["father", "father"]),
    ("my fathers father", ["father", "father"]),
    # Unspecified "Großmutter" is maternal; "die andere" is the paternal one.
    ("andere grossmutter", ["father", "mother"]),
    ("other grandmother", ["father", "mother"]),
    ("andere oma", ["father", "mother"]),
    ("other grandma", ["father", "mother"]),
    ("anderer grossvater", ["mother", "father"]),
    ("andere grossvater", ["mother", "father"]),
    ("other grandfather", ["mother", "father"]),
    ("anderer opa", ["mother", "father"]),
    ("meine grossmutter", ["mother", "mother"]),
    ("my grandmother", ["mother", "mother"]),
    ("meine oma", ["mother", "mother"]),
    ("my grandma", ["mother", "mother"]),
    ("mein grossvater", ["father", "father"]),
    ("my grandfather", ["father", "father"]),
    ("mein opa", ["father", "father"]),
    ("my grandpa", ["father", "father"]),
    ("meine mutter", ["mother"]),
    ("my mother", ["mother"]),
    ("mein vater", ["father"]),
    ("my father", ["father"]),
    ("meine mutter", ["mother"]),
    ("ehepartner", ["spouse"]),
    ("ehemann", ["spouse"]),
    ("ehefrau", ["spouse"]),
    ("my spouse", ["spouse"]),
    ("my wife", ["spouse"]),
    ("my husband", ["spouse"]),
]


def _fold(text: str) -> str:
    folded = (text or "").lower().translate(_UMLAUT)
    folded = re.sub(r"[?!.,:;]+", " ", folded)
    folded = folded.replace("'", "")
    return " ".join(folded.split())


def _detect_path(folded: str) -> list[str]:
    for phrase, path in _PATH_PHRASES:
        if phrase in folded:
            return list(path)
    return []


def _is_about_self(folded: str) -> bool:
    """True if the subject is the tree starting person, not a relative."""
    return bool(
        re.search(
            r"\b(ich|habe ich|hab ich|bin ich|war ich|"
            r"do i|i have|am i|was i|startperson|starting person)\b",
            folded,
        )
    )


def _question_names_relative(folded: str) -> bool:
    return bool(
        re.search(
            r"grossmutter|grossvater|\boma\b|\bopa\b|grandmother|grandfather|"
            r"grandma|grandpa|\bmutter\b|\bvater\b|\bmother\b|\bfather\b|"
            r"ehepartner|ehemann|ehefrau|\bspouse\b|\bhusband\b|\bwife\b|"
            r"onkel|tante|uncle|aunt|schwiegermutter|"
            r"\bandere[rn]?\b|\bother\b",
            folded,
        )
    )


def _looks_like_tree_query(folded: str) -> bool:
    """True if the question is about kinship or person facts in the tree."""
    if _detect_path(folded) or _detect_relative_kind(folded):
        return True
    return bool(
        re.search(
            r"grossmutter|grossvater|grosseltern|\boma\b|\bopa\b|"
            r"grandmother|grandfather|grandma|grandpa|"
            r"\bmutter\b|\bvater\b|\bmama\b|\bpapa\b|\bmother\b|\bfather\b|"
            r"ehepartner|ehemann|ehefrau|\bspouse\b|\bhusband\b|\bwife\b|"
            r"onkel|tante|uncle|aunt|geschwister|sibling|"
            r"bruder|schwester|brother|sister|"
            r"kinder|children|sohn|tochter|\bson\b|\bdaughter\b|"
            r"eltern|parents|enkel|"
            r"geboren|gestorben|\bbirth\b|\bdeath\b|"
            r"wie ?alt|how old|\balter\b|"
            r"verwandt|related|beziehung zwischen|"
            r"startperson|stammbaum",
            folded,
        )
    )


_KIND_QUESTION_CUES = {
    "grandfathers": r"grossvaeter|grandfather|opas|\bopa\b",
    "grandmothers": r"grossmuetter|grandmother|omas|\boma\b",
    "grandparents": r"grosseltern|grandparent",
    "parents": r"eltern|\bparents\b|\bmutter\b|\bvater\b|\bmother\b|\bfather\b",
    "children": r"kinder|children|sohn|tochter|\bson\b|\bdaughter\b",
    "siblings": r"geschwister|sibling|bruder|schwester|brother|sister",
    "brothers": r"brueder|bruder|\bbrothers?\b",
    "sisters": r"schwestern|schwester|\bsisters?\b",
    "spouses": r"ehepartner|ehemann|ehefrau|\bspouses?\b|\bhusband\b|\bwife\b",
    "uncles": r"onkel|\buncles?\b",
    "aunts": r"tanten|\btante\b|\baunts?\b",
    "grandchildren": r"enkel|grandchild",
}

_UNSUPPORTED_INTENTS = frozenset(
    {"unsupported", "unknown", "off_topic", "none", "refuse", "unrelated"}
)

def _off_topic_error() -> str:
    return _(
        "Diese Frage betrifft keine Verwandtschaft oder Personendaten im Stammbaum. "
        "Bitte z. B. nach Großeltern, Kindern, Alter oder Namen fragen."
    )


def _name_mentioned(question_folded: str, name: str) -> bool:
    folded_name = _fold(name)
    if not folded_name:
        return False
    if folded_name in question_folded:
        return True
    parts = [p for p in folded_name.split() if len(p) > 2]
    return bool(parts) and any(part in question_folded for part in parts)


def _plan_fits_question(question: str, plan: dict[str, Any]) -> bool:
    """Reject LLM plans that the question text does not license."""
    folded = _fold(question)
    intent = str(plan.get("intent") or "")
    if intent in _UNSUPPORTED_INTENTS:
        return False
    name = str(plan.get("person_name") or "").strip()
    if name and not _name_mentioned(folded, name):
        return False
    target = str(plan.get("target_name") or "").strip()
    if target and not _name_mentioned(folded, target):
        return False

    if intent in {"count_children", "list_children"}:
        return bool(re.search(r"kinder|children|sohn|tochter|\bson\b|\bdaughter\b", folded))
    if intent == "list_relatives":
        kind = str(plan.get("kind") or plan.get("kinship_set") or "")
        cue = _KIND_QUESTION_CUES.get(kind)
        return bool(cue and re.search(cue, folded))
    if intent == "person_age":
        return bool(re.search(r"wie ?alt|how old|\balter\b|\bage\b|years old", folded))
    if intent == "person_facts":
        return bool(
            re.search(r"geboren|gestorben|\bbirth\b|\bdeath\b|heirat|married", folded)
        )
    if intent == "relation_between":
        return bool(
            re.search(r"zwischen|between|verwandt|related|beziehung", folded)
        )
    if intent == "resolve_kinship":
        if plan.get("kinship_path") and (
            _question_names_relative(folded) or re.search(r"\bmama\b|\bpapa\b", folded)
        ):
            return True
        return bool(name)
    return False


def _drops_named_relative(question: str, plan: dict[str, Any]) -> bool:
    """True if the question names a relative but the plan targets the start person."""
    if plan.get("kinship_path") or plan.get("person_name") or plan.get("kind") or plan.get("kinship_set"):
        return False
    folded = _fold(question)
    if _is_about_self(folded):
        return False
    return _question_names_relative(folded)


def _detect_relative_kind(folded: str) -> str | None:
    if re.search(r"wie ?viele kinder|wieviele kinder|how many children|anzahl (der )?kinder", folded):
        return None
    if re.search(r"grossvaeter|grandfathers|\bopas\b|\bgrandpas\b", folded):
        return "grandfathers"
    if re.search(r"grossmuetter|grandmothers|\bomas\b|\bgrandmas\b", folded):
        return "grandmothers"
    if re.search(r"grosseltern|grandparents", folded):
        return "grandparents"
    if re.search(r"\bonkel\b|\buncles?\b", folded):
        return "uncles"
    if re.search(r"\btanten\b|\baunts?\b", folded):
        return "aunts"
    if re.search(r"\bbrueder\b|\bbrothers\b", folded):
        return "brothers"
    if re.search(r"\bschwestern\b|\bsisters\b", folded):
        return "sisters"
    if re.search(r"geschwister|\bsiblings\b", folded):
        return "siblings"
    if re.search(r"\beltern\b|\bparents\b", folded):
        return "parents"
    if re.search(r"enkelkinder|\benkel\b|grandchildren", folded):
        return "grandchildren"
    if re.search(
        r"wie heissen die kinder|wer sind die kinder|welche kinder|"
        r"list(e)? (the )?children|kinder auflisten|the children of",
        folded,
    ):
        return "children"
    if re.search(r"ehepartner|\bspouses\b", folded):
        return "spouses"
    return None


def _extract_of_person_name(question: str) -> str:
    """Name after 'von' / 'of', if it looks like a person rather than a relative."""
    match = re.search(
        r"\b(?:von|of)\s+(.+?)(?:\?|$)",
        question,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    raw = " ".join(match.group(1).split()).strip(" .,!?")
    folded = _fold(raw)
    if not folded or folded in {"mir", "mich", "me", "uns", "ihm", "ihr"}:
        return ""
    if re.match(
        r"^(dem |der |den |des |the |my |meiner |meinem |meinen |meines )?"
        r"(grossmutter|grossvater|grossvaeter|grossmuetter|grosseltern|"
        r"oma|opa|mutter|vater|grandmother|grandfather|grandmothers|"
        r"grandfathers|grandparents|mother|father|eltern|parents|kinder|children|"
        r"onkel|tanten|geschwister|enkel)\b",
        folded,
    ):
        return ""
    if _detect_path(folded):
        return ""
    return raw


def _detect_intent(folded: str) -> str:
    if re.search(r"beziehung zwischen|verwandtschaft zwischen|relation between|how .*related", folded):
        return "relation_between"
    if re.search(r"wie ?viele kinder|wieviele kinder|how many children|anzahl (der )?kinder", folded):
        return "count_children"
    if re.search(r"welche kinder|wer sind die kinder|list(e)? (the )?children|kinder auflisten", folded):
        return "list_children"
    if re.search(
        r"wie alt|how old|years old|\balter\b|age of|welches alter",
        folded,
    ):
        return "person_age"
    if re.search(r"\bgeboren\b|\bgestorben\b|\bwann\b|\bbirth\b|\bdeath\b|\bgestorben\b", folded):
        if _detect_path(folded) or re.search(r"mein |my |der |die |von ", folded):
            return "person_facts"
    return "resolve_kinship"


def _extract_between_names(question: str) -> tuple[str, str]:
    match = re.search(
        r"(?:zwischen|between)\s+(.+?)\s+(?:und|and)\s+(.+?)(?:\?|$)",
        question,
        flags=re.IGNORECASE,
    )
    if not match:
        return "", ""
    return match.group(1).strip(" .,!?"), match.group(2).strip(" .,!?")


def parse_question_rules(question: str) -> dict[str, Any] | None:
    """Return a plan dict if the question matches a known pattern, else None."""
    folded = _fold(question)
    if not folded:
        return None

    path = _detect_path(folded)
    intent = _detect_intent(folded)
    kind = _detect_relative_kind(folded)
    person_name = _extract_of_person_name(question)
    target_name = ""

    if intent == "relation_between":
        person_name, target_name = _extract_between_names(question)
        if not person_name or not target_name:
            return None
        path = []
    elif kind:
        overlap = {
            "spouses": {"spouse"},
            "children": {"child"},
            "siblings": {"sibling"},
            "brothers": {"sibling"},
            "sisters": {"sibling"},
        }
        if path and set(path) <= overlap.get(kind, set()):
            path = []
        return {
            "intent": "list_relatives",
            "kind": kind,
            "kinship_path": path,
            "person_name": person_name,
            "target_name": "",
            "anchor": "starting_individual",
        }
    elif not path and intent == "resolve_kinship":
        return None
    elif not path and intent in {
        "count_children",
        "list_children",
        "person_facts",
        "person_age",
    }:
        # "meine andere Großmutter" contains "meine" but is not the start person.
        if not _is_about_self(folded):
            return None

    return {
        "intent": intent,
        "kinship_path": path,
        "person_name": person_name,
        "target_name": target_name,
        "anchor": "starting_individual",
    }


def sanitize_llm_plan(raw_plan: dict[str, Any]) -> dict[str, Any]:
    """Drop model-invented ids; keep names and kinship_path."""
    return {
        "intent": raw_plan.get("intent"),
        "kinship_path": raw_plan.get("kinship_path") or [],
        "kind": raw_plan.get("kind") or raw_plan.get("kinship_set") or None,
        "kinship_set": raw_plan.get("kind") or raw_plan.get("kinship_set") or None,
        "person_name": str(raw_plan.get("person_name") or "").strip(),
        "target_name": str(raw_plan.get("target_name") or "").strip(),
        "anchor": "starting_individual",
        "person_id": None,
        "target_id": None,
    }


def parse_natural_language_question(question: str) -> dict[str, Any]:
    """
    Convert a question into a validated tree_query plan.

    Returns ``{ok, error, source, plan}``. ``source`` is ``rules`` or ``llm``.
    """
    question = (question or "").strip()
    if not question:
        return {
            "ok": False,
            "error": _("Bitte eine Frage eingeben."),
            "source": None,
            "plan": None,
        }

    rules_plan = parse_question_rules(question)
    if rules_plan:
        try:
            plan = parse_plan(rules_plan)
        except TreeQueryError as exc:
            return {"ok": False, "error": str(exc), "source": "rules", "plan": None}
        return {"ok": True, "error": None, "source": "rules", "plan": plan}

    folded = _fold(question)
    if not _looks_like_tree_query(folded):
        return {
            "ok": False,
            "error": _off_topic_error(),
            "source": None,
            "plan": None,
        }

    if not llm_parser_enabled():
        return {
            "ok": False,
            "error": _(
                "Diese Frage konnte nicht automatisch zerlegt werden. "
                "Bitte die strukturierte Abfrage nutzen oder Ollama konfigurieren."
            ),
            "source": None,
            "plan": None,
        }

    llm = parse_question_via_llm(question)
    if llm["error"] or not llm["plan"]:
        return {
            "ok": False,
            "error": llm["error"]
            or _("Das Sprachmodell lieferte keinen gültigen Plan."),
            "source": "llm",
            "plan": None,
        }

    raw = sanitize_llm_plan(llm["plan"])
    if str(raw.get("intent") or "").strip().lower() in _UNSUPPORTED_INTENTS:
        return {
            "ok": False,
            "error": _off_topic_error(),
            "source": "llm",
            "plan": None,
        }

    kind = _detect_relative_kind(_fold(question))
    if (
        kind
        and raw.get("intent") == "relation_between"
        and not raw.get("target_name")
    ):
        raw["intent"] = "list_relatives"
        raw["kind"] = kind
        raw["kinship_path"] = raw.get("kinship_path") or []
        raw["target_name"] = ""
        if not raw.get("person_name"):
            raw["person_name"] = _extract_of_person_name(question)

    try:
        plan = parse_plan(raw)
    except TreeQueryError as exc:
        return {"ok": False, "error": str(exc), "source": "llm", "plan": None}

    if _drops_named_relative(question, plan):
        return {
            "ok": False,
            "error": _(
                "Die Frage bezieht sich auf eine Verwandte, die nicht eindeutig "
                "zugeordnet wurde. Bitte mütterlicherseits oder väterlicherseits angeben."
            ),
            "source": "llm",
            "plan": None,
        }
    if not _plan_fits_question(question, plan):
        return {
            "ok": False,
            "error": _(
                "Die Frage konnte nicht eindeutig einer Stammbaum-Abfrage zugeordnet werden. "
                "Bitte die Verwandtschaft genauer benennen."
            ),
            "source": "llm",
            "plan": None,
        }

    if plan["intent"] not in ALLOWED_INTENTS:
        return {
            "ok": False,
            "error": _("Unbekannte Anfrageart vom Sprachmodell."),
            "source": "llm",
            "plan": None,
        }
    return {"ok": True, "error": None, "source": "llm", "plan": plan}
