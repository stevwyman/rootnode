from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.db.utils import IntegrityError
from genview.models import Tree, TreeMembership, Individual

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
        self.assertTrue(response.url.startswith("/accounts/login/")) # Adjust if your login URL is different

    def test_access_denied_for_unauthorized_tree(self):
        """Logged-in users WITHOUT a TreeMembership should get a 403 Forbidden."""
        self.client.login(username="hacker", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403) # 403 means "Forbidden"

    def test_access_granted_for_authorized_user(self):
        """Logged-in users WITH a TreeMembership should see the page and data."""
        self.client.login(username="auth_user", password="password")
        response = self.client.get(self.url)
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Person") # Verify the person's name is rendered in the HTML

    def test_data_leak_prevention(self):
        """Ensure users only see people from the requested tree."""
        # Create a second tree and person that the authorized user DOES have access to
        tree2 = Tree.objects.create(name="Other Tree")
        TreeMembership.objects.create(user=self.authorized_user, gedcom_tree=tree2, role="VIEWER")
        Individual.objects.create(gedcom_tree=tree2, gedcom_id="@I2@", given_name="Secret", surname="Guy")
        
        # Load the page for Tree 1
        self.client.login(username="auth_user", password="password")
        response = self.client.get(self.url)
        
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Person") # From Tree 1
        self.assertNotContains(response, "Secret Guy") # From Tree 2 (Should NOT bleed over)
