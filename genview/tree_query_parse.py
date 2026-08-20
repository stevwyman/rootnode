"""Natural-language → tree_query plan (Phase 2).

Heuristics match incoming German and English questions via
``tree_query_lexicon`` (canonical English keys, surface synonyms).
``_()`` is only for outgoing errors. Anything unmatched goes to the LLM;
the executor still validates the plan.
"""

from __future__ import annotations

import re
from typing import Any

from django.utils.translation import gettext as _

from .llm_client import llm_parser_enabled, parse_question_via_llm
from .tree_query import ALLOWED_INTENTS, TreeQueryError, parse_plan
from .tree_query_capabilities import FANOUT_KINDS, FACT_FOCUS_VALUES
from .tree_query_lexicon import (
    AGE_INTENT,
    AGE_PLAN_FITS,
    BIRTH_FOCUS,
    CHILDREN_PLAN_FITS,
    COUNT_CHILDREN,
    DEATH_FOCUS,
    DEMONYM_PATTERN,
    KIND_DETECT_ORDER,
    KIND_DETECT_PATTERNS,
    KIND_QUESTION_PATTERNS,
    LIST_CHILDREN_INTENT,
    LIST_CHILDREN_KIND,
    MARRIAGE,
    NAME_FILTER_STOP,
    NAMED_PERSON_PATTERNS,
    OF_DETERMINER_PREFIX,
    PATH_PHRASES,
    PERSON_FACTS,
    PLACE_CUE_PATTERNS,
    PLACE_END_WORDS,
    PLACE_LEADING_ARTICLES,
    PLACE_QUOTES,
    PLACE_STOPWORDS,
    PLACE_STRIP_PREP,
    PRONOUNS,
    QUESTION_NAMES_RELATIVE,
    RELATION_BETWEEN,
    RELATION_BETWEEN_CUES,
    RELATIVE_DETERMINER_PREFIX,
    RELATIVE_NOUNS_ALT,
    RELATIVE_NOUNS_BOUNDED,
    SELF_CUES,
    START_PERSON,
    TREE_QUERY_HINT,
    alt,
)

_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_PLACE_QUOTE_RE = re.compile(
    rf"^[{re.escape(PLACE_QUOTES)}](.+?)[{re.escape(PLACE_QUOTES)}]"
)
_PLACE_WORD_RE = re.compile(r"[A-Za-zÄÖÜäöüß][\w.\-äöüß']*")


def _fold(text: str) -> str:
    folded = (text or "").lower().translate(_UMLAUT)
    folded = re.sub(r"[?!.,:;]+", " ", folded)
    folded = folded.replace("'", "")
    return " ".join(folded.split())


def _detect_path(folded: str) -> list[str]:
    for phrase, path in PATH_PHRASES:
        if phrase in folded:
            return list(path)
    return []


def _is_about_self(folded: str) -> bool:
    """True if the subject is the tree starting person, not a relative."""
    return bool(re.search(alt(SELF_CUES, bounded=True), folded))


def _question_names_relative(folded: str) -> bool:
    return bool(re.search(QUESTION_NAMES_RELATIVE, folded))


def _looks_like_tree_query(folded: str) -> bool:
    """True if the question is about kinship or person facts in the tree."""
    if _detect_path(folded) or _detect_relative_kind(folded):
        return True
    return bool(re.search(TREE_QUERY_HINT, folded))


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
    name_filter = str(plan.get("name_filter") or "").strip()
    if name_filter and not _name_mentioned(folded, name_filter):
        return False

    if intent in {"count_children", "list_children"}:
        return bool(re.search(CHILDREN_PLAN_FITS, folded))
    if intent == "list_relatives":
        kind = str(plan.get("kind") or plan.get("kinship_set") or "")
        cue = KIND_QUESTION_PATTERNS.get(kind)
        return bool(cue and re.search(cue, folded))
    if intent == "person_age":
        return bool(re.search(AGE_PLAN_FITS, folded))
    if intent == "person_facts":
        return bool(re.search(PERSON_FACTS, folded))
    if intent == "relation_between":
        return bool(re.search(RELATION_BETWEEN_CUES, folded))
    if intent == "resolve_kinship":
        if plan.get("kinship_path") and (
            _question_names_relative(folded)
            or re.search(r"\bmama\b|\bpapa\b", folded)
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
    if re.search(COUNT_CHILDREN, folded):
        return None
    for kind in KIND_DETECT_ORDER:
        if kind == "spouses":
            continue
        if re.search(KIND_DETECT_PATTERNS[kind], folded):
            return kind
    if re.search(LIST_CHILDREN_KIND, folded):
        return "children"
    if re.search(KIND_DETECT_PATTERNS["spouses"], folded):
        return "spouses"
    return None


def _detect_named_person_relative(folded: str) -> list[str]:
    """Singular relative in 'Vater von NAME' / 'father of NAME' (word boundaries)."""
    for pattern, path in NAMED_PERSON_PATTERNS:
        if re.search(pattern, folded):
            return list(path)
    return []


def _extract_of_person_name(question: str) -> str:
    """Name after 'von' / 'of', if it looks like a person rather than a relative."""
    match = re.search(
        rf"\b(?:{alt(('von', 'of'))})\s+(.+?)(?:\?|$)",
        question,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    raw = " ".join(match.group(1).split()).strip(" .,!?\"'«»“”")
    folded = _fold(raw)
    if not folded or folded in PRONOUNS:
        return ""
    if re.search(alt(START_PERSON, bounded=True), folded):
        return ""
    if re.match(rf"^{OF_DETERMINER_PREFIX}{RELATIVE_NOUNS_BOUNDED}", folded):
        return ""
    if _detect_path(folded):
        return ""
    return raw


def _detect_intent(folded: str) -> str:
    if re.search(RELATION_BETWEEN, folded):
        return "relation_between"
    if re.search(COUNT_CHILDREN, folded):
        return "count_children"
    if re.search(LIST_CHILDREN_INTENT, folded):
        return "list_children"
    if re.search(AGE_INTENT, folded):
        return "person_age"
    if re.search(PERSON_FACTS, folded):
        return "person_facts"
    return "resolve_kinship"


def _detect_fact_focus(folded: str) -> str:
    if re.search(DEATH_FOCUS, folded):
        return "death"
    if re.search(BIRTH_FOCUS, folded):
        return "birth"
    if re.search(alt(MARRIAGE, bounded=True), folded):
        return "marriage"
    return ""


def _clean_extracted_name(raw: str) -> str:
    return " ".join((raw or "").split()).strip(" .,!?\"'«»“”")


def _name_is_relative_or_pronoun(raw: str) -> bool:
    folded = _fold(raw)
    if not folded or folded in PRONOUNS:
        return True
    if re.search(alt(START_PERSON, bounded=True), folded):
        return True
    if _detect_path(folded) or _detect_named_person_relative(folded):
        return True
    return bool(
        re.match(rf"^{RELATIVE_DETERMINER_PREFIX}{RELATIVE_NOUNS_BOUNDED}", folded)
    )


def _extract_subject_person_name(question: str) -> str:
    """Person the question is about, e.g. 'Wie alt ist NAME?'."""
    patterns = (
        r"wie alt (?:ist|war|sind|waren)\s+(.+?)(?:\?|$)",
        r"how old (?:is|was|are|were)\s+(.+?)(?:\?|$)",
        r"(?:age of|alter von)\s+(.+?)(?:\?|$)",
        rf"wann (?:wurde|ist|war)\s+(.+?)\s+{alt(('geboren', 'gestorben'))}",
        rf"when was\s+(.+?)\s+{alt(('born', 'died'))}",
    )
    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if not match:
            continue
        raw = _clean_extracted_name(match.group(1))
        raw = re.sub(
            rf"^{alt(('der', 'die', 'das', 'the'))}\s+",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip()
        if raw and not _name_is_relative_or_pronoun(raw):
            return raw
    return ""


def _extract_between_names(question: str) -> tuple[str, str]:
    patterns = (
        rf"(?:{alt(('zwischen', 'between'))})\s+(.+?)\s+(?:{alt(('und', 'and'))})\s+(.+?)(?:\?|$)",
        rf"(?:wie sind|how are)\s+(.+?)\s+(?:{alt(('und', 'and'))})\s+(.+?)\s+(?:{alt(('verwandt', 'related'))})",
        rf"(?:wie ist|how is)\s+(.+?)\s+(?:{alt(('mit', 'to'))})\s+(.+?)\s*(?:{alt(('verwandt', 'related'))})?",
    )
    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if match:
            left = _clean_extracted_name(match.group(1))
            right = _clean_extracted_name(match.group(2))
            if left and right:
                return left, right
    return "", ""


def _looks_like_place_token(token: str) -> bool:
    folded = _fold(token)
    if not folded or folded in PLACE_STOPWORDS:
        return False
    if re.match(rf"^{RELATIVE_NOUNS_BOUNDED}$", folded):
        return False
    return True


def _is_place_end_token(token: str) -> bool:
    folded = _fold(token)
    if not folded or folded in PLACE_END_WORDS or folded in PLACE_STOPWORDS:
        return True
    return bool(re.match(rf"^{RELATIVE_NOUNS_BOUNDED}$", folded))


def _extract_place_from_rest(rest: str) -> str:
    rest = (rest or "").lstrip()
    if not rest:
        return ""
    quoted = _PLACE_QUOTE_RE.match(rest)
    if quoted:
        raw = _clean_extracted_name(quoted.group(1))
        first = raw.split()[0] if raw else ""
        if raw and _looks_like_place_token(first):
            return raw
        return ""
    collected: list[str] = []
    for token in _PLACE_WORD_RE.findall(rest):
        folded = _fold(token)
        if not collected and folded in PLACE_LEADING_ARTICLES:
            continue
        if collected and _is_place_end_token(token):
            break
        if not collected and _is_place_end_token(token):
            return ""
        if not _looks_like_place_token(token):
            if collected:
                break
            continue
        collected.append(token)
        if len(collected) >= 4:
            break
    return " ".join(collected)


def _extract_relative_name_filter(question: str) -> str:
    """Given name after a relative noun: 'Onkel Albert', 'uncle Albert'."""
    text = " ".join((question or "").split())
    if not text:
        return ""
    match = re.search(
        rf"(?i)\b{RELATIVE_NOUNS_ALT}\s+"
        rf"(?-i:([A-ZÄÖÜ][\w\-äöüß']+(?:\s+[A-ZÄÖÜ][\w\-äöüß']+){{0,2}}))",
        text,
    )
    if not match:
        return ""
    raw = _clean_extracted_name(match.group(1))
    first = raw.split()[0] if raw else ""
    folded_first = _fold(first)
    if not first or folded_first in PLACE_STOPWORDS or folded_first in NAME_FILTER_STOP:
        return ""
    if re.match(rf"^{RELATIVE_NOUNS_BOUNDED}$", folded_first):
        return ""
    return raw


def _extract_place_filter(question: str) -> str:
    """Place mentioned as a discriminator, e.g. 'aus Berlin' / 'from "York cottage"'."""
    text = " ".join((question or "").split())
    if not text:
        return ""
    demonym = re.search(DEMONYM_PATTERN, text)
    if demonym:
        stem = demonym.group(1)
        if _looks_like_place_token(stem):
            return stem
    for cue in PLACE_CUE_PATTERNS:
        match = re.search(cue, text, flags=re.IGNORECASE)
        if not match:
            continue
        raw = _extract_place_from_rest(text[match.end() :])
        if raw:
            return raw
    return ""


def _strip_place_from_name(name: str, place_filter: str) -> str:
    if not name or not place_filter:
        return name
    return re.sub(
        rf"\s+{PLACE_STRIP_PREP}\s+{re.escape(place_filter)}\s*$",
        "",
        name,
        flags=re.IGNORECASE,
    ).strip(" .,!?\"'«»“”")


def _rule_plan(
    *,
    intent: str,
    path: list[str],
    kind: str | None = None,
    person_name: str = "",
    target_name: str = "",
    place_filter: str = "",
    name_filter: str = "",
    fact_focus: str = "",
) -> dict[str, Any]:
    return {
        "intent": intent,
        "kinship_path": path,
        "kind": kind,
        "person_name": person_name,
        "target_name": target_name,
        "place_filter": place_filter,
        "name_filter": name_filter,
        "fact_focus": fact_focus,
        "anchor": "starting_individual",
    }


def parse_question_rules(question: str) -> dict[str, Any] | None:
    """Return a plan dict if the question matches a known pattern, else None."""
    folded = _fold(question)
    if not folded:
        return None

    path = _detect_path(folded)
    intent = _detect_intent(folded)
    kind = _detect_relative_kind(folded)
    place_filter = _extract_place_filter(question)
    fact_focus = _detect_fact_focus(folded)
    name_filter = _extract_relative_name_filter(question)
    person_name = _strip_place_from_name(_extract_of_person_name(question), place_filter)
    target_name = ""
    if not path and not kind:
        path = _detect_named_person_relative(folded)
    if not person_name and intent in {"person_age", "person_facts"}:
        person_name = _strip_place_from_name(
            _extract_subject_person_name(question), place_filter
        )
    if name_filter and person_name and _fold(name_filter) in _fold(person_name):
        person_name = ""

    if intent == "relation_between":
        person_name, target_name = _extract_between_names(question)
        if not person_name or not target_name:
            return None
        path = []
        return _rule_plan(
            intent=intent,
            path=path,
            person_name=person_name,
            target_name=target_name,
            place_filter=place_filter,
            name_filter=name_filter,
            fact_focus=fact_focus,
        )
    elif kind and intent in {"person_facts", "person_age"}:
        overlap = {
            "spouses": {"spouse"},
            "children": {"child"},
            "siblings": {"sibling"},
            "brothers": {"sibling"},
            "sisters": {"sibling"},
        }
        if path and set(path) <= overlap.get(kind, set()):
            path = []
        return _rule_plan(
            intent=intent,
            path=path,
            kind=kind,
            person_name=person_name,
            place_filter=place_filter,
            name_filter=name_filter,
            fact_focus=fact_focus,
        )
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
        return _rule_plan(
            intent="list_relatives",
            path=path,
            kind=kind,
            person_name=person_name,
            place_filter=place_filter,
            name_filter=name_filter,
            fact_focus=fact_focus,
        )
    elif not path and intent == "resolve_kinship":
        return None
    elif not path and intent in {
        "count_children",
        "list_children",
        "person_facts",
        "person_age",
    }:
        # "meine andere Großmutter" contains "meine" but is not the start person.
        if not person_name and not _is_about_self(folded):
            return None

    return _rule_plan(
        intent=intent,
        path=path,
        person_name=person_name,
        target_name=target_name,
        place_filter=place_filter,
        name_filter=name_filter,
        fact_focus=fact_focus,
    )


def sanitize_llm_plan(raw_plan: dict[str, Any]) -> dict[str, Any]:
    """Drop model-invented ids; keep names and kinship_path."""
    fact_focus = str(raw_plan.get("fact_focus") or "").strip().lower()
    if fact_focus not in FACT_FOCUS_VALUES:
        fact_focus = ""
    return {
        "intent": raw_plan.get("intent"),
        "kinship_path": raw_plan.get("kinship_path") or [],
        "kind": raw_plan.get("kind") or raw_plan.get("kinship_set") or None,
        "kinship_set": raw_plan.get("kind") or raw_plan.get("kinship_set") or None,
        "person_name": str(raw_plan.get("person_name") or "").strip(),
        "target_name": str(raw_plan.get("target_name") or "").strip(),
        "place_filter": str(raw_plan.get("place_filter") or "").strip(" .,!?\"'«»“”„"),
        "name_filter": str(raw_plan.get("name_filter") or "").strip(),
        "fact_focus": fact_focus,
        "anchor": "starting_individual",
        "person_id": None,
        "target_id": None,
    }


def rules_plan_is_certain(question: str, plan: dict[str, Any] | None) -> bool:
    """True when heuristics uniquely fill the capability template (no LLM needed)."""
    if not plan:
        return False
    if plan.get("place_filter") or plan.get("name_filter"):
        return False
    kind = plan.get("kind")
    intent = str(plan.get("intent") or "")
    if kind in FANOUT_KINDS and intent in {
        "person_facts",
        "person_age",
        "resolve_kinship",
    }:
        return False
    if kind in FANOUT_KINDS and plan.get("fact_focus"):
        return False
    return True


def _ok_plan(source: str, payload: dict[str, Any]) -> dict[str, Any]:
    plan = parse_plan(payload)
    return {"ok": True, "error": None, "source": source, "plan": plan}


def _refine_llm_raw(question: str, raw: dict[str, Any]) -> dict[str, Any]:
    folded_q = _fold(question)
    detected_intent = _detect_intent(folded_q)
    detected_kind = _detect_relative_kind(folded_q)
    place_filter = _extract_place_filter(question)
    fact_focus = _detect_fact_focus(folded_q)
    name_filter = _extract_relative_name_filter(question)
    if place_filter:
        current = str(raw.get("place_filter") or "").strip(" .,!?\"'«»“”„")
        if not current or _fold(current) in _fold(place_filter):
            raw["place_filter"] = place_filter
    if fact_focus and not raw.get("fact_focus"):
        raw["fact_focus"] = fact_focus
    if name_filter and not raw.get("name_filter"):
        raw["name_filter"] = name_filter
    if raw.get("person_name"):
        raw["person_name"] = _strip_place_from_name(
            str(raw.get("person_name") or ""), raw.get("place_filter") or ""
        )
    of_name = _extract_of_person_name(question)
    kind = raw.get("kind") or detected_kind
    person_name = str(raw.get("person_name") or "").strip()
    if kind in FANOUT_KINDS and person_name and not of_name:
        if not raw.get("name_filter"):
            raw["name_filter"] = person_name
        raw["person_name"] = ""
    if detected_kind and not raw.get("kind") and not raw.get("kinship_set"):
        raw["kind"] = detected_kind
    if (
        detected_intent in {"person_facts", "person_age"}
        and (raw.get("kind") or detected_kind)
        and raw.get("intent") in {"list_relatives", "list_kinship", "resolve_kinship"}
    ):
        raw["intent"] = detected_intent
        if not raw.get("kind"):
            raw["kind"] = detected_kind
    if (
        raw.get("intent") == "relation_between"
        and not re.search(
            RELATION_BETWEEN_CUES, folded_q
        )
    ):
        named_path = _detect_path(folded_q) or _detect_named_person_relative(folded_q)
        of_name = _extract_of_person_name(question)
        if named_path and of_name:
            raw["intent"] = "resolve_kinship"
            raw["kinship_path"] = named_path
            raw["kind"] = None
            raw["kinship_set"] = None
            raw["person_name"] = of_name
            raw["target_name"] = ""
    kind = _detect_relative_kind(folded_q)
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
    return raw


def parse_natural_language_question(question: str) -> dict[str, Any]:
    """
    Convert a question into a validated tree_query plan.

    Unambiguous templates are filled by heuristics. Anything with a place
    or name filter, or a group relative plus a date, goes to the LLM, which
    fills the capability template (optionally starting from the heuristic draft).
    """
    question = (question or "").strip()
    if not question:
        return {
            "ok": False,
            "error": _("Bitte eine Frage eingeben."),
            "source": None,
            "plan": None,
        }

    folded = _fold(question)
    rules_plan = parse_question_rules(question)
    if rules_plan_is_certain(question, rules_plan):
        try:
            return _ok_plan("rules", rules_plan)
        except TreeQueryError as exc:
            return {"ok": False, "error": str(exc), "source": "rules", "plan": None}

    if not rules_plan and not _looks_like_tree_query(folded):
        return {
            "ok": False,
            "error": _off_topic_error(),
            "source": None,
            "plan": None,
        }

    if llm_parser_enabled():
        llm = parse_question_via_llm(question, draft=rules_plan)
        if llm["error"] or not llm["plan"]:
            if rules_plan:
                try:
                    return _ok_plan("rules", rules_plan)
                except TreeQueryError:
                    pass
            return {
                "ok": False,
                "error": llm["error"]
                or _("Das Sprachmodell lieferte keinen gültigen Plan."),
                "source": "llm",
                "plan": None,
            }

        raw = _refine_llm_raw(question, sanitize_llm_plan(llm["plan"]))
        if str(raw.get("intent") or "").strip().lower() in _UNSUPPORTED_INTENTS:
            return {
                "ok": False,
                "error": _off_topic_error(),
                "source": "llm",
                "plan": None,
            }
        try:
            plan = parse_plan(raw)
        except TreeQueryError as exc:
            if rules_plan:
                try:
                    return _ok_plan("rules", rules_plan)
                except TreeQueryError:
                    pass
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
            if rules_plan:
                try:
                    return _ok_plan("rules", rules_plan)
                except TreeQueryError:
                    pass
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

    if rules_plan:
        try:
            return _ok_plan("rules", rules_plan)
        except TreeQueryError as exc:
            return {"ok": False, "error": str(exc), "source": "rules", "plan": None}

    return {
        "ok": False,
        "error": _(
            "Diese Frage konnte nicht automatisch zerlegt werden. "
            "Bitte die strukturierte Abfrage nutzen oder Ollama konfigurieren."
        ),
        "source": None,
        "plan": None,
    }
