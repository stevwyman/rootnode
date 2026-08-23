"""Authenticated, tree-scoped genealogy reports."""

from __future__ import annotations

from django.http import Http404
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from .mixins import AuthenticatedTreeAccessMixin
from .models import Family, Individual
from .reports import (
    PARAM_BOOLEAN,
    PARAM_FAMILY,
    PARAM_PERSON,
    coerce_params,
    get_report,
    reports_by_category,
)


class ReportListView(AuthenticatedTreeAccessMixin, TemplateView):
    template_name = "genview/report_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["report_groups"] = reports_by_category()
        return context


class ReportRunView(AuthenticatedTreeAccessMixin, TemplateView):
    template_name = "genview/report_run.html"

    def get_report(self):
        report = get_report(self.kwargs.get("slug", ""))
        if report is None:
            raise Http404()
        return report

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        report = self.get_report()
        tree = self.gedcom_tree
        apply_privacy = self.get_apply_privacy()
        raw = self.request.GET.copy()
        running = raw.get("run") == "1"
        if running:
            for param in report.parameters:
                if param.kind == PARAM_BOOLEAN and param.name not in raw:
                    raw[param.name] = "false"
        elif tree.starting_individual_id and not raw.get("person_id"):
            if any(param.name == "person_id" for param in report.parameters):
                raw["person_id"] = str(tree.starting_individual_id)

        params, errors = coerce_params(report, raw)
        errors.update(self._validate_scoped_objects(tree, params, apply_privacy))

        result = None
        if running and not errors:
            result = report.run(tree, params, apply_privacy=apply_privacy)

        context.update(
            {
                "report": report,
                "param_fields": self._param_fields(report, params, tree, apply_privacy),
                "param_errors": errors,
                "result": result,
                "ran": running and not errors,
            }
        )
        return context

    def _validate_scoped_objects(self, tree, params, apply_privacy) -> dict[str, str]:
        errors: dict[str, str] = {}
        person_id = params.get("person_id")
        if person_id:
            person = Individual.objects.filter(gedcom_tree=tree, pk=person_id).first()
            if person is None:
                errors["person_id"] = _("Diese Person gehört nicht zu diesem Stammbaum.")
            elif apply_privacy and person.is_confidential:
                errors["person_id"] = _("Diese Person ist vertraulich.")
        family_id = params.get("family_id")
        if family_id:
            family = Family.objects.filter(gedcom_tree=tree, pk=family_id).first()
            if family is None:
                errors["family_id"] = _("Diese Familie gehört nicht zu diesem Stammbaum.")
        return errors

    def _param_fields(self, report, params, tree, apply_privacy):
        fields = []
        selected_person = None
        if params.get("person_id"):
            selected_person = Individual.objects.filter(
                gedcom_tree=tree, pk=params["person_id"]
            ).first()
            if apply_privacy and selected_person and selected_person.is_confidential:
                selected_person = None
        selected_family = None
        if params.get("family_id"):
            selected_family = Family.objects.filter(
                gedcom_tree=tree, pk=params["family_id"]
            ).first()
        for param in report.parameters:
            value = params.get(param.name, param.default)
            fields.append(
                {
                    "param": param,
                    "value": value,
                    "selected_person": selected_person if param.kind == PARAM_PERSON else None,
                    "selected_family": selected_family if param.kind == PARAM_FAMILY else None,
                    "checked": bool(value) if param.kind == PARAM_BOOLEAN else False,
                }
            )
        return fields
