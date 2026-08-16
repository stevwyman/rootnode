from datetime import date

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User

from genview.document_intelligence import (
    extract_document_suggestions,
    apply_document_suggestion,
    _parse_date_from_line,
    _detect_event_tag,
)
from genview.models import (
    Tree,
    TreeMembership,
    Individual,
    MediaObject,
    Event,
    EventType,
    DocumentExtractionSuggestion,
    FaceTag,
)


class DocumentIntelligenceTests(TestCase):
    def setUp(self):
        self.tree = Tree.objects.create(name="Doc Tree")
        self.media = MediaObject.objects.create(
            gedcom_tree=self.tree,
            title="Urkunde",
            category=MediaObject.Category.DOCUMENT,
        )
        self.person = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Maria",
            surname="Schmidt",
        )
        self.media.individuals.add(self.person)

    def test_parse_german_birth_line(self):
        raw, parsed = _parse_date_from_line("geboren am 15.03.1892 in Herford")
        self.assertEqual(raw, "15.03.1892")
        self.assertEqual(parsed, date(1892, 3, 15))
        self.assertEqual(_detect_event_tag("geboren am 15.03.1892"), "BIRT")

    def test_extract_suggestions_from_text(self):
        self.media.extracted_text = "Maria Schmidt, geboren am 15.03.1892 in Herford"
        self.media.save()
        created = extract_document_suggestions(self.media, self.tree.id)
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].event_type_tag, "BIRT")
        self.assertEqual(created[0].individual, self.person)

    def test_apply_suggestion_creates_event(self):
        self.media.extracted_text = "geboren am 01.01.1900"
        self.media.save()
        suggestion = extract_document_suggestions(self.media, self.tree.id)[0]
        event = apply_document_suggestion(suggestion, self.tree.id)
        self.assertEqual(event.event_type.tag, "BIRT")
        self.assertIn(event, self.media.events.all())
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, DocumentExtractionSuggestion.Status.ACCEPTED)


class FaceSuggestionReviewTests(TestCase):
    def setUp(self):
        self.client = __import__("django.test", fromlist=["Client"]).Client()
        self.user = User.objects.create_user(username="editor", password="password")
        self.tree = Tree.objects.create(name="Face Tree")
        TreeMembership.objects.create(
            user=self.user, gedcom_tree=self.tree, role="EDITOR"
        )
        self.media = MediaObject.objects.create(
            gedcom_tree=self.tree,
            title="Photo",
            category=MediaObject.Category.PHOTO,
        )
        self.person = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Hans",
            surname="Meyer",
        )
        self.tag = FaceTag.objects.create(
            media=self.media,
            x_percent=10,
            y_percent=10,
            width_percent=20,
            height_percent=20,
            confidence=0.9,
            suggested_individual=self.person,
            match_distance=0.12,
        )
        self.url = reverse("genview:face-suggestion-review", kwargs={"tree_id": self.tree.id})

    def test_accept_face_suggestion(self):
        self.client.login(username="editor", password="password")
        response = self.client.post(
            self.url,
            {"tag_id": self.tag.pk, "action": "accept"},
        )
        self.assertEqual(response.status_code, 302)
        self.tag.refresh_from_db()
        self.assertEqual(self.tag.individual, self.person)
        self.assertIsNone(self.tag.suggested_individual)
