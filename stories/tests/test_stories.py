from datetime import date

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.features import FEATURE_STORIES, clear_app_settings_cache, feature_enabled, get_app_settings
from genview.models import Event, EventType, Family, Individual, MediaObject, Place, Tree, TreeMembership
from stories.models import StoryPage
from stories.services import prepare_mentioned_people


def _set_stories_enabled(enabled: bool) -> None:
    flags = get_app_settings()
    flags.enable_stories = enabled
    flags.save()
    clear_app_settings_cache()


class StoryAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(name="Story Tree")
        self.editor = User.objects.create_user("story_editor", password="password")
        self.viewer = User.objects.create_user("story_viewer", password="password")
        TreeMembership.objects.create(
            user=self.editor, gedcom_tree=self.tree, role=TreeMembership.Role.EDITOR
        )
        TreeMembership.objects.create(
            user=self.viewer, gedcom_tree=self.tree, role=TreeMembership.Role.VIEWER
        )
        self.published = StoryPage.objects.create(
            gedcom_tree=self.tree,
            title="Die Auswanderung",
            body="Ein **Text**.",
            kind=StoryPage.Kind.STORY,
            status=StoryPage.Status.PUBLISHED,
        )
        self.draft = StoryPage.objects.create(
            gedcom_tree=self.tree,
            title="Entwurf intern",
            body="Geheim",
            status=StoryPage.Status.DRAFT,
        )
        self.index_url = reverse("stories:index", kwargs={"tree_id": self.tree.pk})
        self.published_url = reverse(
            "stories:detail", kwargs={"tree_id": self.tree.pk, "pk": self.published.pk}
        )
        self.draft_url = reverse(
            "stories:detail", kwargs={"tree_id": self.tree.pk, "pk": self.draft.pk}
        )
        self.create_url = reverse("stories:create", kwargs={"tree_id": self.tree.pk})
        clear_app_settings_cache()

    def tearDown(self):
        _set_stories_enabled(True)

    def test_nav_shows_stories_for_members(self):
        self.client.login(username="story_viewer", password="password")
        overview = self.client.get(
            reverse("genview:tree-overview", kwargs={"tree_id": self.tree.pk})
        )
        self.assertEqual(overview.status_code, 200)
        self.assertContains(overview, self.index_url)
        self.assertContains(overview, "Geschichten")

    def test_editor_can_create_and_read_draft(self):
        self.client.login(username="story_editor", password="password")
        response = self.client.post(
            self.create_url,
            {
                "title": "Suche im Stammbaum",
                "kind": StoryPage.Kind.GUIDE,
                "status": StoryPage.Status.PUBLISHED,
                "body": "## Tipps\n\nNutze die Suche oben.",
            },
        )
        self.assertEqual(response.status_code, 302)
        page = StoryPage.objects.get(title="Suche im Stammbaum")
        self.assertEqual(page.kind, StoryPage.Kind.GUIDE)
        self.assertEqual(page.created_by, self.editor)
        detail = self.client.get(self.draft_url)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Entwurf intern")
        self.assertContains(detail, "Geheim")

    def test_viewer_cannot_create_or_see_draft(self):
        self.client.login(username="story_viewer", password="password")
        self.assertEqual(self.client.get(self.create_url).status_code, 403)
        self.assertEqual(self.client.get(self.draft_url).status_code, 404)
        published = self.client.get(self.published_url)
        self.assertEqual(published.status_code, 200)
        self.assertContains(published, "Die Auswanderung")
        self.assertContains(published, "<strong>Text</strong>", html=False)
        self.assertNotContains(published, "Entwurf intern")

    def test_private_tree_is_hidden_from_guests(self):
        self.assertEqual(self.client.get(self.index_url).status_code, 404)
        self.assertEqual(self.client.get(self.published_url).status_code, 404)

    def test_other_tree_does_not_see_the_page(self):
        other = Tree.objects.create(name="Other")
        TreeMembership.objects.create(
            user=self.editor, gedcom_tree=other, role=TreeMembership.Role.EDITOR
        )
        self.client.login(username="story_editor", password="password")
        url = reverse(
            "stories:detail", kwargs={"tree_id": other.pk, "pk": self.published.pk}
        )
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_feature_flag_hides_nav_and_404s(self):
        _set_stories_enabled(False)
        self.assertFalse(feature_enabled(FEATURE_STORIES))
        self.client.login(username="story_editor", password="password")
        overview = self.client.get(
            reverse("genview:tree-overview", kwargs={"tree_id": self.tree.pk})
        )
        self.assertNotContains(overview, self.index_url)
        self.assertNotContains(overview, "Geschichten")
        self.assertEqual(self.client.get(self.index_url).status_code, 404)
        self.assertEqual(self.client.get(self.published_url).status_code, 404)


@override_settings(MEDIA_ROOT="/tmp/rootnode_test_media")
class StoryPublicPrivacyTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(
            name="Public Stories", is_public=True, show_living_people=False
        )
        self.living = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Living", surname="Guest"
        )
        self.historical = Individual.objects.create(
            gedcom_tree=self.tree, given_name="John", surname="Wyman", sex="M"
        )
        birth, _ = EventType.objects.get_or_create(
            tag="BIRT",
            defaults={"name": "Birth", "category": EventType.Category.INDIVIDUAL},
        )
        death, _ = EventType.objects.get_or_create(
            tag="DEAT",
            defaults={"name": "Death", "category": EventType.Category.INDIVIDUAL},
        )
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=self.historical,
            event_type=birth,
            parsed_date=date(1851, 3, 12),
        )
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=self.historical,
            event_type=death,
            parsed_date=date(1922, 11, 4),
        )
        self.portrait = MediaObject.objects.create(
            gedcom_tree=self.tree,
            title="Portrait",
            category=MediaObject.Category.PHOTO,
            file=SimpleUploadedFile("portrait.jpg", b"jpeg", content_type="image/jpeg"),
            is_portrait=True,
        )
        self.portrait.individuals.add(self.historical)
        self.family = Family.objects.create(gedcom_tree=self.tree, husband=self.historical)
        self.place = Place.objects.create(gedcom_tree=self.tree, name="Crediton")
        self.page = StoryPage.objects.create(
            gedcom_tree=self.tree,
            title="Die Auswanderung der Wymans",
            status=StoryPage.Status.PUBLISHED,
            body=(
                f"[John Wyman](person:{self.historical.pk}) "
                f"und [Living](person:{self.living.pk}) "
                f"aus [Crediton](place:{self.place.pk})."
            ),
        )

    def test_guest_sees_published_story_with_living_person_redacted(self):
        url = reverse(
            "stories:detail", kwargs={"tree_id": self.tree.pk, "pk": self.page.pk}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Die Auswanderung der Wymans")
        self.assertContains(response, self.historical.get_absolute_url())
        self.assertContains(response, "vertraulich")
        self.assertNotContains(response, self.living.get_absolute_url())
        self.assertContains(response, self.place.get_absolute_url())
        self.assertContains(response, "Crediton")
        self.assertContains(response, "* 12.03.1851  † 04.11.1922")
        self.assertContains(response, "story-rail-dates")
        self.assertContains(
            response,
            reverse(
                "genview:media-thumb",
                kwargs={
                    "tree_id": self.tree.pk,
                    "pk": self.portrait.pk,
                    "size": "mini",
                },
            ),
        )

    def test_guest_does_not_see_private_portrait(self):
        self.portrait.is_private = True
        self.portrait.save(update_fields=["is_private"])
        url = reverse(
            "stories:detail", kwargs={"tree_id": self.tree.pk, "pk": self.page.pk}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            reverse(
                "genview:media-thumb",
                kwargs={
                    "tree_id": self.tree.pk,
                    "pk": self.portrait.pk,
                    "size": "mini",
                },
            ),
        )
        self.assertContains(response, "👦")

    def test_prepare_mentioned_people_respects_media_privacy(self):
        people = prepare_mentioned_people([self.historical], apply_privacy=True)
        self.assertEqual(people[0].rail_portrait, self.portrait)
        self.assertEqual(people[0].rail_dates, "* 12.03.1851  † 04.11.1922")
        self.portrait.is_private = True
        self.portrait.save(update_fields=["is_private"])
        hidden = prepare_mentioned_people([self.historical], apply_privacy=True)
        self.assertIsNone(hidden[0].rail_portrait)
        shown = prepare_mentioned_people([self.historical], apply_privacy=False)
        self.assertEqual(shown[0].rail_portrait, self.portrait)

    def test_person_detail_lists_backlinks(self):
        editor = User.objects.create_user("pub_editor", password="password")
        TreeMembership.objects.create(
            user=editor, gedcom_tree=self.tree, role=TreeMembership.Role.EDITOR
        )
        self.client.login(username="pub_editor", password="password")
        response = self.client.get(self.historical.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Erwähnt in")
        self.assertContains(response, "Die Auswanderung der Wymans")


class StorySettingsTests(TestCase):
    def test_stories_default_on(self):
        flags = get_app_settings()
        self.assertTrue(flags.enable_stories)