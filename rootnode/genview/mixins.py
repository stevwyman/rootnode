from django.contrib.auth.mixins import UserPassesTestMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404

from .models import TreeMembership, Tree


class TreeAccessMixin(UserPassesTestMixin):
    """
    Prüft, ob der Nutzer Leserechte für den Baum hat.
    Setzt 'can_edit' für das Frontend-Template.
    """
    # Eine leere Variable, um die Mitgliedschaft für diesen Request zwischenzuspeichern
    membership = None

    def test_func(self):
        tree_id = self.kwargs.get("tree_id")
        self.tree_obj = get_object_or_404(Tree, pk=tree_id)

        # 1. SCHRITT: Ist der Benutzer überhaupt eingeloggt?
        if self.request.user.is_authenticated:
            # Wenn ja, prüfen wir, ob er eine explizite Rolle/Mitgliedschaft hat
            try:
                self.membership = TreeMembership.objects.get(
                    user=self.request.user, 
                    gedcom_tree_id=tree_id
                )
                return True # Eingeloggt + Mitglied -> Zugriff erlaubt!
            except TreeMembership.DoesNotExist:
                # Eingeloggt, aber kein Mitglied -> Wir prüfen im nächsten Schritt, ob der Baum öffentlich ist
                pass 

        # 2. SCHRITT: Wenn der User nicht eingeloggt ist ODER kein Mitglied ist:
        # Zugriff wird NUR gewährt, wenn der Baum explizit als öffentlich markiert wurde!
        return self.tree_obj.is_public
    
    # 1. NEU: Die reine Berechnungs-Logik bekommt eine eigene Methode im Mixin
    def get_apply_privacy(self):
        """Ermittelt, ob der Datenschutz für den aktuellen Aufruf gilt."""
        # STANDARD: Datenschutz ist AKTIVIERT (für Gäste und öffentliche Aufrufe)
        if getattr(self, "membership", None):
            if self.membership.role in ["VIEWER", "EDITOR", "ADMIN"]:
                # Voller Durchblick: Datenschutz aushebeln!
                return False
        return True
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        context['tree_id'] = self.tree_obj.pk
        context['can_edit'] = False
        
        # STANDARD: Datenschutz ist AKTIVIERT (für Gäste und öffentliche Aufrufe)
        # Nutzt die neue Helfermethode:
        context["apply_privacy"] = self.get_apply_privacy()

        # Wenn der Nutzer eine Mitgliedschaft hat, prüfen wir die Rolle
        if getattr(self, 'membership', None):
            if self.membership.role in ['VIEWER', 'EDITOR', 'ADMIN']:
                # Voller Durchblick: Datenschutz aushebeln!
                context['apply_privacy'] = False
            
            if self.membership.role in ['EDITOR', 'ADMIN']:
                context['can_edit'] = True
                
        return context


class TreeEditAccessMixin(TreeAccessMixin):
    """
    Sorgt dafür, dass man zum Erstellen/Bearbeiten/Löschen zwingend 
    eingeloggt sein muss UND eine passende Rolle (EDITOR/ADMIN) benötigt.
    """
    def test_func(self):
        # Ruft die test_func von oben auf
        has_access = super().test_func()
        
        # Wenn der Baum zwar öffentlich ist (has_access=True), der User aber nicht 
        # eingeloggt ist (keine membership), wird der Zugriff hier blockiert!
        if has_access and self.membership and self.membership.role in ['EDITOR', 'ADMIN']:
            return True
            
        return False # Blockiert anonyme User und reine VIEWER beim Editieren knallhart


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
    
