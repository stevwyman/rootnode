from __future__ import annotations

import os
import mimetypes
from pathlib import Path

from datetime import date
from itertools import chain
from django.contrib import messages
from django.utils.translation import gettext as _
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Subquery, OuterRef, Prefetch, Value, IntegerField
from django.db.models import Q, F
from django.db.models.functions import Coalesce
from django.forms import modelformset_factory
from django.core.paginator import Paginator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.generic import ListView, DetailView, CreateView, DeleteView, TemplateView
from django.views.generic.edit import UpdateView, FormView
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse_lazy, reverse

from logging import getLogger

from .tree_calendar import (
    collect_upcoming_birthdays,
    collect_upcoming_anniversaries,
    merge_upcoming,
)

logger = getLogger(__name__)

from .models import (
    Individual,
    Family,
    Event,
    EventType,
    ChildFamilyLink,
    MediaObject,
    FaceTag,
    DocumentExtractionSuggestion,
    Tree,
    TreeMembership,
    Source,
    Place,
)
from .forms import (
    IndividualForm,
    IndividualSearchForm,
    FamilyForm,
    ChildFamilyLinkForm,
    MediaObjectForm,
    AddExistingMediaForm,
    EventForm,
    SourceForm,
    PlaceForm
)
from .mixins import (
    SuperuserRequiredMixin,
    UserPassesTestMixin,
    TreeAccessMixin,
    TreeEditAccessMixin,
    TreeAdminAccessMixin,
    user_can_edit_tree,
    apply_privacy_to_individual_qs,
    apply_privacy_to_family_qs,
    apply_privacy_to_media_qs,
    apply_privacy_to_event_qs,
    SortableListViewMixin,
    FilterableListViewMixin,
)
from .utils import get_similar_place_clusters, merge_multiple_places, geocode_place, build_flat_family_tree, generate_thumbnail_for_instance


def home(request):
    """
    Simple landing page that just welcomes the user.
    You can extend it later (e.g. add a dashboard, charts, etc.).
    """
    return render(request, "genview/home.html")


# ----------------------------------------------------------------------
# 1️⃣ Trees
# ----------------------------------------------------------------------


class TreeListView(ListView):
    model = Tree
    template_name = "genview/tree_list.html"
    context_object_name = "trees"

    def get_queryset(self):
        user = self.request.user

        # ==========================================
        # 1. FILTERN: Was darf überhaupt gesehen werden?
        # ==========================================
        if user.is_superuser:
            # Szenario A: Der Boss sieht immer alle Bäume
            qs = Tree.objects.all()
            
        elif user.is_authenticated:
            # Szenario B: Normaler, eingeloggter User
            # Sieht Bäume, die öffentlich sind ODER wo er in der Membership-Tabelle steht
            qs = Tree.objects.filter(
                Q(is_public=True) | Q(memberships__user=user)
            ).distinct() # distinct() verhindert doppelte Zeilen, falls sich Datenbank-Joins überschneiden
            
        else:
            # Szenario C: Nicht eingeloggter Gast (Anonym)
            # Sieht ausnahmslos NUR Bäume, die explizit auf öffentlich stehen
            qs = Tree.objects.filter(is_public=True)


        # ==========================================
        # 2. ANNOTIEREN: Die Rolle für die Buttons anhängen
        # ==========================================
        if user.is_authenticated and not user.is_superuser:
            membership_role = TreeMembership.objects.filter(
                user=user,
                gedcom_tree=OuterRef('pk')
            ).values('role')[:1]

            qs = qs.annotate(user_role=Subquery(membership_role))

        # Neueste Bäume zuerst anzeigen
        return qs.select_related("starting_individual").order_by('-id')


class TreeDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Tree
    template_name = "genview/tree_confirm_delete.html"
    # Passe den Namen deiner Übersichtsseite hier an!
    success_url = reverse_lazy('genview:tree-list') 
    
    # Da deine URLs vermutlich auf <int:tree_id> lauten:
    pk_url_kwarg = 'tree_id'

    def test_func(self):
        """
        Sicherheits-Check: Nur Superuser (Admins) dürfen einen Baum löschen!
        (Alternativ: prüfen, ob der User der Besitzer des Baumes ist).
        """
        return self.request.user.is_superuser

    def form_valid(self, form):
        """Wird aufgerufen, wenn die Löschung bestätigt wird."""
        tree = self.get_object()
        # Eine Erfolgsmeldung für den Admin setzen
        messages.success(self.request, _("Der Stammbaum '%(name)s' und alle dazugehörigen Daten wurden unwiderruflich gelöscht.") % {"name": tree.name})
        return super().form_valid(form)


class TreeOverviewView(TreeAccessMixin, TemplateView):
    """Per-tree dashboard: statistics and upcoming birthdays / anniversaries."""

    template_name = "genview/tree_overview.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tree_id = self.kwargs["tree_id"]
        tree = Tree.objects.select_related("starting_individual").get(pk=tree_id)
        context["tree"] = tree
        apply_privacy = self.get_apply_privacy()

        context["stat_individuals"] = tree.individuals.count()
        context["stat_families"] = tree.families.count()
        context["stat_places"] = tree.places.count()
        context["stat_events"] = Event.objects.filter(gedcom_tree_id=tree_id).count()
        media_qs = MediaObject.objects.filter(gedcom_tree_id=tree_id)
        context["stat_media"] = media_qs.count()
        context["stat_media_photos"] = media_qs.filter(
            category=MediaObject.Category.PHOTO
        ).count()
        context["stat_media_documents"] = media_qs.filter(
            category=MediaObject.Category.DOCUMENT
        ).count()
        context["stat_sources"] = tree.sources.count()

        death_tag = EventType.objects.filter(tag="DEAT").first()
        if death_tag:
            deceased_pks = Event.objects.filter(
                gedcom_tree_id=tree_id,
                event_type=death_tag,
                individual__isnull=False,
            ).values_list("individual_id", flat=True)
            context["stat_deceased"] = len(set(deceased_pks))
        else:
            context["stat_deceased"] = 0
        context["stat_living"] = max(
            context["stat_individuals"] - context["stat_deceased"], 0
        )
        context["show_living_stats"] = not apply_privacy

        birthdays = collect_upcoming_birthdays(tree_id, apply_privacy)
        anniversaries = collect_upcoming_anniversaries(tree_id, apply_privacy)
        today_items, upcoming_items = merge_upcoming(birthdays, anniversaries)
        context["calendar_today"] = today_items
        context["calendar_upcoming"] = upcoming_items

        if context.get("can_edit"):
            birt_tag = EventType.objects.filter(tag="BIRT").first()
            without_birth = context["stat_individuals"]
            if birt_tag:
                with_birth = Event.objects.filter(
                    gedcom_tree_id=tree_id,
                    event_type=birt_tag,
                    individual__isnull=False,
                ).values("individual_id").distinct().count()
                without_birth = context["stat_individuals"] - with_birth
            context["maintenance_without_birth"] = max(without_birth, 0)
            context["maintenance_unlinked_faces"] = FaceTag.objects.filter(
                media__gedcom_tree_id=tree_id,
                individual__isnull=True,
            ).count()
            context["maintenance_face_suggestions"] = FaceTag.objects.filter(
                media__gedcom_tree_id=tree_id,
                individual__isnull=True,
                suggested_individual__isnull=False,
            ).count()
            context["maintenance_doc_suggestions"] = DocumentExtractionSuggestion.objects.filter(
                media__gedcom_tree_id=tree_id,
                status=DocumentExtractionSuggestion.Status.PENDING,
            ).count()

        return context


class TreeJSONView(LoginRequiredMixin, TreeAccessMixin, View):
    """
    Gibt ein flaches Array von Knoten für f3 (family-chart) zurück.
    Liest optional die 'max_depth' aus den GET-Parametern aus.
    """
    # Standardwert, falls das Frontend keinen Parameter mitschickt
    default_max_depth = 4 

    def get(self, request, tree_id, individual_id, *args, **kwargs):
        # 1. Person sicher aus der Datenbank holen
        ind = get_object_or_404(Individual, pk=individual_id, gedcom_tree_id=tree_id)
        
        # 2. Gewünschte Tiefe aus dem GET-Parameter der URL auslesen 
        # (z.B. ?max_depth=2 oder Fallback auf default_max_depth)
        try:
            depth_param = request.GET.get('max_depth')
            max_depth = int(depth_param) if depth_param else self.default_max_depth
        except ValueError:
            max_depth = self.default_max_depth
        max_depth = max(1, min(max_depth, 10))

        # 3. Den Baum mit der dynamischen Tiefe aufbauen (PII redaction when privacy applies)
        result = build_flat_family_tree(
            tree_id,
            ind,
            max_depth=max_depth,
            apply_privacy=self.get_apply_privacy(),
        )
        
        # 4. Als JSON zurückgeben
        return JsonResponse(result, safe=False, json_dumps_params={"ensure_ascii": False})


class FamilyTreeView(LoginRequiredMixin, TreeAccessMixin, TemplateView):
    """
    Renderet das HTML-Template für den Familienstammbaum.
    Die `tree_id` und `individual_id` werden als Kontext-Variablen
    an das Template übergeben, weil das JavaScript sie zum Laden der
    JSON-Daten benötigt.
    """
    template_name = "genview/family_tree.html"

    # -----------------------------------------------------------------
    # URL-Parameter in den Kontext übernehmen
    # -----------------------------------------------------------------
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # `tree_id` und `individual_id` kommen aus den URL-Captures
        context["tree_id"]       = self.kwargs.get("tree_id")
        context["individual_id"] = self.kwargs.get("individual_id")

        # optional: hier weitere Berechtigungs-Checks einbauen
        # if not self.request.user.has_perm(...):
        #     raise PermissionDenied

        return context


def _split_search_terms(query: str) -> list[str]:
    return [part for part in query.split() if part]


def _individual_name_q(term: str) -> Q:
    return (
        Q(given_name__icontains=term)
        | Q(surname__icontains=term)
        | Q(alternative_names__given_name__icontains=term)
        | Q(alternative_names__surname__icontains=term)
        | Q(gedcom_id__icontains=term)
    )


def _individual_and_q(terms: list[str]) -> Q:
    combined = Q()
    for term in terms:
        combined &= _individual_name_q(term)
    return combined


def _term_q(term: str, fields: list[str]) -> Q:
    """Match *term* in any of the given ORM lookup fields."""
    q = Q()
    for field in fields:
        q |= Q(**{f"{field}__icontains": term})
    return q


def _and_terms_q(terms: list[str], fields: list[str]) -> Q:
    """Every token must match somewhere (across fields)."""
    combined = Q()
    for term in terms:
        combined &= _term_q(term, fields)
    return combined


def _or_terms_q(terms: list[str], fields: list[str]) -> Q:
    """Any token may match anywhere."""
    combined = Q()
    for term in terms:
        combined |= _term_q(term, fields)
    return combined


def _search_queryset_by_tokens(qs, query: str, search_fields: list[str], limit: int = 30):
    """
  Ranked token search for Select2: AND matches first, then OR (up to *limit*).
    """
    terms = _split_search_terms(query)
    if not terms or not search_fields:
        return []

    if len(terms) == 1:
        return list(qs.filter(_term_q(terms[0], search_fields)).distinct()[:limit])

    and_pks = list(
        qs.filter(_and_terms_q(terms, search_fields)).distinct().values_list("pk", flat=True)[:limit]
    )
    remaining = limit - len(and_pks)
    or_pks: list[int] = []
    if remaining > 0:
        or_pks = list(
            qs.filter(_or_terms_q(terms, search_fields))
            .exclude(pk__in=and_pks)
            .distinct()
            .values_list("pk", flat=True)[:remaining]
        )

    ordered_pks = and_pks + or_pks
    by_pk = {obj.pk: obj for obj in qs.filter(pk__in=ordered_pks)}
    return [by_pk[pk] for pk in ordered_pks if pk in by_pk]


def _media_thumb_url_if_allowed(media, tree_id, apply_privacy) -> str | None:
    """Return authenticated mini-thumb URL when media may be shown to this viewer."""
    if not media or not media.file or not media.file.name:
        return None
    if not (media.is_image or media.is_pdf):
        return None
    if apply_privacy and (media.is_confidential or media.is_private):
        return None
    return reverse(
        "genview:media-thumb",
        kwargs={"tree_id": tree_id, "pk": media.pk, "size": "mini"},
    )


def _profile_media_for_individual(individual) -> MediaObject | None:
    """Prefer prefetched media list; fall back to a DB query."""
    ordered = getattr(individual, "_ordered_media", None)
    if ordered is not None:
        return ordered[0] if ordered else None
    return individual.profile_image


def _search_thumb_for_individual(individual, tree_id, apply_privacy) -> str | None:
    if apply_privacy and individual.is_confidential:
        return None
    return _media_thumb_url_if_allowed(
        _profile_media_for_individual(individual), tree_id, apply_privacy
    )


def _prefetch_individual_profile_media(individuals: list[Individual]) -> None:
    if not individuals:
        return
    enriched = Individual.objects.filter(pk__in=[i.pk for i in individuals]).prefetch_related(
        Prefetch(
            "media_objects",
            queryset=MediaObject.objects.order_by("-is_portrait", "id"),
            to_attr="_ordered_media",
        )
    )
    by_pk = {ind.pk: ind for ind in enriched}
    for individual in individuals:
        if individual.pk in by_pk:
            individual._ordered_media = by_pk[individual.pk]._ordered_media


def _finalize_individual_search_results(
    results: list[Individual], tree_id, apply_privacy
) -> list[Individual]:
    _prefetch_individual_profile_media(results)
    for ind in results:
        ind.search_thumb_url = _search_thumb_for_individual(ind, tree_id, apply_privacy)
    return results


def _annotate_individual_search_result(individual, apply_privacy, match: str | None = None):
    individual.search_type = _("Person")
    individual.search_icon = "👤"
    individual.search_match = match
    individual.search_show_thumb = True
    if apply_privacy and individual.is_confidential:
        individual.search_title = _("Vertrauliche Person")
        individual.search_desc = _("Datenschutzgeschützt")
        individual.search_url = None
    else:
        individual.search_title = individual.full_name()
        individual.search_desc = _("Geboren: %(date)s") % {"date": individual.birth_date_raw or "?"}
        individual.search_url = individual.get_absolute_url()


def _iter_phonetic_candidates(base_qs, exclude_pks):
    """Prefetch name fields for in-Python phonetic matching."""
    qs = base_qs.exclude(pk__in=exclude_pks).prefetch_related("alternative_names")
    return qs.only(
        "id",
        "given_name",
        "surname",
        "gedcom_id",
        "gedcom_tree_id",
    )


def _search_individuals(
    tree_id,
    query: str,
    apply_privacy,
    *,
    limit_and=20,
    limit_or=20,
    limit_phonetic_and=20,
    limit_phonetic_or=20,
):
    """
    Ranked person search:
      1. exact AND  2. phonetic AND  3. exact OR  4. phonetic OR
    Single-term queries use exact then phonetic only.
    """
    from .phonetics import (
        individual_matches_all_phonetic,
        individual_matches_any_phonetic,
        individual_matches_phonetic_term,
    )

    terms = _split_search_terms(query)
    if not terms:
        return []

    base = Individual.objects.filter(gedcom_tree_id=tree_id)
    results = []
    seen: set[int] = set()

    def _add(ind, match: str | None):
        if ind.pk in seen:
            return False
        seen.add(ind.pk)
        _annotate_individual_search_result(ind, apply_privacy, match=match)
        results.append(ind)
        return True

    # ---------- single term: exact → phonetic ----------
    if len(terms) == 1:
        term = terms[0]
        for ind in base.filter(_individual_name_q(term)).distinct()[:limit_and]:
            _add(ind, None)

        phonetic_added = 0
        for ind in _iter_phonetic_candidates(base, seen):
            if phonetic_added >= limit_phonetic_and:
                break
            if individual_matches_phonetic_term(ind, term) and _add(ind, "phonetic"):
                phonetic_added += 1
        return _finalize_individual_search_results(results, tree_id, apply_privacy)

    # ---------- 1) exact AND ----------
    for ind in base.filter(_individual_and_q(terms)).distinct()[:limit_and]:
        _add(ind, "and")

    # ---------- 2) phonetic AND ----------
    phonetic_and_added = 0
    for ind in _iter_phonetic_candidates(base, seen):
        if phonetic_and_added >= limit_phonetic_and:
            break
        if individual_matches_all_phonetic(ind, terms) and _add(ind, "and_phonetic"):
            phonetic_and_added += 1

    # ---------- 3) exact OR ----------
    or_q = Q()
    for term in terms:
        or_q |= _individual_name_q(term)

    or_added = 0
    for ind in base.filter(or_q).exclude(pk__in=seen).distinct()[:limit_or]:
        if _add(ind, "or"):
            or_added += 1
            if or_added >= limit_or:
                break

    # ---------- 4) phonetic OR ----------
    phonetic_or_added = 0
    for ind in _iter_phonetic_candidates(base, seen):
        if phonetic_or_added >= limit_phonetic_or:
            break
        if individual_matches_any_phonetic(ind, terms) and _add(ind, "or_phonetic"):
            phonetic_or_added += 1

    return _finalize_individual_search_results(results, tree_id, apply_privacy)


class GlobalSearchView(LoginRequiredMixin, TreeAccessMixin, TemplateView):
    template_name = "genview/global_search.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        q = self.request.GET.get('q', '').strip()
        tree_id = self.kwargs.get("tree_id")
        
        results = []

        if q:
            apply_privacy = self.get_apply_privacy()
            terms = _split_search_terms(q)

            individuals = _search_individuals(tree_id, q, apply_privacy)

            # 2. FAMILIEN durchsuchen
            families = Family.objects.filter(gedcom_tree_id=tree_id).filter(
                Q(husband__surname__icontains=q) |
                Q(wife__surname__icontains=q) |
                Q(gedcom_id__icontains=q)
            ).select_related('husband', 'wife')[:10]

            for fam in families:
                fam.search_type = _("Familie")
                fam.search_icon = "👪"
                if apply_privacy and fam.is_confidential:
                    fam.search_title = _("Vertrauliche Familie")
                    fam.search_desc = _("Datenschutzgeschützt")
                    fam.search_url = None
                else:
                    fam.search_title = str(fam)
                    fam.search_desc = _("Heirat: %(date)s") % {"date": fam.marriage_date_raw or "?"}
                    fam.search_url = fam.get_absolute_url()

            # 3. ORTE durchsuchen
            places = Place.objects.filter(gedcom_tree_id=tree_id, name__icontains=q)[:10]
            for place in places:
                place.search_type = _("Ort")
                place.search_icon = "📍"
                place.search_title = place.name
                place.search_desc = _("Ort im Stammbaum")
                place.search_url = place.get_absolute_url()

            # 4. QUELLEN durchsuchen
            sources = Source.objects.filter(gedcom_tree_id=tree_id, title__icontains=q)[:10]
            for src in sources:
                src.search_type = _("Quelle")
                src.search_icon = "📚"
                src.search_title = src.title
                src.search_desc = src.author or _("Kein Autor angegeben")
                src.search_url = src.get_absolute_url()

            # 5. Alles zu einer einzigen flachen Liste zusammenketten!
            results = list(chain(individuals, families, places, sources))

        context['results'] = results
        context['q'] = q
        context['search_multi_word'] = len(_split_search_terms(q)) > 1
        return context

# ----------------------------------------------------------------------
# 2️⃣ Individuals
# ----------------------------------------------------------------------


class IndividualListView(TreeAccessMixin, SortableListViewMixin, FilterableListViewMixin, ListView):
    model = Individual
    template_name = "genview/individual_list.html"
    context_object_name = "people"
    paginate_by = 25  # Helpful if you have thousands of records

    # --- Die Konfiguration für das Sortier-Mixin ---
    sortable_fields = ['given_name', 'surname', 'annotated_birth_date', 'annotated_death_date', 'sex']
    default_sort_field = 'surname'
    default_sort_dir = 'asc'

    # --- NEU: Filter-Konfiguration ---
    search_fields = [
        'given_name', 'surname', 
        'alternative_names__given_name', 'alternative_names__surname' 
    ]
    exact_filter_fields = ['sex']              # Diese Felder müssen exakt übereinstimmen

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        
        # NEU: 1. Die Subquery für das Geburtsdatum bauen
        # Wir suchen das Geburts-Event, dessen 'individual'-Feld auf die aktuelle Person (OuterRef) zeigt.
        birth_date_sq = Event.objects.filter(
            individual_id=OuterRef('pk'),
            event_type__tag='BIRT'
        ).values('parsed_date')[:1]  # Wichtig: [:1] stellt sicher, dass wir exakt EINEN Wert zurückbekommen

        birth_qs = Event.objects.filter(event_type__tag='BIRT')

        # Wir suchen das Todes-Ereignis für dieselbe Person (OuterRef)
        death_date_sq = Event.objects.filter(
            individual_id=OuterRef('pk'),
            event_type__tag='DEAT'
        ).values('parsed_date')[:1]

        death_qs = Event.objects.filter(event_type__tag='DEAT').select_related('event_type')

        # 2. Basis-QuerySet mit der virtuellen Spalte annotieren
        qs = Individual.objects.filter(
            gedcom_tree_id=tree_id
        ).annotate(
            # Virtuelle Spalte 1: Geburtsdatum für die Sortierung
            annotated_birth_date=Subquery(birth_date_sq),
            # 🔥 NEU: Virtuelle Spalte 2: Sterbedatum für die Sortierung
            annotated_death_date=Subquery(death_date_sq)
        ).prefetch_related(
            # Lädt die Geburts-Events blitzschnell vorab ins Template
            Prefetch("events", queryset=birth_qs, to_attr="birth_events"),
            # 🔥 NEU: Lädt die Todes-Events blitzschnell vorab ins Template
            Prefetch("events", queryset=death_qs, to_attr="death_events")
        )

        # 3. Filter anwenden (aus FilterableListViewMixin)
        filters = self.get_queryset_filters()
        if filters:
            qs = qs.filter(filters)

        # 4. Sortierung anwenden (aus SortableListViewMixin)
        ordering = self.get_ordering()
        if ordering:
            # Prüfen, ob absteigend (Minus-Zeichen) oder aufsteigend sortiert werden soll
            if ordering.startswith('-'):
                # Feldname ohne Minus extrahieren
                field_name = ordering[1:]
                # Absteigend sortieren, leere Werte ans Ende!
                qs = qs.order_by(F(field_name).desc(nulls_last=True))
            else:
                # Aufsteigend sortieren, leere Werte ans Ende!
                qs = qs.order_by(F(ordering).asc(nulls_last=True))

        return qs.distinct()


class IndividualDetailView(TreeAccessMixin, DetailView):
    model = Individual
    template_name = "genview/individual_detail.html"
    context_object_name = "person"

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        
        # 🔥 OPTIMIERT: Alle Prefetches für die Profilbilder (Avatare) der Verwandten hinzugefügt!
        return Individual.objects.filter(gedcom_tree_id=tree_id).prefetch_related(
            "events__event_type",
            "events__media_objects",
            
            # Ehepartner + deren Profilbilder
            "families_as_husband__wife__media_objects",
            "families_as_wife__husband__media_objects",
            
            # Kinder-Links + Kinder-Personen + deren Profilbilder
            "families_as_husband__children__child__media_objects",
            "families_as_wife__children__child__media_objects",
            
            # Eltern-Familien + Vater/Mutter + deren Profilbilder (wichtig für Ahnentafel!)
            "parental_families__family__husband__media_objects",
            "parental_families__family__wife__media_objects",
        )

    def get_object(self, queryset=None):
        person = super().get_object(queryset)
        apply_privacy = self.get_apply_privacy()

        if apply_privacy and person.is_confidential:
            raise PermissionDenied(
                "Diese Person unterliegt den Datenschutzrichtlinien. "
                "Sie haben keine Berechtigung, diese Detailseite aufzurufen."
            )
        return person

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        person: Individual = self.object
        
        # Privacy-Flag für das Template zugänglich machen
        apply_privacy = ctx.get('apply_privacy', False)

        # -------------------------------------------------------------
        # 1️⃣ Ehepartner & Familie
        # -------------------------------------------------------------
        spouse = None
        family = None
        husband_fam = person.families_as_husband.first()
        if husband_fam and husband_fam.wife:
            spouse = husband_fam.wife
            family = husband_fam
        if not spouse:
            wife_fam = person.families_as_wife.first()
            if wife_fam and wife_fam.husband:
                spouse = wife_fam.husband
                family = wife_fam

        ctx["spouse"] = spouse
        ctx["family"] = family

        # -------------------------------------------------------------
        # 2️⃣ Kinder
        # -------------------------------------------------------------
        children_links = []
        if husband_fam:
            children_links.extend(list(husband_fam.children.all()))
        if person.families_as_wife.first():
            children_links.extend(list(person.families_as_wife.first().children.all()))
        
        ctx["children_links"] = list({cl.id: cl for cl in children_links}.values())

        # -------------------------------------------------------------
        # 3️⃣ Eltern-Familien
        # -------------------------------------------------------------
        ctx["parent_families"] = list(person.parental_families.all())

        # -------------------------------------------------------------
        # 4️⃣ Ahnentafel (Pedigree)
        # -------------------------------------------------------------
        father = person.father
        mother = person.mother
        ctx['pedigree'] = {
            'father': father,
            'mother': mother,
            'ff': father.father if father else None,
            'fm': father.mother if father else None,
            'mf': mother.father if mother else None,
            'mm': mother.mother if mother else None,
        }

        # -------------------------------------------------------------
        # 5️⃣ Events / Timeline
        # -------------------------------------------------------------
        combined_events = Event.objects.filter(event_type__is_visible=True).filter(
            Q(individual=person) | Q(family__husband=person) | Q(family__wife=person)
        ).select_related('event_type', 'place').prefetch_related('sources').order_by(
            F("parsed_date").asc(nulls_last=True)
        )
        ctx["timeline_events"] = combined_events

        # -------------------------------------------------------------
        # 6️⃣ MEDIEN, GALERIE & ALBUM
        # -------------------------------------------------------------
        
        portrait = person.profile_image
        ctx["portrait"] = portrait

        # Wenn die Person datengeschützt ist, zeigen wir gar keine Medien
        if apply_privacy and person.is_confidential:
            ctx['photos'] = []
            ctx['documents'] = []
            ctx['gallery_photos'] = []
            ctx['gallery_documents'] = []
        else:
            # --- TEIL A: Direkt verknüpfte Medien (Für den Tab "Galerie") ---
            # Das sind nur die Bilder, die hart mit dieser Person verknüpft sind.
            direct_media = person.media_objects.all()
            ctx['photos'] = direct_media.filter(category=MediaObject.Category.PHOTO)
            ctx['documents'] = direct_media.filter(category=MediaObject.Category.DOCUMENT)


            # --- TEIL B: Die intelligente Album-Abfrage (Für den Tab "Album") ---
            # Zieht auch Bilder aus Events, Hochzeiten und Kinder-Familien an.
            tree_id = person.gedcom_tree_id
            birth_family_ids = ChildFamilyLink.objects.filter(
                child=person
            ).values_list('family_id', flat=True)

            all_gallery_media = MediaObject.objects.filter(
                gedcom_tree_id=tree_id
            ).filter(
                Q(individuals=person) | 
                Q(events__individual=person) |
                Q(events__family__husband=person) |
                Q(events__family__wife=person) |
                Q(families__husband=person) |
                Q(families__wife=person) |
                Q(families__in=birth_family_ids) |
                Q(sources__events__individual=person)
            ).distinct().prefetch_related('individuals', 'families', 'events')

            # Portrait-Bild aus dem erweiterten Album ausschließen (optional)
            g_photos = all_gallery_media.filter(category=MediaObject.Category.PHOTO)
            if portrait:
                g_photos = g_photos.exclude(pk=portrait.pk)
                
            ctx['gallery_photos'] = g_photos
            ctx['gallery_documents'] = all_gallery_media.filter(category=MediaObject.Category.DOCUMENT)

        # -------------------------------------------------------------
        # 7️⃣ Stammbaum JSON für dTree (Kompakte Foto-Version)
        # -------------------------------------------------------------
        def format_person(p):
            if not p: return None

            # Avatar-URL holen (falls vorhanden)
            avatar_url = ""
            if p.profile_image and p.profile_image.file:
                avatar_url = reverse("genview:media-thumb", kwargs={"tree_id": tree_id, "pk": p.profile_image.id, "size": 'mini'})
                #avatar_url = p.profile_image.file.url

            # Wir übergeben den Namen und das JSON für die kleinen Knoten
            return {
                "name": f"{p.given_name} {p.surname}",
                "class": "node",
                "extra": {
                    "id": p.pk,
                    "url": p.get_absolute_url(),
                    "avatar": avatar_url,
                    "b_year": p.birth_year, # Nutzt unsere neuen Properties!
                    "d_year": p.death_year,
                },
            }

        def get_marriage_str(fam):
            if not fam: return ""
            
            m_year = ""
            if getattr(fam, 'marriage_date_parsed', None):
                m_year = fam.marriage_date_parsed.year
            elif getattr(fam, 'marriage_date_raw', None):
                import re
                match = re.search(r'\d{4}', fam.marriage_date_raw)
                m_year = match.group() if match else ""
                
            return f"⚭ {m_year}" if m_year else ""
        

        tree_data = []
        parent_link = person.parental_families.first()

        if parent_link and (parent_link.family.husband or parent_link.family.wife):
            fam = parent_link.family
            root_person = fam.husband if fam.husband else fam.wife
            spouse_person = fam.wife if fam.husband else None

            root_node = format_person(root_person)
            target_node = format_person(person)
            target_node["marriages"] = []

            spouse_families = list(person.families_as_husband.all()) + list(person.families_as_wife.all())

            for sp_fam in spouse_families:
                partner = sp_fam.wife if sp_fam.husband == person else sp_fam.husband
                spouse_node = format_person(partner) if partner else {"name": "Unknown Partner", "class": "node", "extra": {}}
                m_str = get_marriage_str(sp_fam)
                if m_str and "extra" in spouse_node:
                    spouse_node["extra"]["marriage_info"] = m_str

                target_node["marriages"].append({
                    "spouse": spouse_node,
                    "children": [format_person(c.child) for c in sp_fam.children.all()],
                })

            spouse_node = format_person(spouse_person) if spouse_person else {"name": "Unknown Partner", "class": "node", "extra": {}}
            m_str = get_marriage_str(fam)
            if m_str and "extra" in spouse_node:
                spouse_node["extra"]["marriage_info"] = m_str

            root_node["marriages"] = [{"spouse": spouse_node, "children": [target_node]}]
            tree_data.append(root_node)
        else:
            target_node = format_person(person)
            target_node["marriages"] = []
            spouse_families = list(person.families_as_husband.all()) + list(person.families_as_wife.all())
            
            for sp_fam in spouse_families:
                partner = sp_fam.wife if sp_fam.husband == person else sp_fam.husband
                spouse_node = format_person(partner) if partner else {"name": "Unknown Partner", "class": "node", "extra": {}}
                m_str = get_marriage_str(sp_fam)
                if m_str and "extra" in spouse_node:
                    spouse_node["extra"]["marriage_info"] = m_str

                target_node["marriages"].append({
                    "spouse": spouse_node,
                    "children": [format_person(c.child) for c in sp_fam.children.all()],
                })
            tree_data.append(target_node)

        ctx["tree_data"] = tree_data

        lifecycle_stations = []
        seen_event_pks = set()

        def append_station(event, title=None):
            place = event.place
            if not place or place.latitude is None or place.longitude is None:
                return
            if event.pk in seen_event_pks:
                return
            seen_event_pks.add(event.pk)
            if event.parsed_date:
                date_str = event.parsed_date.strftime("%d.%m.%Y")
            else:
                date_str = event.raw_date or ""
            lifecycle_stations.append({
                "lat": float(place.latitude),
                "lng": float(place.longitude),
                "title": title or event.event_type_name(),
                "date": date_str,
                "place": place.name,
                "_sort": event.parsed_date,
            })

        for event in combined_events:
            append_station(event)

        for family in person.spousal_families:
            for child_link in family.children.all():
                child = child_link.child
                if not child:
                    continue
                child_birth = child.birth_event
                if child_birth:
                    append_station(
                        child_birth,
                        title=f"Geburt eines Kindes ({child.full_name()})",
                    )

        lifecycle_stations.sort(key=lambda s: s.pop("_sort", None) or date.min)
        ctx["lifecycle_stations"] = lifecycle_stations

        return ctx

    
class IndividualCreateView(LoginRequiredMixin, TreeEditAccessMixin, CreateView):
    model = Individual
    form_class = IndividualForm
    template_name = "genview/individual_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["tree_id"] = self.kwargs.get("tree_id")
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        
        # Prüfen, ob wir explizit einen männlichen Part anlegen (Vater)
        if 'father_of' in self.request.GET or self.request.GET.get('role') == 'husband':
            initial['sex'] = Individual.Sex.MALE
            
        # Prüfen, ob wir explizit einen weiblichen Part anlegen (Mutter)
        elif 'mother_of' in self.request.GET or self.request.GET.get('role') == 'wife':
            initial['sex'] = Individual.Sex.FEMALE
            
        return initial

    def form_valid(self, form):
        tree_id = self.kwargs.get("tree_id")
        
        # 1. Den Stammbaum für die neue Person zuweisen
        form.instance.gedcom_tree_id = tree_id
        
        # 2. Die neue Person in der Datenbank speichern (erzeugt self.object)
        response = super().form_valid(form)
        new_person = self.object

        # 3. URL-Parameter auslesen
        parent_family_id = self.request.GET.get('parent_family')
        single_parent_id = self.request.GET.get('single_parent')
        partner_of_id = self.request.GET.get('partner_of')

        # --- FALL 1: Neues Kind zu einer bestehenden Familie (Partner & Partnerin) ---
        if parent_family_id:
            parent_family = get_object_or_404(Family, pk=parent_family_id, gedcom_tree_id=tree_id)
            ChildFamilyLink.objects.create(
                child=new_person, 
                family=parent_family
            )

        # --- FALL 2: Neues Kind ohne bekannten Partner (Alleinerziehend) ---
        elif single_parent_id:
            parent = get_object_or_404(Individual, pk=single_parent_id, gedcom_tree_id=tree_id)
            new_family = Family(gedcom_tree_id=tree_id)
            
            # Ehemann oder Ehefrau basierend auf dem Geschlecht zuweisen
            if parent.sex == Individual.Sex.MALE:
                new_family.husband = parent
            elif parent.sex == Individual.Sex.FEMALE:
                new_family.wife = parent
            else:
                # Fallback bei unbekanntem Geschlecht
                new_family.husband = parent
                
            new_family.save()
            
            ChildFamilyLink.objects.create(
                child=new_person, 
                family=new_family
            )

        # --- FALL 3: Neuen Partner anlegen ---
        elif partner_of_id:
            original_person = get_object_or_404(Individual, pk=partner_of_id, gedcom_tree_id=tree_id)
            new_family = Family(gedcom_tree_id=tree_id)
            
            # Schlaue Zuweisung anhand der Geschlechter
            if original_person.sex == Individual.Sex.MALE:
                new_family.husband = original_person
                new_family.wife = new_person
            elif original_person.sex == Individual.Sex.FEMALE:
                new_family.wife = original_person
                new_family.husband = new_person
            else:
                # Wenn das Geschlecht der Ausgangsperson unbekannt ist,
                # richten wir uns nach dem Geschlecht des neu erstellten Partners.
                if new_person.sex == Individual.Sex.MALE:
                    new_family.husband = new_person
                    new_family.wife = original_person
                else:
                    # Letzter Ausweg: Standardzuweisung
                    new_family.husband = original_person
                    new_family.wife = new_person
                    
            new_family.save()
        
        # Neue URL-Parameter auslesen
        father_of_id = self.request.GET.get('father_of')
        mother_of_id = self.request.GET.get('mother_of')
        fill_family_id = self.request.GET.get('fill_family')
        fill_role = self.request.GET.get('role')

        # --- FALL 4: Komplett neue Eltern-Familie für ein Kind erstellen ---
        if father_of_id or mother_of_id:
            child_id = father_of_id or mother_of_id
            child = get_object_or_404(Individual, pk=child_id, gedcom_tree_id=tree_id)
            
            new_family = Family(gedcom_tree_id=tree_id)
            
            if father_of_id:
                new_family.husband = new_person
                # (Optional: Das Geschlecht direkt auf Männlich erzwingen)
            elif mother_of_id:
                new_family.wife = new_person
                
            new_family.save()
            
            # Das bestehende Kind mit dieser neuen Familie verknüpfen
            ChildFamilyLink.objects.create(child=child, family=new_family)

        # --- FALL 5: Einen fehlenden Elternteil in einer EXISTIERENDEN Familie auffüllen ---
        elif fill_family_id and fill_role:
            existing_family = get_object_or_404(Family, pk=fill_family_id, gedcom_tree_id=tree_id)
            
            if fill_role == 'husband' and not existing_family.husband:
                existing_family.husband = new_person
                existing_family.save()
            elif fill_role == 'wife' and not existing_family.wife:
                existing_family.wife = new_person
                existing_family.save()

        messages.success(self.request, _("Person %(name)s erfolgreich angelegt.") % {"name": new_person.full_name()})
        return response

    def get_success_url(self):
        # WICHTIG: Die URL benötigt laut get_absolute_url die tree_id
        return reverse_lazy(
            "genview:individual-detail", 
            kwargs={
                "tree_id": self.kwargs.get("tree_id"), 
                "pk": self.object.pk
            }
        )


class IndividualUpdateView(LoginRequiredMixin, TreeEditAccessMixin, UpdateView):
    model = Individual
    form_class = IndividualForm
    template_name = "genview/individual_form.html"

    def get_queryset(self):
        return Individual.objects.filter(gedcom_tree_id=self.kwargs.get("tree_id"))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["tree_id"] = self.kwargs.get("tree_id")
        return kwargs

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Personendaten wurden gespeichert."))  # optional
        return response

    def get_success_url(self):
        return reverse_lazy(
            "genview:individual-detail",
            kwargs={"tree_id": self.object.gedcom_tree_id, "pk": self.object.pk},
        )


class IndividualDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = Individual
    template_name = "genview/individual_confirm_delete.html"

    def get_queryset(self):
        return Individual.objects.filter(gedcom_tree_id=self.kwargs.get("tree_id"))

    def get_success_url(self):
        # Nach dem Löschen leiten wir den Nutzer zurück zur Personen-Liste
        tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, _("Person erfolgreich gelöscht."))
        return reverse_lazy("genview:individual-list", kwargs={"tree_id": tree_id})


class SetTreeStartingIndividualView(LoginRequiredMixin, TreeAdminAccessMixin, View):
    """Tree admin / superuser: set or clear the tree's starting individual."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        tree_id = self.kwargs["tree_id"]
        tree = get_object_or_404(Tree, pk=tree_id)
        person = get_object_or_404(Individual, pk=self.kwargs["pk"], gedcom_tree_id=tree_id)
        action = request.POST.get("action", "set")

        if action == "clear":
            tree.starting_individual = None
            tree.save(update_fields=["starting_individual"])
            messages.success(
                request,
                _("Startperson entfernt. Schnellzugriffe auf die Baumansicht sind deaktiviert."),
            )
        else:
            tree.starting_individual = person
            tree.save(update_fields=["starting_individual"])
            messages.success(
                request,
                _("„%(name)s“ ist jetzt die Startperson für diesen Stammbaum.")
                % {"name": person.full_name()},
            )

        return redirect(
            "genview:individual-detail",
            tree_id=tree_id,
            pk=person.pk,
        )


class IndividualSearchView(LoginRequiredMixin, TreeAccessMixin, ListView):
    """
    Listet Personen und filtert nach dem Suchbegriff `q`.
    Der Suchbegriff wird in mehreren Feldern geprüft:
      * gedcom_id
      * given_name, surname, name_prefix, name_suffix
      * sex (Anzeige von MALE/FEMALE/UNKNOWN)
    """

    model = Individual
    template_name = "genview/individual_list.html"  # das gleiche Template wie zuvor
    context_object_name = "people"
    paginate_by = 25
    ordering = ["surname", "given_name"]

    # ------------------------------------------------------------------
    # Form im Kontext bereitstellen (für das Eingabefeld)
    # ------------------------------------------------------------------
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["search_form"] = IndividualSearchForm(self.request.GET)
        ctx["search_query"] = self.request.GET.get("q", "").strip()
        return ctx

    # ------------------------------------------------------------------
    # QuerySet filtern – case-insensitive, mehrere Felder
    # ------------------------------------------------------------------
    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        qs = Individual.objects.filter(gedcom_tree_id=tree_id).order_by(*self.ordering)
        query = self.request.GET.get("q", "").strip()

        if query:
            # Wir splitten nach Leerzeichen, damit mehrere Begriffe möglich sind
            terms = query.split()
            for term in terms:
                qs = qs.filter(
                    Q(gedcom_id__icontains=term)
                    | Q(given_name__icontains=term)
                    | Q(surname__icontains=term)
                    | Q(name_prefix__icontains=term)
                    | Q(name_suffix__icontains=term)
                    | Q(sex__iexact=term.upper())
                )
        return qs


class IndividualSearchAjaxView(LoginRequiredMixin, TreeAccessMixin, ListView):
    model = Individual
    paginate_by = 25
    ordering = ["surname", "given_name"]

    def get_queryset(self):
        # 1. SECURITY FIX: Lock the base queryset to the current tree FIRST
        tree_id = self.kwargs.get("tree_id")
        qs = Individual.objects.filter(gedcom_tree_id=tree_id).order_by(*self.ordering)

        # 2. Apply the search query terms
        query = self.request.GET.get("q", "").strip()
        if query:
            terms = query.split()
            for term in terms:
                qs = qs.filter(
                    Q(gedcom_id__icontains=term)
                    | Q(given_name__icontains=term)
                    | Q(surname__icontains=term)
                    | Q(name_prefix__icontains=term)
                    | Q(name_suffix__icontains=term)
                    | Q(sex__iexact=term.upper())
                )
        return qs

    def render_to_response(self, context, **response_kwargs):
        """
        Rückgabe eines JSON-Objektes:
        {
            "table":    "<tbody>…</tbody>",
            "pager":    "<nav>…</nav>"
        }
        """
        # Add our custom search query variable directly into the main context
        context["search_query"] = self.request.GET.get("q", "").strip()

        # TEMPLATE FIX: Pass the ENTIRE context dictionary to render_to_string.
        # This ensures your fragments have access to 'tree_id', 'user', etc.
        table_html = render_to_string(
            "genview/_individual_table.html",
            context,
            request=self.request,
        )
        pager_html = render_to_string(
            "genview/_individual_pager.html",
            context,
            request=self.request,
        )

        return JsonResponse({"table": table_html, "pager": pager_html})


class IndividualAddExistingMediaView(LoginRequiredMixin, TreeEditAccessMixin, FormView):
    template_name = "genview/add_existing_media.html"
    form_class = AddExistingMediaForm

    def dispatch(self, request, *args, **kwargs):
        # Wir laden die Familie direkt am Anfang, damit wir sie in allen Methoden griffbereit haben
        self.tree_id = self.kwargs.get("tree_id")
        self.individual = get_object_or_404(Individual, pk=self.kwargs.get("person_pk"), gedcom_tree_id=self.tree_id)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        tree_id = self.kwargs.get("tree_id")
        kwargs['tree'] = get_object_or_404(Tree, pk=tree_id)
        kwargs['target_obj'] = self.individual  # Übergibt die Person
        return kwargs

    def form_valid(self, form):
        # Die ausgewählten Medien holen
        selected_media = form.cleaned_data['media_objects']
        
        # Die Medien mit der Person verknüpfen
        self.individual.media_objects.add(*selected_media)
        
        messages.success(self.request, _("%(count)s Medien erfolgreich mit der Person verknüpft.") % {"count": selected_media.count()})
        
        # 🔥 WICHTIG: Die FormView erwartet als Return das super(), 
        # welches dann automatisch zu get_success_url() weiterleitet.
        return super().form_valid(form)
    def get_success_url(self) -> str:
        # Zurück zur Personen-Detailseite
        return reverse('genview:individual-detail', kwargs={'tree_id': self.kwargs['tree_id'], 'pk': self.individual.pk})

# ----------------------------------------------------------------------
# 3️⃣ Families
# ----------------------------------------------------------------------


class FamilyListView(TreeAccessMixin, SortableListViewMixin, FilterableListViewMixin, ListView):
    model = Family
    template_name = "genview/family_list.html"
    context_object_name = "families"
    paginate_by = 25

    # --- Die Konfiguration für das Sortier-Mixin ---
    # Wir fügen 'children_count' zu den sortierbaren Feldern hinzu!
    sortable_fields = ['husband__surname', 'wife__surname', 'annotated_marriage_date', 'children_count']
    default_sort_field = 'husband__surname'
    default_sort_dir = 'asc'

    search_fields = [
        'husband__given_name', 'husband__surname', 
        'husband__alternative_names__surname', 
        'wife__given_name', 'wife__surname',
        'wife__alternative_names__surname'     
    ]

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        
        # 1. Die Subquery für das Heiratsdatum bauen
        marriage_date_sq = Event.objects.filter(
            family_id=OuterRef('pk'),
            event_type__tag='MARR'
        ).values('parsed_date')[:1]

        # Das Basis-Queryset für das Prefetching im Template
        marriage_qs = Event.objects.filter(event_type__tag='MARR').select_related('event_type')

        # 2. Basis-QuerySet aufbauen und ALLE Annotationen mitgeben
        qs = Family.objects.filter(
            gedcom_tree_id=tree_id
        ).select_related(
            'husband', 'wife'
        ).annotate(
            # Annotation 1: Virtuelle Spalte für das Heiratsdatum
            annotated_marriage_date=Subquery(marriage_date_sq),
            # Annotation 2: Deine bisherige Logik für die Anzahl der Kinder
            children_count=Count("children")
        ).prefetch_related(
            Prefetch("events", queryset=marriage_qs, to_attr="marriage_events")
        )

        # 3. Filter anwenden (aus deinem Mixin)
        filters = self.get_queryset_filters()
        if filters:
            qs = qs.filter(filters)

        # 4. Sortierung anwenden (aus deinem Mixin)
        ordering = self.get_ordering()
        if ordering:
            if ordering.startswith('-'):
                field_name = ordering[1:]
                qs = qs.order_by(F(field_name).desc(nulls_last=True))
            else:
                qs = qs.order_by(F(ordering).asc(nulls_last=True))
        else:
            # Fallback-Sortierung, falls kein Mixin-Ordering greift
            qs = qs.order_by("gedcom_id")

        return qs.distinct()


class FamilyDetailView(TreeAccessMixin, DetailView):
    """
    Detail-Ansicht einer Familie.
    - `husband` und `wife` werden bereits über `select_related` geladen.
    - Kinder über ein Prefetch, das das `relationship_type` mitliefert.
    - Events und Media-Objects werden ebenfalls vorgeholt, damit im Template
      keine extra DB-Queries entstehen.
    """

    model = Family
    template_name = "genview/family_detail.html"
    context_object_name = "family"

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        return (
            Family.objects.filter(gedcom_tree_id=tree_id)
            .select_related("husband", "wife")
            .prefetch_related(
                # 1. Kinder-Links inkl. zugehörigem Child-Individual
                # 🔥 UPDATE: Wir hängen hier direkt .prefetch_related("child__media_objects") an, 
                # damit Django für alle gefundenen Kinder sofort auch deren Bilder lädt!
                Prefetch(
                    "children",
                    queryset=ChildFamilyLink.objects.select_related("child").prefetch_related("child__media_objects"),
                ),
                
                # 2. Alle Events (MARR, DIV, …) der Familie
                Prefetch(
                    "events",
                    queryset=Event.objects.filter(event_type__tag='MARR'),
                    to_attr="marriage_events",  # .marriage_events[0] ist das Event
                ),
                
                # 3. Medien-Objekte, die an die Familie selbst gebunden sind
                Prefetch("media_objects", queryset=MediaObject.objects.all()),
                
                # ==========================================
                # 🔥 NEU: 4. Bilder für Vater und Mutter vorab laden!
                # Da husband und wife oben per select_related geladen werden, 
                # reicht hier der einfache String-Pfad.
                # ==========================================
                "husband__media_objects",
                "wife__media_objects",
                # ==========================================
                # 🔥 NEU: Großeltern und deren Profilbilder vorab laden!
                # ==========================================
                "husband__parental_families__family__husband__media_objects",
                "husband__parental_families__family__wife__media_objects",
                "wife__parental_families__family__husband__media_objects",
                "wife__parental_families__family__wife__media_objects",
            )
            .order_by("-id")
        )

    # -----------------------------------------------------------------
    # Security Check for Data Privacy
    # -----------------------------------------------------------------
    def get_object(self, queryset=None):
        # Object is tree-scoped via get_queryset(); privacy still applies below.
        family = super().get_object(queryset)

        # 2. Nutze die neue Helfermethode aus unserem Mixin (Variante 1 von vorhin)
        apply_privacy = self.get_apply_privacy()

        # 3. Die IDOR-Sperre: Wenn Datenschutz gilt UND die Person vertraulich ist:
        if apply_privacy and family.is_confidential:
            # Django bricht sofort ab und liefert eine saubere "403 Forbidden" Seite aus
            raise PermissionDenied(
                "Diese Familie unterliegt den Datenschutzrichtlinien. "
                "Sie haben keine Berechtigung, diese Detailseite aufzurufen."
            )

        # 4. Nur wenn alles okay ist, wird die Person an die View/das Template übergeben
        return family


    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tree_id"] = self.kwargs.get("tree_id")
        
        family = self.object

        # ==========================================
        # 🔥 NEU: Großeltern-Variablen für das Template vorbereiten
        # ==========================================
        ctx['husband_father'] = family.husband.father if family.husband else None
        ctx['husband_mother'] = family.husband.mother if family.husband else None
        
        ctx['wife_father'] = family.wife.father if family.wife else None
        ctx['wife_mother'] = family.wife.mother if family.wife else None

        return ctx


class FamilyCreateView(LoginRequiredMixin, TreeEditAccessMixin, CreateView):
    model = Family
    form_class = FamilyForm
    template_name = "genview/family_form.html"

    def get_form_kwargs(self):
        # Holt die Standard-Argumente (wie instance, data etc.)
        kwargs = super().get_form_kwargs()
        # Packt unsere tree_id aus der URL mit dazu!
        kwargs["tree_id"] = self.kwargs.get("tree_id")
        return kwargs

    def form_valid(self, form):
        form.instance.gedcom_tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, _("Familie angelegt."))
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "genview:family-detail",
            kwargs={"tree_id": self.object.gedcom_tree_id, "pk": self.object.pk},
        )


class FamilyUpdateView(LoginRequiredMixin, TreeEditAccessMixin, UpdateView):
    model = Family
    form_class = FamilyForm
    template_name = "genview/family_form.html"

    def get_queryset(self):
        return Family.objects.filter(gedcom_tree_id=self.kwargs.get("tree_id"))

    def get_form_kwargs(self):
        # Holt die Standard-Argumente (wie instance, data etc.)
        kwargs = super().get_form_kwargs()
        # Packt unsere tree_id aus der URL mit dazu!
        kwargs["tree_id"] = self.kwargs.get("tree_id")
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, _("Familie wurde aktualisiert."))
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "genview:family-detail",
            kwargs={"tree_id": self.object.gedcom_tree_id, "pk": self.object.pk},
        )


class FamilyDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = Family
    template_name = "genview/family_confirm_delete.html"

    def get_queryset(self):
        return Family.objects.filter(gedcom_tree_id=self.kwargs.get("tree_id"))

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, _("Familie und zugehörige Verknüpfungen erfolgreich gelöscht."))
        # Nutzer nach dem Löschen zur Familien-Übersicht (oder Stammbaum) zurückschicken
        return reverse_lazy("genview:family-list", kwargs={"tree_id": tree_id})


class FamilyAddExistingMediaView(LoginRequiredMixin, TreeEditAccessMixin, FormView):
    template_name = "genview/add_existing_media.html"
    form_class = AddExistingMediaForm

    def dispatch(self, request, *args, **kwargs):
        # Wir laden die Familie direkt am Anfang, damit wir sie in allen Methoden griffbereit haben
        self.tree_id = self.kwargs.get("tree_id")
        self.family = get_object_or_404(Family, pk=self.kwargs.get("family_pk"), gedcom_tree_id=self.tree_id)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['tree'] = get_object_or_404(Tree, pk=self.tree_id)
        
        # 🔥 HIER WICHTIG: Wir übergeben die Familie an das Formular, 
        # damit bereits verknüpfte Bilder im Dropdown ausgeblendet werden!
        kwargs['target_obj'] = self.family 
        return kwargs

    def form_valid(self, form):
        # Die ausgewählten Medien holen
        selected_media = form.cleaned_data['media_objects']
        
        # Die Medien mit der Familie verknüpfen
        self.family.media_objects.add(*selected_media)
        
        messages.success(self.request, _("%(count)s Medien erfolgreich mit der Familie verknüpft.") % {"count": selected_media.count()})
        
        # 🔥 WICHTIG: Die FormView erwartet als Return das super(), 
        # welches dann automatisch zu get_success_url() weiterleitet.
        return super().form_valid(form)

    def get_success_url(self):
        # Der saubere Redirect zurück zur Familien-Detailseite
        return reverse('genview:family-detail', kwargs={'tree_id': self.tree_id, 'pk': self.family.pk})
# ----------------------------------------------------------------------
#  4️⃣ Kind-zu-Familie-Link – hinzufügen / bearbeiten
# ----------------------------------------------------------------------

class ChildFamilyLinkCreateView(LoginRequiredMixin, TreeEditAccessMixin, CreateView):
    model = ChildFamilyLink
    form_class = ChildFamilyLinkForm
    template_name = "genview/childfamilylink_form.html"

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        return ChildFamilyLink.objects.filter(family__gedcom_tree_id=tree_id)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["tree_id"] = self.kwargs.get("tree_id")
        return kwargs

    def get_success_url(self):
        return reverse_lazy(
            "genview:family-detail",
            kwargs={"tree_id": self.kwargs.get("tree_id"), "pk": self.object.family.pk},
        )


class ChildFamilyLinkDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = ChildFamilyLink
    template_name = "genview/childfamilylink_confirm_delete.html"

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        return ChildFamilyLink.objects.filter(family__gedcom_tree_id=tree_id)

    def get_success_url(self):
        return reverse_lazy(
            "genview:family-detail",
            kwargs={"tree_id": self.kwargs.get("tree_id"), "pk": self.object.family.pk},
        )


# ----------------------------------------------------------------------
# 8️⃣ Media
# ----------------------------------------------------------------------


class ProtectedMediaFileView(TreeAccessMixin, DetailView):
    """
    Acts as a secure tunnel to serve media files ONLY if the user
    has access to the specific family tree.
    """

    model = MediaObject

    def get_queryset(self):
        # SECURITY FIX: Ensure the requested media belongs to this tree
        tree_id = self.kwargs.get("tree_id")
        return MediaObject.objects.filter(gedcom_tree_id=tree_id)

    @xframe_options_sameorigin
    def get(self, request, *args, **kwargs):
        # 1. get_object() automatically applies the get_queryset() filter
        # and the TreeAccessMixin automatically checks basic tree access.
        media_obj = self.get_object()

        # ---------------------------------------------------------
        # 🔒 2. DATENSCHUTZ-PRÜFUNG (NEU)
        # ---------------------------------------------------------
        apply_privacy = self.get_apply_privacy()

        if apply_privacy and media_obj.is_confidential:
            # Blockiert den Download mit einem 403 Forbidden Fehler
            raise PermissionDenied("Dieses Dokument enthält vertrauliche Daten und wurde aus Datenschutzgründen gesperrt.")
        
        if apply_privacy and media_obj.is_private:
            # Blockiert den Download mit einem 403 Forbidden Fehler
            raise PermissionDenied("Dieses Dokument enthält vertrauliche Daten und wurde aus Datenschutzgründen gesperrt.")
                
        # ---------------------------------------------------------

        # -------------------------------------------------------------
        # 3. Welches File soll ausgeliefert werden? --------------------
        #   * Ohne `size`-Parameter → Original-File
        #   * Mit `size=mini|small` → entsprechendes Thumbnail
        # -------------------------------------------------------------
        size = self.kwargs.get("size")          # z. B. "mini" oder "small"

        if size in ("mini", "small"):
            # ----- Thumbnail prüfen/erzeugen -----------------------
            thumb_field = getattr(media_obj, f"thumb_{size}")

            # Falls das Thumbnail noch nicht existiert, on-the-fly erzeugen
            if not thumb_field or not thumb_field.name:
                try:
                    generate_thumbnail_for_instance(media_obj, size)
                    # Nach dem Schreiben das Feld neu aus der DB holen,
                    # sonst hat das Model noch keinen Namen.
                    media_obj.refresh_from_db(fields=[f"thumb_{size}"])
                    thumb_field = getattr(media_obj, f"thumb_{size}")
                except Exception as exc:
                    # Thumbnail-Erstellung schlug fehl → 404 zurückgeben
                    raise Http404(f"Thumbnail-Erstellung fehlgeschlagen: {exc}")

            # Pfad des Thumbnails verwenden
            if not thumb_field or not thumb_field.name:
                raise Http404("Thumbnail nicht gefunden.")
            file_path = Path(media_obj.file.storage.path(thumb_field.name))
        else:
            # ----- Original-Datei ---------------------------------
            if not media_obj.file or not os.path.exists(media_obj.file.path):
                raise Http404("Datei nicht gefunden.")
            file_path = Path(media_obj.file.path)

        # -------------------------------------------------------------
        # 4️⃣  Datei existiert? → FileResponse zurückgeben
        # -------------------------------------------------------------
        if not file_path.exists():
            raise Http404("Datei nicht gefunden.")

        # richtiger MIME-Typ (bspw. image/jpeg, application/pdf)
        mime_type, _ = mimetypes.guess_type(str(file_path))
        mime_type = mime_type or "application/octet-stream"

        file_handle = open(file_path, "rb")
        response = FileResponse(file_handle, content_type=mime_type)

        # Inline for images and PDFs (embed in gallery/detail); attachment for other types.
        safe_name = file_path.name.replace('"', "")
        if mime_type.startswith("image/") or mime_type == "application/pdf":
            response["Content-Disposition"] = f'inline; filename="{safe_name}"'
        else:
            response["Content-Disposition"] = f'attachment; filename="{safe_name}"'

        response["Cache-Control"] = "private, max-age=3600"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class MediaObjectDetailView(TreeAccessMixin, DetailView):
    model = MediaObject
    template_name = "genview/mediaobject_detail.html"
    context_object_name = "media"

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        return MediaObject.objects.filter(gedcom_tree_id=tree_id).prefetch_related(
            "individuals",
            "families__husband",
            "families__wife",
            "sources",
            "events__individual",
            "events__family__husband",
            "events__family__wife"
        )
    
    # -----------------------------------------------------------------
    # Security Check for Data Privacy
    # -----------------------------------------------------------------
    def get_object(self, queryset=None):
        media = super().get_object(queryset)
        apply_privacy = self.get_apply_privacy()

        # IDOR & Privacy Sperre
        if apply_privacy and (media.is_confidential or media.is_private):
            raise PermissionDenied(
                "Dieses Medium unterliegt den Datenschutzrichtlinien. "
                "Sie haben keine Berechtigung, diese Detailseite aufzurufen."
            )
        
        return media


    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # Lade die Tags inklusive der verknüpften Personen für das Frontend
        ctx['tags'] = self.object.facetags.select_related('individual', 'suggested_individual')
        ctx['document_suggestions'] = self.object.document_suggestions.filter(
            status=DocumentExtractionSuggestion.Status.PENDING
        ).select_related('individual', 'place')
        
        # ACHTUNG: Bei sehr großen Bäumen (Tausende Personen) sollte das im 
        # Frontend perspektivisch durch ein AJAX-Suchfeld (Select2) ersetzt werden!
        inds = Individual.objects.filter(gedcom_tree_id=self.kwargs.get("tree_id"))
        inds = apply_privacy_to_individual_qs(inds, self.get_apply_privacy())
        ctx["individuals"] = inds.only("id", "given_name", "surname")
        
        return ctx
    
    # -------------------------------------------------
    # POST – Router für Aktionen
    # -------------------------------------------------
    def post(self, request, *args, **kwargs):
        # GET stays readable for public/viewer access; mutations need EDITOR/ADMIN.
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not user_can_edit_tree(request.user, self.kwargs.get("tree_id")):
            raise PermissionDenied(
                "Nur Editoren und Admins dürfen Medien bearbeiten oder OCR/Gesichtserkennung ausführen."
            )

        media = self.get_object()

        if "detect" in request.POST:
            return self._handle_detection(request, media)
        elif "assign" in request.POST:
            return self._handle_assignment(request, media)
        elif "ocr" in request.POST:
            return self._handle_ocr(request, media)
        elif "parse_suggestions" in request.POST:
            return self._handle_parse_suggestions(request, media)
        elif "accept_doc_suggestion" in request.POST:
            return self._handle_doc_suggestion_action(request, media, accept=True)
        elif "reject_doc_suggestion" in request.POST:
            return self._handle_doc_suggestion_action(request, media, accept=False)
        elif "create_portrait" in request.POST:
            return self._handle_create_portrait(request, media)

        return redirect(request.path)
    
    def _handle_ocr(self, request, media):
        from .ocr_client import extract_text_via_api

        if not media.file or not media.file.name:
            messages.error(request, _("Keine Datei vorhanden."))
            return redirect(request.path)

        response = extract_text_via_api(media.file.path)

        if response["error"]:
            messages.error(request, _("OCR-Fehler: %(detail)s") % {"detail": response["error"]})
            return redirect(request.path)

        text = response["text"]
        if not text or not text.strip():
            messages.info(request, _("Kein Text erkannt. Der bisher extrahierte Text bleibt erhalten."))
            return redirect(request.path)

        media.extracted_text = text
        media.save()
        messages.success(request, _("Text erfolgreich extrahiert."))
        return redirect(request.path)

    def _handle_parse_suggestions(self, request, media):
        from .document_intelligence import extract_document_suggestions

        if not (media.extracted_text or "").strip():
            messages.warning(
                request,
                _("Zuerst OCR-Text extrahieren, dann Vorschläge erzeugen."),
            )
            return redirect(request.path)

        tree_id = self.kwargs.get("tree_id")
        created = extract_document_suggestions(media, tree_id)
        if created:
            messages.success(
                request,
                _("%(count)s Ereignis-Vorschlag/Vorschläge erzeugt.") % {"count": len(created)},
            )
        else:
            messages.info(
                request,
                _("Keine erkennbaren Ereignisse im Text gefunden."),
            )
        return redirect(request.path)

    def _handle_doc_suggestion_action(self, request, media, accept: bool):
        from .document_intelligence import apply_document_suggestion

        suggestion_id = request.POST.get("suggestion_id")
        suggestion = get_object_or_404(
            DocumentExtractionSuggestion,
            pk=suggestion_id,
            media=media,
            status=DocumentExtractionSuggestion.Status.PENDING,
        )
        tree_id = self.kwargs.get("tree_id")
        if accept:
            try:
                event = apply_document_suggestion(suggestion, tree_id)
                messages.success(
                    request,
                    _("Ereignis „%(etype)s“ erstellt und mit dem Dokument verknüpft.")
                    % {"etype": event.event_type.name},
                )
            except ValueError as exc:
                messages.error(request, str(exc))
        else:
            suggestion.status = DocumentExtractionSuggestion.Status.REJECTED
            suggestion.save(update_fields=["status", "updated_at"])
            messages.info(request, _("Vorschlag abgelehnt."))
        return redirect(request.path)

    def _handle_create_portrait(self, request, media):
        from .utils import create_portrait_from_crop

        if not media.is_image or not media.file:
            messages.error(request, _("Nur Bilddateien können als Portrait zugeschnitten werden."))
            return redirect(request.path)

        individual_id = request.POST.get("individual_id")
        if not individual_id:
            messages.warning(request, _("Bitte eine Person auswählen."))
            return redirect(request.path)

        tree_id = self.kwargs.get("tree_id")
        individual = get_object_or_404(
            Individual, pk=individual_id, gedcom_tree_id=tree_id
        )

        try:
            x = float(request.POST.get("x_percent", ""))
            y = float(request.POST.get("y_percent", ""))
            w = float(request.POST.get("width_percent", ""))
            h = float(request.POST.get("height_percent", ""))
        except (TypeError, ValueError):
            messages.error(request, _("Ungültige Auswahl. Bitte ein Rechteck auf dem Bild markieren."))
            return redirect(request.path)

        try:
            portrait = create_portrait_from_crop(media, individual, x, y, w, h)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect(request.path)
        except OSError as exc:
            messages.error(
                request,
                _("Bild konnte nicht gelesen werden: %(detail)s") % {"detail": exc},
            )
            return redirect(request.path)

        messages.success(
            request,
            _("Portrait für %(person)s erstellt und als Profilbild gesetzt.")
            % {"person": individual.full_name()},
        )
        return redirect(
            "genview:media-detail",
            tree_id=tree_id,
            pk=portrait.pk,
        )

    # -------------------------------------------------
    # Action-Handler 1: Erkennung & Prozentrechnung
    # -------------------------------------------------
    def _handle_detection(self, request, media):
        from .utils import detect_and_save_faces

        tree_id = self.kwargs.get("tree_id")
        result = detect_and_save_faces(media, tree_id)

        if result["error"]:
            messages.error(
                request,
                _("Erkennungs-Fehler: %(detail)s") % {"detail": result["error"]},
            )
            return redirect(request.path)

        if result["faces_found"] == 0:
            messages.info(
                request,
                _("Keine Gesichter im Bild erkannt. Bestehende Markierungen bleiben erhalten."),
            )
            return redirect(request.path)

        if result["suggested"] > 0:
            messages.success(
                request,
                _("%(saved)s Gesicht(er) erkannt, davon %(matched)s Vorschläge zur Prüfung.")
                % {"saved": result["faces_found"], "matched": result["suggested"]},
            )
        else:
            messages.success(
                request,
                _("%(count)s Gesicht(er) erkannt und gespeichert.")
                % {"count": result["faces_found"]},
            )

        return redirect(request.path)

    # -------------------------------------------------
    # Action-Handler 2: Zuweisung einer Person
    # -------------------------------------------------
    def _handle_assignment(self, request, media):
        tag_id = request.POST.get("tag_id")
        indiv_id = request.POST.get("individual_id")
        
        tag = get_object_or_404(FaceTag, pk=tag_id, media=media)

        if indiv_id:
            tree_id = self.kwargs.get("tree_id")
            individual = get_object_or_404(Individual, pk=indiv_id, gedcom_tree_id=tree_id)
            tag.individual = individual
            tag.suggested_individual = None
            tag.match_distance = None
            tag.save()
            messages.success(request, _("Gesicht erfolgreich mit %(person)s verknüpft.") % {"person": individual})
        else:
            messages.warning(request, _("Keine Person ausgewählt – Tag bleibt unverknüpft."))
            
        return redirect(request.path)
    

class MediaObjectListView(TreeAccessMixin, SortableListViewMixin, FilterableListViewMixin, ListView):
    model = MediaObject
    template_name = "genview/mediaobject_list.html"
    context_object_name = "media"
    paginate_by = 24

    # --- Konfiguration für SortableListViewMixin ---
    sortable_fields = ['title', 'category', 'is_portrait', 'id']
    default_sort_field = 'title'
    default_sort_dir = 'asc'

    # --- Konfiguration für FilterableListViewMixin ---
    search_fields = ['title', 'description', 'individuals__given_name', 'individuals__surname']
    exact_filter_fields = ['category']

    def get_queryset(self):
        # 1. Nur Medien des aktuellen Baums holen
        qs = super().get_queryset().filter(gedcom_tree_id=self.kwargs['tree_id'])
        
        # 2. Die Filter (Suche & Kategorie) aus dem Mixin anwenden
        qs = qs.filter(self.get_queryset_filters()).distinct()

        # 3. Optional: nur Medien mit noch nicht verknüpften Gesichtern
        faces_filter = self.request.GET.get("faces", "").strip()
        if faces_filter == "unlinked":
            qs = qs.filter(facetags__individual__isnull=True).distinct()
        
        # 4. Die Sortierung aus dem Mixin anwenden
        qs = qs.order_by(self.get_ordering())

        # Face tags: linked vs still unassigned (Subquery avoids M2M search inflate)
        linked_faces = (
            FaceTag.objects.filter(
                media_id=OuterRef("pk"),
                individual_id__isnull=False,
            )
            .values("media_id")
            .annotate(_c=Count("id"))
            .values("_c")[:1]
        )
        unlinked_faces = (
            FaceTag.objects.filter(
                media_id=OuterRef("pk"),
                individual_id__isnull=True,
            )
            .values("media_id")
            .annotate(_c=Count("id"))
            .values("_c")[:1]
        )
        qs = qs.annotate(
            linked_face_count=Coalesce(
                Subquery(linked_faces, output_field=IntegerField()),
                Value(0),
            ),
            unlinked_face_count=Coalesce(
                Subquery(unlinked_faces, output_field=IntegerField()),
                Value(0),
            ),
        )
        
        return qs.prefetch_related('individuals', 'families', 'events')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tree_id'] = self.kwargs['tree_id']
        
        # Damit das Dropdown im Template die echten Kategorien kennt (Foto, Dokument)
        context['category_choices'] = MediaObject.Category.choices
        context['current_filter_faces'] = self.request.GET.get('faces', '')
        
        return context


def _face_scan_candidate_qs(tree_id):
    """Photos that still need face detection (never scanned or have unlinked faces)."""
    return (
        MediaObject.objects.filter(
            gedcom_tree_id=tree_id,
            category=MediaObject.Category.PHOTO,
        )
        .exclude(file="")
        .exclude(file__isnull=True)
        .filter(
            Q(facetags__isnull=True) | Q(facetags__individual__isnull=True)
        )
        .distinct()
        .order_by("id")
    )


class MediaFaceScanView(LoginRequiredMixin, TreeEditAccessMixin, TemplateView):
    """Landing page: lists pending photos and drives one-by-one AJAX progress."""

    template_name = "genview/media_face_scan.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        tree_id = self.kwargs["tree_id"]
        candidates = list(
            _face_scan_candidate_qs(tree_id).values("id", "title")
        )
        ctx["tree_id"] = tree_id
        ctx["candidate_count"] = len(candidates)
        ctx["candidates_json"] = candidates
        return ctx


class MediaFaceScanProcessView(LoginRequiredMixin, TreeEditAccessMixin, View):
    """Process a single media object; called repeatedly by the progress UI."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        import json

        tree_id = self.kwargs["tree_id"]
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "Ungültige Anfrage."}, status=400)

        media_id = payload.get("media_id")
        if not media_id:
            return JsonResponse({"ok": False, "error": "media_id fehlt."}, status=400)

        media = get_object_or_404(
            MediaObject,
            pk=media_id,
            gedcom_tree_id=tree_id,
            category=MediaObject.Category.PHOTO,
        )

        from .utils import detect_and_save_faces

        result = detect_and_save_faces(media, tree_id)
        return JsonResponse(
            {
                "ok": result["ok"],
                "error": result["error"],
                "media_id": result["media_id"],
                "title": result["title"],
                "faces_found": result["faces_found"],
                "suggested": result["suggested"],
                "linked": result["suggested"],
                "detail_url": reverse(
                    "genview:media-detail",
                    kwargs={"tree_id": tree_id, "pk": media.pk},
                ),
            }
        )


class FaceSuggestionReviewView(LoginRequiredMixin, TreeEditAccessMixin, TemplateView):
    """Review queue for unlinked faces with optional person suggestions."""

    template_name = "genview/face_suggestion_review.html"
    paginate_by = 24

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        tree_id = self.kwargs["tree_id"]
        qs = (
            FaceTag.objects.filter(
                media__gedcom_tree_id=tree_id,
                individual__isnull=True,
            )
            .select_related("media", "suggested_individual")
            .order_by("-suggested_individual_id", "id")
        )
        paginator = Paginator(qs, self.paginate_by)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        ctx["face_tags"] = page_obj.object_list
        ctx["page_obj"] = page_obj
        ctx["is_paginated"] = page_obj.has_other_pages()
        return ctx

    def _redirect_back(self, request, tree_id):
        page = request.POST.get("page") or request.GET.get("page")
        url = reverse("genview:face-suggestion-review", kwargs={"tree_id": tree_id})
        if page:
            url = f"{url}?page={page}"
        return redirect(url)

    def post(self, request, *args, **kwargs):
        tree_id = self.kwargs["tree_id"]
        tag = get_object_or_404(
            FaceTag,
            pk=request.POST.get("tag_id"),
            media__gedcom_tree_id=tree_id,
            individual__isnull=True,
        )
        action = request.POST.get("action")
        if action == "accept" and tag.suggested_individual_id:
            tag.individual = tag.suggested_individual
            tag.suggested_individual = None
            tag.match_distance = None
            tag.save()
            messages.success(
                request,
                _("Vorschlag übernommen: %(person)s")
                % {"person": tag.individual.full_name()},
            )
        elif action == "reject":
            tag.suggested_individual = None
            tag.match_distance = None
            tag.save(update_fields=["suggested_individual", "match_distance", "updated_at"])
            messages.info(request, _("Vorschlag verworfen."))
        elif action == "assign":
            indiv_id = request.POST.get("individual_id")
            if indiv_id:
                individual = get_object_or_404(Individual, pk=indiv_id, gedcom_tree_id=tree_id)
                tag.individual = individual
                tag.suggested_individual = None
                tag.match_distance = None
                tag.save()
                messages.success(
                    request,
                    _("Gesicht mit %(person)s verknüpft.") % {"person": individual.full_name()},
                )
            else:
                messages.warning(request, _("Keine Person gewählt."))
        return self._redirect_back(request, tree_id)


class DocumentSuggestionReviewView(LoginRequiredMixin, TreeEditAccessMixin, TemplateView):
    """Central queue for OCR-derived event suggestions."""

    template_name = "genview/document_suggestion_review.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        tree_id = self.kwargs["tree_id"]
        ctx["suggestions"] = (
            DocumentExtractionSuggestion.objects.filter(
                media__gedcom_tree_id=tree_id,
                status=DocumentExtractionSuggestion.Status.PENDING,
            )
            .select_related("media", "individual", "place")
            .order_by("media_id", "-created_at")[:200]
        )
        return ctx

    def post(self, request, *args, **kwargs):
        from .document_intelligence import apply_document_suggestion

        tree_id = self.kwargs["tree_id"]
        suggestion = get_object_or_404(
            DocumentExtractionSuggestion,
            pk=request.POST.get("suggestion_id"),
            media__gedcom_tree_id=tree_id,
            status=DocumentExtractionSuggestion.Status.PENDING,
        )
        action = request.POST.get("action")
        if action == "accept":
            try:
                event = apply_document_suggestion(suggestion, tree_id)
                messages.success(
                    request,
                    _("Ereignis „%(etype)s“ erstellt.") % {"etype": event.event_type.name},
                )
            except ValueError as exc:
                messages.error(request, str(exc))
        elif action == "reject":
            suggestion.status = DocumentExtractionSuggestion.Status.REJECTED
            suggestion.save(update_fields=["status", "updated_at"])
            messages.info(request, _("Vorschlag abgelehnt."))
        return redirect("genview:document-suggestion-review", tree_id=tree_id)


class MediaObjectCreateView(LoginRequiredMixin, TreeEditAccessMixin, CreateView):
    model = MediaObject
    form_class = MediaObjectForm
    template_name = "genview/mediaobject_form.html"

    def dispatch(self, request, *args, **kwargs):
        # Deine bisherige, perfekte Logik bleibt!
        self.person = None
        self.family = None
        self.source = None
        self.event = None  

        tree_id = kwargs.get("tree_id")

        if "person_pk" in kwargs:
            self.person = get_object_or_404(Individual, pk=kwargs.get("person_pk"), gedcom_tree_id=tree_id)
        if "family_pk" in kwargs:
            self.family = get_object_or_404(Family, pk=kwargs.get("family_pk"), gedcom_tree_id=tree_id)
        if "source_pk" in kwargs:
            self.source = get_object_or_404(Source, pk=kwargs.get("source_pk"), gedcom_tree_id=tree_id)
        if "event_pk" in kwargs:
            self.event = get_object_or_404(Event, pk=kwargs.get("event_pk"), gedcom_tree_id=tree_id)

        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Baum-ID an die Form übergeben (für die Querysets)
        kwargs["tree_id"] = self.kwargs.get("tree_id")
        return kwargs

    def get_context_data(self, **kwargs):
        """Übergibt die gefundenen Objekte an das Template, damit JS sie anzeigen kann."""
        context = super().get_context_data(**kwargs)
        context['tree_id'] = self.kwargs.get("tree_id")
        context['person'] = self.person
        context['family'] = self.family
        context['source'] = self.source
        context['event'] = self.event
        return context

    def form_valid(self, form):
        # 1. Den Baum zuweisen
        form.instance.gedcom_tree_id = self.kwargs.get("tree_id")
        
        # 2. Speichern. Da wir ein ModelForm nutzen, speichert super().form_valid() 
        # das Objekt UND alle ausgewählten Many-to-Many Verbindungen aus dem Formular automatisch!
        response = super().form_valid(form)
        
        # Hinweis: Die .add() Aufrufe haben wir entfernt! 
        # Warum? Weil die Felder jetzt im UI vorbefüllt sind. Wenn der Nutzer das 
        # vorbefüllte Feld versehentlich weggklickt, würde .add() es gegen seinen Willen 
        # wieder hinzufügen. Wir vertrauen jetzt voll auf das Formular.
        return response

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, _("Bild erfolgreich hochgeladen."))

        if self.person:
            return reverse_lazy(
                "genview:individual-detail",  
                kwargs={"tree_id": tree_id, "pk": self.person.pk},
            )
        if self.family:
            return reverse_lazy(
                "genview:family-detail", 
                kwargs={"tree_id": tree_id, "pk": self.family.pk}
            )
        if self.source:
            return reverse_lazy(
                "genview:source-detail", 
                kwargs={"tree_id": tree_id, "pk": self.source.pk}
            )
        if self.event:  # <-- NEU
            return reverse_lazy(
                "genview:event-edit",  # Use the URL name for your event edit view
                kwargs={"tree_id": tree_id, "pk": self.event.pk}
            )

        # Fallback: Zur Medien-Übersicht des Baums
        return reverse_lazy("genview:media-list", kwargs={"tree_id": tree_id})
   

class MediaObjectUpdateView(LoginRequiredMixin, TreeEditAccessMixin, UpdateView):
    model = MediaObject
    form_class = MediaObjectForm
    template_name = "genview/mediaobject_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()

        # Person übergeben (bei CreateView ggf. None, wenn man aus der Galerie kommt)
        kwargs["person"] = getattr(self, "person", None)

        # NEU: Baum-ID für die Sicherheits-Filter im Formular übergeben!
        kwargs["tree_id"] = self.kwargs.get("tree_id")

        return kwargs

    def get_queryset(self):
        # SICHERHEITS-FIX: Stelle sicher, dass das gesuchte Media-Objekt
        # auch wirklich zu dem Baum in der URL gehört!
        tree_id = self.kwargs.get("tree_id")
        return MediaObject.objects.filter(gedcom_tree_id=tree_id)

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")

        # LOGIK: Nach dem Update einfach auf die Detail-Seite des Bildes leiten!
        tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, _("Medium erfolgreich aktualisiert."))
        return reverse_lazy("genview:media-detail", kwargs={"tree_id": tree_id, "pk": self.object.pk})


class MediaObjectDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = MediaObject
    template_name = "genview/mediaobject_confirm_delete.html"

    def get_queryset(self):
        # SICHERHEITS-FIX: Verhindert, dass Bilder anderer Bäume gelöscht werden
        tree_id = self.kwargs.get("tree_id")
        return MediaObject.objects.filter(gedcom_tree_id=tree_id)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Reiche die person_pk aus der URL an das Template weiter (falls vorhanden)
        context["person_pk"] = self.kwargs.get("person_pk")
        return context

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        person_pk = self.kwargs.get("person_pk")

        messages.success(self.request, _("Bild wurde entfernt."))

        if person_pk:
            return reverse_lazy(
                "genview:individual-detail",
                kwargs={"tree_id": tree_id, "pk": person_pk},
            )

        # Fallback, falls kein person_pk in der URL übergeben wurde
        return reverse_lazy("genview:tree-list")


class BulkMediaUploadView(LoginRequiredMixin, TreeEditAccessMixin, TemplateView):
    template_name = "genview/bulk_media_upload.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tree_id = self.kwargs.get("tree_id")
        
        # Zählen, wie viele Bilder aktuell noch in der Datenbank fehlen
        context['missing_media_count'] = MediaObject.objects.filter(
            gedcom_tree_id=tree_id,
            file=''
        ).count()
        return context

    def post(self, request, *args, **kwargs):
        tree_id = self.kwargs.get("tree_id")
        
        # 'media_files' ist der Name des Eingabefeldes im HTML-Formular
        files = request.FILES.getlist('media_files')
        
        matched_count = 0
        unmatched_count = 0

        allowed_ext = MediaObjectForm.ALLOWED_EXTENSIONS
        max_bytes = MediaObjectForm.MAX_UPLOAD_BYTES

        for uploaded_file in files:
            filename = uploaded_file.name
            ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
            if ext not in allowed_ext:
                unmatched_count += 1
                continue
            if getattr(uploaded_file, "size", 0) > max_bytes:
                unmatched_count += 1
                continue

            # Exact basename match only (avoid photo.jpg matching old_photo.jpg).
            from pathlib import PurePath
            basename = PurePath(filename).name
            media_objs = MediaObject.objects.filter(
                gedcom_tree_id=tree_id,
                file="",
            ).filter(
                Q(gedcom_original_filepath__iexact=basename)
                | Q(gedcom_original_filepath__iendswith="/" + basename)
                | Q(gedcom_original_filepath__iendswith="\\" + basename)
            )

            if media_objs.count() == 1:
                media = media_objs.first()
                media.file = uploaded_file
                media.save()
                matched_count += 1
            else:
                unmatched_count += 1

        # Nutzer-Feedback
        if matched_count > 0:
            messages.success(request, _("%(count)s Bilder wurden erfolgreich zugeordnet!") % {"count": matched_count})
        if unmatched_count > 0:
            messages.warning(request, _("%(count)s hochgeladene Dateien konnten keinem fehlenden Eintrag zugeordnet werden.") % {"count": unmatched_count})

        # Nach dem Post-Request laden wir die Seite neu (Post/Redirect/Get-Pattern)
        return redirect('genview:bulk-media-upload', tree_id=tree_id)


class ToggleMediaCategoryView(LoginRequiredMixin, TreeEditAccessMixin, View):
    """
    Schaltet die Kategorie eines MediaObject von PHOTO ↔ DOCUMENT.
    Erwartet JSON-Body: {"id": <media_id>}
    Liefert JSON-Response: {"id": ..., "new_category": "...", "error": null}
    """
    def post(self, request, *args, **kwargs):
        try:
            data = request.POST or request.body
            # JSON-Payload akzeptieren
            if request.content_type == "application/json":
                import json
                payload = json.loads(request.body)
            else:
                payload = request.POST
            media_id = payload.get("id")
            if not media_id:
                return HttpResponseBadRequest(
                    JsonResponse({"error": "Keine ID übergeben"}))
        except Exception as e:
            return HttpResponseBadRequest(JsonResponse({"error": str(e)}))

        tree_id = self.kwargs.get("tree_id")
        media = get_object_or_404(MediaObject, pk=media_id, gedcom_tree_id=tree_id)

        # ---- Kategorie umschalten ----
        if media.category == MediaObject.Category.PHOTO:
            media.category = MediaObject.Category.DOCUMENT
        else:
            media.category = MediaObject.Category.PHOTO
        media.save(update_fields=["category"])

        return JsonResponse({
            "id": media.id,
            "new_category": media.category,
            "error": None,
        })


# --------------------------------------------------------------
# 6️⃣ Places
# --------------------------------------------------------------

# --- 1. List View ---
class PlaceListView(TreeAccessMixin, SortableListViewMixin, FilterableListViewMixin, ListView):
    model = Place
    template_name = "genview/place_list.html"
    context_object_name = "places"
    paginate_by = 50

    # --- Sorting---
    sortable_fields = ['name']
    default_sort_field = 'name'
    default_sort_dir = 'asc'

    # --- Filter ---
    search_fields = [
        'name'
    ]

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        # Fetch places for this tree, count the linked events, and sort alphabetically
        qs = Place.objects.filter(gedcom_tree_id=tree_id).annotate(
            event_count=Count('events'))
        
        # 2. Filter aus dem Mixin anwenden (durchsucht die search_fields)
        filters = self.get_queryset_filters()
        if filters:
            qs = qs.filter(filters)

        # 3. Sortierung aus dem Mixin anwenden (nutzt nun unser annotiertes 'person_sort')
        ordering = self.get_ordering()
        if ordering:
            qs = qs.order_by(ordering)

        return qs
    

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Pass the tree_id so we can build links in the template
        context["tree_id"] = self.kwargs.get("tree_id")
        return context

# --- 2. Detail View ---
class PlaceDetailView(TreeAccessMixin, DetailView):
    model = Place
    template_name = "genview/place_detail.html"
    context_object_name = "place"

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        
        sorted_events_qs = Event.objects.order_by(
            F("parsed_date").asc(nulls_last=True)
        )

        return Place.objects.filter(gedcom_tree_id=tree_id).prefetch_related(
            Prefetch("events", queryset=sorted_events_qs)
        )

# --- 3. Create View ---
class PlaceCreateView(LoginRequiredMixin, TreeEditAccessMixin, CreateView):
    model = Place
    form_class = PlaceForm
    template_name = "genview/place_form.html"

    def form_valid(self, form):
        form.instance.gedcom_tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, _("Ort erfolgreich hinzugefügt."))
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("genview:place-list", kwargs={"tree_id": self.kwargs.get("tree_id")})
    
# --- 4. Update View ---
class PlaceUpdateView(LoginRequiredMixin, TreeEditAccessMixin, UpdateView):
    model = Place
    form_class = PlaceForm
    template_name = "genview/place_form.html"

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        return Place.objects.filter(gedcom_tree_id=tree_id)

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, _("Orte aktualisiert."))
        return reverse_lazy("genview:place-list", kwargs={"tree_id": tree_id})

# --- 5. Delete View ---
class PlaceDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = Place
    template_name = "genview/place_confirm_delete.html"

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        return Place.objects.filter(gedcom_tree_id=tree_id)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tree_id"] = self.kwargs.get("tree_id")
        return context

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, _("Der Ort wurde erfolgreich gelöscht."))
        return reverse_lazy("genview:place-list", kwargs={"tree_id": tree_id})
    

class PlaceDeduplicationView(LoginRequiredMixin, TreeEditAccessMixin, TemplateView):
    template_name = "genview/place_deduplication.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        tree_id = self.kwargs.get("tree_id")
        
        # Wir übergeben jetzt die Cluster an das Template
        ctx['clusters'] = get_similar_place_clusters(tree_id, threshold=0.80)
        return ctx

    def post(self, request, *args, **kwargs):
        tree_id = self.kwargs.get("tree_id")
        
        # Der eine Ort, den der Nutzer per Radio-Button gewählt hat
        master_id = request.POST.get("master_id")
        
        # getlist() ist wichtig in Django, um alle versteckten inputs mit demselben Namen als Liste zu holen!
        cluster_ids = request.POST.getlist("cluster_ids")
        
        if master_id and cluster_ids:
            # 1. Master-Ort laden
            master_place = get_object_or_404(Place, pk=master_id, gedcom_tree_id=tree_id)
            
            # 2. Die Duplikate filtern (Alle Cluster-IDs, AUSSER dem gewählten Master)
            duplicate_ids = [cid for cid in cluster_ids if str(cid) != str(master_id)]
            
            if duplicate_ids:
                # 3. Alle Duplikat-Objekte auf einmal aus der DB laden
                duplicate_places = Place.objects.filter(pk__in=duplicate_ids, gedcom_tree_id=tree_id)
                duplicates_count = duplicate_places.count()
                
                # 4. Den dynamischen Merge ausführen
                merge_multiple_places(master_place, duplicate_places)
                
                messages.success(
                    request, 
                    _("Erfolgreich! %(count)s Ort(e) wurden gelöscht und in '%(name)s' integriert.") % {"count": duplicates_count, "name": master_place.name}
                )
            else:
                messages.info(request, _("Es gab keine Duplikate zum Zusammenführen."))
            
        return redirect(request.path)


class PlaceGeocodeView(LoginRequiredMixin, TreeEditAccessMixin, View):
    """
    Erwartet POST-Parameter `place_id`.  Holt den Ort, fragt Nominatim,
    speichert die ersten Koordinaten und gibt das Ergebnis als JSON zurück
    (für AJAX) oder leitet zurück zu `PlaceDetailView` (falls kein AJAX).
    """
    def post(self, request, *args, **kwargs):
        place_id = request.POST.get("place_id")
        # Prefer URL tree_id; ignore spoofed POST tree_id when they disagree.
        tree_id = self.kwargs.get("tree_id") or request.POST.get("tree_id")

        if not tree_id:
            return HttpResponseBadRequest("Missing tree_id")

        if not place_id:
            return HttpResponseBadRequest("Missing place_id")

        place = get_object_or_404(Place, pk=place_id, gedcom_tree_id=tree_id)

        # Build die Such-Query.  Priorität: address > name
        query = place.name
        if not query:
            messages.error(request, _("Ort hat weder Namen noch Adresse – kein Geocode möglich."))
            return redirect(reverse('genview:place-detail', args=[tree_id, place.pk]))

        try:
            results = geocode_place(query, limit=1, country_codes="de")
        except Exception as exc:
            messages.error(request, _("Nominatim-Fehler: %(detail)s") % {"detail": exc})
            return redirect(reverse('genview:place-detail', args=[tree_id, place.pk]))

        if not results:
            messages.warning(request, _("Keine Koordinaten für „%(query)s“ gefunden.") % {"query": query})
            return redirect(reverse('genview:place-detail', args=[tree_id, place.pk]))

        best = results[0]
        place.latitude = best["lat"]
        place.longitude = best["lon"]
        place.save()

        logger.info(best)

        # Wenn es ein AJAX-Request ist, return JSON
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({
                "status": "ok",
                "lat": place.latitude,
                "lon": place.longitude,
                "display_name": best["display_name"],
                "message": _("Koordinaten für %(query)s wurden gefunden.") % {"query": query}
            })
        # Sonst: normales Redirect + Django-Message
        messages.success(request,
                         _("Koordinaten (%(lat)s, %(lon)s) für „%(query)s“ gespeichert.") % {"lat": place.latitude, "lon": place.longitude, "query": query})
        return redirect(reverse('genview:place-detail', args=[tree_id, place.pk]))
        

# --------------------------------------------------------------
# 7️⃣ Events
# --------------------------------------------------------------

# --- 1. List View ---
class EventListView(TreeAccessMixin, SortableListViewMixin, FilterableListViewMixin, ListView):
    model = Event
    template_name = "genview/event_list.html"
    context_object_name = "events"
    paginate_by = 50

    # --- Sorting---
    sortable_fields = ['person_sort', 'parsed_date', 'place']
    default_sort_field = 'parsed_date'
    default_sort_dir = 'asc'

    # --- Filter ---
    search_fields = [
        'individual__given_name', 'individual__surname',
        'family__husband__given_name', 'family__husband__surname',
        'family__wife__given_name', 'family__wife__surname'
    ]
    # Filtert jetzt automatisch auf die ID des EventTypes im ForeignKey
    exact_filter_fields = ['event_type']  

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        
        # FIX 1: 'event_type' zu select_related hinzugefügt für optimale Performance!
        qs = Event.objects.filter(gedcom_tree_id=tree_id).select_related(
            'individual', 'family__husband', 'family__wife', 'event_type'
        ).annotate(
            person_sort=Coalesce(
                'individual__surname',
                'family__husband__surname',
                'family__wife__surname'
            )
        )

        filters = self.get_queryset_filters()
        if filters:
            qs = qs.filter(filters)

        ordering = self.get_ordering()
        if ordering:
            if ordering.startswith('-'):
                field_name = ordering[1:]
                qs = qs.order_by(F(field_name).desc(nulls_last=True))
            else:
                qs = qs.order_by(F(ordering).asc(nulls_last=True))

        return qs
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # FIX 2: Wir holen die dynamischen Event-Typen aus der neuen Tabelle
        # (Alphabetisch sortiert, damit das Dropdown schön aufgeräumt ist)
        context['event_types'] = EventType.objects.all().order_by('name') 
        
        return context


class EventDetailView(TreeAccessMixin, DetailView):
    model = Event
    template_name = "genview/event_detail.html"

    def get_queryset(self):
        """
        Sicherheit: Stellt sicher, dass das Event überhaupt zu dem Baum 
        gehört, der in der URL aufgerufen wurde.
        Performance: Lädt die verknüpften Tabellen direkt mit.
        """
        tree_id = self.kwargs.get("tree_id")
        return Event.objects.filter(gedcom_tree_id=tree_id).select_related(
            'event_type', 'individual', 'family', 'place'
        )

    # -----------------------------------------------------------------
    # Security Check for Data Privacy
    # -----------------------------------------------------------------
    def get_object(self, queryset=None):
        # 1. Hole das EREIGNIS aus der Datenbank.
        # (Das get_queryset oben sichert bereits ab, dass es zum Baum gehört)
        event = super().get_object(queryset)

        # 2. Nutze die Helfermethode aus dem TreeAccessMixin
        apply_privacy = self.get_apply_privacy()

        # 3. Die IDOR-Sperre: Wenn Datenschutz gilt UND das Ereignis vertraulich ist:
        if apply_privacy and event.is_confidential:
            raise PermissionDenied(
                "Dieses Ereignis unterliegt den Datenschutzrichtlinien. "
                "Sie haben keine Berechtigung, diese Detailseite aufzurufen."
            )

        # 4. Nur wenn alles okay ist, wird das Ereignis an das Template übergeben
        return event
    

class EventCreateView(LoginRequiredMixin, TreeEditAccessMixin, CreateView):
    model = Event
    form_class = EventForm
    template_name = "genview/event_form.html"

    def get_initial(self):
        initial = super().get_initial()
        
        # Holt die IDs direkt aus der URL (?individual=3 oder ?family=5)
        if 'individual' in self.request.GET:
            initial['individual'] = self.request.GET.get('individual')
        if 'family' in self.request.GET:
            initial['family'] = self.request.GET.get('family')
            
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['target_type'] = self.kwargs.get('target_type', 'individual')
        kwargs['tree_id'] = self.kwargs.get('tree_id')
        return kwargs

    def form_valid(self, form):
        # 1. Den Baum zuweisen
        form.instance.gedcom_tree_id = self.kwargs.get("tree_id")
        
        # 2. Speichern lassen (dadurch wird self.object erstellt)
        response = super().form_valid(form)
        
        # 3. Maßgeschneiderte Erfolgsmeldung NACH dem erfolgreichen Speichern
        if self.object.individual:
            messages.success(self.request, _("Ereignis für %(person)s erfolgreich hinzugefügt.") % {"person": self.object.individual})
        elif self.object.family:
            messages.success(self.request, _("Familien-Ereignis erfolgreich hinzugefügt."))
        else:
            messages.success(self.request, _("Ereignis erfolgreich gespeichert."))

        return response

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        
        # Zurück zur Person?
        if self.object.individual:
            return reverse("genview:individual-detail", kwargs={"tree_id": tree_id, "pk": self.object.individual.pk})
            
        # Zurück zur Familie?
        elif self.object.family:
            return reverse("genview:family-detail", kwargs={"tree_id": tree_id, "pk": self.object.family.pk})
            
        # Fallback zur Liste
        return reverse("genview:event-list", kwargs={"tree_id": tree_id})
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["target_type"] = self.kwargs.get('target_type')
        context["tree_id"] = self.kwargs.get("tree_id")
        return context


class EventUpdateView(LoginRequiredMixin, TreeEditAccessMixin, UpdateView):
    model = Event
    form_class = EventForm
    template_name = "genview/event_form.html"

    def get_queryset(self):
        # SECURITY FIX: Verhindert das Bearbeiten von fremden Events per geratener ID
        tree_id = self.kwargs.get("tree_id")
        return Event.objects.filter(gedcom_tree_id=tree_id)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["tree_id"] = self.kwargs.get("tree_id")
        
        # FIX: Dem Formular mitteilen, welchen Typ wir gerade bearbeiten,
        # damit es irrelevante Dropdowns (wie Familie bei einer Person) versteckt.
        if self.object.family:
            kwargs["target_type"] = "family"
        else:
            kwargs["target_type"] = "individual"
            
        return kwargs

    def form_valid(self, form):
        # FIX: Saubere Erfolgsmeldungen (und kein "Bild" mehr 😉)
        if self.object.individual:
            messages.success(self.request, _("Ereignis für %(person)s erfolgreich aktualisiert.") % {"person": self.object.individual})
        elif self.object.family:
            messages.success(self.request, _("Familien-Ereignis erfolgreich aktualisiert."))
        else:
            messages.success(self.request, _("Ereignis erfolgreich aktualisiert."))

        return super().form_valid(form)
    
    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        
        if self.object.individual:
            return reverse("genview:individual-detail", kwargs={"tree_id": tree_id, "pk": self.object.individual.pk})
        elif self.object.family:
            return reverse("genview:family-detail", kwargs={"tree_id": tree_id, "pk": self.object.family.pk})
            
        return reverse("genview:event-list", kwargs={"tree_id": tree_id})

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["tree_id"] = self.kwargs.get("tree_id")
        
        # Den target_type auch an das Template übergeben (nützlich für Überschriften)
        context["target_type"] = "family" if self.object.family else "individual"
        context["person"] = self.object.individual
        context["family"] = self.object.family
        return context


class AddExistingMediaToEventView(LoginRequiredMixin, TreeEditAccessMixin, FormView):
    template_name = "genview/add_existing_media.html"
    form_class = AddExistingMediaForm

    def get_form_kwargs(self):
        """Übergibt den aktuellen Stammbaum an die Form für das Queryset."""
        kwargs = super().get_form_kwargs()
        # TreeAccessMixin stellt meistens das Tree-Objekt bereit, 
        # alternativ holen wir es über die URL-Parameter
        tree_id = self.kwargs.get("tree_id")
        kwargs['tree'] = get_object_or_404(Tree, pk=tree_id)
        return kwargs

    def form_valid(self, form):
        tree_id = self.kwargs.get("tree_id")
        event_id = self.kwargs.get("event_id")
        
        # Das bestehende Ereignis holen
        event = get_object_or_404(Event, pk=event_id, gedcom_tree_id=tree_id)
        
        # Die ausgewählten Medien aus der Form holen
        selected_media = form.cleaned_data['media_objects']
        
        # MAGIE: Mittels .add(*queryset) fügen wir alle ausgewählten Medien 
        # dem Many-to-Many Feld 'media_objects' des Events hinzu!
        event.media_objects.add(*selected_media)
        
        messages.success(self.request, _("%(count)s Medien erfolgreich mit dem Ereignis verknüpft.") % {"count": selected_media.count()})
        
        # Weiterleitung zurück zur Detailseite des Events oder der Person
        if event.individual:
            return redirect('genview:individual-detail', tree_id=tree_id, pk=event.individual.pk)
        elif event.family:
            return redirect('genview:family-detail', tree_id=tree_id, pk=event.family.pk)
        return redirect('genview:tree-dashboard', tree_id=tree_id)


class EventDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = Event
    template_name = "genview/event_confirm_delete.html"

    def get_queryset(self):
        return Event.objects.filter(gedcom_tree_id=self.kwargs.get("tree_id"))

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        
        # 1. Prüfen: Gehörte das gelöschte Event zu einer Person?
        if self.object.individual:
            messages.success(self.request, _("Ereignis erfolgreich gelöscht."))
            return reverse_lazy(
                "genview:individual-detail", 
                kwargs={"tree_id": tree_id, "pk": self.object.individual.pk}
            )
            
        # 2. Prüfen: Gehörte das Event zu einer Familie?
        elif self.object.family:
            messages.success(self.request, _("Familien-Ereignis erfolgreich gelöscht."))
            return reverse_lazy(
                "genview:family-detail", 
                kwargs={"tree_id": tree_id, "pk": self.object.family.pk}
            )
            
        # 3. Fallback
        messages.success(self.request, _("Ereignis erfolgreich gelöscht."))
        # Passe diesen Fallback an deine existierende Übersichtsseite an
        return reverse_lazy("genview:tree-detail", kwargs={"tree_id": tree_id})

   
#
# 7️⃣.1️⃣ EventTypes
# --------------------------------------------------------------


# 1. READ: Liste aller EventTypes
class EventTypeListView(SuperuserRequiredMixin, ListView):
    model = EventType
    template_name = "genview/eventtype_list.html"
    context_object_name = "event_types"

# 2. CREATE: Neuen EventType anlegen
class EventTypeCreateView(SuperuserRequiredMixin, CreateView):
    model = EventType
    fields = ['tag', 'name', 'category', 'is_visible']
    template_name = "genview/eventtype_form.html"
    success_url = reverse_lazy('genview:eventtype-list')

# 3. UPDATE: Bestehenden EventType bearbeiten
class EventTypeUpdateView(SuperuserRequiredMixin, UpdateView):
    model = EventType
    fields = ['tag', 'name', 'category', 'is_visible']
    template_name = "genview/eventtype_form.html"
    success_url = reverse_lazy('genview:eventtype-list')

# 4. DELETE: EventType löschen (Vorsicht wegen on_delete=RESTRICT)
class EventTypeDeleteView(SuperuserRequiredMixin, DeleteView):
    model = EventType
    template_name = "genview/eventtype_confirm_delete.html"
    success_url = reverse_lazy('genview:eventtype-list')


# --------------------------------------------------------------
# 2️⃣ Sources
# --------------------------------------------------------------

# --- 1. List View ---
class SourceListView(LoginRequiredMixin, TreeAccessMixin, ListView):
    model = Source
    template_name = "genview/source_list.html"
    context_object_name = "sources"
    paginate_by = 25

    def get_queryset(self):
        # SECURITY FIX: Only show sources for this tree
        tree_id = self.kwargs.get("tree_id")
        return Source.objects.filter(gedcom_tree_id=tree_id).order_by("title")

# --- 2. Detail View ---
class SourceDetailView(LoginRequiredMixin, TreeAccessMixin, DetailView):
    model = Source
    template_name = "genview/source_detail.html"
    context_object_name = "source"

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        return Source.objects.filter(gedcom_tree_id=tree_id)

# --- 3. Create View ---
class SourceCreateView(LoginRequiredMixin, TreeEditAccessMixin, CreateView):
    model = Source
    form_class = SourceForm
    template_name = "genview/source_form.html"

    def form_valid(self, form):
        # Automatically assign the source to the current tree
        tree_id = self.kwargs.get("tree_id")
        form.instance.gedcom_tree_id = tree_id
        messages.success(self.request, _("Quelle erfolgreich erstellt."))
        return super().form_valid(form)

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        return reverse_lazy("genview:source-list", kwargs={"tree_id": tree_id})

# --- 4. Update View ---
class SourceUpdateView(LoginRequiredMixin, TreeEditAccessMixin, UpdateView):
    model = Source
    form_class = SourceForm
    template_name = "genview/source_form.html"

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        return Source.objects.filter(gedcom_tree_id=tree_id)

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, _("Quelle aktualisiert."))
        return reverse_lazy("genview:source-list", kwargs={"tree_id": tree_id})

class AddExistingMediaToSourceView(LoginRequiredMixin, TreeEditAccessMixin, FormView):
    template_name = "genview/add_existing_media.html"
    form_class = AddExistingMediaForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        tree_id = self.kwargs.get("tree_id")
        kwargs['tree'] = get_object_or_404(Tree, pk=tree_id)
        return kwargs

    def form_valid(self, form):
        tree_id = self.kwargs.get("tree_id")
        source_id = self.kwargs.get("source_id")
        
        # Die bestehende Quelle holen
        source = get_object_or_404(Source, pk=source_id, gedcom_tree_id=tree_id)
        
        # Die ausgewählten Medien holen
        selected_media = form.cleaned_data['media_objects']
        
        # Da 'sources' im MediaObject definiert ist, greifen wir über den 
        # related_name 'media_objects' von der Quelle aus darauf zu:
        source.media_objects.add(*selected_media)
        
        messages.success(self.request, _("%(count)s Medien erfolgreich mit der Quelle verknüpft.") % {"count": selected_media.count()})
        
        # Zurück zur Detailseite der Quelle leiten
        return redirect('genview:source-detail', tree_id=tree_id, pk=source.pk)

# --- 5. Delete View ---
class SourceDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = Source
    template_name = "genview/source_confirm_delete.html"

    def get_queryset(self):
        tree_id = self.kwargs.get("tree_id")
        return Source.objects.filter(gedcom_tree_id=tree_id)

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, _("Quelle gelöscht."))
        return reverse_lazy("genview:source-list", kwargs={"tree_id": tree_id})
    

# --------------------------------------------------------------
# Search API
# --------------------------------------------------------------

class GenericSelect2APIView(TreeAccessMixin, View):
    """
    Eine generische API-View für Select2-Dropdowns.
    Kann für jedes beliebige Modell im Stammbaum verwendet werden.
    """
    model = None          # Z.B. Individual, Source, Place
    search_fields = []    # Z.B. ['given_name', 'surname'] oder ['title']
    
    def get_display_text(self, obj):
        """
        Kann überschrieben werden, um festzulegen, wie das Objekt 
        im Dropdown angezeigt wird. Standard: Die __str__ Methode.
        """
        return str(obj)

    def get(self, request, *args, **kwargs):
        tree_id = self.kwargs.get("tree_id")
        query = request.GET.get('q', '').strip()
        
        if len(query) < 2:
            return JsonResponse({'results': []})
            
        # 1. Auf den aktuellen Stammbaum filtern
        qs = self.model.objects.filter(gedcom_tree_id=tree_id)
        apply_privacy = self.get_apply_privacy()
        if self.model is Individual:
            qs = apply_privacy_to_individual_qs(qs, apply_privacy)
        elif self.model is Family:
            qs = apply_privacy_to_family_qs(qs, apply_privacy, tree_id)
        elif self.model is MediaObject:
            qs = apply_privacy_to_media_qs(qs, apply_privacy, tree_id)
        elif self.model is Event:
            qs = apply_privacy_to_event_qs(qs, apply_privacy, tree_id)

        # 2. Token search: AND matches first, then OR (e.g. "Max Werner" → Werner Max)
        if self.search_fields:
            objects = _search_queryset_by_tokens(qs, query, self.search_fields, limit=30)
        else:
            objects = []
        
        # 4. JSON zusammenbauen
        results = [
            {'id': obj.id, 'text': self.get_display_text(obj)} 
            for obj in objects
        ]
        
        return JsonResponse({'results': results})
    
# --- Die API für Personen ---
class IndividualSearchAPIView(GenericSelect2APIView):
    model = Individual
    search_fields = [
        "given_name",
        "surname",
        "gedcom_id",
        "alternative_names__given_name",
        "alternative_names__surname",
    ]
    
    def get_display_text(self, obj):
        return f"{obj.given_name} {obj.surname} ({obj.gedcom_id})"

# --- Die API für Quellen ---
class SourceSearchAPIView(GenericSelect2APIView):
    model = Source
    search_fields = ['title', 'author', 'gedcom_id']

# --- Die API für Familien ---
class FamilySearchAPIView(GenericSelect2APIView):
    model = Family
    search_fields = ['gedcom_id', 'husband__surname', 'wife__surname']
    
    def get_display_text(self, obj):
        # Nutzt einfach die __str__ Methode deiner Familie (z.B. "Mustermann & Müller")
        return f"{obj} ({obj.gedcom_id})"
    
# --- Die API für Events ---    
class EventSearchAPIView(GenericSelect2APIView):
    model = Event
    # Wir suchen im Event-Typ, im Datum, in der Notiz/Description und im Namen der Person!
    search_fields = [
        'event_type__name', 
        'raw_date', 
        'description',
        'individual__given_name',
        'individual__surname',
        'family__husband__surname'
    ]
    
    def get_display_text(self, obj):
        """
        Baut einen sprechenden Namen für das Dropdown zusammen.
        Beispiel: 'Geburt - 1880 (Max Mustermann)' oder 'Heirat - 1890 (Familie Müller)'
        """
        # 1. Typ und Datum
        event_name = obj.event_type.name if obj.event_type else "Unbekanntes Ereignis"
        date_str = obj.parsed_date.strftime('%d.%m.%Y') if obj.parsed_date else obj.raw_date
        date_display = f" am {date_str}" if date_str else ""
        
        # 2. Wem gehört das Event?
        owner_display = ""
        if obj.individual:
            owner_display = f" ({obj.individual.given_name} {obj.individual.surname})"
        elif obj.family:
            owner_display = f" (Familie {obj.family})"
            
        return f"{event_name}{date_display}{owner_display}"    

# --- Die API für Media Objects --- 
class MediaSearchAPIView(GenericSelect2APIView):
    model = MediaObject
    # Wir suchen im Titel, in der Beschreibung und im Dateipfad!
    search_fields = ['title', 'description', 'gedcom_original_filepath']
    
    def get_display_text(self, obj):
        # Zeigt z.B. "Hochzeitsfoto (Foto)" oder "Geburtsurkunde Max (Dokument)" an
        category = obj.get_category_display()
        title = obj.title if obj.title else "Ohne Titel"
        return f"{title} [{category}]"

# --- Die API für Orte ---     
class PlaceSearchAPIView(GenericSelect2APIView):
    model = Place
    search_fields = ['name']
    
    def get_display_text(self, obj):
        return obj.name


class UserSearchAPIView(LoginRequiredMixin, View):
    """
    AJAX endpoint for Select2 user search (membership management).
    Restricted to tree admins / superusers; returns minimal fields.
    """
    def get(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            is_tree_admin = TreeMembership.objects.filter(
                user=request.user,
                role=TreeMembership.Role.ADMIN,
            ).exists()
            if not is_tree_admin:
                raise PermissionDenied("Nur Stammbaum-Admins dürfen Benutzer suchen.")

        query = request.GET.get('q', '').strip()
        if len(query) < 2:
            return JsonResponse({'results': []})

        users_qs = User.objects.exclude(is_superuser=True)
        user_fields = ["username", "first_name", "last_name"]
        users = _search_queryset_by_tokens(users_qs, query, user_fields, limit=20)

        results = []
        for u in users:
            full = u.get_full_name().strip()
            display = f"{u.username} ({full})" if full else u.username
            results.append({'id': u.id, 'text': display})

        return JsonResponse({'results': results})

# --------------------------------------------------------------
# --- ADMIN
# --------------------------------------------------------------

from io import StringIO
import tempfile
from django.core.management import call_command
from .forms import GedcomImportForm, TreeMembershipForm, UserRegisterForm


class RegisterView(CreateView):
    form_class = UserRegisterForm
    template_name = 'genview/register.html'
    success_url = reverse_lazy('two_factor:login') # Nach Erfolg zurück zum Login
    RATE_LIMIT = 5
    RATE_WINDOW_SECONDS = 3600

    def dispatch(self, request, *args, **kwargs):
        # Wenn ein bereits eingeloggter User versucht sich zu registrieren,
        # leiten wir ihn einfach auf das Dashboard weiter.
        if request.user.is_authenticated:
            return redirect('genview:tree-list') # Passe das an deine Startseite an
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        from django.core.cache import cache

        ip = self.request.META.get("REMOTE_ADDR", "unknown")
        key = f"register_rate:{ip}"
        count = cache.get(key, 0)
        if count >= self.RATE_LIMIT:
            messages.error(
                self.request,
                _("Zu viele Registrierungsversuche. Bitte später erneut versuchen."),
            )
            return redirect(self.success_url)
        cache.set(key, count + 1, self.RATE_WINDOW_SECONDS)

        # Formular validieren, aber noch nicht final in die DB schreiben
        user = form.save(commit=False)
        
        # 🔥 DER SICHERHEITS-SCHLÜSSEL: 
        # Account ist inaktiv, bis der Superadmin ihn freischaltet!
        user.is_active = False 
        user.save()

        # Dem User eine freundliche Nachricht anzeigen
        messages.info(
            self.request, 
            _("Registrierung für „%(username)s“ erfolgreich! Dein Account ist aktuell noch inaktiv. Ein Administrator prüft deine Anmeldung und schaltet dich in Kürze frei.") % {"username": user.username}
        )
        return redirect(self.success_url)


# --- 1. Die Listenansicht aller Benutzer ---
class UserManagementListView(SuperuserRequiredMixin, ListView):
    model = User
    template_name = "genview/user_management.html"
    context_object_name = "users"

    def get_queryset(self):
        # Wir listen alle User auf, außer den Superuser selbst (damit man sich nicht aus Versehen löscht)
        return User.objects.exclude(is_superuser=True).order_by('-date_joined')


# --- 2. Die Action-View für Aktivierung und Löschen ---
class UserManagementActionView(SuperuserRequiredMixin, View):
    def post(self, request, user_id, action):
        user = get_object_or_404(User, pk=user_id)
        
        if action == "toggle_active":
            user.is_active = not user.is_active
            user.save()
            status = "aktiviert" if user.is_active else "deaktiviert"
            messages.success(request, _("Benutzer %(username)s wurde erfolgreich %(status)s.") % {"username": user.username, "status": status})
            
        elif action == "delete":
            username = user.username
            user.delete()
            messages.success(request, _("Benutzer %(username)s wurde dauerhaft gelöscht.") % {"username": username})
            
        return redirect('genview:user-management-list')
    

class GedcomImportView(SuperuserRequiredMixin, FormView):
    """
    Front-End-Ersatz für `python manage.py import_gedcom`.
    1. User wählt Datei + Namen → POST
    2. Wir speichern die Datei kurzzeitig in einem TemporaryFile
    3. `call_command('import_gedcom', <temp-path>, '--tree-name', <name>)`
    4. Bei Erfolg: Success-Message + Weiterleitung zur Baum-Detail-Seite
    5. Bei Fehler: Fehlermeldung im Formular anzeigen
    """
    template_name = "genview/gedcom_import.html"
    form_class = GedcomImportForm
    success_url = reverse_lazy("tree-list")   # ggf. an deine Ansicht anpassen

    # -----------------------------------------------------------------
    # Optional: Wenn du das Edit-Mixin nicht nutzt, überschreibe `test_func`
    # -----------------------------------------------------------------
    # def test_func(self):
    #     # Beispiel: jeder eingeloggte User darf importieren
    #     return self.request.user.is_authenticated

    def form_valid(self, form):
        gedcom_file = form.cleaned_data["gedcom_file"]
        tree_name   = form.cleaned_data["tree_name"]

        out_stream = StringIO()
        err_stream = StringIO()

        # 1️⃣ Temporäre Datei erzeugen (wird automatisch gelöscht)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            for chunk in gedcom_file.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name

        # 2️⃣ Management-Command ausführen
        try:
            # Das gleiche CLI-Interface wie im Terminal:
            call_command(
                "import_gedcom",
                tmp_path,
                "--tree-name",
                tree_name,
                stdout=out_stream, # Fängt self.stdout.write() ab
                stderr=err_stream, # Fängt self.stderr.write() ab
            )

            # 🔥 HIER IST DER TRICK: Die Texte aus dem Puffer extrahieren!
            import_log = out_stream.getvalue()
            error_log = err_stream.getvalue()
            
            # (Optional für dich als Entwickler: Den Text trotzdem ins Server-Terminal drucken)
            print("--- GEDCOM IMPORT PROTOKOLL ---")
            print(import_log)
            if error_log:
                print("--- FEHLER ---")
                print(error_log)

            # 🔥 NEU: Den gerade erstellten Baum holen und die ADMIN-Rolle vergeben 🔥
            # (order_by('-id') garantiert, dass wir den allerneuesten Baum erwischen, 
            # falls jemand zufällig exakt denselben Namen nochmal verwendet)
            new_tree = Tree.objects.filter(name=tree_name).order_by('-id').first()
            
            if new_tree:
                TreeMembership.objects.get_or_create(
                    user=self.request.user,
                    gedcom_tree=new_tree,
                    defaults={'role': TreeMembership.Role.ADMIN}
                )

        except Exception as exc:                 # catch any DB-/Import-Fehler
            messages.error(self.request,
                _("Import fehlgeschlagen: %(detail)s") % {"detail": exc})
            return self.form_invalid(form)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

        # 4️⃣ Erfolgsmeldung (temp file already removed in finally)
        messages.success(self.request,
            _("GEDCOM-Datei erfolgreich importiert – Stammbaum „%(name)s“ angelegt.") % {"name": tree_name})
        return super().form_valid(form)

    # -----------------------------------------------------------------
    # Hilfs-Methoden, um das Command-Output ggf. zu loggen
    # -----------------------------------------------------------------
    def _capture_stdout(self):
        from io import StringIO
        self._stdout_buf = StringIO()
        return self._stdout_buf

    def _capture_stderr(self):
        from io import StringIO
        self._stderr_buf = StringIO()
        return self._stderr_buf


class TreeMembershipManageView(LoginRequiredMixin, TreeAdminAccessMixin, View):
    template_name = "genview/tree_membership_manage.html"

    def get_formset(self, tree, post_data=None):
        """Erstellt das Formset für die bestehenden Mitglieder dieses Baums."""
        MembershipFormSet = modelformset_factory(
            TreeMembership,
            form=TreeMembershipForm,
            extra=0,
            can_delete=True # Aktiviert Djangos interne Löschlogik im Formset
        )
        queryset = TreeMembership.objects.filter(gedcom_tree=tree).select_related('user')
        return MembershipFormSet(data=post_data, queryset=queryset)

    def get(self, request, tree_id):
        tree = get_object_or_404(Tree, pk=tree_id)
        formset = self.get_formset(tree)
        return render(request, self.template_name, {
            'tree': tree,
            'formset': formset,
            'tree_id': tree_id,
        })

    def post(self, request, tree_id):
        tree = get_object_or_404(Tree, pk=tree_id)

        # 🔥 NEU: Prüfen, ob der Sichtbarkeits-Schalter geklickt wurde
        if 'toggle_public' in request.POST:
            tree.is_public = not tree.is_public
            tree.save()
            status = "öffentlich zugänglich" if tree.is_public else "privat und geschützt"
            messages.info(request, _("Der Stammbaum ist jetzt %(status)s.") % {"status": status})
            return redirect('genview:manage-memberships', tree_id=tree.id)
        

        formset = self.get_formset(tree, request.POST)
        
        # 1. Workflow: NEUEN USER HINZUFÜGEN
        new_user_id = request.POST.get('new_user')
        new_user_role = request.POST.get('new_user_role')
        
        if new_user_id and new_user_role:
            valid_roles = {c.value for c in TreeMembership.Role}
            if new_user_role not in valid_roles:
                messages.error(request, _("Ungültige Rolle."))
                return redirect('genview:manage-memberships', tree_id=tree.id)

            new_user = get_object_or_404(User, pk=new_user_id)
            # unique_together absichern mit get_or_create
            membership, created = TreeMembership.objects.get_or_create(
                gedcom_tree=tree,
                user=new_user,
                defaults={'role': new_user_role}
            )
            if created:
                messages.success(request, _("Benutzer %(username)s wurde erfolgreich hinzugefügt.") % {"username": new_user.username})
            else:
                messages.warning(request, _("%(username)s ist bereits Mitglied in diesem Stammbaum.") % {"username": new_user.username})
            return redirect('genview:manage-memberships', tree_id=tree.id)

        # 2. Workflow: BESTEHENDE ROLLER ÄNDERN / LÖSCHEN
        if formset.is_valid():
            formset.save()
            messages.success(request, _("Mitgliederlisten und Rollen erfolgreich aktualisiert."))
            return redirect('genview:manage-memberships', tree_id=tree.id)
            
        return render(request, self.template_name, {
            'tree': tree,
            'formset': formset,
        })
    
from django.views.decorators.http import require_POST
from .decorators import superuser_required

@superuser_required(raise_exception=False, redirect_to="/no-permission/")
@require_POST
def toggle_eventtype_visibility(request, pk):
    """Schaltet die Sichtbarkeit eines EventTypes per AJAX um."""
    event_type = get_object_or_404(EventType, pk=pk)
    
    # Status umkehren
    event_type.is_visible = not event_type.is_visible
    
    # Aus Performance-Gründen nur dieses eine Feld speichern
    event_type.save(update_fields=['is_visible'])
    
    return JsonResponse({
        'status': 'success', 
        'is_visible': event_type.is_visible
    })

