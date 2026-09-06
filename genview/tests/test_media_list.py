from datetime import date

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from genview.models import Event, EventType, Family, Individual, MediaObject, Tree, TreeMembership


class MediaObjectListRelationsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(name="Media List Tree")
        self.editor = User.objects.create_user(username="editor", password="password")
        TreeMembership.objects.create(
            user=self.editor, gedcom_tree=self.tree, role="EDITOR"
        )
        self.husband = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Max", surname="Mustermann"
        )
        self.wife = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Erika", surname="Musterfrau"
        )
        self.family = Family.objects.create(
            gedcom_tree=self.tree, husband=self.husband, wife=self.wife
        )
        birth_type, _ = EventType.objects.get_or_create(
            tag="BIRT", defaults={"name": "Geburt"}
        )
        self.birth = Event.objects.create(
            gedcom_tree=self.tree,
            event_type=birth_type,
            individual=self.husband,
            parsed_date=date(1880, 5, 12),
        )
        self.media = MediaObject.objects.create(
            gedcom_tree=self.tree,
            title="Taufschein Max",
            category=MediaObject.Category.DOCUMENT,
            file=SimpleUploadedFile("taufschein.pdf", b"pdf", content_type="application/pdf"),
        )
        self.media.individuals.add(self.husband)
        self.media.families.add(self.family)
        self.media.events.add(self.birth)
        self.client.login(username="editor", password="password")
        self.url = reverse("genview:media-list", kwargs={"tree_id": self.tree.pk})

    def test_list_shows_related_people_families_and_events(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Taufschein Max")
        self.assertContains(response, "Personen")
        self.assertContains(response, "Max Mustermann")
        self.assertContains(response, "Familien")
        self.assertContains(response, "Mustermann")
        self.assertContains(response, "Musterfrau")
        self.assertContains(response, "Ereignisse")
        self.assertContains(response, "Geburt")
        self.assertContains(response, "12.05.1880")
        self.assertContains(
            response,
            reverse(
                "genview:event-detail",
                kwargs={"tree_id": self.tree.pk, "pk": self.birth.pk},
            ),
        )

    def test_list_shows_overflow_count_for_extra_events(self):
        occu, _ = EventType.objects.get_or_create(
            tag="OCCU", defaults={"name": "Beruf"}
        )
        extra = []
        for year in (1900, 1901, 1902):
            extra.append(
                Event.objects.create(
                    gedcom_tree=self.tree,
                    event_type=occu,
                    individual=self.husband,
                    parsed_date=date(year, 1, 1),
                    description=f"Job {year}",
                )
            )
        self.media.events.add(*extra)
        response = self.client.get(self.url)
        self.assertContains(response, "weitere")
