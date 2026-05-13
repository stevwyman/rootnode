from django.contrib.auth.mixins import UserPassesTestMixin
from .models import TreeMembership

class TreeAccessMixin(UserPassesTestMixin):
    """
    Ensures the user has at least VIEWER access to the tree in the URL.
    Also automatically injects the `tree_id` into the template context.
    """
    def test_func(self):
        # 1. Grab the tree_id from the URL
        tree_id = self.kwargs.get('tree_id')
        
        # 2. Return True if a membership exists, False otherwise (triggers a 403 Forbidden)
        return TreeMembership.objects.filter(
            user=self.request.user, 
            gedcom_tree_id=tree_id
        ).exists()

    def get_context_data(self, **kwargs):
        # Automatically add `tree_id` to the context for EVERY view that uses this mixin!
        context = super().get_context_data(**kwargs)
        context['tree_id'] = self.kwargs.get('tree_id')
        return context


class TreeEditAccessMixin(TreeAccessMixin):
    """
    Ensures the user has EDITOR or ADMIN access to modify data.
    Inherits from TreeAccessMixin to keep the context injection.
    """
    def test_func(self):
        tree_id = self.kwargs.get('tree_id')
        return TreeMembership.objects.filter(
            user=self.request.user, 
            gedcom_tree_id=tree_id,
            role__in=['EDITOR', 'ADMIN']
        ).exists()