from datetime import date

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from genview.models import Event, EventType, Family, Individual, MediaObject, Tree, TreeMembership


def _etype(tag, name, category):
    et, _ = EventType.objects.get_or_create(
        tag=tag, defaults={"name": name, "category": category}
    )
    if et.category != category or et.name != name:
        et.category = category
        et.name = name
        et.save(update_fields=["category", "name"])
    return et


@override_settings(MEDIA_ROOT="/tmp/rootnode_test_media")
class FamilyDetailEventTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(name="Family Events Tree")
        self.user = User.objects.create_user(username="viewer", password="password")
        TreeMembership.objects.create(
            user=self.user, gedcom_tree=self.tree, role="VIEWER"
        )
        self.husband = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Max",
            surname="Muster",
        )
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=self.husband,
            event_type=_etype("BIRT", "Geburt", EventType.Category.INDIVIDUAL),
            parsed_date=date(1920, 1, 1),
        )
        self.wife = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Erika",
            surname="Muster",
        )
        self.family = Family.objects.create(
            gedcom_tree=self.tree,
            husband=self.husband,
            wife=self.wife,
        )
        Event.objects.create(
            gedcom_tree=self.tree,
            family=self.family,
            event_type=_etype("MARR", "Heirat", EventType.Category.FAMILY),
            parsed_date=date(1945, 6, 1),
        )
        self.url = reverse(
            "genview:family-detail",
            kwargs={"tree_id": self.tree.pk, "pk": self.family.pk},
        )

    def test_shows_person_linked_div_with_both_category(self):
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=self.husband,
            event_type=_etype("DIV", "Scheidung", EventType.Category.BOTH),
            parsed_date=date(1960, 3, 15),
        )
        self.client.login(username="viewer", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Heirat")
        self.assertContains(response, "Scheidung")
        self.assertNotContains(response, "Geburt")

    def test_shows_family_linked_div(self):
        Event.objects.create(
            gedcom_tree=self.tree,
            family=self.family,
            event_type=_etype("DIV", "Scheidung", EventType.Category.FAMILY),
            parsed_date=date(1962, 4, 1),
        )
        self.client.login(username="viewer", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Scheidung")
        self.assertEqual(len(list(response.context["family_events"])), 2)

    def test_shows_pdf_thumbnail_in_media_section(self):
        pdf = MediaObject.objects.create(
            gedcom_tree=self.tree,
            title="Heiratsurkunde",
            category=MediaObject.Category.DOCUMENT,
            file=SimpleUploadedFile(
                "marriage.pdf", b"%PDF-1.4 test", content_type="application/pdf"
            ),
        )
        pdf.families.add(self.family)
        self.client.login(username="viewer", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Heiratsurkunde")
        self.assertContains(response, "PDF")
        thumb_url = reverse(
            "genview:media-thumb",
            kwargs={"tree_id": self.tree.pk, "pk": pdf.pk, "size": "small"},
        )
        self.assertContains(response, thumb_url)

    def test_shows_event_linked_pdf_in_media_section(self):
        marr = self.family.events.filter(event_type__tag="MARR").first()
        pdf = MediaObject.objects.create(
            gedcom_tree=self.tree,
            title="Kirchenbucheintrag",
            category=MediaObject.Category.DOCUMENT,
            file=SimpleUploadedFile(
                "church.pdf", b"%PDF-1.4 test", content_type="application/pdf"
            ),
        )
        pdf.events.add(marr)
        self.client.login(username="viewer", password="password")
        response = self.client.get(self.url)
        self.assertContains(response, "Kirchenbucheintrag")
        self.assertContains(response, "PDF")
