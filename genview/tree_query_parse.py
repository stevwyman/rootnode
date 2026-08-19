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
from .tree_query_capabilities import FANOUT_KINDS, FACT_FOCUS_VALUES

_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

_RELATIVE_NOUNS = (
    r"urgrossmutter|urgrossvater|grossmutter|grossvater|grossvaeter|grossmuetter|"
    r"grosseltern|oma|opa|mutter|vater|grandmother|grandfather|grandmothers|"
    r"grandfathers|grandparents|mother|father|eltern|parents|kinder|children|"
    r"onkel|tante|tanten|uncle|aunt|uncles|aunts|geschwister|sibling|siblings|"
    r"bruder|schwester|brother|sister|enkel|sohn|tochter|son|daughter"
)

_PLACE_STOPWORDS = frozenset(
    {
        "dem",
        "der",
        "den",
        "des",
        "die",
        "das",
        "the",
        "a",
        "an",
        "my",
        "his",
        "her",
        "meiner",
        "meinem",
        "meinen",
        "meines",
        "jahr",
        "jahre",
        "stammbaum",
    }
)

# Longest phrases first.
_PATH_PHRASES: list[tuple[str, list[str]]] = [
    # --- Great-Grandparents (Urgroßeltern) ---
    ("urgrossmutter muetterlicherseits", ["mother", "mother", "mother"]),
    ("urgrossvater vaeterlicherseits", ["father", "father", "father"]),
    ("great grandmother on my mothers side", ["mother", "mother", "mother"]),
    ("great grandfather on my fathers side", ["father", "father", "father"]),

    # --- Grandparents (Großeltern) ---
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

    # --- Uncles and Aunts (Onkel und Tanten) ---
    ("onkel muetterlicherseits", ["mother", "brother"]),
    ("bruder meiner mutter", ["mother", "brother"]),
    ("bruder der mutter", ["mother", "brother"]),
    ("maternal uncle", ["mother", "brother"]),
    ("uncle on my mothers side", ["mother", "brother"]),
    
    ("onkel vaeterlicherseits", ["father", "brother"]),
    ("bruder meines vaters", ["father", "brother"]),
    ("bruder des vaters", ["father", "brother"]),
    ("paternal uncle", ["father", "brother"]),
    ("uncle on my fathers side", ["father", "brother"]),

    ("tante muetterlicherseits", ["mother", "sister"]),
    ("schwester meiner mutter", ["mother", "sister"]),
    ("schwester der mutter", ["mother", "sister"]),
    ("maternal aunt", ["mother", "sister"]),
    ("aunt on my mothers side", ["mother", "sister"]),
    
    ("tante vaeterlicherseits", ["father", "sister"]),
    ("schwester meines vaters", ["father", "sister"]),
    ("schwester des vaters", ["father", "sister"]),
    ("paternal aunt", ["father", "sister"]),
    ("aunt on my fathers side", ["father", "sister"]),

    # --- Unspecified "other" relatives ---
    ("andere grossmutter", ["father", "mother"]),
    ("other grandmother", ["father", "mother"]),
    ("andere oma", ["father", "mother"]),
    ("other grandma", ["father", "mother"]),
    ("anderer grossvater", ["mother", "father"]),
    ("andere grossvater", ["mother", "father"]),
    ("other grandfather", ["mother", "father"]),
    ("anderer opa", ["mother", "father"]),

    # --- Direct family members (Eltern, Geschwister, Partner) ---
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
    
    ("mein bruder", ["brother"]),
    ("my brother", ["brother"]),
    ("meine schwester", ["sister"]),
    ("my sister", ["sister"]),
    
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
            r"geboren|gestorben|\bstarb\b|verstorben|"
            r"\bbirth\b|\bdeath\b|\bdied\b|\bdead\b|\bborn\b|"
            r"when did|wann (?:ist|war|wurde|starb)|"
            r"geburtsdatum|geburtstag|sterbedatum|"
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
            re.search(
                r"geboren|gestorben|\bstarb\b|verstorben|"
                r"\bbirth\b|\bdeath\b|\bdied\b|\bdead\b|\bborn\b|"
                r"when did|wann (?:ist|war|wurde|starb)|"
                r"heirat|married|"
                r"geburtsdatum|geburtstag|sterbedatum|todestag|"
                r"birthdate|birth date|date of birth|date of death|death date",
                folded,
            )
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


_NAMED_PERSON_RELATIVE: list[tuple[str, list[str]]] = [
    (r"\burgrossmutter\b", ["mother", "mother", "mother"]),
    (r"\burgrossvater\b", ["father", "father", "father"]),
    (r"\bgrossmutter\b|\bgrandmother\b", ["mother", "mother"]),
    (r"\bgrossvater\b|\bgrandfather\b", ["father", "father"]),
    (r"\bmutter(?:s)?\b|\bmother\b|\bmama\b|\bmom\b|\bmum\b", ["mother"]),
    (r"\bvater(?:s)?\b|\bfather\b|\bpapa\b|\bdad\b|\bdaddy\b", ["father"]),
    (r"\behefrau\b|\behemann\b|\behepartner\b|\bwife\b|\bhusband\b|\bspouse\b", ["spouse"]),
    (r"\bschwester\b|\bsister\b", ["sister"]),
    (r"\bbruder\b|\bbrother\b", ["brother"]),
    (r"\bsohn\b|\btochter\b|\bson\b|\bdaughter\b", ["child"]),
]


def _detect_named_person_relative(folded: str) -> list[str]:
    """Singular relative in 'Vater von NAME' / 'father of NAME' (word boundaries)."""
    for pattern, path in _NAMED_PERSON_RELATIVE:
        if re.search(pattern, folded):
            return list(path)
    return []


def _extract_of_person_name(question: str) -> str:
    """Name after 'von' / 'of', if it looks like a person rather than a relative."""
    match = re.search(
        r"\b(?:von|of)\s+(.+?)(?:\?|$)",
        question,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    raw = " ".join(match.group(1).split()).strip(" .,!?\"'«»“”")
    folded = _fold(raw)
    if not folded or folded in {"mir", "mich", "me", "uns", "ihm", "ihr"}:
        return ""
    if re.search(r"startperson|starting person", folded):
        return ""
    if re.match(
        rf"^(dem |der |den |des |the |my |mein |meine |meiner |meinem |meinen |meines )?"
        rf"({_RELATIVE_NOUNS})\b",
        folded,
    ):
        return ""
    if _detect_path(folded):
        return ""
    return raw


def _detect_intent(folded: str) -> str:
    if re.search(
        r"beziehung zwischen|verwandtschaft zwischen|relation between|"
        r"how (?:are|is) .*related|wie sind .+ verwandt|wie ist .+ verwandt",
        folded,
    ):
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
    if re.search(
        r"geboren|gestorben|\bstarb\b|verstorben|"
        r"\bbirth\b|\bdeath\b|\bdied\b|\bdead\b|\bborn\b|"
        r"when did|wann (?:ist|war|wurde|starb)|"
        r"geburtsdatum|geburtstag|sterbedatum|todestag|"
        r"birthdate|birth date|date of birth|date of death|death date",
        folded,
    ):
        return "person_facts"
    return "resolve_kinship"


def _detect_fact_focus(folded: str) -> str:
    if re.search(
        r"gestorben|\bstarb\b|verstorben|\bdied\b|\bdead\b|"
        r"sterbedatum|todestag|date of death|death date|"
        r"when did .+\bdie\b",
        folded,
    ):
        return "death"
    if re.search(
        r"geboren|geburtsdatum|geburtstag|birthdate|\bborn\b|\bbirth\b|"
        r"date of birth|birth date",
        folded,
    ):
        return "birth"
    if re.search(r"heirat|married|hochzeit|marriage", folded):
        return "marriage"
    return ""


def _clean_extracted_name(raw: str) -> str:
    return " ".join((raw or "").split()).strip(" .,!?\"'«»“”")


def _name_is_relative_or_pronoun(raw: str) -> bool:
    folded = _fold(raw)
    if not folded or folded in {"mir", "mich", "me", "uns", "ihm", "ihr", "ich", "i"}:
        return True
    if re.search(r"startperson|starting person", folded):
        return True
    if _detect_path(folded) or _detect_named_person_relative(folded):
        return True
    return bool(
        re.match(
            rf"^(dem |der |den |des |die |das |the |my |mein |meine |meiner |meinem |meinen |meines )?"
            rf"({_RELATIVE_NOUNS})\b",
            folded,
        )
    )


def _extract_subject_person_name(question: str) -> str:
    """Person the question is about, e.g. 'Wie alt ist NAME?'."""
    patterns = (
        r"wie alt (?:ist|war|sind|waren)\s+(.+?)(?:\?|$)",
        r"how old (?:is|was|are|were)\s+(.+?)(?:\?|$)",
        r"(?:age of|alter von)\s+(.+?)(?:\?|$)",
        r"wann (?:wurde|ist|war)\s+(.+?)\s+(?:geboren|gestorben)",
        r"when was\s+(.+?)\s+(?:born|died)",
    )
    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if not match:
            continue
        raw = _clean_extracted_name(match.group(1))
        raw = re.sub(r"^(der|die|das|the)\s+", "", raw, flags=re.IGNORECASE).strip()
        if raw and not _name_is_relative_or_pronoun(raw):
            return raw
    return ""


def _extract_between_names(question: str) -> tuple[str, str]:
    patterns = (
        r"(?:zwischen|between)\s+(.+?)\s+(?:und|and)\s+(.+?)(?:\?|$)",
        r"(?:wie sind|how are)\s+(.+?)\s+(?:und|and)\s+(.+?)\s+(?:verwandt|related)",
        r"(?:wie ist|how is)\s+(.+?)\s+(?:mit|to)\s+(.+?)\s*(?:verwandt|related)?",
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
    if not folded or folded in _PLACE_STOPWORDS:
        return False
    if re.match(rf"^({_RELATIVE_NOUNS})$", folded):
        return False
    return True


def _extract_place_filter(question: str) -> str:
    """Place mentioned as a discriminator, e.g. 'aus Berlin' / 'from Berlin'."""
    text = " ".join((question or "").split())
    if not text:
        return ""
    demonym = re.search(
        r"\b([A-ZÄÖÜ][\w\-äöüß]*)er\s+(?:onkel|tante|uncle|aunt)s?\b",
        text,
    )
    if demonym:
        stem = demonym.group(1)
        if _looks_like_place_token(stem):
            return stem
    patterns = (
        r"\b(?:aus|from)\s+(?!dem\b|der\b|den\b|des\b|die\b|das\b|the\b|my\b|meiner\b|meinem\b)([A-Za-zÄÖÜäöüß][\w.\-äöüß]*(?:\s+[A-ZÄÖÜ][\w.\-äöüß]*){0,3})",
        r"\b(?:geboren|gestorben|born|died)\s+(?:in|at)\s+(?!dem\b|der\b|the\b)([A-Za-zÄÖÜäöüß][\w.\-äöüß]*(?:\s+[A-ZÄÖÜ][\w.\-äöüß]*){0,3})",
        r"\bin\s+([A-ZÄÖÜ][\w.\-äöüß]*(?:\s+[A-ZÄÖÜ][\w.\-äöüß]*){0,2})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        raw = _clean_extracted_name(match.group(1))
        raw = re.sub(
            rf"\s+({_RELATIVE_NOUNS}|geboren|gestorben|born|died)\b.*$",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip(" .,!?\"'«»“”")
        first = raw.split()[0] if raw else ""
        if raw and _looks_like_place_token(first):
            return raw
    return ""


def _strip_place_from_name(name: str, place_filter: str) -> str:
    if not name or not place_filter:
        return name
    return re.sub(
        rf"\s+(?:aus|from|in|of)\s+{re.escape(place_filter)}\s*$",
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
    fact_focus: str = "",
) -> dict[str, Any]:
    return {
        "intent": intent,
        "kinship_path": path,
        "kind": kind,
        "person_name": person_name,
        "target_name": target_name,
        "place_filter": place_filter,
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
    person_name = _strip_place_from_name(_extract_of_person_name(question), place_filter)
    target_name = ""
    if not path and not kind:
        path = _detect_named_person_relative(folded)
    if not person_name and intent in {"person_age", "person_facts"}:
        person_name = _strip_place_from_name(
            _extract_subject_person_name(question), place_filter
        )

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
        "place_filter": str(raw_plan.get("place_filter") or "").strip(),
        "fact_focus": fact_focus,
        "anchor": "starting_individual",
        "person_id": None,
        "target_id": None,
    }


def rules_plan_is_certain(question: str, plan: dict[str, Any] | None) -> bool:
    """True when heuristics uniquely fill the capability template (no LLM needed)."""
    if not plan:
        return False
    if plan.get("place_filter"):
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
    if place_filter and not raw.get("place_filter"):
        raw["place_filter"] = place_filter
    if fact_focus and not raw.get("fact_focus"):
        raw["fact_focus"] = fact_focus
    if raw.get("person_name"):
        raw["person_name"] = _strip_place_from_name(
            str(raw.get("person_name") or ""), raw.get("place_filter") or ""
        )
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
            r"zwischen|between|verwandt|related|beziehung", folded_q
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
    filter or a group relative plus a date goes to the LLM, which fills the
    capability template (optionally starting from the heuristic draft).
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
