from __future__ import annotations

from django.contrib import messages
from django.db.models import Count, Prefetch
from django.db.models import Q
from django.http import JsonResponse
from django.views.generic import ListView, DetailView, CreateView, DeleteView
from django.views.generic.edit import UpdateView
from django.shortcuts import render, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils.safestring import mark_safe
from typing import Set, List, Tuple
from .models import Individual, Family, Event, ChildFamilyLink, MediaObject
from .forms import IndividualForm, IndividualSearchForm, FamilyForm, ChildFamilyLinkForm, MediaObjectForm

def home(request):
    """
    Simple landing page that just welcomes the user.
    You can extend it later (e.g. add a dashboard, charts, etc.).
    """
    return render(request, "genview/home.html")

# ----------------------------------------------------------------------
# Individuals
# ----------------------------------------------------------------------

class IndividualListView(ListView):
    model = Individual
    template_name = 'genview/individual_list.html'
    context_object_name = 'people'
    paginate_by = 25  # Helpful if you have thousands of records

    def get_queryset(self):
        birth_qs = Event.objects.filter(event_type=Event.EventType.BIRTH)
        return Individual.objects.prefetch_related(
            Prefetch('events', queryset=birth_qs, to_attr='birth_events')
        ).order_by('surname', 'given_name')


class IndividualDetailView(DetailView):
    model = Individual
    template_name = 'genview/individual_detail.html'
    context_object_name = 'person'

    def get_queryset(self):
        """
        Wir holen gleich:
        * Person‑Events (bereits über das Standard‑Related‑Name `events`).
        * Ehepartner‑Familien (husband‑ bzw. wife‑Relation) – dafür brauchen wir
          das zugehörige Gegenstück (wife bzw. husband).
        * Kinder‑Links (Family.children) – selektiert über das Through‑Model.
        * Eltern‑Familien (person.parental_families) – ebenfalls über Prefetch.
        """
        return (
            Individual.objects.prefetch_related(
                # 1️⃣ Events (einfaches Related‐Name)
                Prefetch("events", queryset=Event.objects.all()),

                # 2️⃣ Familien, in denen die Person Husband ist → wir brauchen die Wife
                Prefetch(
                    "families_as_husband",
                    queryset=Family.objects.select_related("wife")
                                        .prefetch_related(
                                              Prefetch("wife__events", to_attr="prefetched_events")
                                          )
                                          .prefetch_related(
                                              Prefetch(
                                                  "children",
                                                  queryset=ChildFamilyLink.objects.select_related("child")
                                              )
                                          ),
                ),

                # 3️⃣ Familien, in denen die Person Wife ist → wir brauchen den Husband
                Prefetch(
                    "families_as_wife",
                    queryset=Family.objects.select_related("husband")
                                            .prefetch_related(
                                              Prefetch("husband__events", to_attr="prefetched_events")
                                          )
                                          .prefetch_related(
                                              Prefetch(
                                                  "children",
                                                  queryset=ChildFamilyLink.objects.select_related("child")
                                              )
                                          ),
                ),
                # -----------------------------------------------------------------
                # Events für die Kinder selbst (falls du später deren weitere Daten
                # brauchst). Hier holen wir die Events der Child‑Individuals.
                # -----------------------------------------------------------------
                Prefetch(
                    "families_as_husband__children__child",
                    queryset=Individual.objects.prefetch_related("events"),
                ),
                Prefetch(
                    "families_as_wife__children__child",
                    queryset=Individual.objects.prefetch_related("events"),
                ),

            ).order_by('-id')
        )


    # -----------------------------------------------------------------
    # Kontext‑Aufbereitung: Ehepartner, Kinder, Eltern
    # -----------------------------------------------------------------
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        person: Individual = self.object

        # -------------------------------------------------------------
        # 1️⃣ Ehepartner (falls vorhanden) – wir suchen die *anderen*
        #     Elternteile in den Familien, in denen die Person ein
        #     Husband bzw. Wife ist.
        # -------------------------------------------------------------
        spouse = None
        family = None
        # Husband‑Familie → Wife ist der Ehepartner
        husband_fam = person.families_as_husband.first()
        if husband_fam and husband_fam.wife:
            spouse = husband_fam.wife
            family = husband_fam
        # Wife‑Familie → Husband ist der Ehepartner (falls noch nicht gefunden)
        if not spouse:
            wife_fam = person.families_as_wife.first()
            if wife_fam and wife_fam.husband:
                spouse = wife_fam.husband
                family = wife_fam

        ctx["spouse"] = spouse
        ctx["family"] = family

        # -------------------------------------------------------------
        # 2️⃣ Kinder (alle ChildFamilyLink‑Objekte, über die beiden Familien)
        # -------------------------------------------------------------
        children_links = []      # Liste von ChildFamilyLink‑Instanzen
        # Husband‑Familie: ihre Children‑Links
        if husband_fam:
            children_links.extend(list(husband_fam.children.all()))
        # Wife‑Familie: ebenfalls Children‑Links (kann Überschneidungen geben)
        if person.families_as_wife.first():
            children_links.extend(list(person.families_as_wife.first().children.all()))
        # Doppelte Einträge entfernen (gleiche ChildFamilyLink‑Instanz)
        children_links = list({cl.id: cl for cl in children_links}.values())
        ctx["children_links"] = children_links

        # -------------------------------------------------------------
        # 3️⃣ Eltern‑Familien (direkt über das M2M‑Through‑Model)
        # -------------------------------------------------------------
        ctx["parent_families"] = list(person.parental_families.all())
        #   Jeder Familie hat bereits husband und wife via `select_related` oben.

        # -------------- Portrait holen --------------
        portrait = person.media_objects.filter(is_portrait=True).first()
        # Falls kein explizites Portrait gesetzt ist, nimm das erste Bild:
        if not portrait:
            portrait = person.media_objects.first()
        ctx['portrait'] = portrait

        # Alle übrigen Bilder (ausgenommen das Portrait‑Bild)
        ctx['gallery_images'] = (
            person.media_objects.exclude(pk=portrait.pk) if portrait else person.media_objects.all()
        )

        return ctx


class IndividualCreateView(CreateView):
    model = Individual
    form_class = IndividualForm
    template_name = "genview/individual_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Person erfolgreich angelegt.")  # optional
        return response

    def get_success_url(self):
        return reverse_lazy("genview:individual-detail", kwargs={"pk": self.object.pk})


class IndividualUpdateView(UpdateView):
    model = Individual
    form_class = IndividualForm
    template_name = "genview/individual_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Personendaten wurden gespeichert.")  # optional
        return response

    def get_success_url(self):
        return reverse_lazy("genview:individual-detail", kwargs={"pk": self.object.pk})


class IndividualDeleteView(DeleteView):
    model = Individual
    template_name = "genview/individual_confirm_delete.html"
    success_url = reverse_lazy("genview:individual-list")


class IndividualSearchView(ListView):
    """
    Listet Personen und filtert nach dem Suchbegriff `q`.
    Der Suchbegriff wird in mehreren Feldern geprüft:
      * gedcom_id
      * given_name, surname, name_prefix, name_suffix
      * sex (Anzeige von MALE/FEMALE/UNKNOWN)
    """
    model = Individual
    template_name = "genview/individual_list.html"   # das gleiche Template wie zuvor
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
    # QuerySet filtern – case‑insensitive, mehrere Felder
    # ------------------------------------------------------------------
    def get_queryset(self):
        qs = super().get_queryset()
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


class IndividualSearchAjaxView(ListView):
    model = Individual
    paginate_by = 25
    ordering = ["surname", "given_name"]

    def get_queryset(self):
        qs = super().get_queryset()
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
        Rückgabe eines JSON‑Objektes:
        {
            "table":    "<tbody>…</tbody>",
            "pager":    "<nav>…</nav>"
        }
        """
        table_html = render_to_string(
            "genview/_individual_table.html",   # <tbody>‑Fragment
            {"object_list": context["object_list"]},
            request=self.request,
        )
        pager_html = render_to_string(
            "genview/_individual_pager.html",   # komplette Pagination‑Leiste
            {
                "page_obj": context["page_obj"],
                "paginator": context["paginator"],
                "is_paginated": context["is_paginated"],
                "search_query": self.request.GET.get("q", "").strip(),
            },
            request=self.request,
        )
        return JsonResponse({"table": table_html, "pager": pager_html})
    
# ----------------------------------------------------------------------
# Families
# ----------------------------------------------------------------------

class FamilyListView(ListView):
    """
    Zeigt eine paginierte Liste aller Familien.
    Zusätzliche Annotationen:
    * `children_count` – wie viele Kinder in der Familie verknüpft sind.
    * `husband_name` / `wife_name` – für schnellere Anzeige (keine extra Query).
    """
    model = Family
    template_name = "genview/family_list.html"
    context_object_name = "families"
    paginate_by = 25

    def get_queryset(self):
        # Anzahl der Kinder pro Familie ermitteln (via annotate)
        qs = (
            Family.objects.select_related("husband", "wife")
            .annotate(children_count=Count("children"))
            .order_by("gedcom_id")
        )
        return qs


class FamilyDetailView(DetailView):
    """
    Detail‑Ansicht einer Familie.
    - `husband` und `wife` werden bereits über `select_related` geladen.
    - Kinder über ein Prefetch, das das `relationship_type` mitliefert.
    - Events und Media‑Objects werden ebenfalls vorgeholt, damit im Template
      keine extra DB‑Queries entstehen.
    """
    model = Family
    template_name = "genview/family_detail.html"
    context_object_name = "family"

    def get_queryset(self):
        return (
            Family.objects.select_related("husband", "wife")
            .prefetch_related(
                # Kinder‑Links inkl. zugehörigem Child‑Individual
                Prefetch(
                    "children",
                    queryset=ChildFamilyLink.objects.select_related("child"),
                ),
                # Alle Events (MARR, DIV, …) der Familie
                #Prefetch("events", queryset=Event.objects.all()),
                Prefetch(
                    "events",
                    queryset=Event.objects.filter(event_type=Event.EventType.MARRIAGE),
                    to_attr="marriage_events",   # .marriage_events[0] ist das Event
                ),
                # Medien‑Objekte, die an die Familie gebunden sind
                Prefetch("media_objects", queryset=MediaObject.objects.all()),
            )
            .order_by("-id")
        )


class FamilyCreateView(CreateView):
    model = Family
    form_class = FamilyForm
    template_name = "genview/family_form.html"

    def form_valid(self, form):
        messages.success(self.request, "Familie angelegt.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("genview:family-detail", kwargs={"pk": self.object.pk})


class FamilyUpdateView(UpdateView):
    model = Family
    form_class = FamilyForm
    template_name = "genview/family_form.html"

    def form_valid(self, form):
        messages.success(self.request, "Familie wurde aktualisiert.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("genview:family_detail", kwargs={"pk": self.object.pk})


class FamilyDeleteView(DeleteView):
    model = Family
    template_name = "genview/family_confirm_delete.html"
    success_url = reverse_lazy("genview:family-list")


# ----------------------------------------------------------------------
#  Kind‑zu‑Familie‑Link – hinzufügen / bearbeiten
# ----------------------------------------------------------------------
class ChildFamilyLinkCreateView(CreateView):
    model = ChildFamilyLink
    form_class = ChildFamilyLinkForm
    template_name = "genview/childfamilylink_form.html"

    def get_success_url(self):
        return reverse_lazy(
            "genview:family-detail",
            kwargs={"pk": self.object.family.pk},
        )


class ChildFamilyLinkDeleteView(DeleteView):
    model = ChildFamilyLink
    template_name = "genview/childfamilylink_confirm_delete.html"

    def get_success_url(self):
        return reverse_lazy(
            "genview:family-detail",
            kwargs={"pk": self.object.family.pk},
        )
      

# --------------------------------------------------------------
#  Medien‑Liste (optional – Übersicht aller Medien)
# --------------------------------------------------------------
class MediaObjectListView(ListView):
    model = MediaObject
    template_name = "genview/mediaobject_list.html"
    context_object_name = "media"
    paginate_by = 20
    ordering = ["title"]


# --------------------------------------------------------------
#  Bild‑Upload & Zuordnung zu Personen
# --------------------------------------------------------------
class MediaObjectCreateView(CreateView):
    model = MediaObject
    form_class = MediaObjectForm
    template_name = "genview/mediaobject_form.html"

    def dispatch(self, request, *args, **kwargs):
        """
        Holt die Person (falls `person_pk` in der URL übergeben wurde) und
        speichert sie im View‑Objekt, damit wir sie im `get_form_kwargs()`
        weitergeben können.
        """
        self.person = None
        person_pk = kwargs.get("person_pk")
        if person_pk:
            self.person = get_object_or_404(Individual, pk=person_pk)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Wir übergeben die Person‑Instanz (oder None) an das Form‑Konstruktor‑Argument.
        kwargs["person"] = self.person
        return kwargs

    def get_success_url(self):
        """
        Nach erfolgreichem Upload zurück zur Detail‑Seite der Person,
        zu der das Bild gerade hochgeladen wurde.
        """
        messages.success(self.request, "Bild erfolgreich hochgeladen.")
        if self.person:
            return reverse_lazy(
                "genview:individual-detail",
                kwargs={"pk": self.person.pk},
            )
        # Fallback: Zur Medien‑Übersicht
        return reverse_lazy("genview:media-list")

# --------------------------------------------------------------
# 3️⃣  Bild‑Bearbeitung (für ein beliebiges MediaObject)
# --------------------------------------------------------------
class MediaObjectUpdateView(UpdateView):
    model = MediaObject
    form_class = MediaObjectForm
    template_name = "genview/mediaobject_form.html"

    def get_success_url(self):
        # Nach dem Ändern zurück zur Person‑Detail‑Seite
        person = self.object.individuals.first()
        return reverse_lazy("genview:individual_detail", kwargs={"pk": person.pk})


# --------------------------------------------------------------
# 4️⃣  Bild‑Löschung (nach Bestätigung)
# --------------------------------------------------------------
class MediaObjectDeleteView(DeleteView):
    model = MediaObject
    template_name = "genview/mediaobject_confirm_delete.html"

    def get_success_url(self):
        """
        Wir leiten zurück zur Person‑Detail‑Seite,
        von der aus das Bild gelöscht wurde (person_pk via URL übergeben).
        """
        person_pk = self.kwargs.get("person_pk")
        messages.success(self.request, "Bild wurde entfernt.")
        return reverse_lazy("genview:individual_detail", kwargs={"pk": person_pk})
    