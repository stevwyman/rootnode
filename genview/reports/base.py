"""Pluggable genealogy reports.

Add a new report by subclassing ``Report``, declaring ``parameters``, and
calling ``register()``. Views stay generic: they render the catalog, bind
GET parameters, and display a tabular result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from django.utils.translation import gettext_lazy as _


PARAM_PERSON = "person"
PARAM_FAMILY = "family"
PARAM_INTEGER = "integer"
PARAM_BOOLEAN = "boolean"
PARAM_CHOICE = "choice"

CATEGORY_PEDIGREE = "pedigree"
CATEGORY_RESEARCH = "research"
CATEGORY_LISTS = "lists"


@dataclass(frozen=True)
class ReportParam:
    name: str
    kind: str
    label: str
    required: bool = False
    default: Any = None
    min_value: int | None = None
    max_value: int | None = None
    help_text: str = ""
    choices: tuple[tuple[str, str], ...] = ()


@dataclass
class ReportCell:
    text: str
    url: str | None = None
    css: str = ""
    title: str = ""
    children: list["ReportCell"] = field(default_factory=list)


@dataclass
class ReportResult:
    title: str
    summary: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[ReportCell]] = field(default_factory=list)
    group_labels: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_row(self, cells: Iterable[ReportCell | str], group: str = "") -> None:
        normalized: list[ReportCell] = []
        for cell in cells:
            if isinstance(cell, ReportCell):
                normalized.append(cell)
            else:
                normalized.append(ReportCell(text=str(cell)))
        self.rows.append(normalized)
        self.group_labels.append(group)

    def iter_rows(self):
        """Yield (group_header, cells) with a header only when the group changes."""
        previous = None
        labels = self.group_labels or [""] * len(self.rows)
        for cells, group in zip(self.rows, labels):
            header = group if group and group != previous else ""
            previous = group
            yield header, cells


class Report:
    """One catalog entry. Subclasses implement ``run()``."""

    slug: str = ""
    title: str = ""
    description: str = ""
    category: str = CATEGORY_LISTS
    parameters: tuple[ReportParam, ...] = ()

    def run(self, tree, params: dict[str, Any], *, apply_privacy: bool) -> ReportResult:
        raise NotImplementedError


_REGISTRY: dict[str, Report] = {}


def register(report: Report) -> Report:
    if not report.slug:
        raise ValueError("Report.slug is required")
    _REGISTRY[report.slug] = report
    return report


def get_report(slug: str) -> Report | None:
    return _REGISTRY.get(slug)


def all_reports() -> list[Report]:
    order = {
        CATEGORY_PEDIGREE: 0,
        CATEGORY_RESEARCH: 1,
        CATEGORY_LISTS: 2,
    }
    return sorted(
        _REGISTRY.values(),
        key=lambda r: (order.get(r.category, 9), str(r.title), r.slug),
    )


def reports_by_category() -> list[tuple[str, list[Report]]]:
    labels = {
        CATEGORY_PEDIGREE: _("Abstammung"),
        CATEGORY_RESEARCH: _("Forschung"),
        CATEGORY_LISTS: _("Listen"),
    }
    grouped: dict[str, list[Report]] = {}
    for report in all_reports():
        grouped.setdefault(report.category, []).append(report)
    return [
        (str(labels.get(key, key)), grouped[key])
        for key in (CATEGORY_PEDIGREE, CATEGORY_RESEARCH, CATEGORY_LISTS)
        if key in grouped
    ]


def coerce_params(report: Report, raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (values, field_errors). Missing optional params use defaults."""
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for param in report.parameters:
        raw_value = raw.get(param.name)
        if isinstance(raw_value, list):
            raw_value = raw_value[0] if raw_value else None
        if raw_value in (None, ""):
            if param.required and param.default is None:
                errors[param.name] = str(_("Dieses Feld ist erforderlich."))
            else:
                values[param.name] = param.default
            continue
        try:
            values[param.name] = _coerce_value(param, raw_value)
        except ValueError:
            errors[param.name] = str(_("Ungültiger Wert."))
    return values, errors


def _coerce_value(param: ReportParam, raw_value: Any) -> Any:
    if param.kind == PARAM_BOOLEAN:
        if isinstance(raw_value, bool):
            return raw_value
        return str(raw_value).lower() in {"1", "true", "on", "yes", "ja"}
    if param.kind == PARAM_INTEGER:
        number = int(str(raw_value).strip())
        if param.min_value is not None and number < param.min_value:
            raise ValueError("min")
        if param.max_value is not None and number > param.max_value:
            raise ValueError("max")
        return number
    if param.kind in {PARAM_PERSON, PARAM_FAMILY}:
        return int(str(raw_value).strip())
    if param.kind == PARAM_CHOICE:
        text = str(raw_value)
        allowed = {choice[0] for choice in param.choices}
        if text not in allowed:
            raise ValueError("choice")
        return text
    return str(raw_value)


def yes_no(flag: bool) -> ReportCell:
    if flag:
        return ReportCell(text=str(_("ja")), css="text-success")
    return ReportCell(text=str(_("nein")), css="text-danger")


def dash() -> ReportCell:
    return ReportCell(text="—", css="text-muted")


def person_cell(person, *, apply_privacy: bool) -> ReportCell:
    from genview.models import Individual

    if person is None:
        return dash()
    if apply_privacy and getattr(person, "is_confidential", False):
        return ReportCell(text=str(_("Vertrauliche Person")), css="text-muted fst-italic")
    name = person.full_name() if isinstance(person, Individual) else str(person)
    return ReportCell(text=name, url=person.get_absolute_url())


def people_cell(people, *, apply_privacy: bool) -> ReportCell:
    """One cell for several people, each name linked when possible."""
    parts = [
        person_cell(person, apply_privacy=apply_privacy)
        for person in people
        if person is not None
    ]
    if not parts:
        return dash()
    if len(parts) == 1:
        return parts[0]
    return ReportCell(text=", ".join(part.text for part in parts), children=parts)


def text_cell(value, *, empty: str = "—") -> ReportCell:
    text = (value or "").strip() if isinstance(value, str) else (str(value) if value else "")
    if not text:
        return ReportCell(text=empty, css="text-muted")
    return ReportCell(text=text)
