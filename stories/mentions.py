from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import bleach
import markdown
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

MENTION_LINK_RE = re.compile(
    r"\[([^\]]*)\]\((person|family|place):(\d+)\)",
    re.IGNORECASE,
)
HREF_RE = re.compile(r"^(person|family|place):(\d+)$", re.IGNORECASE)

ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "em",
    "b",
    "i",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "blockquote",
    "code",
    "pre",
    "hr",
    "a",
    "span",
]
ALLOWED_ATTRIBUTES = {
    "a": ["href", "class", "title"],
    "span": ["class"],
}


def extract_mention_ids(body: str) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {"person": [], "family": [], "place": []}
    seen: dict[str, set[int]] = {"person": set(), "family": set(), "place": set()}
    for _label, kind, pk_text in MENTION_LINK_RE.findall(body or ""):
        kind = kind.lower()
        pk = int(pk_text)
        if pk in seen[kind]:
            continue
        seen[kind].add(pk)
        found[kind].append(pk)
    return found


def unwrap_anchor(element) -> None:
    element.tag = "span"
    element.attrib.clear()


class MentionTreeprocessor(Treeprocessor):
    def __init__(self, md, resolver):
        super().__init__(md)
        self.resolver = resolver
        self.resolved: dict[str, list[Any]] = {
            "person": [],
            "family": [],
            "place": [],
        }

    def run(self, root):
        for element in list(root.iter("a")):
            href = (element.get("href") or "").strip()
            match = HREF_RE.match(href)
            if not match:
                continue
            kind = match.group(1).lower()
            pk = int(match.group(2))
            result = self.resolver(kind, pk)
            state = result.get("state") if result else "missing"
            if state == "ok":
                element.set("href", result["url"])
                element.set("class", f"story-mention story-mention-{kind}")
                obj = result.get("obj")
                if obj is not None:
                    self.resolved[kind].append(obj)
            elif state == "hidden":
                unwrap_anchor(element)
                element.set("class", "text-muted")
                element.text = result.get("label") or _("vertraulich")
                for child in list(element):
                    element.remove(child)
            else:
                unwrap_anchor(element)
        return root


class MentionExtension(Extension):
    def __init__(self, **kwargs):
        self.resolver = kwargs.pop("resolver")
        self.processor = None
        super().__init__(**kwargs)

    def extendMarkdown(self, md):
        self.processor = MentionTreeprocessor(md, self.resolver)
        md.treeprocessors.register(self.processor, "story_mentions", 8)


@dataclass
class RenderedStory:
    html: str
    individuals: list[Any] = field(default_factory=list)
    families: list[Any] = field(default_factory=list)
    places: list[Any] = field(default_factory=list)


def resolve_mention(kind: str, pk: int, *, tree_id: int, apply_privacy: bool) -> dict:
    from genview.models import Family, Individual, Place

    if kind == "person":
        obj = Individual.objects.filter(pk=pk, gedcom_tree_id=tree_id).first()
        if obj is None:
            return {"state": "missing"}
        if apply_privacy and obj.is_confidential:
            return {"state": "hidden", "label": _("vertraulich")}
        return {"state": "ok", "url": obj.get_absolute_url(), "obj": obj}
    if kind == "family":
        obj = Family.objects.filter(pk=pk, gedcom_tree_id=tree_id).select_related(
            "husband", "wife"
        ).first()
        if obj is None:
            return {"state": "missing"}
        if apply_privacy and obj.is_confidential:
            return {"state": "hidden", "label": _("vertraulich")}
        return {"state": "ok", "url": obj.get_absolute_url(), "obj": obj}
    if kind == "place":
        obj = Place.objects.filter(pk=pk, gedcom_tree_id=tree_id).first()
        if obj is None:
            return {"state": "missing"}
        return {"state": "ok", "url": obj.get_absolute_url(), "obj": obj}
    return {"state": "missing"}


def _unique(items: list[Any]) -> list[Any]:
    seen = set()
    unique = []
    for item in items:
        if item.pk in seen:
            continue
        seen.add(item.pk)
        unique.append(item)
    return unique


def render_story_body(body: str, *, tree_id: int, apply_privacy: bool) -> RenderedStory:
    text = body or ""
    extension = MentionExtension(
        resolver=lambda kind, pk: resolve_mention(
            kind, pk, tree_id=tree_id, apply_privacy=apply_privacy
        )
    )
    html = markdown.markdown(
        text,
        extensions=["nl2br", "sane_lists", extension],
        output_format="html",
    )
    cleaned = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True,
    )
    processor = extension.processor
    individuals = _unique(processor.resolved["person"]) if processor else []
    families = _unique(processor.resolved["family"]) if processor else []
    places = _unique(processor.resolved["place"]) if processor else []
    if not cleaned.strip():
        return RenderedStory(html="", individuals=individuals, families=families, places=places)
    return RenderedStory(
        html=mark_safe(cleaned),
        individuals=individuals,
        families=families,
        places=places,
    )