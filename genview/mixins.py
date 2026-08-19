from datetime import date

from django.contrib.auth.mixins import UserPassesTestMixin
from django.db.models import Exists, OuterRef, Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import TreeMembership, Tree
from .privacy import (
    BIRTH_PRIVACY_YEARS,
    DEATH_PRIVACY_YEARS,
    MARRIAGE_PRIVACY_YEARS,
    cutoff_date,
)

class SuperuserRequiredMixin(UserPassesTestMixin):
    """Sicherheits-Mixin: Lässt nur globale Superuser durch."""

    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_superuser # type: ignore
    


def filter_public_individuals(qs):
    """
    ORM approximation of Individual.is_confidential == False.
    A person is public only if they have at least one parsed birth, death, or
    marriage date and none of those dates fall inside the privacy windows.
    """
    from genview.models import Event

    today = date.today()
    death_cutoff = cutoff_date(DEATH_PRIVACY_YEARS, today)
    birth_cutoff = cutoff_date(BIRTH_PRIVACY_YEARS, today)
    marriage_cutoff = cutoff_date(MARRIAGE_PRIVACY_YEARS, today)

    has_recent_death = Exists(
        Event.objects.filter(
            individual_id=OuterRef("pk"),
            event_type__tag="DEAT",
            parsed_date__gt=death_cutoff,
        )
    )
    has_recent_birth = Exists(
        Event.objects.filter(
            individual_id=OuterRef("pk"),
            event_type__tag="BIRT",
            parsed_date__gt=birth_cutoff,
        )
    )
    has_recent_marriage = Exists(
        Event.objects.filter(
            event_type__tag="MARR",
            parsed_date__gt=marriage_cutoff,
        ).filter(
            Q(family__husband_id=OuterRef("pk")) | Q(family__wife_id=OuterRef("pk"))
        )
    )
    has_dated_death = Exists(
        Event.objects.filter(
            individual_id=OuterRef("pk"),
            event_type__tag="DEAT",
            parsed_date__isnull=False,
        )
    )
    has_dated_birth = Exists(
        Event.objects.filter(
            individual_id=OuterRef("pk"),
            event_type__tag="BIRT",
            parsed_date__isnull=False,
        )
    )
    has_dated_marriage = Exists(
        Event.objects.filter(
            event_type__tag="MARR",
            parsed_date__isnull=False,
        ).filter(
            Q(family__husband_id=OuterRef("pk")) | Q(family__wife_id=OuterRef("pk"))
        )
    )
    return qs.annotate(
        _has_recent_death=has_recent_death,
        _has_recent_birth=has_recent_birth,
        _has_recent_marriage=has_recent_marriage,
        _has_dated_death=has_dated_death,
        _has_dated_birth=has_dated_birth,
        _has_dated_marriage=has_dated_marriage,
    ).filter(
        Q(_has_recent_death=False)
        & Q(_has_recent_birth=False)
        & Q(_has_recent_marriage=False)
        & (
            Q(_has_dated_death=True)
            | Q(_has_dated_birth=True)
            | Q(_has_dated_marriage=True)
        )
    )


def public_individual_pks(tree_id):
    """Primary keys of people who pass living-person privacy for *tree_id*."""
    from genview.models import Individual

    return (
        filter_public_individuals(Individual.objects.filter(gedcom_tree_id=tree_id))
        .order_by()
        .values("pk")
    )


def apply_privacy_to_individual_qs(qs, apply_privacy: bool):
    if not apply_privacy:
        return qs
    return filter_public_individuals(qs)


def apply_privacy_to_family_qs(qs, apply_privacy: bool, tree_id, public_ids=None):
    if not apply_privacy:
        return qs
    from genview.models import ChildFamilyLink, Event

    if public_ids is None:
        public_ids = public_individual_pks(tree_id)
    marriage_cutoff = cutoff_date(MARRIAGE_PRIVACY_YEARS)
    has_nonpublic_child = ChildFamilyLink.objects.filter(
        family_id=OuterRef("pk"),
        child_id__isnull=False,
    ).exclude(child_id__in=public_ids)
    has_recent_marriage = Event.objects.filter(
        family_id=OuterRef("pk"),
        event_type__tag="MARR",
        parsed_date__gt=marriage_cutoff,
    )
    return qs.filter(
        (Q(husband__isnull=True) | Q(husband_id__in=public_ids))
        & (Q(wife__isnull=True) | Q(wife_id__in=public_ids))
        & ~Exists(has_nonpublic_child)
        & ~Exists(has_recent_marriage)
    )


def apply_privacy_to_media_qs(qs, apply_privacy: bool, tree_id, public_ids=None):
    if not apply_privacy:
        return qs
    from genview.models import Individual

    if public_ids is None:
        public_ids = public_individual_pks(tree_id)
    has_nonpublic_person = Individual.objects.filter(
        gedcom_tree_id=tree_id,
        media_objects=OuterRef("pk"),
    ).exclude(pk__in=public_ids)
    return qs.filter(is_private=False).filter(~Exists(has_nonpublic_person))


def apply_privacy_to_event_qs(
    qs, apply_privacy: bool, tree_id, public_ids=None, public_family_ids=None
):
    if not apply_privacy:
        return qs
    from genview.models import Family

    if public_ids is None:
        public_ids = public_individual_pks(tree_id)
    if public_family_ids is None:
        public_family_ids = apply_privacy_to_family_qs(
            Family.objects.filter(gedcom_tree_id=tree_id),
            True,
            tree_id,
            public_ids=public_ids,
        ).values("pk")

    today = date.today()
    return qs.filter(
        Q(individual_id__in=public_ids) | Q(family_id__in=public_family_ids)
    ).exclude(
        Q(
            event_type__tag="BIRT",
            parsed_date__gt=cutoff_date(BIRTH_PRIVACY_YEARS, today),
        )
        | Q(
            event_type__tag="DEAT",
            parsed_date__gt=cutoff_date(DEATH_PRIVACY_YEARS, today),
        )
        | Q(
            event_type__tag="MARR",
            parsed_date__gt=cutoff_date(MARRIAGE_PRIVACY_YEARS, today),
        )
    )


def user_may_see_tree(user, tree) -> bool:
    """True if *user* may open the tree at all (public, member, or superuser)."""
    if tree is None:
        return False
    if tree.is_public:
        return True
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "is_authenticated", False) and user.is_authenticated:
        return TreeMembership.objects.filter(user=user, gedcom_tree=tree).exists()
    return False


def apply_privacy_for_request(user, tree) -> bool:
    """
    True when living-person rules must be applied.

    Privacy is off for superusers, EDITOR/ADMIN of this tree (even on a public
    tree with show_living_people=False), and any member of a private tree
    (including VIEWER). On a public tree, guests and VIEWER follow
    show_living_people.
    """
    if getattr(user, "is_superuser", False):
        return False

    membership = None
    if getattr(user, "is_authenticated", False) and user.is_authenticated:
        membership = TreeMembership.objects.filter(
            user=user, gedcom_tree_id=tree.pk
        ).first()
        if membership and membership.role in (
            TreeMembership.Role.EDITOR,
            TreeMembership.Role.ADMIN,
        ):
            return False

    if tree.is_public:
        return not bool(tree.show_living_people)

    if membership:
        return False

    return True


def _visible_relative(person, apply_privacy):
    """Return *person* only when guests may see their identity."""
    if not person:
        return None
    if apply_privacy and person.is_confidential:
        return None
    return person


class TreeAccessMixin(UserPassesTestMixin):

    def get_apply_privacy(self):
        tree = getattr(self, "gedcom_tree", None)
        tree_id = self.kwargs.get("tree_id")
        if tree is None or getattr(tree, "pk", None) != tree_id:
            tree = get_object_or_404(Tree, pk=tree_id)
            self.gedcom_tree = tree
        return apply_privacy_for_request(self.request.user, tree)

    def handle_no_permission(self):
        tree_id = self.kwargs.get("tree_id")
        tree = getattr(self, "gedcom_tree", None)
        if tree is None and tree_id:
            tree = Tree.objects.filter(pk=tree_id).first()
        if tree and user_may_see_tree(self.request.user, tree):
            return super().handle_no_permission()
        raise Http404()

    def dispatch(self, request, *args, **kwargs):
        tree_id = self.kwargs.get("tree_id")
        tree = get_object_or_404(Tree, pk=tree_id)
        self.gedcom_tree = tree

        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        if tree.is_public:
            return super().dispatch(request, *args, **kwargs)

        if request.user.is_authenticated:
            is_member = TreeMembership.objects.filter(
                user=request.user, gedcom_tree=tree
            ).exists()
            if is_member:
                return super().dispatch(request, *args, **kwargs)

        return self.handle_no_permission()
    
    def test_func(self):
        tree_id = self.kwargs.get("tree_id")
        tree = getattr(self, "gedcom_tree", None)
        if tree is None or getattr(tree, "pk", None) != tree_id:
            tree = get_object_or_404(Tree, pk=tree_id)
            self.gedcom_tree = tree

        if self.request.user.is_superuser:
            return True

        if tree.is_public:
            return True

        if self.request.user.is_authenticated:
            return TreeMembership.objects.filter(
                user=self.request.user, 
                gedcom_tree=tree
            ).exists()

        return False

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tree_id = self.kwargs.get('tree_id')
        tree = get_object_or_404(
            Tree.objects.select_related("starting_individual"),
            pk=tree_id,
        )
        
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
        else:
            tree = context.get('tree')
            context['apply_privacy'] = (
                apply_privacy_for_request(user, tree) if tree else True
            )

        # ==========================================
        # 🔥 DER FIX FÜR DEINE TEMPLATES
        # ==========================================
        # Wir stellen sicher, dass deine alten {% if can_edit %} Abfragen 
        # exakt denselben Wert bekommen wie die neue Editor-Rolle.
        context['can_edit'] = context.get('is_tree_editor', False)
        context['can_manage_events'] = context.get('is_tree_admin', False)
        context['visible_starting_individual'] = _visible_relative(
            tree.starting_individual,
            context['apply_privacy'],
        )

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
    """True if *user* may manage tree settings, events, places cleanup, and admin tools."""
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
    """Requires login + tree ADMIN (or superuser) for tree settings and admin tools."""
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

    def get_extra_search_q(self, search_query: str):
        """Optional extra Q ORed into the ?q= text search."""
        return Q()

    def get_queryset_filters(self):
        filters = Q()
        
        # 1. Textsuche (Suchfeld: ?q=...)
        search_query = self.request.GET.get('q', '').strip()
        if search_query:
            search_conditions = Q()
            for field in self.search_fields:
                search_conditions |= Q(**{f"{field}__icontains": search_query})
            extra = self.get_extra_search_q(search_query)
            if extra:
                search_conditions |= extra
            if search_conditions:
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
    

