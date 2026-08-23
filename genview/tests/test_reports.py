from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from genview.models import (
    ChildFamilyLink,
    Event,
    EventType,
    Family,
    Individual,
    Place,
    Source,
    Tree,
    TreeMembership,
)
from genview.reports import get_report
from genview.reports.walking import collect_ancestors


def _event_type(tag, name, category=EventType.Category.INDIVIDUAL):
    event_type, _ = EventType.objects.get_or_create(
        tag=tag,
        defaults={"name": name, "category": category, "is_visible": True},
    )
    return event_type


def _event(tree, *, person=None, family=None, tag="BIRT", name="Birth", when=None, raw="", place=None):
    category = (
        EventType.Category.FAMILY if tag == "MARR" else EventType.Category.INDIVIDUAL
    )
    event = Event.objects.create(
        gedcom_tree=tree,
        individual=person,
        family=family,
        event_type=_event_type(tag, name, category),
        parsed_date=when,
        raw_date=raw or (when.isoformat() if when else ""),
        place=place,
    )
    return event


class ReportHelperTests(TestCase):
    def setUp(self):
        self.tree = Tree.objects.create(name="Report Tree")
        self.child = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Lea", surname="Kind"
        )
        self.father = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Otto", surname="Vater"
        )
        self.mother = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Maria", surname="Mutter"
        )
        self.gfather = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Hans", surname="Großvater"
        )
        self.gmother = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Anna", surname="Großmutter"
        )
        self.ggfather = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Karl", surname="Urgroßvater"
        )
        parents = Family.objects.create(
            gedcom_tree=self.tree, husband=self.father, wife=self.mother
        )
        ChildFamilyLink.objects.create(child=self.child, family=parents)
        grandparents = Family.objects.create(
            gedcom_tree=self.tree, husband=self.gfather, wife=self.gmother
        )
        ChildFamilyLink.objects.create(child=self.father, family=grandparents)
        great = Family.objects.create(
            gedcom_tree=self.tree, husband=self.ggfather, wife=None
        )
        ChildFamilyLink.objects.create(child=self.gfather, family=great)

    def test_ancestor_depth_stops_at_requested_generations(self):
        nodes = collect_ancestors(self.tree.pk, self.child.pk, 2, apply_privacy=False)
        names = {node.person.given_name for node in nodes}
        self.assertIn("Lea", names)
        self.assertIn("Otto", names)
        self.assertIn("Hans", names)
        self.assertNotIn("Karl", names)

    def test_missing_information_flags_birth_without_source(self):
        place = Place.objects.create(gedcom_tree=self.tree, name="Herford")
        birth = _event(
            self.tree,
            person=self.child,
            tag="BIRT",
            name="Geburt",
            when=date(1900, 1, 1),
            place=place,
        )
        _event(
            self.tree,
            person=self.child,
            tag="DEAT",
            name="Tod",
            when=date(1970, 1, 1),
            place=place,
        )
        source = Source.objects.create(gedcom_tree=self.tree, title="Kirchenbuch")
        sourced = _event(
            self.tree,
            person=self.father,
            tag="BIRT",
            name="Geburt",
            when=date(1870, 5, 5),
            place=place,
        )
        sourced.sources.add(source)
        _event(
            self.tree,
            person=self.father,
            tag="DEAT",
            name="Tod",
            when=date(1940, 1, 1),
            place=place,
        )
        death = Event.objects.get(individual=self.father, event_type__tag="DEAT")
        death.sources.add(source)
        marriage = _event(
            self.tree,
            family=Family.objects.get(husband=self.father, wife=self.mother),
            tag="MARR",
            name="Heirat",
            when=date(1898, 6, 1),
            place=place,
        )
        marriage.sources.add(source)

        report = get_report("missing-information")
        result = report.run(
            self.tree,
            {
                "scope": "ancestors",
                "person_id": self.child.pk,
                "generations": 2,
                "require_place": True,
                "require_source": True,
                "skip_living_death": True,
            },
            apply_privacy=False,
        )
        texts = " ".join(cell.text for row in result.rows for cell in row)
        self.assertIn("Lea", texts)
        self.assertIn("Geburt ohne Quelle", texts)
        self.assertNotIn("Otto", texts)


class ReportViewAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(name="Private Reports")
        self.member = User.objects.create_user(username="member", password="password")
        TreeMembership.objects.create(
            user=self.member, gedcom_tree=self.tree, role=TreeMembership.Role.VIEWER
        )
        self.person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Eva", surname="Beispiel"
        )
        self.tree.starting_individual = self.person
        self.tree.save(update_fields=["starting_individual"])
        self.list_url = reverse("genview:report-list", kwargs={"tree_id": self.tree.pk})
        self.run_url = reverse(
            "genview:report-run",
            kwargs={"tree_id": self.tree.pk, "slug": "ancestors"},
        )

    def test_anonymous_user_cannot_open_private_tree_reports(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 404)

    def test_outsider_gets_404_on_private_tree(self):
        User.objects.create_user(username="outsider", password="password")
        self.client.login(username="outsider", password="password")
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 404)

    def test_member_can_open_catalog_and_run_ancestor_report(self):
        self.client.login(username="member", password="password")
        listing = self.client.get(self.list_url)
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "Vorfahren")
        self.assertContains(listing, "Fehlende")

        form = self.client.get(self.run_url)
        self.assertEqual(form.status_code, 200)
        self.assertContains(form, "Eva")

        result = self.client.get(
            self.run_url,
            {"run": "1", "person_id": self.person.pk, "generations": 3},
        )
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Eva Beispiel")
        self.assertContains(result, "Generation")

    def test_public_tree_still_requires_login(self):
        self.tree.is_public = True
        self.tree.save(update_fields=["is_public"])
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url.lower())
