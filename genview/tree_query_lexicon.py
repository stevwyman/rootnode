"""Input synonyms for tree-query parsing.

Canonical keys are English (same as the executor plan). Values are the
surface forms users type, already folded (ae/oe/ue, no apostrophes).

These are *not* gettext strings. ``_()`` translates outgoing UI text for the
current locale; this module matches incoming German *and* English questions
regardless of UI language.
"""

from __future__ import annotations

import re
from typing import Iterable

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def alt(words: Iterable[str], *, bounded: bool = False) -> str:
    """Join words into a regex alternation, longest first."""
    uniq: list[str] = []
    seen: set[str] = set()
    for word in sorted(words, key=len, reverse=True):
        if not word or word in seen:
            continue
        seen.add(word)
        uniq.append(re.escape(word))
    if not uniq:
        return r"(?!)"
    body = "|".join(uniq)
    if bounded:
        return rf"\b(?:{body})\b"
    return rf"(?:{body})"


def _prefix_alt(words: Iterable[str]) -> str:
    parts = [re.escape(word) + r" " for word in words if word]
    if not parts:
        return ""
    return "(?:" + "|".join(parts) + ")?"


# ---------------------------------------------------------------------------
# Function words
# ---------------------------------------------------------------------------

ARTICLES = (
    "the",
    "a",
    "an",
    "der",
    "die",
    "das",
    "dem",
    "den",
    "des",
)

POSSESSIVES = (
    "my",
    "his",
    "her",
    "mein",
    "meine",
    "meiner",
    "meinem",
    "meinen",
    "meines",
)

PREP_FROM = ("from", "aus")
PREP_OF = ("of", "von")
PREP_IN = ("in", "at")

CONJUNCTIONS = ("and", "und", "or", "oder")

QUESTION_WORDS = ("when", "wann", "how", "wie", "who", "wer")

AUXILIARIES = (
    "is",
    "was",
    "ist",
    "war",
    "wurde",
    "hat",
    "had",
)

PRONOUNS = ("i", "ich", "me", "mir", "mich", "uns", "ihm", "ihr")

SELF_CUES = (
    "ich",
    "habe ich",
    "hab ich",
    "bin ich",
    "war ich",
    "do i",
    "i have",
    "am i",
    "was i",
    "startperson",
    "starting person",
)

START_PERSON = ("startperson", "starting person")

# ---------------------------------------------------------------------------
# Facts (birth / death / marriage / age)
# ---------------------------------------------------------------------------

BIRTH = (
    "geboren",
    "geburtsdatum",
    "geburtstag",
    "born",
    "birth",
    "birthdate",
    "birth date",
    "date of birth",
)

DEATH = (
    "gestorben",
    "starb",
    "verstorben",
    "sterbedatum",
    "todestag",
    "died",
    "dead",
    "die",
    "death",
    "death date",
    "date of death",
)

MARRIAGE = ("heirat", "married", "hochzeit", "marriage")

AGE = ("wie alt", "how old", "years old", "alter", "age", "age of", "welches alter")

FACT_EVENT = ("geboren", "gestorben", "born", "died")

# ---------------------------------------------------------------------------
# Relative nouns (any mention of a relative word)
# ---------------------------------------------------------------------------

RELATIVE_NOUNS: dict[str, tuple[str, ...]] = {
    "great_grandmother": ("urgrossmutter",),
    "great_grandfather": ("urgrossvater",),
    "grandmother": ("grossmutter", "grandmother", "oma"),
    "grandfather": ("grossvater", "grandfather", "opa"),
    "grandmothers": ("grossmuetter", "grandmothers"),
    "grandfathers": ("grossvaeter", "grandfathers"),
    "grandparents": ("grosseltern", "grandparents"),
    "mother": ("mutter", "mother"),
    "father": ("vater", "father"),
    "parents": ("eltern", "parents"),
    "child": ("sohn", "tochter", "son", "daughter"),
    "children": ("kinder", "children"),
    "uncle": ("onkel", "uncle", "uncles"),
    "aunt": ("tante", "tanten", "aunt", "aunts"),
    "sibling": ("geschwister", "sibling", "siblings"),
    "brother": ("bruder", "brother"),
    "sister": ("schwester", "sister"),
    "grandchild": ("enkel",),
}

RELATIVE_NOUN_LIST: tuple[str, ...] = tuple(
    word for words in RELATIVE_NOUNS.values() for word in words
)

# ---------------------------------------------------------------------------
# Kind: detect in the question vs accept an LLM list_relatives plan
# ---------------------------------------------------------------------------

KIND_DETECT_ORDER = (
    "grandfathers",
    "grandmothers",
    "grandparents",
    "uncles",
    "aunts",
    "brothers",
    "sisters",
    "siblings",
    "parents",
    "grandchildren",
    "spouses",
)

# Tokens that select a group kind. Slightly stricter than RELATIVE_NOUNS
# (e.g. singular "tante" / "bruder" do not pick aunts / brothers).
KIND_DETECT: dict[str, tuple[str, ...]] = {
    "grandfathers": ("grossvaeter", "grandfathers", "opas", "grandpas"),
    "grandmothers": ("grossmuetter", "grandmothers", "omas", "grandmas"),
    "grandparents": ("grosseltern", "grandparents"),
    "uncles": ("onkel", "uncle", "uncles"),
    "aunts": ("tanten", "aunt", "aunts"),
    "brothers": ("brueder", "brothers"),
    "sisters": ("schwestern", "sisters"),
    "siblings": ("geschwister", "siblings"),
    "parents": ("eltern", "parents"),
    "grandchildren": ("enkelkinder", "enkel", "grandchildren"),
    "spouses": ("ehepartner", "spouses"),
}

KIND_QUESTION_CUES: dict[str, tuple[str, ...]] = {
    "grandfathers": ("grossvaeter", "grandfather", "grandfathers", "opas", "opa"),
    "grandmothers": ("grossmuetter", "grandmother", "grandmothers", "omas", "oma"),
    "grandparents": ("grosseltern", "grandparent", "grandparents"),
    "parents": ("eltern", "parents", "mutter", "vater", "mother", "father"),
    "children": ("kinder", "children", "sohn", "tochter", "son", "daughter"),
    "siblings": (
        "geschwister",
        "sibling",
        "siblings",
        "bruder",
        "schwester",
        "brother",
        "sister",
    ),
    "brothers": ("brueder", "bruder", "brother", "brothers"),
    "sisters": ("schwestern", "schwester", "sister", "sisters"),
    "spouses": (
        "ehepartner",
        "ehemann",
        "ehefrau",
        "spouse",
        "spouses",
        "husband",
        "wife",
    ),
    "uncles": ("onkel", "uncle", "uncles"),
    "aunts": ("tanten", "tante", "aunt", "aunts"),
    "grandchildren": ("enkel", "enkelkinder", "grandchild", "grandchildren"),
}

# Executor aliases: German/English surface → canonical kind.
KIND_CANONICAL = (
    "grandfathers",
    "grandmothers",
    "grandparents",
    "parents",
    "children",
    "siblings",
    "brothers",
    "sisters",
    "spouses",
    "uncles",
    "aunts",
    "grandchildren",
)

KIND_SURFACE_ALIASES = {
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

KIND_ALIASES: dict[str, str] = {key: key for key in KIND_CANONICAL}
KIND_ALIASES.update(KIND_SURFACE_ALIASES)

DEMONYM_RELATIVES = ("onkel", "tante", "uncle", "aunt")

# ---------------------------------------------------------------------------
# Kinship path phrases (longest / most specific groups first)
# ---------------------------------------------------------------------------

# (canonical path, folded phrases). First matching phrase wins.
PATH_INPUT: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("mother", "mother", "mother"),
        (
            "urgrossmutter muetterlicherseits",
            "great grandmother on my mothers side",
        ),
    ),
    (
        ("father", "father", "father"),
        (
            "urgrossvater vaeterlicherseits",
            "great grandfather on my fathers side",
        ),
    ),
    (
        ("mother", "mother"),
        (
            "grossmutter muetterlicherseits",
            "grandmother on my mothers side",
            "grandmother on mothers side",
            "grandmother on my mother's side",
            "muetterliche grossmutter",
            "mutter meiner mutter",
            "mutter der mutter",
        ),
    ),
    (
        ("father", "mother"),
        (
            "grossmutter vaeterlicherseits",
            "grandmother on my fathers side",
            "grandmother on fathers side",
            "vaeterliche grossmutter",
            "mutter meines vaters",
            "mutter des vaters",
        ),
    ),
    (
        ("mother", "father"),
        (
            "grossvater muetterlicherseits",
            "grandfather on my mothers side",
            "grandfather on mothers side",
            "muetterlicher grossvater",
            "vater meiner mutter",
            "vater der mutter",
        ),
    ),
    (
        ("father", "father"),
        (
            "grossvater vaeterlicherseits",
            "grandfather on my fathers side",
            "grandfather on fathers side",
            "fathers father",
            "father's father",
            "vaeterlicher grossvater",
            "vater meines vaters",
            "vater des vaters",
            "my father's father",
            "my fathers father",
        ),
    ),
    (
        ("mother", "brother"),
        (
            "onkel muetterlicherseits",
            "bruder meiner mutter",
            "bruder der mutter",
            "maternal uncle",
            "uncle on my mothers side",
        ),
    ),
    (
        ("father", "brother"),
        (
            "onkel vaeterlicherseits",
            "bruder meines vaters",
            "bruder des vaters",
            "paternal uncle",
            "uncle on my fathers side",
        ),
    ),
    (
        ("mother", "sister"),
        (
            "tante muetterlicherseits",
            "schwester meiner mutter",
            "schwester der mutter",
            "maternal aunt",
            "aunt on my mothers side",
        ),
    ),
    (
        ("father", "sister"),
        (
            "tante vaeterlicherseits",
            "schwester meines vaters",
            "schwester des vaters",
            "paternal aunt",
            "aunt on my fathers side",
        ),
    ),
    (
        ("father", "mother"),
        (
            "andere grossmutter",
            "other grandmother",
            "andere oma",
            "other grandma",
        ),
    ),
    (
        ("mother", "father"),
        (
            "anderer grossvater",
            "andere grossvater",
            "other grandfather",
            "anderer opa",
        ),
    ),
    (
        ("mother", "mother"),
        (
            "meine grossmutter",
            "my grandmother",
            "meine oma",
            "my grandma",
        ),
    ),
    (
        ("father", "father"),
        (
            "mein grossvater",
            "my grandfather",
            "mein opa",
            "my grandpa",
        ),
    ),
    (("mother",), ("meine mutter", "my mother")),
    (("father",), ("mein vater", "my father")),
    (("brother",), ("mein bruder", "my brother")),
    (("sister",), ("meine schwester", "my sister")),
    (
        ("spouse",),
        (
            "ehepartner",
            "ehemann",
            "ehefrau",
            "my spouse",
            "my wife",
            "my husband",
        ),
    ),
)

PATH_PHRASES: list[tuple[str, list[str]]] = [
    (phrase, list(path))
    for path, phrases in PATH_INPUT
    for phrase in phrases
]

# Singular relative in "Vater von NAME" / "father of NAME".
NAMED_PERSON_RELATIVE: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("mother", "mother", "mother"), ("urgrossmutter",)),
    (("father", "father", "father"), ("urgrossvater",)),
    (("mother", "mother"), ("grossmutter", "grandmother")),
    (("father", "father"), ("grossvater", "grandfather")),
    (("mother",), ("mutter", "mutters", "mother", "mama", "mom", "mum")),
    (("father",), ("vater", "vaters", "father", "papa", "dad", "daddy")),
    (("spouse",), ("ehefrau", "ehemann", "ehepartner", "wife", "husband", "spouse")),
    (("sister",), ("schwester", "sister")),
    (("brother",), ("bruder", "brother")),
    (("child",), ("sohn", "tochter", "son", "daughter")),
)

# ---------------------------------------------------------------------------
# Place parsing
# ---------------------------------------------------------------------------

PLACE_STOPWORDS = frozenset(
    ARTICLES
    + POSSESSIVES
    + (
        "jahr",
        "jahre",
        "stammbaum",
    )
)

PLACE_END_WORDS = frozenset(
    FACT_EVENT
    + QUESTION_WORDS
    + AUXILIARIES
    + CONJUNCTIONS
    + PREP_FROM
    + PREP_OF
    + ("die", "dead")
)

PLACE_LEADING_ARTICLES = frozenset(ARTICLES + POSSESSIVES)

PLACE_QUOTES = '"«»“”„‚\'\u2019'

NAME_FILTER_STOP = frozenset(
    PREP_FROM + PREP_OF + PREP_IN + FACT_EVENT + POSSESSIVES
)

# ---------------------------------------------------------------------------
# Intent / tree-query cues (phrase regexes, already folded)
# ---------------------------------------------------------------------------

COUNT_CHILDREN = (
    r"wie ?viele kinder|wieviele kinder|how many children|anzahl (der )?kinder"
)
LIST_CHILDREN_INTENT = (
    r"welche kinder|wer sind die kinder|list(e)? (the )?children|kinder auflisten"
)
LIST_CHILDREN_KIND = (
    r"wie heissen die kinder|wer sind die kinder|welche kinder|"
    r"list(e)? (the )?children|kinder auflisten|the children of"
)
RELATION_BETWEEN = (
    r"beziehung zwischen|verwandtschaft zwischen|relation between|"
    r"how (?:are|is) .*related|wie sind .+ verwandt|wie ist .+ verwandt"
)
RELATION_BETWEEN_CUES = r"zwischen|between|verwandt|related|beziehung"
PERSON_FACTS_WHEN = r"when did|wann (?:ist|war|wurde|starb)"
AGE_INTENT = r"wie alt|how old|years old|\balter\b|age of|welches alter"
AGE_PLAN_FITS = r"wie ?alt|how old|\balter\b|\bage\b|years old"
DEATH_FOCUS = (
    r"gestorben|\bstarb\b|verstorben|\bdied\b|\bdead\b|"
    r"sterbedatum|todestag|date of death|death date|"
    r"when did .+\bdie\b"
)
BIRTH_FOCUS = (
    r"geboren|geburtsdatum|geburtstag|birthdate|\bborn\b|\bbirth\b|"
    r"date of birth|birth date"
)
PERSON_FACTS = (
    r"geboren|gestorben|\bstarb\b|verstorben|"
    r"\bbirth\b|\bdeath\b|\bdied\b|\bdead\b|\bborn\b|"
    r"when did|wann (?:ist|war|wurde|starb)|"
    r"heirat|married|"
    r"geburtsdatum|geburtstag|sterbedatum|todestag|"
    r"birthdate|birth date|date of birth|date of death|death date"
)

# ---------------------------------------------------------------------------
# Compiled pieces used by the parser
# ---------------------------------------------------------------------------

RELATIVE_NOUNS_ALT = alt(RELATIVE_NOUN_LIST)
RELATIVE_NOUNS_BOUNDED = alt(RELATIVE_NOUN_LIST, bounded=True)

OF_DETERMINER_PREFIX = _prefix_alt(
    ("dem", "der", "den", "des", "the", "my") + POSSESSIVES
)
RELATIVE_DETERMINER_PREFIX = _prefix_alt(
    ("dem", "der", "den", "des", "die", "das", "the", "my") + POSSESSIVES
)

KIND_DETECT_PATTERNS: dict[str, str] = {
    kind: alt(words, bounded=True) for kind, words in KIND_DETECT.items()
}
KIND_QUESTION_PATTERNS: dict[str, str] = {
    kind: alt(words, bounded=True) for kind, words in KIND_QUESTION_CUES.items()
}

NAMED_PERSON_PATTERNS: list[tuple[str, list[str]]] = [
    (alt(words, bounded=True), list(path))
    for path, words in NAMED_PERSON_RELATIVE
]

PLACE_CUE_PATTERNS = (
    rf"\b{alt(PREP_FROM)}\s+",
    rf"\b{alt(FACT_EVENT)}\s+{alt(PREP_IN)}\s+",
    rf"\bin\s+",
)

DEMONYM_PATTERN = (
    rf"\b([A-ZÄÖÜ][\w\-äöüß]*)er\s+{alt(DEMONYM_RELATIVES)}s?\b"
)

PLACE_STRIP_PREP = alt(("aus", "from", "in", "of"))

TREE_QUERY_HINT = "|".join(
    (
        RELATIVE_NOUNS_BOUNDED,
        alt(
            (
                "grandma",
                "grandpa",
                "mama",
                "papa",
                "mom",
                "mum",
                "dad",
                "daddy",
                "ehepartner",
                "ehemann",
                "ehefrau",
                "spouse",
                "husband",
                "wife",
            ),
            bounded=True,
        ),
        PERSON_FACTS,
        AGE_PLAN_FITS,
        RELATION_BETWEEN_CUES,
        r"startperson|stammbaum",
    )
)

QUESTION_NAMES_RELATIVE = "|".join(
    (
        alt(
            (
                "grossmutter",
                "grossvater",
                "oma",
                "opa",
                "grandmother",
                "grandfather",
                "grandma",
                "grandpa",
                "mutter",
                "vater",
                "mother",
                "father",
                "ehepartner",
                "ehemann",
                "ehefrau",
                "spouse",
                "husband",
                "wife",
                "onkel",
                "tante",
                "uncle",
                "aunt",
                "schwiegermutter",
            ),
            bounded=True,
        ),
        r"\bandere[rn]?\b|\bother\b",
    )
)

CHILDREN_PLAN_FITS = alt(
    ("kinder", "children", "sohn", "tochter", "son", "daughter"),
    bounded=True,
)
