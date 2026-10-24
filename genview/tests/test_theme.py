from pathlib import Path

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from genview.models import Individual, MediaObject, Tree, TreeMembership

THEME_CSS = Path(__file__).resolve().parents[1] / "static" / "css" / "theme.css"


class ColorSchemeSwitcherTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user("theme_user", password="password")
        self.tree = Tree.objects.create(name="Theme Tree")
        TreeMembership.objects.create(
            user=self.user, gedcom_tree=self.tree, role=TreeMembership.Role.EDITOR
        )
        self.person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Ada", surname="Lovelace"
        )
        self.tree.starting_individual = self.person
        self.tree.save(update_fields=["starting_individual"])
        MediaObject.objects.create(
            gedcom_tree=self.tree,
            title="Portrait",
            file=SimpleUploadedFile("portrait.jpg", b"jpeg", content_type="image/jpeg"),
        )

    def _assert_theme_switcher(self, response):
        self.assertContains(response, 'id="bd-theme"')
        self.assertContains(response, 'data-bs-theme-value="light"')
        self.assertContains(response, 'data-bs-theme-value="dark"')
        self.assertContains(response, 'data-bs-theme-value="auto"')
        self.assertContains(response, "css/theme.css")
        self.assertContains(response, "js/theme.js")
        self.assertContains(response, "rootnode-color-theme")
        self.assertContains(response, "sw-navbar")
        self.assertContains(response, "app-logo")
        self.assertContains(response, "logo-plate")
        self.assertContains(response, "logo.svg")
        self.assertNotContains(response, "navbar-dark bg-primary")

    def test_login_page_includes_theme_switcher(self):
        response = self.client.get(reverse("two_factor:login"))
        self.assertEqual(response.status_code, 200)
        self._assert_theme_switcher(response)

    def test_tree_list_uses_palette_not_success_green(self):
        self.client.login(username="theme_user", password="password")
        response = self.client.get(reverse("genview:tree-list"))
        self.assertEqual(response.status_code, 200)
        self._assert_theme_switcher(response)
        self.assertContains(response, "Theme Tree")
        self.assertContains(response, "card-header bg-primary")
        self.assertContains(response, "btn btn-primary")
        self.assertNotContains(response, "bg-success")
        self.assertNotContains(response, "btn-success")
        self.assertNotContains(response, "border-success")

    def test_media_list_footer_is_not_a_white_stripe(self):
        self.client.login(username="theme_user", password="password")
        response = self.client.get(
            reverse("genview:media-list", kwargs={"tree_id": self.tree.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "card-footer bg-transparent")
        self.assertNotContains(response, "card-footer bg-white")

    def test_individual_page_uses_primary_instead_of_success(self):
        self.client.login(username="theme_user", password="password")
        response = self.client.get(
            reverse(
                "genview:individual-detail",
                kwargs={"tree_id": self.tree.pk, "pk": self.person.pk},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'text-primary">Medienarchiv')
        self.assertContains(response, 'text-primary">Album')
        self.assertContains(response, "timeline-events")
        self.assertNotContains(response, "btn-outline-success")
        self.assertNotContains(response, "card-footer bg-white")

    def test_theme_css_uses_shared_nav_and_avoids_white_cards(self):
        css = THEME_CSS.read_text(encoding="utf-8")
        self.assertIn(".sw-navbar", css)
        self.assertIn("border-bottom: 3px solid var(--sw-orange)", css)
        self.assertIn("--bs-card-bg: var(--sw-nav-bg)", css)
        self.assertNotIn("--bs-card-bg: #fff", css)
        self.assertIn("background-color: var(--bs-body-bg) !important", css)
