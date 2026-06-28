from django.contrib.auth.mixins import UserPassesTestMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404

from .models import TreeMembership, Tree

class SuperuserRequiredMixin(UserPassesTestMixin):
    """Sicherheits-Mixin: Lässt nur globale Superuser durch."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_superuser # type: ignore
    

class TreeAccessMixin(UserPassesTestMixin):

    def user_can_edit(self) -> bool:
        """True if the current user may create/update/delete in this tree."""
        if self.request.user.is_superuser:
            return True
        tree_id = self.kwargs.get("tree_id")
        if self.request.user.is_authenticated and tree_id is not None:
            return TreeMembership.objects.filter(
                user=self.request.user,
                gedcom_tree_id=tree_id,
                role__in=[TreeMembership.Role.EDITOR, TreeMembership.Role.ADMIN],
            ).exists()
        return False

    def get_apply_privacy(self):
        """True when confidential/private content should be redacted."""
        if self.request.user.is_superuser:
            return False
        return not self.user_can_edit()

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

        context['apply_privacy'] = self.get_apply_privacy()
        context['can_edit'] = self.user_can_edit()

        return context
    

class TreeEditAccessMixin(TreeAccessMixin):
    """
    Sorgt dafür, dass man zum Erstellen/Bearbeiten/Löschen zwingend 
    eingeloggt sein muss UND eine passende Rolle (EDITOR/ADMIN) benötigt.
    """
    def test_func(self):
        return self.user_can_edit()


class TreeScopedQuerySetMixin:
    """Restrict get_queryset() to records belonging to the tree_id URL kwarg."""

    tree_scoped_lookup = "gedcom_tree_id"

    def get_queryset(self):
        qs = super().get_queryset()
        tree_id = self.kwargs.get("tree_id")
        if tree_id is not None:
            qs = qs.filter(**{self.tree_scoped_lookup: tree_id})
        return qs


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
    

