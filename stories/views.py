from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, DetailView, TemplateView, UpdateView

from core.features import FEATURE_STORIES
from genview.mixins import (
    FeatureRequiredMixin,
    TreeAccessMixin,
    TreeEditAccessMixin,
    user_can_edit_tree,
)

from .forms import StoryPageForm
from .mentions import render_story_body
from .models import StoryPage
from .services import story_nav_tree, visible_story_pages


class StoryFeatureMixin(FeatureRequiredMixin):
    required_feature = FEATURE_STORIES


def _shell_context(view, ctx, page=None):
    tree_id = view.kwargs.get("tree_id")
    can_edit = bool(ctx.get("can_edit"))
    pages = list(visible_story_pages(tree_id, can_edit=can_edit))
    ctx["story_nav"] = story_nav_tree(pages, current_pk=page.pk if page else None)
    ctx["story_page"] = page
    ctx["story_pages"] = pages
    if page is None:
        ctx["rendered_html"] = ""
        ctx["mention_people"] = []
        ctx["mention_families"] = []
        ctx["mention_places"] = []
        return ctx
    rendered = render_story_body(
        page.body,
        tree_id=tree_id,
        apply_privacy=bool(ctx.get("apply_privacy")),
    )
    ctx["rendered_html"] = rendered.html
    ctx["mention_people"] = rendered.individuals
    ctx["mention_families"] = rendered.families
    ctx["mention_places"] = rendered.places
    return ctx


class StoryIndexView(StoryFeatureMixin, TreeAccessMixin, TemplateView):
    template_name = "stories/reader.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        return _shell_context(self, ctx, page=None)


class StoryDetailView(StoryFeatureMixin, TreeAccessMixin, DetailView):
    model = StoryPage
    template_name = "stories/reader.html"
    context_object_name = "story_page"

    def get_queryset(self):
        return StoryPage.objects.filter(gedcom_tree_id=self.kwargs.get("tree_id"))

    def get_object(self, queryset=None):
        page = super().get_object(queryset)
        if page.status != StoryPage.Status.PUBLISHED and not user_can_edit_tree(
            self.request.user, self.kwargs.get("tree_id")
        ):
            raise Http404()
        return page

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        return _shell_context(self, ctx, page=self.object)


class StoryFormMixin(StoryFeatureMixin, LoginRequiredMixin, TreeEditAccessMixin):
    model = StoryPage
    form_class = StoryPageForm
    template_name = "stories/form.html"

    def get_queryset(self):
        return StoryPage.objects.filter(gedcom_tree_id=self.kwargs.get("tree_id"))

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["tree_id"] = self.kwargs.get("tree_id")
        return kwargs

    def get_success_url(self):
        return reverse(
            "stories:detail",
            kwargs={"tree_id": self.kwargs.get("tree_id"), "pk": self.object.pk},
        )


class StoryCreateView(StoryFormMixin, CreateView):
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        messages.success(self.request, _("Geschichte gespeichert."))
        return super().form_valid(form)


class StoryUpdateView(StoryFormMixin, UpdateView):
    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, _("Geschichte gespeichert."))
        return super().form_valid(form)


class StoryDeleteView(
    StoryFeatureMixin, LoginRequiredMixin, TreeEditAccessMixin, DeleteView
):
    model = StoryPage
    template_name = "stories/confirm_delete.html"

    def get_queryset(self):
        return StoryPage.objects.filter(gedcom_tree_id=self.kwargs.get("tree_id"))

    def get_success_url(self):
        messages.success(self.request, _("Geschichte gelöscht."))
        return reverse("stories:index", kwargs={"tree_id": self.kwargs.get("tree_id")})