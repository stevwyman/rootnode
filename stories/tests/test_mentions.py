from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from genview.models import Event, EventType, Individual, Tree
from stories.mentions import extract_mention_ids, render_story_body
from stories.models import StoryPage


class MentionParseTests(TestCase):
    def test_extracts_unique_ids_by_kind(self):
        body = (
            "[Anna](person:12) and [Anna again](person:12) "
            "married into [Wyman](family:3) at [Crediton](place:9)."
        )
        ids = extract_mention_ids(body)
        self.assertEqual(ids["person"], [12])
        self.assertEqual(ids["family"], [3])
        self.assertEqual(ids["place"], [9])

    def test_render_escapes_html_and_keeps_markdown(self):
        tree = Tree.objects.create(name="Mentions")
        html = render_story_body(
            "**Hi** <script>alert(1)</script>",
            tree_id=tree.pk,
            apply_privacy=False,
        ).html
        self.assertIn("<strong>Hi</strong>", html)
        self.assertNotIn("<script>", html)
        self.assertIn("alert(1)", html)

    def test_fenced_code_block_uses_pre_and_code(self):
        tree = Tree.objects.create(name="Mentions")
        html = render_story_body(
            "Intro\n\n```\nSELECT * FROM person\nWHERE id = 1\n```\n",
            tree_id=tree.pk,
            apply_privacy=False,
        ).html
        self.assertIn("<pre>", html)
        self.assertIn("<code>", html)
        self.assertIn("SELECT * FROM person", html)
        self.assertIn("WHERE id = 1", html)
        self.assertNotIn("<script>", html)


class MentionPrivacyTests(TestCase):
    def setUp(self):
        self.tree = Tree.objects.create(name="Privacy Tree")
        self.living = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Living", surname="Person"
        )
        self.historical = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Old", surname="Soul"
        )
        birth, _ = EventType.objects.get_or_create(
            tag="BIRT",
            defaults={"name": "Birth", "category": EventType.Category.INDIVIDUAL},
        )
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=self.historical,
            event_type=birth,
            parsed_date=date(1800, 1, 1),
        )

    def test_living_person_is_redacted_when_privacy_applies(self):
        body = f"[Living](person:{self.living.pk}) and [Old](person:{self.historical.pk})"
        rendered = render_story_body(body, tree_id=self.tree.pk, apply_privacy=True)
        self.assertIn("vertraulich", rendered.html)
        self.assertNotIn(self.living.get_absolute_url(), rendered.html)
        self.assertIn(self.historical.get_absolute_url(), rendered.html)
        self.assertEqual(rendered.individuals, [self.historical])

    def test_missing_person_is_plain_text(self):
        rendered = render_story_body(
            "[Ghost](person:99999)",
            tree_id=self.tree.pk,
            apply_privacy=False,
        )
        self.assertNotIn("href=", rendered.html)
        self.assertIn("Ghost", rendered.html)


class MentionSyncTests(TestCase):
    def test_save_syncs_mentions_for_this_tree_only(self):
        tree = Tree.objects.create(name="A")
        other = Tree.objects.create(name="B")
        person = Individual.objects.create(
            gedcom_tree=tree, given_name="Anna", surname="Schmidt"
        )
        Individual.objects.create(
            gedcom_tree=other, given_name="Other", surname="Tree"
        )
        user = User.objects.create_user("author", password="password")
        page = StoryPage.objects.create(
            gedcom_tree=tree,
            title="Story",
            body=f"[Anna](person:{person.pk}) [Nope](person:99999)",
            status=StoryPage.Status.PUBLISHED,
            created_by=user,
            updated_by=user,
        )
        self.assertEqual(list(page.mentioned_individuals.all()), [person])
        self.assertEqual(
            page.get_absolute_url(),
            reverse("stories:detail", kwargs={"tree_id": tree.pk, "pk": page.pk}),
        )