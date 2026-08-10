from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.db.utils import IntegrityError
from genview.models import Tree, TreeMembership, Individual, EventType

class IndividualModelTests(TestCase):
    def setUp(self):
        # Create two separate trees to test isolation
        self.tree1 = Tree.objects.create(name="Smith Tree")
        self.tree2 = Tree.objects.create(name="Doe Tree")

    def test_individual_creation(self):
        """Test basic creation of an individual."""
        person = Individual.objects.create(
            gedcom_tree=self.tree1,
            gedcom_id="@I1@",
            given_name="John",
            surname="Smith"
        )
        self.assertEqual(person.given_name, "John")
        self.assertEqual(person.gedcom_tree.name, "Smith Tree")

    def test_unique_together_constraint(self):
        """Test that gedcom_id must be unique WITHIN a tree."""
        Individual.objects.create(gedcom_tree=self.tree1, gedcom_id="@I1@", given_name="John")
        
        # Attempting to create another @I1@ in tree1 should crash
        with self.assertRaises(IntegrityError):
            Individual.objects.create(gedcom_tree=self.tree1, gedcom_id="@I1@", given_name="Jane")

    def test_multi_tenant_id_isolation(self):
        """Test that different trees CAN share the same gedcom_id."""
        Individual.objects.create(gedcom_tree=self.tree1, gedcom_id="@I1@", given_name="John")
        
        # This should succeed without an IntegrityError because it's a different tree
        person_tree2 = Individual.objects.create(gedcom_tree=self.tree2, gedcom_id="@I1@", given_name="John")
        self.assertEqual(person_tree2.gedcom_tree, self.tree2)

    def test_auto_generate_gedcom_id(self):
        """Test that gedcom_id is auto-generated if left blank."""
        person = Individual.objects.create(
            gedcom_tree=self.tree1,
            given_name="Auto",
            surname="Generated"
        )
        # Fix: Die @-Zeichen ergänzen, die dein Mixin generiert!
        self.assertEqual(person.gedcom_id, f"@I-M{person.pk}@")

class IndividualListViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        
        # 1. Setup Users
        self.authorized_user = User.objects.create_user(username="auth_user", password="password")
        self.hacker_user = User.objects.create_user(username="hacker", password="password")
        
        # 2. Setup Data
        self.tree = Tree.objects.create(name="Test Tree")
        TreeMembership.objects.create(user=self.authorized_user, gedcom_tree=self.tree, role="VIEWER")
        
        self.person = Individual.objects.create(
            gedcom_tree=self.tree, 
            gedcom_id="@I1@", 
            given_name="Test", 
            surname="Person"
        )
        
        # URL for the list view of this specific tree
        self.url = reverse("genview:individual-list", kwargs={"tree_id": self.tree.id})

    def test_redirect_if_not_logged_in(self):
        """Anonymous users should be redirected to the login page."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/account/login/")) # Adjust if your login URL is different

    def test_access_denied_for_unauthorized_tree(self):
        """Logged-in users without a TreeMembership are denied with 403."""
        self.client.login(username="hacker", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_access_granted_for_authorized_user(self):
        """VIEWER members see historically public people unredacted."""
        from datetime import date
        from genview.models import Event

        et, _ = EventType.objects.get_or_create(tag="DEAT", defaults={"name": "Death"})
        # Death > 35 years ago ⇒ not confidential under privacy rules.
        Event.objects.create(
            individual=self.person,
            event_type=et,
            gedcom_tree=self.tree,
            parsed_date=date(1950, 1, 1),
        )

        self.client.login(username="auth_user", password="password")
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test")
        self.assertContains(response, "Person")

    def test_privacy_hides_confidential_names(self):
        """VIEWER privacy keeps confidential people in the list but redacts names."""
        confidential_person = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Secret",
            surname="LivingPerson",
        )

        self.client.login(username="auth_user", password="password")
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        loaded_person = next(
            p for p in response.context["object_list"] if p.pk == confidential_person.pk
        )
        self.assertTrue(loaded_person.is_confidential)
        self.assertTrue(response.context["apply_privacy"])
        self.assertContains(response, "Vertrauliche Person")
        self.assertNotContains(response, "Secret")
        self.assertNotContains(response, "LivingPerson")

    def test_data_leak_prevention(self):
        """Ensure users only see people from the requested tree."""
        from datetime import date
        from genview.models import Event

        et, _ = EventType.objects.get_or_create(tag="DEAT", defaults={"name": "Death"})
        Event.objects.create(
            individual=self.person,
            event_type=et,
            gedcom_tree=self.tree,
            parsed_date=date(1950, 1, 1),
        )

        tree2 = Tree.objects.create(name="Other Tree")
        TreeMembership.objects.create(
            user=self.authorized_user, gedcom_tree=tree2, role="VIEWER"
        )
        Individual.objects.create(
            gedcom_tree=tree2, gedcom_id="@I2@", given_name="Secret", surname="Guy"
        )

        self.client.login(username="auth_user", password="password")
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test")
        self.assertContains(response, "Person")
        self.assertNotContains(response, "Secret Guy")


class GlobalSearchViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="search_user", password="password")
        self.tree = Tree.objects.create(name="Search Tree")
        TreeMembership.objects.create(user=self.user, gedcom_tree=self.tree, role="VIEWER")

        self.john_smith = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="John",
            surname="Smith",
        )
        self.jane_doe = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Jane",
            surname="Doe",
        )
        self.johnson = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Robert",
            surname="Johnson",
        )

        self.url = reverse("genview:global-search", kwargs={"tree_id": self.tree.id})

    def test_multi_word_and_match_before_or_match(self):
        self.client.login(username="search_user", password="password")
        response = self.client.get(self.url, {"q": "John Smith"})

        self.assertEqual(response.status_code, 200)
        results = response.context["results"]
        person_results = [r for r in results if getattr(r, "search_type", None) == "Person"]
        self.assertGreaterEqual(len(person_results), 2)

        and_results = [r for r in person_results if getattr(r, "search_match", None) == "and"]
        or_results = [r for r in person_results if getattr(r, "search_match", None) == "or"]

        self.assertEqual(and_results[0].pk, self.john_smith.pk)
        self.assertTrue(any(r.pk == self.johnson.pk for r in or_results))
        self.assertEqual(person_results.index(and_results[0]), 0)
        if or_results:
            self.assertGreater(person_results.index(or_results[0]), person_results.index(and_results[-1]))

    def test_single_word_search_has_no_match_tiers(self):
        self.client.login(username="search_user", password="password")
        response = self.client.get(self.url, {"q": "Jane"})

        self.assertEqual(response.status_code, 200)
        person_results = [r for r in response.context["results"] if getattr(r, "search_type", None) == "Person"]
        self.assertEqual(person_results[0].pk, self.jane_doe.pk)
        exact = [r for r in person_results if getattr(r, "search_match", None) is None]
        self.assertTrue(exact)
        self.assertEqual(exact[0].pk, self.jane_doe.pk)

    def test_phonetic_and_before_exact_or(self):
        """'Hans Schmitt' finds phonetic Hans Schmidt (AND) before OR-only hits."""
        hans_schmidt = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Hans",
            surname="Schmidt",
        )
        self.client.login(username="search_user", password="password")
        response = self.client.get(self.url, {"q": "Hans Schmitt"})

        self.assertEqual(response.status_code, 200)
        person_results = [r for r in response.context["results"] if getattr(r, "search_type", None) == "Person"]

        phonetic_and = [r for r in person_results if getattr(r, "search_match", None) == "and_phonetic"]
        or_results = [r for r in person_results if getattr(r, "search_match", None) == "or"]

        self.assertTrue(any(r.pk == hans_schmidt.pk for r in phonetic_and))
        if or_results:
            self.assertLess(
                person_results.index(phonetic_and[0]),
                person_results.index(or_results[0]),
            )

    def test_single_word_phonetic_after_exact(self):
        schmitt = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Anna",
            surname="Schmitt",
        )
        schmidt = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Bernd",
            surname="Schmidt",
        )
        self.client.login(username="search_user", password="password")
        response = self.client.get(self.url, {"q": "Schmitt"})

        person_results = [r for r in response.context["results"] if getattr(r, "search_type", None) == "Person"]
        exact = [r for r in person_results if getattr(r, "search_match", None) is None]
        phonetic = [r for r in person_results if getattr(r, "search_match", None) == "phonetic"]

        self.assertTrue(any(r.pk == schmitt.pk for r in exact))
        self.assertTrue(any(r.pk == schmidt.pk for r in phonetic))
        self.assertLess(person_results.index(exact[0]), person_results.index(phonetic[0]))
