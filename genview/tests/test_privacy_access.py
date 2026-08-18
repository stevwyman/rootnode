from datetime import date
import json

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import AnonymousUser, User
from django.test import Client, TestCase
from django.urls import reverse

from genview.mixins import (
    apply_privacy_for_request,
    apply_privacy_to_family_qs,
    apply_privacy_to_individual_qs,
)
from genview.models import (
    ChildFamilyLink,
    Event,
    EventType,
    Family,
    Individual,
    Place,
    Tree,
    TreeMembership,
)
from genview.privacy import (
    BIRTH_PRIVACY_YEARS,
    DEATH_PRIVACY_YEARS,
    MARRIAGE_PRIVACY_YEARS,
)


def _etype(tag, name, category=EventType.Category.INDIVIDUAL):
    et, _ = EventType.objects.get_or_create(
        tag=tag, defaults={"name": name, "category": category}
    )
    return et


def _event(tree, *, individual=None, family=None, tag="DEAT", when=None, name="Death"):
    category = (
        EventType.Category.FAMILY
        if tag == "MARR"
        else EventType.Category.INDIVIDUAL
    )
    return Event.objects.create(
        gedcom_tree=tree,
        individual=individual,
        family=family,
        event_type=_etype(tag, name, category),
        parsed_date=when,
    )


class ConfidentialWindowTests(TestCase):
    def setUp(self):
        self.tree = Tree.objects.create(name="Window Tree")
        self.today = date.today()

    def test_undated_person_is_confidential(self):
        person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="No", surname="Dates"
        )
        self.assertTrue(person.is_confidential)

    def test_recent_birth_is_confidential(self):
        person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Born", surname="Recently"
        )
        _event(
            self.tree,
            individual=person,
            tag="BIRT",
            name="Birth",
            when=self.today - relativedelta(years=BIRTH_PRIVACY_YEARS - 1),
        )
        self.assertTrue(person.is_confidential)

    def test_old_birth_without_death_is_public(self):
        person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Old", surname="Birth"
        )
        _event(
            self.tree,
            individual=person,
            tag="BIRT",
            name="Birth",
            when=self.today - relativedelta(years=BIRTH_PRIVACY_YEARS + 1),
        )
        self.assertFalse(person.is_confidential)

    def test_death_within_80_years_is_confidential(self):
        person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Recent", surname="Death"
        )
        _event(
            self.tree,
            individual=person,
            tag="DEAT",
            when=self.today - relativedelta(years=DEATH_PRIVACY_YEARS - 1),
        )
        self.assertTrue(person.is_confidential)

    def test_death_older_than_80_years_is_public(self):
        person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Old", surname="Death"
        )
        _event(
            self.tree,
            individual=person,
            tag="BIRT",
            name="Birth",
            when=self.today - relativedelta(years=BIRTH_PRIVACY_YEARS + 20),
        )
        _event(
            self.tree,
            individual=person,
            tag="DEAT",
            when=self.today - relativedelta(years=DEATH_PRIVACY_YEARS + 1),
        )
        self.assertFalse(person.is_confidential)

    def test_old_death_with_recent_birth_is_confidential(self):
        person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Inconsistent", surname="Dates"
        )
        _event(
            self.tree,
            individual=person,
            tag="BIRT",
            name="Birth",
            when=self.today - relativedelta(years=BIRTH_PRIVACY_YEARS - 5),
        )
        _event(
            self.tree,
            individual=person,
            tag="DEAT",
            when=self.today - relativedelta(years=DEATH_PRIVACY_YEARS + 1),
        )
        self.assertTrue(person.is_confidential)

    def test_recent_marriage_is_confidential(self):
        husband = Individual.objects.create(
            gedcom_tree=self.tree, given_name="H", surname="Marr"
        )
        wife = Individual.objects.create(
            gedcom_tree=self.tree, given_name="W", surname="Marr"
        )
        family = Family.objects.create(
            gedcom_tree=self.tree, husband=husband, wife=wife
        )
        _event(
            self.tree,
            family=family,
            tag="MARR",
            name="Marriage",
            when=self.today - relativedelta(years=MARRIAGE_PRIVACY_YEARS - 1),
        )
        self.assertTrue(husband.is_confidential)
        self.assertTrue(family.is_confidential)

    def test_old_marriage_without_birth_or_death_is_public(self):
        husband = Individual.objects.create(
            gedcom_tree=self.tree, given_name="H", surname="OldMarr"
        )
        family = Family.objects.create(gedcom_tree=self.tree, husband=husband)
        _event(
            self.tree,
            family=family,
            tag="MARR",
            name="Marriage",
            when=self.today - relativedelta(years=MARRIAGE_PRIVACY_YEARS + 1),
        )
        self.assertFalse(husband.is_confidential)
        self.assertFalse(family.is_confidential)


class ApplyPrivacyDecisionTests(TestCase):
    def setUp(self):
        self.tree = Tree.objects.create(name="Decision Tree")
        self.guest = AnonymousUser()
        self.viewer = User.objects.create_user("viewer", password="x")
        self.editor = User.objects.create_user("editor", password="x")
        self.admin = User.objects.create_user("tree_admin_user", password="x")
        TreeMembership.objects.create(
            user=self.viewer, gedcom_tree=self.tree, role="VIEWER"
        )
        TreeMembership.objects.create(
            user=self.editor, gedcom_tree=self.tree, role="EDITOR"
        )
        TreeMembership.objects.create(
            user=self.admin, gedcom_tree=self.tree, role="ADMIN"
        )

    def test_private_tree_members_skip_privacy(self):
        self.tree.is_public = False
        self.tree.show_living_people = False
        self.assertFalse(apply_privacy_for_request(self.viewer, self.tree))
        self.assertFalse(apply_privacy_for_request(self.editor, self.tree))
        self.assertFalse(apply_privacy_for_request(self.admin, self.tree))
        self.assertTrue(apply_privacy_for_request(self.guest, self.tree))

    def test_public_tree_guest_follows_show_living_flag(self):
        self.tree.is_public = True
        self.tree.show_living_people = False
        self.assertTrue(apply_privacy_for_request(self.guest, self.tree))
        self.assertTrue(apply_privacy_for_request(self.viewer, self.tree))
        self.tree.show_living_people = True
        self.assertFalse(apply_privacy_for_request(self.guest, self.tree))
        self.assertFalse(apply_privacy_for_request(self.viewer, self.tree))

    def test_public_tree_editors_bypass_even_when_flag_is_off(self):
        self.tree.is_public = True
        self.tree.show_living_people = False
        self.assertFalse(apply_privacy_for_request(self.editor, self.tree))
        self.assertFalse(apply_privacy_for_request(self.admin, self.tree))


class PublicTreePrivacyViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(
            name="Public Tree", is_public=True, show_living_people=False
        )
        self.living = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Secret", surname="Living"
        )
        self.historic = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Ada", surname="Lovelace"
        )
        _event(
            self.tree,
            individual=self.historic,
            tag="BIRT",
            name="Birth",
            when=date(1815, 12, 10),
        )
        _event(
            self.tree,
            individual=self.historic,
            tag="DEAT",
            when=date(1852, 11, 27),
        )
        self.url = reverse("genview:individual-list", kwargs={"tree_id": self.tree.id})
        self.editor = User.objects.create_user("pub_editor", password="password")
        TreeMembership.objects.create(
            user=self.editor, gedcom_tree=self.tree, role="EDITOR"
        )

    def test_guest_sees_historic_names_and_omits_living(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["apply_privacy"])
        self.assertContains(response, "Ada")
        self.assertNotContains(response, "Vertrauliche Person")
        self.assertNotContains(response, "Secret")

    def test_guest_people_list_search_omits_living_name(self):
        response = self.client.get(self.url, {"q": "Secret"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Vertrauliche Person")
        people = list(response.context["people"])
        self.assertFalse(any(p.pk == self.living.pk for p in people))
        self.assertContains(response, "Keine Personen gefunden.")

    def test_guest_sees_living_when_flag_on(self):
        self.tree.show_living_people = True
        self.tree.save(update_fields=["show_living_people"])
        response = self.client.get(self.url)
        self.assertFalse(response.context["apply_privacy"])
        self.assertContains(response, "Secret")

    def test_editor_sees_living_when_flag_off(self):
        self.client.login(username="pub_editor", password="password")
        response = self.client.get(self.url)
        self.assertFalse(response.context["apply_privacy"])
        self.assertContains(response, "Secret")

    def test_guest_tree_list_hides_confidential_start_person(self):
        self.tree.starting_individual = self.living
        self.tree.save(update_fields=["starting_individual"])
        list_url = reverse("genview:tree-list")
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        tree = response.context["trees"][0]
        self.assertIsNone(tree.visible_starting_individual)
        self.assertNotContains(response, "Secret")
        self.assertNotContains(response, "Zur Startperson")
        self.assertNotContains(response, "Baumansicht starten")

    def test_guest_tree_list_shows_public_start_person(self):
        self.tree.starting_individual = self.historic
        self.tree.save(update_fields=["starting_individual"])
        response = self.client.get(reverse("genview:tree-list"))
        self.assertEqual(response.status_code, 200)
        tree = response.context["trees"][0]
        self.assertEqual(tree.visible_starting_individual.pk, self.historic.pk)
        self.assertContains(response, "Ada")
        self.assertContains(response, "Zur Startperson")
        self.assertContains(response, "Baumansicht starten")

    def test_editor_tree_list_shows_confidential_start_person(self):
        self.tree.starting_individual = self.living
        self.tree.save(update_fields=["starting_individual"])
        self.client.login(username="pub_editor", password="password")
        response = self.client.get(reverse("genview:tree-list"))
        self.assertEqual(response.status_code, 200)
        tree = response.context["trees"][0]
        self.assertEqual(tree.visible_starting_individual.pk, self.living.pk)
        self.assertContains(response, "Secret")
        self.assertContains(response, "Zur Startperson")

    def test_guest_nav_hides_confidential_start_person(self):
        self.tree.starting_individual = self.living
        self.tree.save(update_fields=["starting_individual"])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["visible_starting_individual"])
        self.assertContains(response, "Übersicht")
        self.assertNotContains(response, "Baumansicht starten")
        self.assertNotContains(response, "⭐ Startperson")
        living_tree_url = reverse(
            "genview:family_tree_view",
            kwargs={"tree_id": self.tree.id, "individual_id": self.living.pk},
        )
        self.assertNotContains(response, living_tree_url)

    def test_guest_nav_shows_public_start_person(self):
        self.tree.starting_individual = self.historic
        self.tree.save(update_fields=["starting_individual"])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["visible_starting_individual"].pk, self.historic.pk
        )
        self.assertContains(response, "Baumansicht starten")
        self.assertContains(response, "⭐ Startperson")
        historic_tree_url = reverse(
            "genview:family_tree_view",
            kwargs={"tree_id": self.tree.id, "individual_id": self.historic.pk},
        )
        self.assertContains(response, historic_tree_url)

    def test_editor_nav_shows_confidential_start_person(self):
        self.tree.starting_individual = self.living
        self.tree.save(update_fields=["starting_individual"])
        self.client.login(username="pub_editor", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["visible_starting_individual"].pk, self.living.pk
        )
        self.assertContains(response, "Baumansicht starten")
        self.assertContains(response, "⭐ Startperson")


class AdminToolAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(name="Role Tree")
        self.editor = User.objects.create_user("role_editor", password="password")
        self.admin = User.objects.create_user("role_admin", password="password")
        TreeMembership.objects.create(
            user=self.editor, gedcom_tree=self.tree, role="EDITOR"
        )
        TreeMembership.objects.create(
            user=self.admin, gedcom_tree=self.tree, role="ADMIN"
        )
        self.person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Pat", surname="Role"
        )

    def test_editor_forbidden_from_admin_tools(self):
        self.client.login(username="role_editor", password="password")
        paths = [
            reverse("genview:place-deduplication", kwargs={"tree_id": self.tree.id}),
            reverse("genview:event-create-person", kwargs={"tree_id": self.tree.id}),
            reverse("genview:bulk-media-upload", kwargs={"tree_id": self.tree.id}),
            reverse("genview:media-face-scan", kwargs={"tree_id": self.tree.id}),
            reverse("genview:face-suggestion-review", kwargs={"tree_id": self.tree.id}),
            reverse("genview:document-suggestion-review", kwargs={"tree_id": self.tree.id}),
            reverse("genview:manage-memberships", kwargs={"tree_id": self.tree.id}),
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 403)

    def test_admin_can_open_admin_tools(self):
        self.client.login(username="role_admin", password="password")
        paths = [
            reverse("genview:place-deduplication", kwargs={"tree_id": self.tree.id}),
            reverse("genview:event-create-person", kwargs={"tree_id": self.tree.id}),
            reverse("genview:bulk-media-upload", kwargs={"tree_id": self.tree.id}),
            reverse("genview:manage-memberships", kwargs={"tree_id": self.tree.id}),
        ]
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_editor_can_still_edit_people(self):
        self.client.login(username="role_editor", password="password")
        url = reverse(
            "genview:individual-edit",
            kwargs={"tree_id": self.tree.id, "pk": self.person.pk},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class FamilyPrivacyFilterTests(TestCase):
    def setUp(self):
        self.tree = Tree.objects.create(name="Fam Filter")
        self.today = date.today()

    def test_family_with_living_child_is_filtered(self):
        father = Individual.objects.create(
            gedcom_tree=self.tree, given_name="F", surname="Hist"
        )
        _event(
            self.tree,
            individual=father,
            tag="BIRT",
            name="Birth",
            when=self.today - relativedelta(years=BIRTH_PRIVACY_YEARS + 20),
        )
        _event(
            self.tree,
            individual=father,
            tag="DEAT",
            when=self.today - relativedelta(years=DEATH_PRIVACY_YEARS + 5),
        )
        child = Individual.objects.create(
            gedcom_tree=self.tree, given_name="C", surname="Living"
        )
        family = Family.objects.create(gedcom_tree=self.tree, husband=father)
        ChildFamilyLink.objects.create(family=family, child=child)

        public_qs = apply_privacy_to_family_qs(
            Family.objects.filter(gedcom_tree=self.tree), True, self.tree.id
        )
        self.assertFalse(public_qs.filter(pk=family.pk).exists())
        self.assertTrue(family.is_confidential)
        self.assertTrue(
            apply_privacy_to_individual_qs(
                Individual.objects.filter(pk=father.pk), True
            ).exists()
        )
        self.assertFalse(
            apply_privacy_to_individual_qs(
                Individual.objects.filter(pk=child.pk), True
            ).exists()
        )


class GuestAncestorPagePrivacyTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(
            name="Ancestor Tree", is_public=True, show_living_people=False
        )
        self.ada = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Ada", surname="Lovelace", sex="F"
        )
        self.charles = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Charles", surname="Babbage", sex="M"
        )
        self.living_child = Individual.objects.create(
            gedcom_tree=self.tree, given_name="SecretChild", surname="Living", sex="F"
        )
        self.living_sibling = Individual.objects.create(
            gedcom_tree=self.tree, given_name="SecretSibling", surname="Living", sex="M"
        )
        for person, birth, death in (
            (self.ada, date(1815, 12, 10), date(1852, 11, 27)),
            (self.charles, date(1791, 12, 26), date(1871, 10, 18)),
        ):
            _event(self.tree, individual=person, tag="BIRT", name="Birth", when=birth)
            _event(self.tree, individual=person, tag="DEAT", when=death)

        self.family = Family.objects.create(
            gedcom_tree=self.tree, husband=self.charles, wife=self.ada
        )
        ChildFamilyLink.objects.create(family=self.family, child=self.living_child)

        parent_family = Family.objects.create(gedcom_tree=self.tree, wife=self.ada)
        ChildFamilyLink.objects.create(family=parent_family, child=self.ada)
        ChildFamilyLink.objects.create(family=parent_family, child=self.living_sibling)

        self.london = Place.objects.create(
            gedcom_tree=self.tree,
            name="London",
            latitude=51.507400,
            longitude=-0.127800,
        )
        child_birth = _event(
            self.tree,
            individual=self.living_child,
            tag="BIRT",
            name="Birth",
            when=date.today(),
        )
        child_birth.place = self.london
        child_birth.save(update_fields=["place"])
        ada_death = self.ada.events.filter(event_type__tag="DEAT").first()
        ada_death.place = self.london
        ada_death.save(update_fields=["place"])

        self.detail_url = reverse(
            "genview:individual-detail",
            kwargs={"tree_id": self.tree.id, "pk": self.ada.pk},
        )

    def test_guest_ancestor_json_and_map_omit_living_child(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        tree_json = json.dumps(response.context["tree_data"])
        stations_json = json.dumps(
            response.context["lifecycle_stations"], default=str
        )
        self.assertIn("Ada Lovelace", tree_json)
        self.assertIn("Charles Babbage", tree_json)
        self.assertNotIn("SecretChild", tree_json)
        self.assertNotIn("SecretChild", stations_json)
        self.assertNotContains(response, "SecretChild")
        self.assertNotContains(response, "SecretSibling")
        self.assertNotContains(response, "Vertrauliche Person")
        self.assertContains(response, "Charles")
        visible_siblings = list(response.context["visible_siblings"])
        self.assertFalse(any(s.pk == self.living_sibling.pk for s in visible_siblings))
        self.assertFalse(
            any(
                cl.child_id == self.living_child.pk
                for cl in response.context["children_links"]
            )
        )

    def test_guest_lists_and_search_omit_living_relatives(self):
        people = self.client.get(
            reverse("genview:individual-list", kwargs={"tree_id": self.tree.id})
        )
        self.assertNotContains(people, "SecretChild")
        self.assertNotContains(people, "Vertrauliche Person")

        families = self.client.get(
            reverse("genview:family-list", kwargs={"tree_id": self.tree.id})
        )
        self.assertNotContains(families, "SecretChild")
        fams = list(families.context["families"])
        self.assertFalse(any(f.pk == self.family.pk for f in fams))

        events = self.client.get(
            reverse("genview:event-list", kwargs={"tree_id": self.tree.id})
        )
        self.assertNotContains(events, "SecretChild")

        place = self.client.get(
            reverse(
                "genview:place-detail",
                kwargs={"tree_id": self.tree.id, "pk": self.london.pk},
            )
        )
        self.assertEqual(place.status_code, 200)
        self.assertNotContains(place, "SecretChild")
        self.assertContains(place, "Ada")
        self.assertEqual(place.context["place_event_count"], 1)

        overview = self.client.get(
            reverse("genview:tree-overview", kwargs={"tree_id": self.tree.id})
        )
        self.assertEqual(overview.context["stat_individuals"], 2)
        self.assertNotContains(overview, "SecretChild")

        search = self.client.get(
            reverse("genview:global-search", kwargs={"tree_id": self.tree.id}),
            {"q": "SecretChild"},
        )
        self.assertNotContains(search, "Vertrauliche Person")
        self.assertContains(search, "0 Treffer")
        results = search.context["results"]
        self.assertFalse(any(getattr(r, "pk", None) == self.living_child.pk for r in results))
