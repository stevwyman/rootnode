from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from genview.event_types import ensure_standard_family_event_types
from genview.models import ChildFamilyLink, Event, EventType, Family, Individual, Tree, TreeMembership


def _etype(tag, name, category):
    et, _ = EventType.objects.get_or_create(
        tag=tag, defaults={"name": name, "category": category}
    )
    if et.category != category or et.name != name:
        et.category = category
        et.name = name
        et.save(update_fields=["category", "name"])
    return et


class FamilyMemberAndEventTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(name="Family CRUD Tree")
        self.editor = User.objects.create_user(username="editor", password="password")
        self.admin = User.objects.create_user(username="tree_admin", password="password")
        self.viewer = User.objects.create_user(username="viewer", password="password")
        TreeMembership.objects.create(
            user=self.editor, gedcom_tree=self.tree, role="EDITOR"
        )
        TreeMembership.objects.create(
            user=self.admin, gedcom_tree=self.tree, role="ADMIN"
        )
        TreeMembership.objects.create(
            user=self.viewer, gedcom_tree=self.tree, role="VIEWER"
        )
        self.husband = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Max", surname="Muster"
        )
        self.wife = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Erika", surname="Muster"
        )
        self.child = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Anna", surname="Muster"
        )
        self.other = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Otto", surname="Gast"
        )
        self.family = Family.objects.create(
            gedcom_tree=self.tree, husband=self.husband, wife=self.wife
        )
        self.marr = _etype("MARR", "Heirat", EventType.Category.FAMILY)
        Event.objects.create(
            gedcom_tree=self.tree,
            family=self.family,
            event_type=self.marr,
            parsed_date=date(1945, 6, 1),
        )
        self.detail_url = reverse(
            "genview:family-detail",
            kwargs={"tree_id": self.tree.pk, "pk": self.family.pk},
        )

    def test_editor_sees_member_actions(self):
        self.client.login(username="editor", password="password")
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Familienmitglieder")
        self.assertContains(response, "Kind verknüpfen")
        self.assertContains(response, "Neues Kind")
        self.assertContains(response, "Ereignis")
        self.assertContains(
            response,
            reverse(
                "genview:family-unlink-spouse",
                kwargs={
                    "tree_id": self.tree.pk,
                    "family_pk": self.family.pk,
                    "role": "husband",
                },
            ),
        )

    def test_viewer_does_not_see_member_actions(self):
        self.client.login(username="viewer", password="password")
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Kind verknüpfen")
        self.assertNotContains(response, "Aus Familie entfernen")

    def test_editor_unlinks_husband_without_deleting_person(self):
        url = reverse(
            "genview:family-unlink-spouse",
            kwargs={
                "tree_id": self.tree.pk,
                "family_pk": self.family.pk,
                "role": "husband",
            },
        )
        self.client.login(username="editor", password="password")
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.family.refresh_from_db()
        self.assertIsNone(self.family.husband_id)
        self.assertTrue(Individual.objects.filter(pk=self.husband.pk).exists())

    def test_viewer_cannot_unlink_husband(self):
        url = reverse(
            "genview:family-unlink-spouse",
            kwargs={
                "tree_id": self.tree.pk,
                "family_pk": self.family.pk,
                "role": "husband",
            },
        )
        self.client.login(username="viewer", password="password")
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)
        self.family.refresh_from_db()
        self.assertEqual(self.family.husband_id, self.husband.pk)

    def test_editor_assigns_and_links_child(self):
        self.client.login(username="editor", password="password")
        assign_url = reverse(
            "genview:family-assign-spouse",
            kwargs={
                "tree_id": self.tree.pk,
                "family_pk": self.family.pk,
                "role": "husband",
            },
        )
        response = self.client.get(assign_url)
        self.assertEqual(response.status_code, 302)

        add_child = reverse(
            "genview:family-add-child",
            kwargs={"tree_id": self.tree.pk, "family_pk": self.family.pk},
        )
        response = self.client.post(
            add_child,
            {
                "family": self.family.pk,
                "child": self.child.pk,
                "relationship_type": ChildFamilyLink.Relationship.BIOLOGICAL,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            ChildFamilyLink.objects.filter(family=self.family, child=self.child).exists()
        )
        self.assertContains(response, "Anna Muster")

    def test_editor_removes_child_link_keeps_person(self):
        link = ChildFamilyLink.objects.create(family=self.family, child=self.child)
        url = reverse(
            "genview:link-delete",
            kwargs={"tree_id": self.tree.pk, "pk": link.pk},
        )
        self.client.login(username="editor", password="password")
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ChildFamilyLink.objects.filter(pk=link.pk).exists())
        self.assertTrue(Individual.objects.filter(pk=self.child.pk).exists())

    def test_assign_existing_person_as_wife(self):
        self.family.wife = None
        self.family.save(update_fields=["wife"])
        url = reverse(
            "genview:family-assign-spouse",
            kwargs={
                "tree_id": self.tree.pk,
                "family_pk": self.family.pk,
                "role": "wife",
            },
        )
        self.client.login(username="editor", password="password")
        response = self.client.post(url, {"individual": self.other.pk}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.family.refresh_from_db()
        self.assertEqual(self.family.wife_id, self.other.pk)

    def test_family_div_and_cens_appear_on_both_spouses(self):
        ensure_standard_family_event_types()
        div = EventType.objects.get(tag="DIV")
        cens = EventType.objects.get(tag="CENS")
        Event.objects.create(
            gedcom_tree=self.tree,
            family=self.family,
            event_type=div,
            parsed_date=date(1962, 4, 1),
            description="Scheidung in Herford",
        )
        Event.objects.create(
            gedcom_tree=self.tree,
            family=self.family,
            event_type=cens,
            parsed_date=date(1950, 9, 13),
            description="Volkszählung",
        )
        self.client.login(username="viewer", password="password")
        for person in (self.husband, self.wife):
            url = reverse(
                "genview:individual-detail",
                kwargs={"tree_id": self.tree.pk, "pk": person.pk},
            )
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Heirat")
            self.assertContains(response, "Scheidung in Herford")
            self.assertContains(response, "Volkszählung")
            if person == self.husband:
                self.assertContains(response, "Mit Erika Muster")
            else:
                self.assertContains(response, "Mit Max Muster")

        family_page = self.client.get(self.detail_url)
        self.assertContains(family_page, "Scheidung")
        self.assertContains(family_page, "Volkszählung")

    def test_child_birth_shows_on_family_not_parent_birth(self):
        birt = _etype("BIRT", "Geburt", EventType.Category.INDIVIDUAL)
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=self.husband,
            event_type=birt,
            parsed_date=date(1920, 1, 1),
            description="Geburt des Vaters",
        )
        ChildFamilyLink.objects.create(family=self.family, child=self.child)
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=self.child,
            event_type=birt,
            parsed_date=date(1948, 5, 2),
            description="Geburt der Tochter",
        )
        self.client.login(username="viewer", password="password")
        response = self.client.get(self.detail_url)
        self.assertContains(response, "Geburt der Tochter")
        self.assertNotContains(response, "Geburt des Vaters")

    def test_admin_can_create_family_div_from_family_url(self):
        ensure_standard_family_event_types()
        div = EventType.objects.get(tag="DIV")
        url = reverse(
            "genview:event-add-for-family",
            kwargs={"tree_id": self.tree.pk, "family_pk": self.family.pk},
        )
        self.client.login(username="editor", password="password")
        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Scheidung")
        self.assertContains(get_response, "Volkszählung")

        response = self.client.post(
            url,
            {
                "family": self.family.pk,
                "event_type": div.pk,
                "raw_date": "4 APR 1962",
                "parsed_date": "1962-04-04",
                "description": "",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Event.objects.filter(
                family=self.family, event_type=div, parsed_date=date(1962, 4, 4)
            ).exists()
        )

    def test_editor_updates_and_deletes_family_div(self):
        ensure_standard_family_event_types()
        div = EventType.objects.get(tag="DIV")
        event = Event.objects.create(
            gedcom_tree=self.tree,
            family=self.family,
            event_type=div,
            parsed_date=date(1962, 4, 1),
            description="alt",
        )
        edit_url = reverse(
            "genview:event-edit",
            kwargs={"tree_id": self.tree.pk, "pk": event.pk},
        )
        self.client.login(username="editor", password="password")
        response = self.client.post(
            f"{edit_url}?family={self.family.pk}",
            {
                "family": self.family.pk,
                "event_type": div.pk,
                "raw_date": "5 APR 1962",
                "parsed_date": "1962-04-05",
                "description": "Scheidung in Herford",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.detail_url)
        event.refresh_from_db()
        self.assertEqual(event.description, "Scheidung in Herford")

        husband_page = self.client.get(
            reverse(
                "genview:individual-detail",
                kwargs={"tree_id": self.tree.pk, "pk": self.husband.pk},
            )
        )
        self.assertContains(husband_page, "Scheidung in Herford")

        delete_url = reverse(
            "genview:event-delete",
            kwargs={"tree_id": self.tree.pk, "pk": event.pk},
        )
        response = self.client.post(f"{delete_url}?family={self.family.pk}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.detail_url)
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    def test_editor_records_child_birth_from_family(self):
        ChildFamilyLink.objects.create(family=self.family, child=self.child)
        birt = _etype("BIRT", "Geburt", EventType.Category.INDIVIDUAL)
        url = reverse(
            "genview:event-add-for-person",
            kwargs={"tree_id": self.tree.pk, "person_pk": self.child.pk},
        )
        self.client.login(username="editor", password="password")
        get_response = self.client.get(f"{url}?family={self.family.pk}")
        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "Geburt")

        response = self.client.post(
            f"{url}?family={self.family.pk}",
            {
                "individual": self.child.pk,
                "event_type": birt.pk,
                "raw_date": "2 MAY 1948",
                "parsed_date": "1948-05-02",
                "description": "Geburt der Tochter",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.detail_url)
        self.assertTrue(
            Event.objects.filter(
                individual=self.child,
                event_type=birt,
                parsed_date=date(1948, 5, 2),
            ).exists()
        )
        child_page = self.client.get(
            reverse(
                "genview:individual-detail",
                kwargs={"tree_id": self.tree.pk, "pk": self.child.pk},
            )
        )
        self.assertContains(child_page, "Geburt der Tochter")
        family_page = self.client.get(self.detail_url)
        self.assertContains(family_page, "Geburt der Tochter")
        self.assertNotContains(family_page, "Geburt erfassen")

    def test_editor_cannot_create_person_event(self):
        birt = _etype("BIRT", "Geburt", EventType.Category.INDIVIDUAL)
        url = reverse(
            "genview:event-add-for-person",
            kwargs={"tree_id": self.tree.pk, "person_pk": self.husband.pk},
        )
        self.client.login(username="editor", password="password")
        response = self.client.post(
            url,
            {
                "individual": self.husband.pk,
                "event_type": birt.pk,
                "raw_date": "1920",
                "parsed_date": "1920-01-01",
                "description": "",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_editor_cannot_edit_parent_birth(self):
        birt = _etype("BIRT", "Geburt", EventType.Category.INDIVIDUAL)
        event = Event.objects.create(
            gedcom_tree=self.tree,
            individual=self.husband,
            event_type=birt,
            parsed_date=date(1920, 1, 1),
        )
        url = reverse(
            "genview:event-edit",
            kwargs={"tree_id": self.tree.pk, "pk": event.pk},
        )
        self.client.login(username="editor", password="password")
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_new_child_returns_to_family(self):
        url = reverse("genview:individual-add", kwargs={"tree_id": self.tree.pk})
        self.client.login(username="editor", password="password")
        response = self.client.post(
            f"{url}?parent_family={self.family.pk}",
            {
                "given_name": "Lisa",
                "surname": "Muster",
                "sex": Individual.Sex.FEMALE,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.detail_url)
        child = Individual.objects.get(given_name="Lisa", surname="Muster")
        self.assertTrue(
            ChildFamilyLink.objects.filter(family=self.family, child=child).exists()
        )
