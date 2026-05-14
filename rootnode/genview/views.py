from __future__ import annotations

import json
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch
from django.db.models import Q, F
from django.http import JsonResponse
from django.views.generic import ListView, DetailView, CreateView, DeleteView
from django.views.generic.edit import UpdateView
from django.shortcuts import render, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils.safestring import mark_safe
from typing import Set, List, Tuple

from logging import getLogger

logger = getLogger(__name__)

from .models import Individual, Family, Event, ChildFamilyLink, MediaObject, Tree, TreeMembership
from .forms import IndividualForm, IndividualSearchForm, FamilyForm, ChildFamilyLinkForm, MediaObjectForm
from .mixins import TreeAccessMixin, TreeEditAccessMixin

def home(request):
    """
    Simple landing page that just welcomes the user.
    You can extend it later (e.g. add a dashboard, charts, etc.).
    """
    return render(request, "genview/home.html")

# ----------------------------------------------------------------------
# 1️⃣ Trees
# ----------------------------------------------------------------------

class TreeListView(LoginRequiredMixin, ListView):
    model = Tree
    template_name = 'genview/tree_list.html'
    context_object_name = 'trees'

    def get_queryset(self):
        # Only show trees where the user has a membership
        allowed_tree_ids = TreeMembership.objects.filter(
            user=self.request.user
        ).values_list('gedcom_tree_id', flat=True)
        return Tree.objects.filter(id__in=allowed_tree_ids)

# ----------------------------------------------------------------------
# 2️⃣ Individuals
# ----------------------------------------------------------------------

class IndividualListView(LoginRequiredMixin, TreeAccessMixin, ListView):
    model = Individual
    template_name = 'genview/individual_list.html'
    context_object_name = 'people'
    paginate_by = 25  # Helpful if you have thousands of records

    def get_queryset(self):
        tree_id = self.kwargs.get('tree_id')

        birth_qs = Event.objects.filter(event_type=Event.EventType.BIRTH)

        return Individual.objects.filter(gedcom_tree_id=tree_id).prefetch_related(
            Prefetch('events', queryset=birth_qs, to_attr='birth_events')
        ).order_by('surname', 'given_name')


class IndividualDetailView(LoginRequiredMixin, TreeAccessMixin, DetailView):
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
        tree_id = self.kwargs.get('tree_id')
        return (
            Individual.objects.filter(gedcom_tree_id=tree_id).prefetch_related(
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


        # -------------------------------------------------------------
        # 4️⃣ Events
        # -------------------------------------------------------------
        # Fetch events where the person is the individual, OR the husband, OR the wife
        combined_events = Event.objects.filter(
            Q(individual=person) | 
            Q(family__husband=person) | 
            Q(family__wife=person)
        ).order_by(F('parsed_date').asc(nulls_last=True)) # Sorts by date, puts None values at the end
        
        ctx['timeline_events'] = combined_events

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

        #
        # for tree view
        #

        # Helper function to format a person for dTree
        def format_person(p):
            if not p: return None

            birth_date = ""
            death_date = ""

            # Check the person's events to find birth and death dates
            # We use .all() and loop to avoid hitting the database multiple times per person
            for event in p.events.all():
                if event.event_type == 'BIRT' and not birth_date:
                    birth_date = event.raw_date
                elif event.event_type == 'DEAT' and not death_date:
                    death_date = event.raw_date

            # Format the date string nicely
            date_str = ""
            if birth_date or death_date:
                b_str = birth_date if birth_date else "?"
                d_str = death_date if death_date else "Present"
                
                if not death_date:
                    date_str = f"b. {b_str}"
                else:
                    date_str = f"{b_str} - {d_str}"

            return {
                "name": f"{p.given_name} {p.surname}",
                "class": "node",
                "extra": {
                    "id": p.pk,
                    "gedcom_id": p.gedcom_id,
                    "dates": date_str,
                    "url": p.get_absolute_url()
                }
            }
        
        # helper to build the marriage string
        def get_marriage_str(fam):
            if not fam: return ""
            m_date = fam.marriage_date_raw
            m_place = fam.marriage_place
            parts = [p for p in (m_date, m_place) if p]
            return "⚭ " + ", ".join(parts) if parts else ""

        tree_data = []
        parent_link = person.parental_families.first()
        
        if parent_link and (parent_link.family.husband or parent_link.family.wife):
            family = parent_link.family
            root_person = family.husband if family.husband else family.wife
            spouse_person = family.wife if family.husband else None

            root_node = format_person(root_person)
            target_node = format_person(person)
            target_node["marriages"] = []
            
            # --- TARGET'S MARRIAGES ---
            spouse_families = list(person.families_as_husband.all()) + list(person.families_as_wife.all())
            
            for fam in spouse_families:
                partner = fam.wife if fam.husband == person else fam.husband
                # Ensure we always have a dictionary, even for unknown partners
                spouse_node = format_person(partner) if partner else {"name": "Unknown Partner", "class": "node", "extra": {}}
                
                # Inject marriage info
                m_str = get_marriage_str(fam)
                if m_str and "extra" in spouse_node:
                    spouse_node["extra"]["marriage_info"] = m_str
                
                marriage_data = {
                    "spouse": spouse_node,
                    "children": [format_person(c.child) for c in fam.children.all()]
                }
                target_node["marriages"].append(marriage_data)

            # --- PARENTS' MARRIAGE ---
            spouse_node = format_person(spouse_person) if spouse_person else {"name": "Unknown Partner", "class": "node", "extra": {}}
            m_str = get_marriage_str(family)
            if m_str and "extra" in spouse_node:
                spouse_node["extra"]["marriage_info"] = m_str

            root_node["marriages"] = [{
                "spouse": spouse_node,
                "children": [target_node]
            }]
            
            tree_data.append(root_node)

        else:
            # If no parents, target node is the root
            target_node = format_person(person)
            target_node["marriages"] = []
            
            spouse_families = list(person.families_as_husband.all()) + list(person.families_as_wife.all())
            for fam in spouse_families:
                partner = fam.wife if fam.husband == person else fam.husband
                spouse_node = format_person(partner) if partner else {"name": "Unknown Partner", "class": "node", "extra": {}}
                
                # Inject marriage info
                m_str = get_marriage_str(fam)
                if m_str and "extra" in spouse_node:
                    spouse_node["extra"]["marriage_info"] = m_str

                marriage_data = {
                    "spouse": spouse_node,
                    "children": [format_person(c.child) for c in fam.children.all()]
                }
                target_node["marriages"].append(marriage_data)
                
            tree_data.append(target_node)

        # Convert the Python dictionary to a JSON string for the template
        ctx['tree_json'] = json.dumps(tree_data)

        return ctx 


class IndividualCreateView(LoginRequiredMixin, TreeEditAccessMixin, CreateView):
    model = Individual
    form_class = IndividualForm
    template_name = "genview/individual_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Person erfolgreich angelegt.")  # optional
        return response

    def get_success_url(self):
        return reverse_lazy("genview:individual-detail", kwargs={"pk": self.object.pk})


class IndividualUpdateView(LoginRequiredMixin, TreeEditAccessMixin, UpdateView):
    model = Individual
    form_class = IndividualForm
    template_name = "genview/individual_form.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Personendaten wurden gespeichert.")  # optional
        return response

    def get_success_url(self):
        return reverse_lazy("genview:individual-detail", kwargs={
            "tree_id": self.object.gedcom_tree_id,
            "pk": self.object.pk
        })


class IndividualDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = Individual
    template_name = "genview/individual_confirm_delete.html"
    success_url = reverse_lazy("genview:individual-list")


class IndividualSearchView(LoginRequiredMixin, TreeAccessMixin, ListView):
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


class IndividualSearchAjaxView(LoginRequiredMixin, TreeAccessMixin, ListView):
    model = Individual
    paginate_by = 25
    ordering = ["surname", "given_name"]

    def get_queryset(self):
        # 1. SECURITY FIX: Lock the base queryset to the current tree FIRST
        tree_id = self.kwargs.get('tree_id')
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
        Rückgabe eines JSON‑Objektes:
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
        
# ----------------------------------------------------------------------
# 3️⃣ Families
# ----------------------------------------------------------------------

class FamilyListView(LoginRequiredMixin, TreeAccessMixin, ListView):
    """
    Zeigt eine paginierte Liste aller Familien des ausgewählten Baums.
    Zusätzliche Annotationen:
    * `children_count` – wie viele Kinder in der Familie verknüpft sind.
    * `husband_name` / `wife_name` – für schnellere Anzeige (keine extra Query).
    """
    model = Family
    template_name = "genview/family_list.html"
    context_object_name = "families"
    paginate_by = 25

    def get_queryset(self):
        # 1. Grab the tree_id from the URL
        tree_id = self.kwargs.get('tree_id')

        # 2. Filter by the tree FIRST, then do your joins and annotations
        qs = (
            Family.objects.filter(gedcom_tree_id=tree_id) 
            .select_related("husband", "wife")
            .annotate(children_count=Count("children"))
            .order_by("gedcom_id")
        )
        return qs


class FamilyDetailView(LoginRequiredMixin, TreeAccessMixin, DetailView):
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
    
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['tree_id'] = self.kwargs.get('tree_id')
        return ctx


class FamilyCreateView(LoginRequiredMixin, TreeEditAccessMixin, CreateView):
    model = Family
    form_class = FamilyForm
    template_name = "genview/family_form.html"

    def form_valid(self, form):
        messages.success(self.request, "Familie angelegt.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("genview:family-detail", kwargs={"pk": self.object.pk})


class FamilyUpdateView(LoginRequiredMixin, TreeEditAccessMixin, UpdateView):
    model = Family
    form_class = FamilyForm
    template_name = "genview/family_form.html"

    def form_valid(self, form):
        messages.success(self.request, "Familie wurde aktualisiert.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("genview:family-detail", kwargs={
            "tree_id": self.object.gedcom_tree_id, 
            "pk": self.object.pk
        })


class FamilyDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = Family
    template_name = "genview/family_confirm_delete.html"
    success_url = reverse_lazy("genview:family-list")


# ----------------------------------------------------------------------
#  4️⃣ Kind‑zu‑Familie‑Link – hinzufügen / bearbeiten
# ----------------------------------------------------------------------
class ChildFamilyLinkCreateView(LoginRequiredMixin, TreeEditAccessMixin, CreateView):
    model = ChildFamilyLink
    form_class = ChildFamilyLinkForm
    template_name = "genview/childfamilylink_form.html"

    def get_success_url(self):
        return reverse_lazy(
            "genview:family-detail",
            kwargs={"pk": self.object.family.pk},
        )


class ChildFamilyLinkDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = ChildFamilyLink
    template_name = "genview/childfamilylink_confirm_delete.html"

    def get_success_url(self):
        return reverse_lazy(
            "genview:family-detail",
            kwargs={"pk": self.object.family.pk},
        )
      

# ----------------------------------------------------------------------
# 5️⃣ Media
# ----------------------------------------------------------------------

# --------------------------------------------------------------
#  Medien‑Liste (optional – Übersicht aller Medien)
# --------------------------------------------------------------
class MediaObjectListView(LoginRequiredMixin, TreeAccessMixin, ListView):
    model = MediaObject
    template_name = "genview/mediaobject_list.html"
    context_object_name = "media"
    paginate_by = 20

    def get_queryset(self):
        # 1. Grab the tree ID from the URL
        tree_id = self.kwargs.get('tree_id')

        # 2. SECURITY FIX: Return ONLY media belonging to this specific tree
        return MediaObject.objects.filter(
            gedcom_tree_id=tree_id
        ).order_by("title")


# --------------------------------------------------------------
#  Bild‑Upload & Zuordnung zu Personen
# --------------------------------------------------------------
class MediaObjectCreateView(LoginRequiredMixin, TreeEditAccessMixin, CreateView):
    model = MediaObject
    form_class = MediaObjectForm
    template_name = "genview/mediaobject_form.html"

    def dispatch(self, request, *args, **kwargs):
        self.person = None
        person_pk = kwargs.get("person_pk")
        if person_pk:
            # Sicherstellen, dass die Person auch zum aktuellen Baum gehört
            self.person = get_object_or_404(
                Individual, 
                pk=person_pk, 
                gedcom_tree_id=kwargs.get("tree_id")
            )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["person"] = self.person
        return kwargs

    def form_valid(self, form):
        """
        Hier weisen wir dem neuen MediaObject den aktuellen Baum zu,
        bevor es in die Datenbank geschrieben wird.
        """
        tree_id = self.kwargs.get("tree_id")
        form.instance.gedcom_tree_id = tree_id
        
        # Falls eine Person verknüpft ist, fügen wir sie direkt dem ManyToMany-Feld hinzu
        # (Das muss nach dem super().form_valid passieren, da das Objekt erst eine ID braucht)
        response = super().form_valid(form)
        if self.person:
            self.object.individuals.add(self.person)
        
        return response

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, "Bild erfolgreich hochgeladen.")
        
        if self.person:
            return reverse_lazy(
                "genview:individual-detail", # Achte auf Konsistenz (Unterstrich vs Bindestrich)
                kwargs={
                    "tree_id": tree_id, 
                    "pk": self.person.pk
                },
            )
        
        # Fallback: Zur Medien-Übersicht des Baums
        return reverse_lazy(
            "genview:media-list", 
            kwargs={"tree_id": tree_id}
        )


# --------------------------------------------------------------
# Bild-Bearbeitung
# --------------------------------------------------------------
class MediaObjectUpdateView(LoginRequiredMixin, TreeEditAccessMixin, UpdateView):
    model = MediaObject
    form_class = MediaObjectForm
    template_name = "genview/mediaobject_form.html"

    def get_queryset(self):
        # SICHERHEITS-FIX: Stelle sicher, dass das gesuchte Media-Objekt
        # auch wirklich zu dem Baum in der URL gehört!
        tree_id = self.kwargs.get("tree_id")
        return MediaObject.objects.filter(gedcom_tree_id=tree_id)

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        
        # Versuche die erste verknüpfte Person zu finden
        person = self.object.individuals.first()
        
        if person:
            # Achte darauf, ob dein URL-Name einen Bindestrich oder Unterstrich hat!
            # Meistens ist es 'individual_detail'
            return reverse_lazy("genview:individual-detail", kwargs={
                "tree_id": tree_id, 
                "pk": person.pk
            })
            
        # Fallback, falls das Bild an keine Person gehängt ist (z.B. Familienbild)
        messages.success(self.request, "Bild aktualisiert.")
        return reverse_lazy("genview:tree-list") # Oder deine Medien-Übersicht


# --------------------------------------------------------------
# Bild-Löschung (nach Bestätigung)
# --------------------------------------------------------------
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
        context['person_pk'] = self.kwargs.get('person_pk')
        return context

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        person_pk = self.kwargs.get("person_pk")
        
        messages.success(self.request, "Bild wurde entfernt.")
        
        if person_pk:
            return reverse_lazy("genview:individual-detail", kwargs={
                "tree_id": tree_id, 
                "pk": person_pk
            })
            
        # Fallback, falls kein person_pk in der URL übergeben wurde
        return reverse_lazy("genview:tree-list")
    