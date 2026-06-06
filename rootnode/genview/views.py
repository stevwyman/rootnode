from __future__ import annotations

import json
import os
from itertools import chain
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Subquery, OuterRef, Prefetch
from django.db.models import Q, F
from django.db.models.functions import Coalesce
from django.forms import modelformset_factory
from django.http import JsonResponse, FileResponse, Http404
from django.views import View
from django.views.generic import ListView, DetailView, CreateView, DeleteView, TemplateView
from django.views.generic.edit import UpdateView, FormView
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse_lazy, reverse
from django.utils.safestring import mark_safe
from typing import Set, List, Tuple

from logging import getLogger

logger = getLogger(__name__)

from .models import (
    Individual,
    Family,
    Event,
    EventType,
    ChildFamilyLink,
    MediaObject,
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
    EventTypeForm,
    SourceForm,
    PlaceForm,
)
from .mixins import UserPassesTestMixin, TreeAccessMixin, TreeEditAccessMixin, SortableListViewMixin, FilterableListViewMixin


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
        return qs.order_by('-id')


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
        messages.success(self.request, f"Der Stammbaum '{tree.name}' und alle dazugehörigen Daten wurden unwiderruflich gelöscht.")
        return super().form_valid(form)


class GlobalSearchView(LoginRequiredMixin, TreeAccessMixin, TemplateView):
    template_name = "genview/global_search.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        q = self.request.GET.get('q', '').strip()
        tree_id = self.kwargs.get("tree_id")
        
        results = []

        if q:
            # 1. PERSONEN durchsuchen
            individuals = Individual.objects.filter(gedcom_tree_id=tree_id).filter(
                Q(given_name__icontains=q) | Q(surname__icontains=q) | Q(gedcom_id__icontains=q)
            )[:20] # Limit auf 20, damit die Datenbank nicht explodiert
            
            for ind in individuals:
                ind.search_type = "Person"
                ind.search_icon = "👤"
                ind.search_title = ind.full_name()
                ind.search_desc = f"Geboren: {ind.birth_date_raw or '?'}"
                ind.search_url = ind.get_absolute_url()

            # 2. FAMILIEN durchsuchen (Sucht im Namen des Mannes oder der Frau)
            families = Family.objects.filter(gedcom_tree_id=tree_id).filter(
                Q(husband__surname__icontains=q) | 
                Q(wife__surname__icontains=q) |
                Q(gedcom_id__icontains=q)
            ).select_related('husband', 'wife')[:10]
            
            for fam in families:
                fam.search_type = "Familie"
                fam.search_icon = "👪"
                fam.search_title = str(fam)
                fam.search_desc = f"Heirat: {fam.marriage_date_raw or '?'}"
                fam.search_url = fam.get_absolute_url()

            # 3. ORTE durchsuchen
            places = Place.objects.filter(gedcom_tree_id=tree_id, name__icontains=q)[:10]
            for place in places:
                place.search_type = "Ort"
                place.search_icon = "📍"
                place.search_title = place.name
                place.search_desc = "Ort im Stammbaum"
                # Falls du noch keine Detail-URL für Orte hast, kannst du hier ein '#' setzen
                place.search_url = place.get_absolute_url()

            # 4. QUELLEN durchsuchen
            sources = Source.objects.filter(gedcom_tree_id=tree_id, title__icontains=q)[:10]
            for src in sources:
                src.search_type = "Quelle"
                src.search_icon = "📚"
                src.search_title = src.title
                src.search_desc = src.author or "Kein Autor angegeben"
                src.search_url = src.get_absolute_url()

            # 5. Alles zu einer einzigen flachen Liste zusammenketten!
            results = list(chain(individuals, families, places, sources))

        context['results'] = results
        context['q'] = q
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
        'alternative_names__given_name', 'alternative_names__surname' # 🔥 NEU!
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
        
        # Die saubere, robuste String-Notation für den Prefetch:
        return Individual.objects.filter(gedcom_tree_id=tree_id).prefetch_related(
            "events__media_objects",
            "families_as_husband__wife",             # Holt die Ehefrauen, wenn die Person Ehemann ist
            "families_as_husband__children__child",  # Holt die Kinder-Links + die Kinder-Personen dazu
            "families_as_wife__husband",             # Holt die Ehemänner, wenn die Person Ehefrau ist
            "families_as_wife__children__child",     # Holt die Kinder-Links + die Kinder-Personen dazu
            "events__event_type"
        )

    # -----------------------------------------------------------------
    # Security Check for Data Privacy
    # -----------------------------------------------------------------
    def get_object(self, queryset=None):
        # 1. Hole die Person wie gewohnt aus der Datenbank.
        # (Hier greift bereits die Basis-Absicherung deines TreeAccessMixin,
        # dass die Person überhaupt zu diesem Stammbaum gehört!)
        person = super().get_object(queryset)

        # 2. Nutze die neue Helfermethode aus unserem Mixin (Variante 1 von vorhin)
        apply_privacy = self.get_apply_privacy()

        # 3. Die IDOR-Sperre: Wenn Datenschutz gilt UND die Person vertraulich ist:
        if apply_privacy and person.is_confidential:
            # Django bricht sofort ab und liefert eine saubere "403 Forbidden" Seite aus
            raise PermissionDenied(
                "Diese Person unterliegt den Datenschutzrichtlinien. "
                "Sie haben keine Berechtigung, diese Detailseite aufzurufen."
            )

        # 4. Nur wenn alles okay ist, wird die Person an die View/das Template übergeben
        return person

    # -----------------------------------------------------------------
    # Kontext-Aufbereitung: Ehepartner, Kinder, Eltern
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
        # Husband-Familie → Wife ist der Ehepartner
        husband_fam = person.families_as_husband.first()
        if husband_fam and husband_fam.wife:
            spouse = husband_fam.wife
            family = husband_fam
        # Wife-Familie → Husband ist der Ehepartner (falls noch nicht gefunden)
        if not spouse:
            wife_fam = person.families_as_wife.first()
            if wife_fam and wife_fam.husband:
                spouse = wife_fam.husband
                family = wife_fam

        ctx["spouse"] = spouse
        ctx["family"] = family

        # -------------------------------------------------------------
        # 2️⃣ Kinder (alle ChildFamilyLink-Objekte, über die beiden Familien)
        # -------------------------------------------------------------
        children_links = []  # Liste von ChildFamilyLink-Instanzen
        # Husband-Familie: ihre Children-Links
        if husband_fam:
            children_links.extend(list(husband_fam.children.all()))
        # Wife-Familie: ebenfalls Children-Links (kann Überschneidungen geben)
        if person.families_as_wife.first():
            children_links.extend(list(person.families_as_wife.first().children.all()))
        # Doppelte Einträge entfernen (gleiche ChildFamilyLink-Instanz)
        children_links = list({cl.id: cl for cl in children_links}.values())
        ctx["children_links"] = children_links

        # -------------------------------------------------------------
        # 3️⃣ Eltern-Familien (direkt über das M2M-Through-Model)
        # -------------------------------------------------------------
        ctx["parent_families"] = list(person.parental_families.all())
        #   Jeder Familie hat bereits husband und wife via `select_related` oben.

        # -------------------------------------------------------------
        # 4️⃣ Events
        # -------------------------------------------------------------
        # Fetch events where the person is the individual, OR the husband, OR the wife
        combined_events = Event.objects.filter(
            Q(individual=person) | Q(family__husband=person) | Q(family__wife=person)
        ).prefetch_related('sources').order_by(
            F("parsed_date").asc(nulls_last=True)
        )  # Sorts by date, puts None values at the end

        ctx["timeline_events"] = combined_events

        # -------------- Portrait holen --------------
        """
        TODO:
        Weil deine is_confidential-Methode über .all() auf die verknüpften Personen, Familien und Events zugreift, feuert sie für jedes einzelne Bild in einer Galerie drei Datenbankabfragen ab.
        Wenn du später eine Seite baust, die 50 Bilder (MediaObject) gleichzeitig anzeigt, stelle sicher, dass die View dazu diese Daten vorab lädt:
        MediaObject.objects.filter(...).prefetch_related('individuals', 'families', 'events')
        Damit bleibt dein Galerie-Rendering blitzschnell!
        """
        portrait = person.media_objects.filter(is_portrait=True).first()
        # Falls kein explizites Portrait gesetzt ist, nimm das erste Bild:
        if not portrait:
            portrait = person.media_objects.first()
        ctx["portrait"] = portrait

        # Alle übrigen Bilder (ausgenommen das Portrait-Bild)
        ctx["gallery_images"] = (
            person.media_objects.exclude(pk=portrait.pk)
            if portrait
            else person.media_objects.all()
        )

        #
        # unsere neue intelligente Gallery
        #

        # 🔥 Die magische ODER-Abfrage für die Galerie
        tree_id = person.gedcom_tree_id
        birth_family_ids = ChildFamilyLink.objects.filter(
            child=person
        ).values_list('family_id', flat=True)

        all_gallery_media = MediaObject.objects.filter(
            gedcom_tree_id=tree_id
        ).filter(
            # Bedingung 1: Das Medium hängt direkt an der Person
            Q(individuals=person) | 
            
            # Bedingung 2: Das Medium hängt an einem individuellen Ereignis (z.B. Geburt)
            Q(events__individual=person) |
            
            # Bedingung 3: Das Medium hängt an einem Familien-Ereignis (z.B. Heirat)
            Q(events__family__husband=person) |
            Q(events__family__wife=person) |
            
            # Bedingung 4: Das Medium hängt direkt an der Familie, wo die Person Vater/Mutter ist
            Q(families__husband=person) |
            Q(families__wife=person) |
            
            # 🔥 HIER IST DER ERSATZ: 
            # Das Medium hängt an einer Familie, in der die Person ein Kind ist (nutzt die IDs von oben)
            Q(families__in=birth_family_ids) |
            
            # Bedingung 6: Das Medium hängt an einer Quelle eines individuellen Events
            Q(sources__events__individual=person)
            
        ).distinct() # Verhindert Duplikate in der Anzeige

        # 2. Die Medien in zwei Listen für das Template aufteilen
        ctx['gallery_photos'] = all_gallery_media.filter(category=MediaObject.Category.PHOTO)
        ctx['gallery_documents'] = all_gallery_media.filter(category=MediaObject.Category.DOCUMENT)

        #
        # for tree view
        #

        # Helper function to format a person for dTree
        def format_person(p):
            if not p:
                return None

            birth_date = ""
            death_date = ""

            # Check the person's events to find birth and death dates
            # We use .all() and loop to avoid hitting the database multiple times per person
            for event in p.events.all():
                if event.event_type and event.event_type.tag == 'BIRT' and not birth_date:
                    birth_date = event.raw_date
                elif event.event_type and event.event_type.tag == 'DEAT' and not death_date:
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
                    "url": p.get_absolute_url(),
                },
            }

        # helper to build the marriage string
        def get_marriage_str(fam):
            if not fam:
                return ""
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
            spouse_families = list(person.families_as_husband.all()) + list(
                person.families_as_wife.all()
            )

            for fam in spouse_families:
                partner = fam.wife if fam.husband == person else fam.husband
                # Ensure we always have a dictionary, even for unknown partners
                spouse_node = (
                    format_person(partner)
                    if partner
                    else {"name": "Unknown Partner", "class": "node", "extra": {}}
                )

                # Inject marriage info
                m_str = get_marriage_str(fam)
                if m_str and "extra" in spouse_node:
                    spouse_node["extra"]["marriage_info"] = m_str

                marriage_data = {
                    "spouse": spouse_node,
                    "children": [format_person(c.child) for c in fam.children.all()],
                }
                target_node["marriages"].append(marriage_data)

            # --- PARENTS' MARRIAGE ---
            spouse_node = (
                format_person(spouse_person)
                if spouse_person
                else {"name": "Unknown Partner", "class": "node", "extra": {}}
            )
            m_str = get_marriage_str(family)
            if m_str and "extra" in spouse_node:
                spouse_node["extra"]["marriage_info"] = m_str

            root_node["marriages"] = [
                {"spouse": spouse_node, "children": [target_node]}
            ]

            tree_data.append(root_node)

        else:
            # If no parents, target node is the root
            target_node = format_person(person)
            target_node["marriages"] = []

            spouse_families = list(person.families_as_husband.all()) + list(
                person.families_as_wife.all()
            )
            for fam in spouse_families:
                partner = fam.wife if fam.husband == person else fam.husband
                spouse_node = (
                    format_person(partner)
                    if partner
                    else {"name": "Unknown Partner", "class": "node", "extra": {}}
                )

                # Inject marriage info
                m_str = get_marriage_str(fam)
                if m_str and "extra" in spouse_node:
                    spouse_node["extra"]["marriage_info"] = m_str

                marriage_data = {
                    "spouse": spouse_node,
                    "children": [format_person(c.child) for c in fam.children.all()],
                }
                target_node["marriages"].append(marriage_data)

            tree_data.append(target_node)

        # Convert the Python dictionary to a JSON string for the template
        ctx["tree_json"] = json.dumps(tree_data)

        # pedigree / table view of ancestors
        person = self.object

        # 1. Eltern laden
        father = person.father
        mother = person.mother

        # 2. Ein flaches Dictionary für die 3-Generationen-Tabelle packen
        ctx['pedigree'] = {
            'father': father,
            'mother': mother,
            # Großeltern väterlicherseits (ff = father's father, fm = father's mother)
            'ff': father.father if father else None,
            'fm': father.mother if father else None,
            # Großeltern mütterlicherseits (mf = mother's father, mm = mother's mother)
            'mf': mother.father if mother else None,
            'mm': mother.mother if mother else None,
        }

    
        # Wenn der Datenschutz greift UND die Person vertraulich ist:
        if ctx.get('apply_privacy') and self.object.is_confidential:
            ctx['photos'] = []
            ctx['documents'] = []
        else:
            all_media = self.object.media_objects.all()
            ctx['photos'] = all_media.filter(category='PHOTO')
            ctx['documents'] = all_media.filter(category='DOCUMENT')

        return ctx


class IndividualCreateView(LoginRequiredMixin, TreeEditAccessMixin, CreateView):
    model = Individual
    form_class = IndividualForm
    template_name = "genview/individual_form.html"

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

        messages.success(self.request, f"Person {new_person.full_name()} erfolgreich angelegt.")
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

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, "Personendaten wurden gespeichert.")  # optional
        return response

    def get_success_url(self):
        return reverse_lazy(
            "genview:individual-detail",
            kwargs={"tree_id": self.object.gedcom_tree_id, "pk": self.object.pk},
        )


class IndividualDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = Individual
    template_name = "genview/individual_confirm_delete.html"

    def get_success_url(self):
        # Nach dem Löschen leiten wir den Nutzer zurück zur Personen-Liste
        tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, f"Person erfolgreich gelöscht.")
        return reverse_lazy("genview:individual-list", kwargs={"tree_id": tree_id})


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
        'husband__alternative_names__surname', # 🔥 NEU!
        'wife__given_name', 'wife__surname',
        'wife__alternative_names__surname'     # 🔥 NEU!
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
        return (
            Family.objects.select_related("husband", "wife")
            .prefetch_related(
                # Kinder-Links inkl. zugehörigem Child-Individual
                Prefetch(
                    "children",
                    queryset=ChildFamilyLink.objects.select_related("child"),
                ),
                # Alle Events (MARR, DIV, …) der Familie
                # Prefetch("events", queryset=Event.objects.all()),
                Prefetch(
                    "events",
                    queryset=Event.objects.filter(event_type__tag='MARR'),
                    to_attr="marriage_events",  # .marriage_events[0] ist das Event
                ),
                # Medien-Objekte, die an die Familie gebunden sind
                Prefetch("media_objects", queryset=MediaObject.objects.all()),
            )
            .order_by("-id")
        )

    # -----------------------------------------------------------------
    # Security Check for Data Privacy
    # -----------------------------------------------------------------
    def get_object(self, queryset=None):
        # 1. Hole die Person wie gewohnt aus der Datenbank.
        # (Hier greift bereits die Basis-Absicherung deines TreeAccessMixin,
        # dass die Person überhaupt zu diesem Stammbaum gehört!)
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
        messages.success(self.request, "Familie angelegt.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("genview:family-detail", kwargs={"pk": self.object.pk})


class FamilyUpdateView(LoginRequiredMixin, TreeEditAccessMixin, UpdateView):
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
        messages.success(self.request, "Familie wurde aktualisiert.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            "genview:family-detail",
            kwargs={"tree_id": self.object.gedcom_tree_id, "pk": self.object.pk},
        )


class FamilyDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = Family
    template_name = "genview/family_confirm_delete.html"

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        messages.success(self.request, "Familie und zugehörige Verknüpfungen erfolgreich gelöscht.")
        # Nutzer nach dem Löschen zur Familien-Übersicht (oder Stammbaum) zurückschicken
        return reverse_lazy("genview:family-list", kwargs={"tree_id": tree_id})


# ----------------------------------------------------------------------
#  4️⃣ Kind-zu-Familie-Link – hinzufügen / bearbeiten
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

    def get(self, request, *args, **kwargs):
        # 1. get_object() automatically applies the get_queryset() filter
        # and the TreeAccessMixin automatically checks basic tree access.
        media_obj = self.get_object()

        # ---------------------------------------------------------
        # 🔒 2. DATENSCHUTZ-PRÜFUNG (NEU)
        # ---------------------------------------------------------
        # Hier musst du deine bestehende Logik für 'apply_privacy' einsetzen.
        # (z.B. prüfen, ob der User nur die Rolle "VIEWER" hat).
        # Beispiel: apply_privacy = request.tree_membership.role == 'VIEWER'
        
        apply_privacy = self.get_apply_privacy()  # ERSETZE DIES durch deine echte Rollen-Prüfung!

        if apply_privacy and media_obj.is_confidential:
            # Blockiert den Download mit einem 403 Forbidden Fehler
            raise PermissionDenied("Dieses Dokument enthält vertrauliche Daten und wurde aus Datenschutzgründen gesperrt.")
        
        if apply_privacy and media_obj.is_private:
            # Blockiert den Download mit einem 403 Forbidden Fehler
            raise PermissionDenied("Dieses Dokument enthält vertrauliche Daten und wurde aus Datenschutzgründen gesperrt.")
                
        # ---------------------------------------------------------

        # 3. Check if the file actually exists on the hard drive
        if not media_obj.file or not os.path.exists(media_obj.file.path):
            raise Http404("Datei nicht gefunden.")

        # 4. Serve the file securely
        file_handle = open(media_obj.file.path, "rb")
        return FileResponse(file_handle)


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
            "events__individual",       # Lädt Events von Personen vorab
            "events__family__husband",  # Lädt Events von Familien vorab
            "events__family__wife"
        )
    
        # -----------------------------------------------------------------
    # Security Check for Data Privacy
    # -----------------------------------------------------------------
    def get_object(self, queryset=None):
        # 1. Hole die Person wie gewohnt aus der Datenbank.
        # (Hier greift bereits die Basis-Absicherung deines TreeAccessMixin,
        # dass die Person überhaupt zu diesem Stammbaum gehört!)
        media = super().get_object(queryset)

        # 2. Nutze die neue Helfermethode aus unserem Mixin (Variante 1 von vorhin)
        apply_privacy = self.get_apply_privacy()

        # 3. Die IDOR-Sperre: Wenn Datenschutz gilt UND die Person vertraulich ist:
        if apply_privacy and media.is_confidential:
            # Django bricht sofort ab und liefert eine saubere "403 Forbidden" Seite aus
            raise PermissionDenied(
                "Diese Person unterliegt den Datenschutzrichtlinien. "
                "Sie haben keine Berechtigung, diese Detailseite aufzurufen."
            )
        
        if apply_privacy and media.is_private:
            # Django bricht sofort ab und liefert eine saubere "403 Forbidden" Seite aus
            raise PermissionDenied(
                "Diese Person unterliegt den Datenschutzrichtlinien. "
                "Sie haben keine Berechtigung, diese Detailseite aufzurufen."
            )
        
        # 4. Nur wenn alles okay ist, wird die Person an die View/das Template übergeben
        return media

    
class MediaObjectListView(TreeAccessMixin, ListView):
    model = MediaObject
    template_name = "genview/mediaobject_list.html"
    context_object_name = "media"
    paginate_by = 20

    def get_queryset(self):
        # 1. Grab the tree ID from the URL
        tree_id = self.kwargs.get("tree_id")

        # 2. SECURITY FIX: Return ONLY media belonging to this specific tree
        return MediaObject.objects.filter(gedcom_tree_id=tree_id).order_by("title")


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
        messages.success(self.request, "Bild erfolgreich hochgeladen.")

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
        messages.success(self.request, "Medium erfolgreich aktualisiert.")
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

        messages.success(self.request, "Bild wurde entfernt.")

        if person_pk:
            return reverse_lazy(
                "genview:individual-detail",
                kwargs={"tree_id": tree_id, "pk": person_pk},
            )

        # Fallback, falls kein person_pk in der URL übergeben wurde
        return reverse_lazy("genview:tree-list")


class BulkMediaUploadView(TreeAccessMixin, TemplateView):
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

        for uploaded_file in files:
            filename = uploaded_file.name
            
            # MAGIE: Wir suchen Datensätze, die noch leer sind (file='')
            # UND deren alter GEDCOM-Pfad mit dem hochgeladenen Dateinamen endet.
            # iendswith = Case Insensitive (ignoriert Groß-/Kleinschreibung)
            media_objs = MediaObject.objects.filter(
                gedcom_tree_id=tree_id,
                file=''
            ).filter(gedcom_original_filepath__iendswith=filename)

            if media_objs.exists():
                for media in media_objs:
                    media.file = uploaded_file
                    media.save()
                    matched_count += 1
            else:
                unmatched_count += 1

        # Nutzer-Feedback
        if matched_count > 0:
            messages.success(request, f"{matched_count} Bilder wurden erfolgreich zugeordnet!")
        if unmatched_count > 0:
            messages.warning(request, f"{unmatched_count} hochgeladene Dateien konnten keinem fehlenden Eintrag zugeordnet werden.")

        # Nach dem Post-Request laden wir die Seite neu (Post/Redirect/Get-Pattern)
        return redirect('genview:bulk-media-upload', tree_id=tree_id)


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
        messages.success(self.request, "Ort erfolgreich hinzugefügt.")
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
        messages.success(self.request, "Orte aktualisiert.")
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
        messages.success(self.request, "Der Ort wurde erfolgreich gelöscht.")
        return reverse_lazy("genview:place-list", kwargs={"tree_id": tree_id})
    

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
            messages.success(self.request, f"Ereignis für {self.object.individual} erfolgreich hinzugefügt.")
        elif self.object.family:
            messages.success(self.request, "Familien-Ereignis erfolgreich hinzugefügt.")
        else:
            messages.success(self.request, "Ereignis erfolgreich gespeichert.")

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
            messages.success(self.request, f"Ereignis für {self.object.individual} erfolgreich aktualisiert.")
        elif self.object.family:
            messages.success(self.request, "Familien-Ereignis erfolgreich aktualisiert.")
        else:
            messages.success(self.request, "Ereignis erfolgreich aktualisiert.")

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


class AddExistingMediaToEventView(TreeAccessMixin, FormView):
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
        
        messages.success(self.request, f"{selected_media.count()} Medien erfolgreich mit dem Ereignis verknüpft.")
        
        # Weiterleitung zurück zur Detailseite des Events oder der Person
        if event.individual:
            return redirect('genview:individual-detail', tree_id=tree_id, pk=event.individual.pk)
        elif event.family:
            return redirect('genview:family-detail', tree_id=tree_id, pk=event.family.pk)
        return redirect('genview:tree-dashboard', tree_id=tree_id)


class EventDeleteView(LoginRequiredMixin, TreeEditAccessMixin, DeleteView):
    model = Event
    template_name = "genview/event_confirm_delete.html"

    def get_success_url(self):
        tree_id = self.kwargs.get("tree_id")
        
        # 1. Prüfen: Gehörte das gelöschte Event zu einer Person?
        if self.object.individual:
            messages.success(self.request, "Ereignis erfolgreich gelöscht.")
            return reverse_lazy(
                "genview:individual-detail", 
                kwargs={"tree_id": tree_id, "pk": self.object.individual.pk}
            )
            
        # 2. Prüfen: Gehörte das Event zu einer Familie?
        elif self.object.family:
            messages.success(self.request, "Familien-Ereignis erfolgreich gelöscht.")
            return reverse_lazy(
                "genview:family-detail", 
                kwargs={"tree_id": tree_id, "pk": self.object.family.pk}
            )
            
        # 3. Fallback
        messages.success(self.request, "Ereignis erfolgreich gelöscht.")
        # Passe diesen Fallback an deine existierende Übersichtsseite an
        return reverse_lazy("genview:tree-detail", kwargs={"tree_id": tree_id})


class EventTypeManageView(TreeEditAccessMixin, View):
    template_name = "genview/event_type_manage.html"

    def get_formset(self, tree_id, post_data=None):
        """Hilfsmethode, um das Formset zu erstellen und zu befüllen."""
        # Wir bauen eine Fabrik für EventType-Formulare
        EventTypeFormSet = modelformset_factory(
            EventType,
            form=EventTypeForm,
            extra=0  # Keine leeren, neuen Zeilen anzeigen
        )
        
        # 🔥 Nur Event-Typen holen, die in DIESEM Stammbaum existieren
        queryset = EventType.objects.filter(
            events__gedcom_tree_id=tree_id
        ).distinct().order_by('tag')
        
        return EventTypeFormSet(data=post_data, queryset=queryset)

    def get(self, request, tree_id):
        formset = self.get_formset(tree_id)
        return render(request, self.template_name, {
            'formset': formset,
            'tree_id': tree_id
        })

    def post(self, request, tree_id):
        formset = self.get_formset(tree_id, request.POST)
        
        if formset.is_valid():
            formset.save()
            messages.success(request, "Alle Event-Typen wurden erfolgreich aktualisiert!")
            return redirect('genview:manage-event-types', tree_id=tree_id)
            
        return render(request, self.template_name, {
            'formset': formset,
            'tree_id': tree_id
        })
    
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
        messages.success(self.request, "Quelle erfolgreich erstellt.")
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
        messages.success(self.request, "Quelle aktualisiert.")
        return reverse_lazy("genview:source-list", kwargs={"tree_id": tree_id})

class AddExistingMediaToSourceView(TreeAccessMixin, FormView):
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
        
        messages.success(self.request, f"{selected_media.count()} Medien erfolgreich mit der Quelle verknüpft.")
        
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
        messages.success(self.request, "Quelle gelöscht.")
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
        
        # 2. Dynamisch nach allen definierten Feldern suchen (mit ODER verknüpft)
        if self.search_fields:
            q_objects = Q()
            for field in self.search_fields:
                # Baut dynamisch z.B. Q(given_name__icontains=query)
                q_objects |= Q(**{f"{field}__icontains": query})
            
            qs = qs.filter(q_objects)
            
        # 3. Auf 30 Treffer limitieren für maximale Performance
        qs = qs[:30]
        
        # 4. JSON zusammenbauen
        results = [
            {'id': obj.id, 'text': self.get_display_text(obj)} 
            for obj in qs
        ]
        
        return JsonResponse({'results': results})
    
# --- Die API für Personen ---
class IndividualSearchAPIView(GenericSelect2APIView):
    model = Individual
    search_fields = ['given_name', 'surname', 'gedcom_id']
    
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


class UserSearchAPIView(View):
    """
    AJAX-Endpoint für Select2, um Benutzer nach Username, 
    E-Mail oder Vor-/Nachname im gesamten System zu suchen.
    """
    def get(self, request, *args, **kwargs):
        query = request.GET.get('q', '').strip()
        
        if len(query) < 2:
            return JsonResponse({'results': []})
            
        # Suche nach Username, E-Mail, Vorname oder Nachname
        users = User.objects.filter(
            Q(username__icontains=query) |
            Q(email__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query)
        ).exclude(is_superuser=True)[:20] # Superadmins müssen meist nicht verwaltet werden
        
        results = []
        for u in users:
            display = f"{u.username} ({u.get_full_name() or u.email})"
            results.append({'id': u.id, 'text': display})
            
        return JsonResponse({'results': results})

#
# --- ADMIN
#

import os
from io import StringIO
import tempfile
from django.core.management import call_command
from .forms import GedcomImportForm, TreeMembershipForm, UserRegisterForm
from .mixins import SuperuserRequiredMixin

class RegisterView(CreateView):
    form_class = UserRegisterForm
    template_name = 'registration/register.html'
    success_url = reverse_lazy('login') # Nach Erfolg zurück zum Login

    def dispatch(self, request, *args, **kwargs):
        # Wenn ein bereits eingeloggter User versucht sich zu registrieren,
        # leiten wir ihn einfach auf das Dashboard weiter.
        if request.user.is_authenticated:
            return redirect('genview:tree-list') # Passe das an deine Startseite an
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # Formular validieren, aber noch nicht final in die DB schreiben
        user = form.save(commit=False)
        
        # 🔥 DER SICHERHEITS-SCHLÜSSEL: 
        # Account ist inaktiv, bis der Superadmin ihn freischaltet!
        user.is_active = False 
        user.save()

        # Dem User eine freundliche Nachricht anzeigen
        messages.info(
            self.request, 
            f"Registrierung für „{user.username}“ erfolgreich! Dein Account ist aktuell noch inaktiv. "
            f"Ein Administrator prüft deine Anmeldung und schaltet dich in Kürze frei."
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
            messages.success(request, f"Benutzer {user.username} wurde erfolgreich {status}.")
            
        elif action == "delete":
            username = user.username
            user.delete()
            messages.success(request, f"Benutzer {username} wurde dauerhaft gelöscht.")
            
        return redirect('genview:user-management-list')
    

class GedcomImportView(LoginRequiredMixin, FormView):
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
                f"Import fehlgeschlagen: {exc}")
            # Aufräumen, dann zum Formular zurück
            os.remove(tmp_path)
            return self.form_invalid(form)

        # 3️⃣ Aufräumen (Temp-File löschen)
        os.remove(tmp_path)

        # 4️⃣ Erfolgsmeldung
        messages.success(self.request,
            f"GEDCOM-Datei erfolgreich importiert – Stammbaum „{tree_name}“ angelegt.")
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


class TreeMembershipManageView(View): # Nutze hier dein passendes Mixin (z.B. TreeAdminAccessMixin)
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
            messages.info(request, f"Der Stammbaum ist jetzt {status}.")
            return redirect('genview:manage-memberships', tree_id=tree.id)
        

        formset = self.get_formset(tree, request.POST)
        
        # 1. Workflow: NEUEN USER HINZUFÜGEN
        new_user_id = request.POST.get('new_user')
        new_user_role = request.POST.get('new_user_role')
        
        if new_user_id and new_user_role:
            new_user = get_object_or_404(User, pk=new_user_id)
            # unique_together absichern mit get_or_create
            membership, created = TreeMembership.objects.get_or_create(
                gedcom_tree=tree,
                user=new_user,
                defaults={'role': new_user_role}
            )
            if created:
                messages.success(request, f"Benutzer {new_user.username} wurde erfolgreich hinzugefügt.")
            else:
                messages.warning(request, f"{new_user.username} ist bereits Mitglied in diesem Stammbaum.")
            return redirect('genview:manage-memberships', tree_id=tree.id)

        # 2. Workflow: BESTEHENDE ROLLER ÄNDERN / LÖSCHEN
        if formset.is_valid():
            formset.save()
            messages.success(request, "Mitgliederlisten und Rollen erfolgreich aktualisiert.")
            return redirect('genview:manage-memberships', tree_id=tree.id)
            
        return render(request, self.template_name, {
            'tree': tree,
            'formset': formset,
        })
    
