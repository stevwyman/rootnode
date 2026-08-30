from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from genview.models import EntityTag, Event, EventType, Family, Individual, Place, Tree, TreeMembership


class EntityTagTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(
            name="Tag Tree", is_public=True, show_living_people=True
        )
        self.other_tree = Tree.objects.create(name="Other Tree")
        self.editor = User.objects.create_user(username="editor", password="password")
        self.viewer = User.objects.create_user(username="viewer", password="password")
        TreeMembership.objects.create(
            user=self.editor, gedcom_tree=self.tree, role="EDITOR"
        )
        TreeMembership.objects.create(
            user=self.viewer, gedcom_tree=self.tree, role="VIEWER"
        )
        self.person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Max", surname="Muster"
        )
        self.family = Family.objects.create(gedcom_tree=self.tree, husband=self.person)
        self.place = Place.objects.create(gedcom_tree=self.tree, name="Herford")
        self.marr, _ = EventType.objects.get_or_create(
            tag="MARR",
            defaults={"name": "Heirat", "category": EventType.Category.FAMILY},
        )
        self.event = Event.objects.create(
            gedcom_tree=self.tree,
            family=self.family,
            event_type=self.marr,
        )
        self.list_url = reverse(
            "genview:entity-tag-list", kwargs={"tree_id": self.tree.pk}
        )
        self.browse_url = reverse(
            "genview:entity-tag-browse", kwargs={"tree_id": self.tree.pk}
        )
        self.person_url = reverse(
            "genview:individual-detail",
            kwargs={"tree_id": self.tree.pk, "pk": self.person.pk},
        )

    def test_guest_does_not_see_tags_on_public_person(self):
        tag = EntityTag.objects.create(
            gedcom_tree=self.tree, name="Unklar", color="#FFC107"
        )
        self.person.entity_tags.add(tag)
        response = self.client.get(self.person_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Unklar")

    def test_viewer_sees_tags_on_person_and_list(self):
        tag = EntityTag.objects.create(
            gedcom_tree=self.tree,
            name="Fehlende Daten",
            description="Noch unvollständig",
            color="#DC3545",
        )
        self.person.entity_tags.add(tag)
        self.client.login(username="viewer", password="password")
        response = self.client.get(self.person_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fehlende Daten")
        people = self.client.get(
            reverse("genview:individual-list", kwargs={"tree_id": self.tree.pk})
        )
        self.assertContains(people, "Fehlende Daten")
        self.assertEqual(self.client.get(self.list_url).status_code, 200)

    def test_viewer_cannot_create_or_assign_tags(self):
        self.client.login(username="viewer", password="password")
        add_url = reverse("genview:entity-tag-add", kwargs={"tree_id": self.tree.pk})
        self.assertEqual(
            self.client.post(
                add_url, {"name": "Vollständig", "color": "#198754", "description": ""}
            ).status_code,
            403,
        )
        tag = EntityTag.objects.create(
            gedcom_tree=self.tree, name="Unklar", color="#FFC107"
        )
        assign = reverse(
            "genview:entity-tag-assign",
            kwargs={
                "tree_id": self.tree.pk,
                "target": "individual",
                "pk": self.person.pk,
            },
        )
        self.assertEqual(
            self.client.post(assign, {"tag_ids": [tag.pk]}).status_code, 403
        )

    def test_editor_creates_assigns_and_sees_event_tag_on_person(self):
        self.client.login(username="editor", password="password")
        add_url = reverse("genview:entity-tag-add", kwargs={"tree_id": self.tree.pk})
        response = self.client.post(
            add_url,
            {
                "name": "Vollständig",
                "description": "Datensatz geprüft",
                "color": "#198754",
            },
        )
        self.assertEqual(response.status_code, 302)
        tag = EntityTag.objects.get(gedcom_tree=self.tree, name="Vollständig")
        self.assertEqual(tag.color, "#198754")

        assign = reverse(
            "genview:entity-tag-assign",
            kwargs={
                "tree_id": self.tree.pk,
                "target": "event",
                "pk": self.event.pk,
            },
        )
        response = self.client.post(
            assign,
            {"tag_ids": [tag.pk], "next": self.person_url},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(tag, self.event.entity_tags.all())
        person_page = self.client.get(self.person_url)
        self.assertContains(person_page, "Vollständig")
        family_page = self.client.get(
            reverse(
                "genview:family-detail",
                kwargs={"tree_id": self.tree.pk, "pk": self.family.pk},
            )
        )
        self.assertContains(family_page, "Vollständig")

    def test_editor_cannot_assign_tag_from_other_tree(self):
        foreign = EntityTag.objects.create(
            gedcom_tree=self.other_tree, name="Fremd", color="#000000"
        )
        self.client.login(username="editor", password="password")
        assign = reverse(
            "genview:entity-tag-assign",
            kwargs={
                "tree_id": self.tree.pk,
                "target": "individual",
                "pk": self.person.pk,
            },
        )
        self.client.post(assign, {"tag_ids": [foreign.pk]})
        self.assertFalse(self.person.entity_tags.filter(pk=foreign.pk).exists())

    def test_editor_deletes_tag_and_clears_assignments(self):
        tag = EntityTag.objects.create(
            gedcom_tree=self.tree, name="Unklar", color="#FFC107"
        )
        self.person.entity_tags.add(tag)
        self.place.entity_tags.add(tag)
        self.client.login(username="editor", password="password")
        delete_url = reverse(
            "genview:entity-tag-delete",
            kwargs={"tree_id": self.tree.pk, "pk": tag.pk},
        )
        response = self.client.post(delete_url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(EntityTag.objects.filter(pk=tag.pk).exists())
        self.assertFalse(self.person.entity_tags.exists())

    def test_guest_cannot_browse_tagged_entries(self):
        response = self.client.get(self.browse_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url.lower())

    def test_viewer_browses_entries_matching_any_selected_tag(self):
        complete = EntityTag.objects.create(
            gedcom_tree=self.tree, name="Vollständig", color="#198754"
        )
        missing = EntityTag.objects.create(
            gedcom_tree=self.tree, name="Fehlende Daten", color="#DC3545"
        )
        other = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Otto", surname="OhneMarkierung"
        )
        self.person.entity_tags.add(complete)
        self.family.entity_tags.add(missing)
        self.place.entity_tags.add(complete)
        self.event.entity_tags.add(missing)

        self.client.login(username="viewer", password="password")
        empty = self.client.get(self.browse_url)
        self.assertEqual(empty.status_code, 200)
        self.assertContains(empty, "Wähle oben")
        self.assertNotContains(empty, "Muster")
        self.assertNotContains(empty, "OhneMarkierung")

        only_complete = self.client.get(self.browse_url, {"tag": complete.pk})
        self.assertContains(only_complete, "Muster")
        self.assertContains(only_complete, "Herford")
        self.assertNotContains(only_complete, "OhneMarkierung")
        self.assertNotContains(only_complete, self.marr.name)

        both = self.client.get(
            self.browse_url, {"tag": [complete.pk, missing.pk]}
        )
        self.assertContains(both, "Muster")
        self.assertContains(both, "Herford")
        self.assertContains(both, self.marr.name)
        self.assertNotContains(both, "OhneMarkierung")
        self.assertEqual(both.context["result_count"], 4)

    def test_tag_pages_use_data_browse_and_admin_manage_nav(self):
        self.client.login(username="viewer", password="password")
        viewer_page = self.client.get(self.person_url)
        self.assertContains(viewer_page, f'href="{self.browse_url}"')
        self.assertNotContains(viewer_page, "Markierungen verwalten")

        self.client.login(username="editor", password="password")
        editor_page = self.client.get(self.person_url)
        self.assertContains(editor_page, f'href="{self.browse_url}"')
        self.assertContains(editor_page, "Markierungen verwalten")
        self.assertContains(editor_page, f'href="{self.list_url}"')
