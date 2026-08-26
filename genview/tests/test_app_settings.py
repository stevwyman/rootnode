from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.features import (
    FEATURE_COLORIZE,
    FEATURE_FACE_RECOGNITION,
    FEATURE_OCR,
    FEATURE_TREE_QUERY,
    clear_app_settings_cache,
    feature_enabled,
    get_app_settings,
)
from core.models import AppSettings
from genview.models import MediaObject, Tree, TreeMembership


def _set_flags(**kwargs):
    flags = get_app_settings()
    for name, value in kwargs.items():
        setattr(flags, name, value)
    flags.save()
    clear_app_settings_cache()


class AppSettingsPageTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            "settings_admin", "admin@example.com", "password"
        )
        self.user = User.objects.create_user("settings_user", password="password")
        self.url = reverse("genview:app-settings")
        clear_app_settings_cache()

    def tearDown(self):
        _set_flags(
            enable_tree_query=True,
            enable_ocr=True,
            enable_face_recognition=True,
            enable_colorize=False,
        )

    def test_superuser_can_open_settings(self):
        self.client.login(username="settings_admin", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Anwendungseinstellungen")
        self.assertContains(response, "Stammbaum fragen")
        self.assertContains(response, "OCR / Texterkennung")
        self.assertContains(response, "Gesichtserkennung")
        self.assertContains(response, "Fotos kolorisieren")

    def test_non_superuser_is_forbidden(self):
        self.client.login(username="settings_user", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_superuser_can_disable_features(self):
        self.client.login(username="settings_admin", password="password")
        response = self.client.post(self.url, {}, follow=True)
        self.assertEqual(response.status_code, 200)
        flags = AppSettings.objects.get(pk=1)
        self.assertFalse(flags.enable_tree_query)
        self.assertFalse(flags.enable_ocr)
        self.assertFalse(flags.enable_face_recognition)
        self.assertFalse(flags.enable_colorize)
        self.assertFalse(feature_enabled(FEATURE_TREE_QUERY))
        self.assertFalse(feature_enabled(FEATURE_OCR))
        self.assertFalse(feature_enabled(FEATURE_FACE_RECOGNITION))
        self.assertFalse(feature_enabled(FEATURE_COLORIZE))

    def test_defaults_are_enabled(self):
        flags = get_app_settings()
        self.assertTrue(flags.enable_tree_query)
        self.assertTrue(flags.enable_ocr)
        self.assertTrue(flags.enable_face_recognition)
        self.assertFalse(flags.enable_colorize)


@override_settings(MEDIA_ROOT="/tmp/rootnode_test_media")
class FeatureFlagGatingTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user("flag_user", password="password")
        self.tree = Tree.objects.create(name="Flag Tree")
        TreeMembership.objects.create(
            user=self.user, gedcom_tree=self.tree, role="ADMIN"
        )
        self.photo = MediaObject.objects.create(
            gedcom_tree=self.tree,
            title="Foto",
            category=MediaObject.Category.PHOTO,
            file=SimpleUploadedFile("photo.jpg", b"fake-image", content_type="image/jpeg"),
        )
        self.document = MediaObject.objects.create(
            gedcom_tree=self.tree,
            title="Urkunde",
            category=MediaObject.Category.DOCUMENT,
            file=SimpleUploadedFile("doc.jpg", b"fake-doc", content_type="image/jpeg"),
            extracted_text="geboren am 1.1.1900",
        )
        self.overview_url = reverse(
            "genview:tree-overview", kwargs={"tree_id": self.tree.id}
        )
        self.query_url = reverse(
            "genview:tree-query", kwargs={"tree_id": self.tree.id}
        )
        self.execute_url = reverse(
            "genview:tree-query-execute", kwargs={"tree_id": self.tree.id}
        )
        self.face_scan_url = reverse(
            "genview:media-face-scan", kwargs={"tree_id": self.tree.id}
        )
        self.face_review_url = reverse(
            "genview:face-suggestion-review", kwargs={"tree_id": self.tree.id}
        )
        self.doc_review_url = reverse(
            "genview:document-suggestion-review", kwargs={"tree_id": self.tree.id}
        )
        self.photo_url = reverse(
            "genview:media-detail",
            kwargs={"tree_id": self.tree.id, "pk": self.photo.pk},
        )
        self.document_url = reverse(
            "genview:media-detail",
            kwargs={"tree_id": self.tree.id, "pk": self.document.pk},
        )
        self.client.login(username="flag_user", password="password")
        clear_app_settings_cache()

    def tearDown(self):
        _set_flags(
            enable_tree_query=True,
            enable_ocr=True,
            enable_face_recognition=True,
            enable_colorize=False,
        )

    def test_enabled_features_are_visible(self):
        overview = self.client.get(self.overview_url)
        self.assertEqual(overview.status_code, 200)
        self.assertContains(overview, self.query_url)
        self.assertContains(overview, "Stammbaum fragen")
        self.assertContains(overview, "Gesichter scannen")
        self.assertContains(overview, "Dokument-Vorschläge")

        photo = self.client.get(self.photo_url)
        self.assertContains(photo, "Bild analysieren")
        document = self.client.get(self.document_url)
        self.assertContains(document, "Text extrahieren (OCR)")
        self.assertContains(document, "geboren am 1.1.1900")

    def test_tree_query_hidden_and_404_when_disabled(self):
        _set_flags(enable_tree_query=False)
        overview = self.client.get(self.overview_url)
        self.assertNotContains(overview, self.query_url)
        self.assertNotContains(overview, "Stammbaum fragen")
        self.assertEqual(self.client.get(self.query_url).status_code, 404)
        self.assertEqual(
            self.client.post(
                self.execute_url,
                data='{"intent": "person_facts", "kinship_path": []}',
                content_type="application/json",
            ).status_code,
            404,
        )

    def test_face_recognition_hidden_and_404_when_disabled(self):
        _set_flags(enable_face_recognition=False)
        overview = self.client.get(self.overview_url)
        self.assertNotContains(overview, "Gesichter scannen")
        self.assertNotContains(overview, "Gesichter zuordnen")
        self.assertEqual(self.client.get(self.face_scan_url).status_code, 404)
        self.assertEqual(self.client.get(self.face_review_url).status_code, 404)

        photo = self.client.get(self.photo_url)
        self.assertEqual(photo.status_code, 200)
        self.assertNotContains(photo, "Bild analysieren")
        self.assertEqual(
            self.client.post(self.photo_url, {"detect": "1"}).status_code,
            404,
        )

    def test_ocr_hidden_and_404_when_disabled(self):
        _set_flags(enable_ocr=False)
        overview = self.client.get(self.overview_url)
        self.assertNotContains(overview, "Dokument-Vorschläge")
        self.assertEqual(self.client.get(self.doc_review_url).status_code, 404)

        document = self.client.get(self.document_url)
        self.assertEqual(document.status_code, 200)
        self.assertContains(document, "geboren am 1.1.1900")
        self.assertNotContains(document, "Text extrahieren (OCR)")
        self.assertNotContains(document, "Ereignis-Vorschläge erzeugen")
        self.assertEqual(
            self.client.post(self.document_url, {"ocr": "1"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(self.document_url, {"parse_suggestions": "1"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                self.document_url,
                {"accept_doc_suggestion": "1", "suggestion_id": "1"},
            ).status_code,
            404,
        )

    def test_colorize_hidden_and_404_when_disabled(self):
        photo = self.client.get(self.photo_url)
        self.assertEqual(photo.status_code, 200)
        self.assertNotContains(photo, "Kolorisieren")
        self.assertEqual(
            self.client.post(self.photo_url, {"colorize": "1"}).status_code,
            404,
        )

    def test_colorize_visible_when_enabled(self):
        _set_flags(enable_colorize=True)
        photo = self.client.get(self.photo_url)
        self.assertContains(photo, "Kolorisieren")

    def test_colorize_post_saves_a_copy(self):
        from io import BytesIO

        from PIL import Image

        _set_flags(enable_colorize=True)
        buffer = BytesIO()
        Image.new("RGB", (8, 8), color=(12, 34, 56)).save(buffer, format="JPEG")
        colorized_bytes = buffer.getvalue()
        with patch("genview.colorize_client.colorize_via_api") as mock_colorize:
            mock_colorize.return_value = {
                "image": colorized_bytes,
                "content_type": "image/jpeg",
                "error": None,
            }
            response = self.client.post(self.photo_url, {"colorize": "1"})
        self.assertEqual(response.status_code, 302)
        copies = MediaObject.objects.filter(gedcom_tree=self.tree).exclude(pk=self.photo.pk)
        colorized = copies.exclude(category=MediaObject.Category.DOCUMENT)
        self.assertEqual(colorized.count(), 1)
        new_media = colorized.first()
        self.assertIn("Kolorisiert", new_media.title)
        self.assertEqual(
            response.url,
            reverse(
                "genview:media-detail",
                kwargs={"tree_id": self.tree.id, "pk": new_media.pk},
            ),
        )
