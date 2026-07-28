from datetime import date

from django.contrib.auth.mixins import UserPassesTestMixin
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404

from .models import TreeMembership, Tree

class SuperuserRequiredMixin(UserPassesTestMixin):
    """Sicherheits-Mixin: Lässt nur globale Superuser durch."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_superuser # type: ignore
    


def filter_public_individuals(qs):
    """
    ORM approximation of Individual.is_confidential == False.
    Slightly over-redacts edge cases (e.g. old marriage, no birth/death).
    """
    from genview.models import Event

    today = date.today()
    death_cutoff = date(today.year - 35, today.month, today.day)
    birth_cutoff = date(today.year - 115, today.month, today.day)

    has_public_death = Exists(
        Event.objects.filter(
            individual_id=OuterRef("pk"),
            event_type__tag="DEAT",
            parsed_date__lt=death_cutoff,
        )
    )
    has_dated_death = Exists(
        Event.objects.filter(
            individual_id=OuterRef("pk"),
            event_type__tag="DEAT",
            parsed_date__isnull=False,
        )
    )
    has_old_birth = Exists(
        Event.objects.filter(
            individual_id=OuterRef("pk"),
            event_type__tag="BIRT",
            parsed_date__lt=birth_cutoff,
        )
    )
    return qs.annotate(
        _has_public_death=has_public_death,
        _has_dated_death=has_dated_death,
        _has_old_birth=has_old_birth,
    ).filter(
        Q(_has_public_death=True)
        | (Q(_has_dated_death=False) & Q(_has_old_birth=True))
    )


def apply_privacy_to_individual_qs(qs, apply_privacy: bool):
    if not apply_privacy:
        return qs
    return filter_public_individuals(qs)


def apply_privacy_to_family_qs(qs, apply_privacy: bool, tree_id):
    if not apply_privacy:
        return qs
    from genview.models import Individual

    public_ids = filter_public_individuals(
        Individual.objects.filter(gedcom_tree_id=tree_id)
    ).values_list("pk", flat=True)
    return qs.filter(
        (Q(husband__isnull=True) | Q(husband_id__in=public_ids))
        & (Q(wife__isnull=True) | Q(wife_id__in=public_ids))
    )


def apply_privacy_to_media_qs(qs, apply_privacy: bool, tree_id):
    if not apply_privacy:
        return qs
    from genview.models import Individual

    confidential_ids = Individual.objects.filter(gedcom_tree_id=tree_id).exclude(
        pk__in=filter_public_individuals(
            Individual.objects.filter(gedcom_tree_id=tree_id)
        ).values_list("pk", flat=True)
    )
    return qs.filter(is_private=False).exclude(individuals__in=confidential_ids).distinct()


def apply_privacy_to_event_qs(qs, apply_privacy: bool, tree_id):
    if not apply_privacy:
        return qs
    from genview.models import Individual

    public_ids = filter_public_individuals(
        Individual.objects.filter(gedcom_tree_id=tree_id)
    ).values_list("pk", flat=True)
    # Keep events for public individuals, or family events where both spouses are public/null
    return qs.filter(
        Q(individual_id__in=public_ids)
        | (
            Q(individual__isnull=True)
            & (Q(family__husband__isnull=True) | Q(family__husband_id__in=public_ids))
            & (Q(family__wife__isnull=True) | Q(family__wife_id__in=public_ids))
        )
    )


class TreeAccessMixin(UserPassesTestMixin):

    def get_apply_privacy(self):
        """Ermittelt, ob der Datenschutz für den aktuellen Aufruf gilt."""
        
        # 1. Superuser haben immer vollen Durchblick
        if self.request.user.is_superuser:
            return False
            
        # 2. Prüfen, ob der User eine Mitgliedschaft in diesem Baum hat
        if self.request.user.is_authenticated:
            tree_id = self.kwargs.get('tree_id')
            from genview.models import TreeMembership
            
            try:
                membership = TreeMembership.objects.get(
                    user=self.request.user, 
                    gedcom_tree_id=tree_id
                )
                
                # EDITOR/ADMIN see unredacted data; VIEWER still gets privacy.
                if membership.role in [
                    TreeMembership.Role.EDITOR,
                    TreeMembership.Role.ADMIN,
                ]:
                    return False
                    
            except TreeMembership.DoesNotExist:
                pass
                
        # STANDARD: Datenschutz ist AKTIVIERT (für Gäste und öffentliche Aufrufe ohne Mitgliedschaft)
        return True

    def dispatch(self, request, *args, **kwargs):
        tree_id = self.kwargs.get('tree_id')
        tree = get_object_or_404(Tree, pk=tree_id)

        # 1. Superuser dürfen immer
        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        # 🔥 2. NEU: Wenn der Baum öffentlich ist, darf ihn jeder (lesen)!
        if tree.is_public:
            return super().dispatch(request, *args, **kwargs)

        # 3. Wenn privat, prüfen wir die Mitgliedschaft (wie bisher)
        if request.user.is_authenticated:
            is_member = TreeMembership.objects.filter(
                user=request.user, gedcom_tree=tree
            ).exists()
            if is_member:
                return super().dispatch(request, *args, **kwargs)

        # Wenn nichts zutrifft: Zugriff verweigern
        return self.handle_no_permission()
    
    def test_func(self):
        tree_id = self.kwargs.get('tree_id')
        tree = get_object_or_404(Tree, pk=tree_id)

        # 1. Superuser dürfen immer
        if self.request.user.is_superuser:
            return True

        # 2. Wenn der Baum öffentlich ist, darf ihn jeder sehen
        if tree.is_public:
            return True

        # 3. Wenn privat, prüfen wir die Mitgliedschaft
        if self.request.user.is_authenticated:
            return TreeMembership.objects.filter(
                user=self.request.user, 
                gedcom_tree=tree
            ).exists()

        # Wenn nichts zutrifft: Zugriff verweigern
        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tree_id = self.kwargs.get('tree_id')
        tree = get_object_or_404(Tree, pk=tree_id)
        
        # 🔥 DER FIX: Wir müssen dem Template die ID (und am besten gleich 
        # das ganze Baum-Objekt) wieder zur Verfügung stellen!
        context['tree_id'] = tree_id
        context['tree'] = tree

        user = self.request.user

        context['is_tree_admin'] = False
        context['is_tree_editor'] = False
        context['is_tree_viewer'] = False

        if user.is_superuser:
            context['is_tree_admin'] = context['is_tree_editor'] = context['is_tree_viewer'] = True
        elif user.is_authenticated:
            try:
                membership = TreeMembership.objects.get(user=user, gedcom_tree_id=tree_id)
                context['is_tree_viewer'] = True 
                if membership.role in [TreeMembership.Role.EDITOR, TreeMembership.Role.ADMIN]:
                    context['is_tree_editor'] = True
                if membership.role == TreeMembership.Role.ADMIN:
                    context['is_tree_admin'] = True
            except TreeMembership.DoesNotExist:
                pass

        if tree.is_public:
            context['is_tree_viewer'] = True

        # ==========================================
        # 🔥 DER PRIVACY-FIX
        # ==========================================
        
        # Variante A: Wenn du deine eigene Methode `get_apply_privacy()` weiter nutzen willst:
        if hasattr(self, 'get_apply_privacy'):
            context['apply_privacy'] = self.get_apply_privacy()
            
        # Variante B: Die moderne, automatische Methode über unsere neuen Rollen!
        # (Falls du get_apply_privacy in Zukunft nicht mehr manuell berechnen willst):
        else:
            # Regel: Wer Editor oder Admin ist, darf alles unzensiert sehen (Privacy = False).
            # Wer nur Viewer ist (oder unregistriert auf einem öffentlichen Baum), 
            # bekommt die Zensur aktiviert (Privacy = True).
            context['apply_privacy'] = not context.get('is_tree_editor', False)

        # ==========================================
        # 🔥 DER FIX FÜR DEINE TEMPLATES
        # ==========================================
        # Wir stellen sicher, dass deine alten {% if can_edit %} Abfragen 
        # exakt denselben Wert bekommen wie die neue Editor-Rolle.
        context['can_edit'] = context.get('is_tree_editor', False)

        return context
    

def user_can_edit_tree(user, tree_id) -> bool:
    """True if *user* may mutate data in the given tree (EDITOR/ADMIN/superuser)."""
    if getattr(user, "is_superuser", False):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    return TreeMembership.objects.filter(
        user=user,
        gedcom_tree_id=tree_id,
        role__in=[TreeMembership.Role.EDITOR, TreeMembership.Role.ADMIN],
    ).exists()


def user_can_admin_tree(user, tree_id) -> bool:
    """True if *user* may manage memberships / public flag for the tree."""
    if getattr(user, "is_superuser", False):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    return TreeMembership.objects.filter(
        user=user,
        gedcom_tree_id=tree_id,
        role=TreeMembership.Role.ADMIN,
    ).exists()


class TreeEditAccessMixin(TreeAccessMixin):
    """
    Sorgt dafür, dass man zum Erstellen/Bearbeiten/Löschen zwingend
    eingeloggt sein muss UND eine passende Rolle (EDITOR/ADMIN) benötigt.
    """
    def test_func(self):
        return user_can_edit_tree(self.request.user, self.kwargs.get("tree_id"))


class TreeAdminAccessMixin(TreeAccessMixin):
    """Requires login + tree ADMIN (or superuser) for membership / visibility mgmt."""
    def test_func(self):
        return user_can_admin_tree(self.request.user, self.kwargs.get("tree_id"))


class SortableListViewMixin:
    """
    Macht jede ListView über URL-Parameter (?sort=feldname&dir=asc) sortierbar.
    """
    sortable_fields = []         # Welche Felder dürfen sortiert werden? (Sicherheitsfeature!)
    default_sort_field = 'id'    # Standard-Feld, falls nichts angegeben ist
    default_sort_dir = 'asc'     # Standard-Richtung ('asc' oder 'desc')

    def get_ordering(self):
        # 1. Parameter aus der URL lesen
        sort_field = self.request.GET.get('sort', self.default_sort_field)
        sort_dir = self.request.GET.get('dir', self.default_sort_dir)

        # 2. Sicherheits-Check: Verhindern, dass User über die URL fremde/geheime Felder abfragen
        if sort_field not in self.sortable_fields:
            sort_field = self.default_sort_field

        # 3. Das '-' für absteigende Sortierung (descending) voranstellen
        if sort_dir == 'desc':
            return f"-{sort_field}"
        return sort_field

    def get_context_data(self, **kwargs):
        # 4. Aktuelle Sortierung an das Template übergeben (für die UI/Pfeile)
        context = super().get_context_data(**kwargs)
        sort_field = self.request.GET.get('sort', self.default_sort_field)
        
        if sort_field not in self.sortable_fields:
            sort_field = self.default_sort_field
            
        context['current_sort'] = sort_field
        context['current_dir'] = self.request.GET.get('dir', self.default_sort_dir)
        return context
    

class FilterableListViewMixin:
    """
    Erlaubt das Filtern einer ListView über URL-Parameter.
    Unterstützt eine allgemeine Suche (?q=begriff) und exakte Filter (?sex=M).
    """
    search_fields = []        # Felder für die Textsuche (icontains)
    exact_filter_fields = []  # Felder für Dropdowns (exakter Match)

    def get_queryset_filters(self):
        filters = Q()
        
        # 1. Textsuche (Suchfeld: ?q=...)
        search_query = self.request.GET.get('q', '').strip()
        if search_query and self.search_fields:
            search_conditions = Q()
            for field in self.search_fields:
                # Baut z.B.: Q(given_name__icontains=search_query) | Q(surname__icontains=search_query)
                search_conditions |= Q(**{f"{field}__icontains": search_query})
            filters &= search_conditions
            
        # 2. Exakte Filter (z.B. Dropdown: ?sex=M)
        for field in self.exact_filter_fields:
            value = self.request.GET.get(field, '').strip()
            if value:
                filters &= Q(**{field: value})
                
        return filters

    def get_context_data(self, **kwargs):
        # Die aktuellen Werte ans Template übergeben, damit die Formularfelder gefüllt bleiben
        context = super().get_context_data(**kwargs)
        context['current_search'] = self.request.GET.get('q', '')
        
        for field in self.exact_filter_fields:
            context[f'current_filter_{field}'] = self.request.GET.get(field, '')
            
        return context
    

