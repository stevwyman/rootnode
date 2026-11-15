"""Download GEDCOM files and merge another GEDCOM into an existing tree."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import TemplateView
from django.views.generic.edit import FormView

from .forms import GedcomMergeUploadForm
from .gedcom_export import (
    all_tree_families,
    all_tree_people,
    build_gedcom_text,
    collect_ancestor_ids,
    collect_descendant_ids,
    export_filename,
    families_for_people,
    people_for_ids,
)
from .gedcom_match import suggest_matches
from .gedcom_merge import (
    apply_merge,
    clear_merge_payload,
    decisions_from_post,
    load_existing_match_people,
    load_merge_payload,
    store_merge_payload,
)
from .gedcom_text import payload_from_bytes
from .mixins import (
    AuthenticatedTreeAccessMixin,
    TreeAdminAccessMixin,
    apply_privacy_to_family_qs,
    apply_privacy_to_individual_qs,
)
from .models import Individual


def _gedcom_download(content: str, filename: str) -> HttpResponse:
    response = HttpResponse(content, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


class TreeGedcomExportView(AuthenticatedTreeAccessMixin, View):
    def get(self, request, *args, **kwargs):
        tree = self.gedcom_tree
        apply_privacy = self.get_apply_privacy()
        people = apply_privacy_to_individual_qs(all_tree_people(tree), apply_privacy)
        families = apply_privacy_to_family_qs(
            all_tree_families(tree), apply_privacy, tree.pk
        )
        content = build_gedcom_text(tree, people, families)
        return _gedcom_download(content, export_filename(tree, "tree"))


class IndividualGedcomExportView(AuthenticatedTreeAccessMixin, View):
    def get(self, request, *args, **kwargs):
        tree = self.gedcom_tree
        person = get_object_or_404(
            Individual, pk=kwargs["pk"], gedcom_tree=tree
        )
        apply_privacy = self.get_apply_privacy()
        if apply_privacy and person.is_confidential:
            raise Http404()

        scope = (request.GET.get("scope") or "ancestors").strip().lower()
        if scope in ("children", "descendants"):
            scope = "descendants"
            person_ids = collect_descendant_ids(person)
        elif scope == "ancestors":
            person_ids = collect_ancestor_ids(person)
        else:
            return HttpResponseBadRequest("Unknown export scope.")

        people = apply_privacy_to_individual_qs(
            people_for_ids(tree, person_ids), apply_privacy
        )
        visible_ids = set(people.values_list("pk", flat=True))
        families = apply_privacy_to_family_qs(
            families_for_people(tree, visible_ids), apply_privacy, tree.pk
        )
        content = build_gedcom_text(tree, people, families)
        return _gedcom_download(content, export_filename(tree, scope, person))


class GedcomMergeUploadView(LoginRequiredMixin, TreeAdminAccessMixin, FormView):
    template_name = "genview/gedcom_merge_upload.html"
    form_class = GedcomMergeUploadForm

    def get_success_url(self):
        return reverse(
            "genview:gedcom-merge-match",
            kwargs={"tree_id": self.gedcom_tree.pk},
        )

    def form_valid(self, form):
        uploaded = form.cleaned_data["gedcom_file"]
        uploaded.seek(0)
        try:
            payload = payload_from_bytes(uploaded.read())
        except (OSError, UnicodeError, ValueError):
            form.add_error(
                "gedcom_file",
                _("Die GEDCOM-Datei konnte nicht gelesen werden."),
            )
            return self.form_invalid(form)
        if not payload.get("individuals"):
            form.add_error(
                "gedcom_file",
                _("Die Datei enthält keine Personen (INDI)."),
            )
            return self.form_invalid(form)
        store_merge_payload(
            self.request, self.gedcom_tree, payload, uploaded.name
        )
        messages.info(
            self.request,
            _(
                "%(people)s Personen und %(families)s Familien gelesen. "
                "Bitte prüfe die Zuordnungen."
            )
            % {
                "people": len(payload["individuals"]),
                "families": len(payload.get("families") or []),
            },
        )
        return super().form_valid(form)


class GedcomMergeMatchView(LoginRequiredMixin, TreeAdminAccessMixin, TemplateView):
    template_name = "genview/gedcom_merge_match.html"

    def _job(self):
        loaded = load_merge_payload(self.request, self.gedcom_tree)
        if loaded is None:
            messages.error(
                self.request,
                _("Kein ausstehender GEDCOM-Abgleich. Bitte zuerst eine Datei hochladen."),
            )
            return None
        return loaded

    def get(self, request, *args, **kwargs):
        loaded = self._job()
        if loaded is None:
            return redirect(
                "genview:gedcom-merge-upload", tree_id=self.gedcom_tree.pk
            )
        self._payload, self._meta = loaded
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        payload = getattr(self, "_payload", None)
        meta = getattr(self, "_meta", {}) or {}
        if payload is None:
            return context
        existing = load_existing_match_people(self.gedcom_tree)
        rows = suggest_matches(payload, existing)
        auto_matched = sum(1 for row in rows if row["suggested_pk"])
        context.update(
            {
                "filename": meta.get("filename") or "",
                "rows": rows,
                "incoming_count": len(rows),
                "auto_matched": auto_matched,
                "new_count": len(rows) - auto_matched,
                "existing_count": len(existing),
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        loaded = self._job()
        if loaded is None:
            return redirect(
                "genview:gedcom-merge-upload", tree_id=self.gedcom_tree.pk
            )
        payload, _meta = loaded
        try:
            mapping = decisions_from_post(payload, request.POST, self.gedcom_tree)
        except ValueError:
            messages.error(
                request,
                _("Eine Zuordnung zeigt auf eine Person, die nicht zu diesem Stammbaum gehört."),
            )
            return redirect(
                "genview:gedcom-merge-match", tree_id=self.gedcom_tree.pk
            )
        stats = apply_merge(self.gedcom_tree, payload, mapping)
        clear_merge_payload(request, self.gedcom_tree.pk)
        messages.success(
            request,
            _(
                "GEDCOM übernommen: %(created)s neu, %(matched)s zugeordnet, "
                "%(families)s Familien, %(events)s Ereignisse."
            )
            % {
                "created": stats["created_people"],
                "matched": stats["matched_people"],
                "families": stats["created_families"],
                "events": stats["created_events"],
            },
        )
        return redirect("genview:tree-overview", tree_id=self.gedcom_tree.pk)


class GedcomMergeCancelView(LoginRequiredMixin, TreeAdminAccessMixin, View):
    def post(self, request, *args, **kwargs):
        clear_merge_payload(request, self.gedcom_tree.pk)
        messages.info(request, _("GEDCOM-Abgleich abgebrochen."))
        return redirect("genview:tree-overview", tree_id=self.gedcom_tree.pk)
