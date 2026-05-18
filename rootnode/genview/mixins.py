from django.contrib.auth.mixins import UserPassesTestMixin
from django.db.models import Q

from .models import TreeMembership


class TreeAccessMixin(UserPassesTestMixin):
    """
    Ensures the user has at least VIEWER access to the tree in the URL.
    Also automatically injects the `tree_id` into the template context.
    """

    def test_func(self):
        # 1. Grab the tree_id from the URL
        tree_id = self.kwargs.get("tree_id")

        # 2. Return True if a membership exists, False otherwise (triggers a 403 Forbidden)
        return TreeMembership.objects.filter(
            user=self.request.user, gedcom_tree_id=tree_id
        ).exists()

    def get_context_data(self, **kwargs):
        # Automatically add `tree_id` to the context for EVERY view that uses this mixin!
        context = super().get_context_data(**kwargs)
        context["tree_id"] = self.kwargs.get("tree_id")
        return context


class TreeEditAccessMixin(TreeAccessMixin):
    """
    Ensures the user has EDITOR or ADMIN access to modify data.
    Inherits from TreeAccessMixin to keep the context injection.
    """

    def test_func(self):
        tree_id = self.kwargs.get("tree_id")
        return TreeMembership.objects.filter(
            user=self.request.user, gedcom_tree_id=tree_id, role__in=["EDITOR", "ADMIN"]
        ).exists()


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
    
