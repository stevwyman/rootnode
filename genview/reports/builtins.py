"""Built-in genealogy reports. Register additional modules the same way."""

from __future__ import annotations

from django.db.models import Count, Max, Min, Prefetch, Q
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from genview.mixins import apply_privacy_to_event_qs, apply_privacy_to_individual_qs
from genview.models import ChildFamilyLink, Event, Family, Individual, Place

from .base import (
    CATEGORY_LISTS,
    CATEGORY_PEDIGREE,
    CATEGORY_RESEARCH,
    PARAM_BOOLEAN,
    PARAM_CHOICE,
    PARAM_FAMILY,
    PARAM_INTEGER,
    PARAM_PERSON,
    Report,
    ReportCell,
    ReportParam,
    ReportResult,
    dash,
    person_cell,
    register,
    text_cell,
)
from .walking import (
    BIRT,
    DEAT,
    MARR,
    annotate_vital_gaps,
    collect_ancestors,
    collect_descendants,
    event_date_text,
    event_for,
    event_has_source,
    marriage_event,
    parents_of,
    prefetch_people,
    spouses_of,
)


def _person_param(*, required=True, help_text=""):
    return ReportParam(
        name="person_id",
        kind=PARAM_PERSON,
        label=_lazy("Person"),
        required=required,
        help_text=help_text,
    )


def _generations_param(default=4, max_value=15):
    return ReportParam(
        name="generations",
        kind=PARAM_INTEGER,
        label=_lazy("Generationen"),
        default=default,
        min_value=1,
        max_value=max_value,
        help_text=_lazy("Anzahl der Generationen einschließlich der Startperson (Generation 0)."),
    )


SCOPE_CHOICES = (
    ("ancestors", _lazy("Vorfahren der Person")),
    ("descendants", _lazy("Nachkommen der Person")),
    ("tree", _lazy("Gesamter Stammbaum")),
)


def _scope_people(tree, params, *, apply_privacy):
    """Resolve the person set for research reports that share a scope control."""
    scope = params.get("scope") or "ancestors"
    generations = params.get("generations") or 8
    person_id = params.get("person_id")
    qs = apply_privacy_to_individual_qs(
        Individual.objects.filter(gedcom_tree=tree),
        apply_privacy,
    )
    note = None
    if scope == "tree":
        return qs, None
    if not person_id:
        return qs.none(), _("Bitte eine Person für den gewählten Bereich angeben.")
    if scope == "ancestors":
        nodes = collect_ancestors(tree.pk, person_id, generations, apply_privacy=apply_privacy)
    else:
        nodes = collect_descendants(tree.pk, person_id, generations, apply_privacy=apply_privacy)
    return qs.filter(pk__in=[node.person.pk for node in nodes]), note


def _vital_row(person, *, apply_privacy, extra=None):
    birth = event_for(person, BIRT)
    death = event_for(person, DEAT)
    cells = [
        person_cell(person, apply_privacy=apply_privacy),
        text_cell(event_date_text(birth)),
        text_cell(birth.place.name if birth and birth.place_id else ""),
        text_cell(event_date_text(death)),
        text_cell(death.place.name if death and death.place_id else ""),
    ]
    if extra:
        cells.extend(extra)
    return cells


class AncestorReport(Report):
    slug = "ancestors"
    title = _lazy("Vorfahren (Ahnentafel)")
    description = _lazy(
        "Listet Vorfahren einer Person mit Ahnentafel-Nummer, Generation "
        "und den bekannten Geburts- und Sterbedaten."
    )
    category = CATEGORY_PEDIGREE
    parameters = (_person_param(), _generations_param(default=4))

    def run(self, tree, params, *, apply_privacy: bool) -> ReportResult:
        generations = params.get("generations") or 4
        nodes = collect_ancestors(
            tree.pk, params["person_id"], generations, apply_privacy=apply_privacy
        )
        root = next((node.person for node in nodes if node.generation == 0), None)
        result = ReportResult(
            title=str(self.title),
            summary=_("%(count)s Personen in %(gens)s Generation(en) ab %(name)s.")
            % {
                "count": len(nodes),
                "gens": generations,
                "name": root.full_name() if root else "—",
            },
            columns=[
                _("Nr."),
                _("Generation"),
                _("Person"),
                _("Geburt"),
                _("Geburtsort"),
                _("Tod"),
                _("Sterbeort"),
            ],
        )
        for node in nodes:
            birth = event_for(node.person, BIRT)
            death = event_for(node.person, DEAT)
            result.add_row(
                [
                    ReportCell(text=str(node.ahnentafel or "")),
                    ReportCell(text=str(node.generation)),
                    person_cell(node.person, apply_privacy=apply_privacy),
                    text_cell(event_date_text(birth)),
                    text_cell(birth.place.name if birth and birth.place_id else ""),
                    text_cell(event_date_text(death)),
                    text_cell(death.place.name if death and death.place_id else ""),
                ],
                group=_("Generation %(n)s") % {"n": node.generation},
            )
        if not nodes:
            result.notes.append(_("Die gewählte Person wurde nicht gefunden oder ist vertraulich."))
        return result


class DescendantReport(Report):
    slug = "descendants"
    title = _lazy("Nachkommen")
    description = _lazy(
        "Listet Nachkommen einer Person über eine wählbare Anzahl von Generationen, "
        "einschließlich Partnern."
    )
    category = CATEGORY_PEDIGREE
    parameters = (_person_param(), _generations_param(default=3, max_value=12))

    def run(self, tree, params, *, apply_privacy: bool) -> ReportResult:
        generations = params.get("generations") or 3
        nodes = collect_descendants(
            tree.pk, params["person_id"], generations, apply_privacy=apply_privacy
        )
        root = next((node.person for node in nodes if node.generation == 0), None)
        result = ReportResult(
            title=str(self.title),
            summary=_("%(count)s Personen in %(gens)s Generation(en) ab %(name)s.")
            % {
                "count": len(nodes),
                "gens": generations,
                "name": root.full_name() if root else "—",
            },
            columns=[
                _("Generation"),
                _("Person"),
                _("Eltern"),
                _("Partner"),
                _("Geburt"),
                _("Tod"),
            ],
        )
        for node in nodes:
            partners = ", ".join(
                partner.full_name()
                for partner in spouses_of(node.person)
                if not (apply_privacy and partner.is_confidential)
            )
            result.add_row(
                [
                    ReportCell(text=str(node.generation)),
                    person_cell(node.person, apply_privacy=apply_privacy),
                    person_cell(node.parent, apply_privacy=apply_privacy)
                    if node.parent
                    else dash(),
                    text_cell(partners),
                    text_cell(event_date_text(event_for(node.person, BIRT))),
                    text_cell(event_date_text(event_for(node.person, DEAT))),
                ],
                group=_("Generation %(n)s") % {"n": node.generation},
            )
        return result


class FamilyGroupReport(Report):
    slug = "family-group"
    title = _lazy("Familienblatt")
    description = _lazy(
        "Klassisches Familienblatt: Partner, Heirat und Kinder mit Geburts- und Sterbedaten."
    )
    category = CATEGORY_PEDIGREE
    parameters = (
        ReportParam(
            name="family_id",
            kind=PARAM_FAMILY,
            label=_lazy("Familie"),
            required=True,
        ),
    )

    def run(self, tree, params, *, apply_privacy: bool) -> ReportResult:
        family = (
            Family.objects.select_related("husband", "wife")
            .prefetch_related(
                Prefetch(
                    "children",
                    queryset=ChildFamilyLink.objects.select_related("child"),
                ),
                Prefetch(
                    "events",
                    queryset=Event.objects.select_related("event_type", "place").prefetch_related(
                        "sources"
                    ),
                ),
            )
            .filter(gedcom_tree=tree, pk=params["family_id"])
            .first()
        )
        if family is None:
            return ReportResult(
                title=str(self.title),
                notes=[_("Die gewählte Familie wurde nicht gefunden.")],
            )
        child_ids = [link.child_id for link in family.children.all() if link.child_id]
        loaded = prefetch_people(
            Individual.objects.filter(
                pk__in=[pk for pk in [family.husband_id, family.wife_id, *child_ids] if pk]
            )
        )
        people = {person.pk: person for person in loaded}
        husband = people.get(family.husband_id)
        wife = people.get(family.wife_id)
        marriage = marriage_event(family)
        marriage_text = event_date_text(marriage)
        if marriage and marriage.place_id:
            marriage_text = f"{marriage_text} — {marriage.place.name}".strip(" —")
        result = ReportResult(
            title=str(self.title),
            summary=str(family),
            columns=[
                _("Rolle"),
                _("Person"),
                _("Geburt"),
                _("Geburtsort"),
                _("Tod"),
                _("Sterbeort"),
                _("Heirat"),
            ],
        )
        for role, person in ((_("Ehemann"), husband), (_("Ehefrau"), wife)):
            if person is None:
                result.add_row(
                    [role, dash(), dash(), dash(), dash(), dash(), text_cell(marriage_text)],
                    group=_("Eltern"),
                )
                continue
            cells = [ReportCell(text=role), *_vital_row(person, apply_privacy=apply_privacy)]
            cells.append(text_cell(marriage_text))
            result.add_row(cells, group=_("Eltern"))
        for link in family.children.all():
            child = people.get(link.child_id)
            if child is None or (apply_privacy and child.is_confidential):
                continue
            cells = [ReportCell(text=_("Kind")), *_vital_row(child, apply_privacy=apply_privacy), dash()]
            result.add_row(cells, group=_("Kinder"))
        if marriage:
            sourced = _(" (mit Quelle)") if event_has_source(marriage) else _(" (ohne Quelle)")
            result.notes.append(
                _("Heirat: %(when)s%(source)s")
                % {"when": marriage_text or "—", "source": sourced}
            )
        return result


class MissingInformationReport(Report):
    slug = "missing-information"
    title = _lazy("Fehlende Geburts-, Heirats- und Sterbedaten")
    description = _lazy(
        "Zeigt, wo Geburt, Heirat oder Tod noch ohne Datum, Ort oder Quelle sind — "
        "die typische To-do-Liste für die weitere Forschung."
    )
    category = CATEGORY_RESEARCH
    parameters = (
        ReportParam(
            name="scope",
            kind=PARAM_CHOICE,
            label=_lazy("Bereich"),
            default="ancestors",
            choices=SCOPE_CHOICES,
        ),
        _person_param(
            required=False,
            help_text=_lazy("Wird für Vorfahren- und Nachkommen-Bereich benötigt."),
        ),
        _generations_param(default=8),
        ReportParam(
            name="require_place",
            kind=PARAM_BOOLEAN,
            label=_lazy("Ort verlangen"),
            default=True,
        ),
        ReportParam(
            name="require_source",
            kind=PARAM_BOOLEAN,
            label=_lazy("Quelle verlangen"),
            default=True,
        ),
        ReportParam(
            name="skip_living_death",
            kind=PARAM_BOOLEAN,
            label=_lazy("Fehlenden Tod bei vermutlich Lebenden ignorieren"),
            default=True,
        ),
    )

    def run(self, tree, params, *, apply_privacy: bool) -> ReportResult:
        require_place = bool(params.get("require_place"))
        require_source = bool(params.get("require_source"))
        skip_living = bool(params.get("skip_living_death"))
        qs, note = _scope_people(tree, params, apply_privacy=apply_privacy)
        if note:
            return ReportResult(title=str(self.title), notes=[note])
        people = list(annotate_vital_gaps(qs).order_by("surname", "given_name", "pk"))
        result = ReportResult(
            title=str(self.title),
            columns=[
                _("Person"),
                _("Geburt"),
                _("Heirat"),
                _("Tod"),
                _("Lücken"),
            ],
        )
        missing_count = 0
        for person in people:
            issues = _gap_labels(
                person,
                require_place=require_place,
                require_source=require_source,
                skip_living_death=skip_living,
            )
            if not issues:
                continue
            missing_count += 1
            result.add_row(
                [
                    person_cell(person, apply_privacy=apply_privacy),
                    _status_cell(
                        person.has_birth_date,
                        person.has_birth_place,
                        person.has_birth_source,
                        require_place,
                        require_source,
                    ),
                    _marriage_status_cell(person, require_place, require_source),
                    _death_status_cell(person, require_place, require_source, skip_living),
                    ReportCell(text=", ".join(issues), css="text-danger"),
                ]
            )
        result.summary = _(
            "%(gaps)s von %(total)s Personen haben noch Lücken bei Geburt, Heirat oder Tod."
        ) % {"gaps": missing_count, "total": len(people)}
        if missing_count == 0:
            result.notes.append(_("Keine Lücken für die gewählten Kriterien gefunden."))
        return result


def _status_cell(has_date, has_place, has_source, require_place, require_source) -> ReportCell:
    parts = [_("Datum") if has_date else _("kein Datum")]
    if require_place:
        parts.append(_("Ort") if has_place else _("kein Ort"))
    if require_source:
        parts.append(_("Quelle") if has_source else _("keine Quelle"))
    ok = has_date and (has_place or not require_place) and (has_source or not require_source)
    return ReportCell(text=", ".join(parts), css="text-success" if ok else "text-danger")


def _marriage_status_cell(person, require_place, require_source) -> ReportCell:
    if not person.has_family:
        return ReportCell(text=_("keine Familie"), css="text-muted")
    return _status_cell(
        person.has_marriage_date,
        person.has_marriage_place,
        person.has_marriage_source,
        require_place,
        require_source,
    )


def _death_status_cell(person, require_place, require_source, skip_living) -> ReportCell:
    if skip_living and person.born_recently and not person.has_death:
        return ReportCell(text=_("vermutlich lebend"), css="text-muted")
    return _status_cell(
        person.has_death_date,
        person.has_death_place,
        person.has_death_source,
        require_place,
        require_source,
    )


def _gap_labels(person, *, require_place, require_source, skip_living_death) -> list[str]:
    issues: list[str] = []
    if not person.has_birth_date:
        issues.append(_("Geburt ohne Datum"))
    elif require_place and not person.has_birth_place:
        issues.append(_("Geburt ohne Ort"))
    if require_source and not person.has_birth_source:
        issues.append(_("Geburt ohne Quelle"))
    if person.has_family:
        if not person.has_marriage_date:
            issues.append(_("Heirat ohne Datum"))
        elif require_place and not person.has_marriage_place:
            issues.append(_("Heirat ohne Ort"))
        if require_source and not person.has_marriage_source:
            issues.append(_("Heirat ohne Quelle"))
    skip_death = skip_living_death and person.born_recently and not person.has_death
    if not skip_death:
        if not person.has_death_date:
            issues.append(_("Tod ohne Datum"))
        elif require_place and not person.has_death_place:
            issues.append(_("Tod ohne Ort"))
        if require_source and not person.has_death_source:
            issues.append(_("Tod ohne Quelle"))
    return issues


class EndOfLineReport(Report):
    slug = "end-of-line"
    title = _lazy("Enden der Linie")
    description = _lazy(
        "Personen, bei denen Vater, Mutter oder beide Eltern unbekannt sind — "
        "klassische Ansatzpunkte für die weitere Forschung."
    )
    category = CATEGORY_RESEARCH
    parameters = (
        ReportParam(
            name="scope",
            kind=PARAM_CHOICE,
            label=_lazy("Bereich"),
            default="ancestors",
            choices=SCOPE_CHOICES,
        ),
        _person_param(required=False),
        _generations_param(default=8),
    )

    def run(self, tree, params, *, apply_privacy: bool) -> ReportResult:
        qs, note = _scope_people(tree, params, apply_privacy=apply_privacy)
        if note:
            return ReportResult(title=str(self.title), notes=[note])
        qs = prefetch_people(qs)
        result = ReportResult(
            title=str(self.title),
            columns=[_("Person"), _("Vater"), _("Mutter"), _("Fehlt")],
        )
        count = 0
        for person in qs.order_by("surname", "given_name"):
            father, mother = parents_of(person)
            if apply_privacy:
                if father and father.is_confidential:
                    father = None
                if mother and mother.is_confidential:
                    mother = None
            missing = []
            if father is None:
                missing.append(_("Vater"))
            if mother is None:
                missing.append(_("Mutter"))
            if not missing:
                continue
            count += 1
            result.add_row(
                [
                    person_cell(person, apply_privacy=apply_privacy),
                    person_cell(father, apply_privacy=apply_privacy),
                    person_cell(mother, apply_privacy=apply_privacy),
                    ReportCell(text=", ".join(missing), css="text-danger"),
                ]
            )
        result.summary = _("%(count)s Personen ohne vollständige Eltern.") % {"count": count}
        return result


class UnsourcedEventsReport(Report):
    slug = "unsourced-events"
    title = _lazy("Ereignisse ohne Quelle")
    description = _lazy("Alle Ereignisse im Stammbaum, denen noch keine Quelle zugeordnet ist.")
    category = CATEGORY_RESEARCH
    parameters = (
        ReportParam(
            name="only_vitals",
            kind=PARAM_BOOLEAN,
            label=_lazy("Nur Geburt, Heirat und Tod"),
            default=True,
        ),
    )

    def run(self, tree, params, *, apply_privacy: bool) -> ReportResult:
        only_vitals = bool(params.get("only_vitals", True))
        qs = apply_privacy_to_event_qs(
            Event.objects.filter(gedcom_tree=tree).select_related(
                "event_type",
                "place",
                "individual",
                "family__husband",
                "family__wife",
            ),
            apply_privacy,
            tree.pk,
        ).annotate(source_n=Count("sources")).filter(source_n=0)
        if only_vitals:
            qs = qs.filter(event_type__tag__in=[BIRT, DEAT, MARR])
        rows = list(qs.order_by("event_type__name", "parsed_date", "pk"))
        result = ReportResult(
            title=str(self.title),
            summary=_("%(count)s Ereignisse ohne Quelle.") % {"count": len(rows)},
            columns=[_("Ereignis"), _("Datum"), _("Ort"), _("Person / Familie")],
        )
        for event in rows:
            if event.individual_id:
                subject = person_cell(event.individual, apply_privacy=apply_privacy)
            elif event.family_id:
                subject = ReportCell(text=str(event.family), url=event.family.get_absolute_url())
            else:
                subject = dash()
            result.add_row(
                [
                    text_cell(event.event_type.name if event.event_type_id else ""),
                    text_cell(event_date_text(event)),
                    text_cell(event.place.name if event.place_id else ""),
                    subject,
                ]
            )
        return result


class SurnameReport(Report):
    slug = "surnames"
    title = _lazy("Nachnamen")
    description = _lazy("Häufigkeit der Familiennamen mit frühester und spätester Geburt.")
    category = CATEGORY_LISTS
    parameters = ()

    def run(self, tree, params, *, apply_privacy: bool) -> ReportResult:
        qs = apply_privacy_to_individual_qs(
            Individual.objects.filter(gedcom_tree=tree),
            apply_privacy,
        )
        stats = (
            qs.exclude(surname="")
            .values("surname")
            .annotate(
                n=Count("pk"),
                first_birth=Min("events__parsed_date", filter=Q(events__event_type__tag=BIRT)),
                last_birth=Max("events__parsed_date", filter=Q(events__event_type__tag=BIRT)),
            )
            .order_by("-n", "surname")
        )
        rows = list(stats)
        result = ReportResult(
            title=str(self.title),
            summary=_("%(count)s unterschiedliche Nachnamen.") % {"count": len(rows)},
            columns=[_("Nachname"), _("Personen"), _("Früheste Geburt"), _("Späteste Geburt")],
        )
        for row in rows:
            result.add_row(
                [
                    text_cell(row["surname"]),
                    ReportCell(text=str(row["n"])),
                    text_cell(row["first_birth"].strftime("%Y") if row["first_birth"] else ""),
                    text_cell(row["last_birth"].strftime("%Y") if row["last_birth"] else ""),
                ]
            )
        return result


class PlaceUsageReport(Report):
    slug = "places"
    title = _lazy("Orte")
    description = _lazy("Welche Orte im Stammbaum vorkommen, mit Anzahl der Ereignisse.")
    category = CATEGORY_LISTS
    parameters = ()

    def run(self, tree, params, *, apply_privacy: bool) -> ReportResult:
        events = apply_privacy_to_event_qs(
            Event.objects.filter(gedcom_tree=tree, place_id__isnull=False),
            apply_privacy,
            tree.pk,
        )
        stats = (
            events.values("place_id", "place__name")
            .annotate(n=Count("pk"))
            .order_by("-n", "place__name")
        )
        rows = list(stats)
        result = ReportResult(
            title=str(self.title),
            summary=_("%(count)s Orte mit Ereignissen.") % {"count": len(rows)},
            columns=[_("Ort"), _("Ereignisse")],
        )
        for row in rows:
            url = None
            if row["place_id"]:
                url = Place(pk=row["place_id"], gedcom_tree_id=tree.pk).get_absolute_url()
            result.add_row(
                [
                    ReportCell(text=row["place__name"] or "—", url=url),
                    ReportCell(text=str(row["n"])),
                ]
            )
        return result


class CalendarReport(Report):
    slug = "calendar"
    title = _lazy("Ereigniskalender")
    description = _lazy(
        "Geburtstage, Todestage und Hochzeitstage nach Monat und Tag — unabhängig vom Jahr."
    )
    category = CATEGORY_LISTS
    parameters = (
        ReportParam(
            name="month",
            kind=PARAM_INTEGER,
            label=_lazy("Monat (1–12, leer = alle)"),
            min_value=1,
            max_value=12,
        ),
    )

    def run(self, tree, params, *, apply_privacy: bool) -> ReportResult:
        month = params.get("month")
        qs = apply_privacy_to_event_qs(
            Event.objects.filter(
                gedcom_tree=tree,
                parsed_date__isnull=False,
                event_type__tag__in=[BIRT, DEAT, MARR],
            ).select_related("event_type", "place", "individual", "family__husband", "family__wife"),
            apply_privacy,
            tree.pk,
        )
        if month:
            qs = qs.filter(parsed_date__month=month)
        events = sorted(
            qs,
            key=lambda event: (event.parsed_date.month, event.parsed_date.day, event.parsed_date.year),
        )
        result = ReportResult(
            title=str(self.title),
            summary=_("%(count)s datierte Ereignisse.") % {"count": len(events)},
            columns=[_("Datum"), _("Ereignis"), _("Person / Familie"), _("Jahr"), _("Ort")],
        )
        for event in events:
            if event.individual_id:
                subject = person_cell(event.individual, apply_privacy=apply_privacy)
            elif event.family_id:
                subject = ReportCell(text=str(event.family), url=event.family.get_absolute_url())
            else:
                subject = dash()
            when = event.parsed_date
            result.add_row(
                [
                    ReportCell(text=when.strftime("%d.%m.") if when else "—"),
                    text_cell(event.event_type.name if event.event_type_id else ""),
                    subject,
                    ReportCell(text=str(when.year) if when else "—"),
                    text_cell(event.place.name if event.place_id else ""),
                ],
                group=when.strftime("%B") if when else "",
            )
        return result


def register_builtin_reports() -> None:
    for report in (
        AncestorReport(),
        DescendantReport(),
        FamilyGroupReport(),
        MissingInformationReport(),
        EndOfLineReport(),
        UnsourcedEventsReport(),
        SurnameReport(),
        PlaceUsageReport(),
        CalendarReport(),
    ):
        register(report)
