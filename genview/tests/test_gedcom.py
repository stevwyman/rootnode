from datetime import date

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from genview.gedcom_export import (
    build_gedcom_text,
    collect_ancestor_ids,
    collect_descendant_ids,
)
from genview.gedcom_match import MatchPerson, score_pair, suggest_for_incoming
from genview.gedcom_text import payload_from_bytes
from genview.models import ChildFamilyLink, Event, EventType, Family, Individual, Tree, TreeMembership


def _etype(tag, name, category=EventType.Category.INDIVIDUAL):
    et, _ = EventType.objects.get_or_create(
        tag=tag, defaults={"name": name, "category": category}
    )
    return et


def _event(tree, *, individual=None, family=None, tag="BIRT", when=None, name="Birth"):
    category = (
        EventType.Category.FAMILY if tag == "MARR" else EventType.Category.INDIVIDUAL
    )
    return Event.objects.create(
        gedcom_tree=tree,
        individual=individual,
        family=family,
        event_type=_etype(tag, name, category),
        parsed_date=when,
        raw_date=when.isoformat() if when else "",
    )


SAMPLE_GEDCOM = """0 HEAD
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Johann /Mueller/
1 SEX M
1 BIRT
2 DATE 12 JAN 1800
2 PLAC Berlin
0 @I2@ INDI
1 NAME Klaus /Neumann/
1 SEX M
1 BIRT
2 DATE 4 APR 1810
0 @F1@ FAM
1 HUSB @I1@
1 CHIL @I2@
0 TRLR
"""


class GedcomParseTests(TestCase):
    def test_payload_reads_people_and_families(self):
        payload = payload_from_bytes(SAMPLE_GEDCOM.encode("utf-8"))
        self.assertEqual(len(payload["individuals"]), 2)
        self.assertEqual(len(payload["families"]), 1)
        johann = payload["individuals"][0]
        self.assertEqual(johann["given_name"], "Johann")
        self.assertEqual(johann["surname"], "Mueller")
        self.assertEqual(johann["birth_year"], 1800)
        self.assertEqual(johann["birth_place"], "Berlin")
        self.assertEqual(payload["families"][0]["husb"], "@I1@")
        self.assertEqual(payload["families"][0]["children"], ["@I2@"])


class GedcomMatchAlgorithmTests(TestCase):
    def test_same_name_and_birth_year_is_a_strong_match(self):
        incoming = {
            "given_name": "Johann",
            "surname": "Mueller",
            "sex": "M",
            "birth_year": 1800,
            "names": [],
        }
        existing = MatchPerson(
            pk=1,
            given_name="Johann",
            surname="Mueller",
            sex="M",
            birth_year=1800,
            death_year=None,
            label="Johann Mueller (*1800)",
        )
        self.assertGreaterEqual(score_pair(incoming, existing), 0.88)

    def test_unrelated_person_is_not_suggested(self):
        incoming = {
            "given_name": "Johann",
            "surname": "Mueller",
            "sex": "M",
            "birth_year": 1800,
            "names": [],
        }
        other = MatchPerson(
            pk=2,
            given_name="Anna",
            surname="Schmidt",
            sex="F",
            birth_year=1920,
            death_year=None,
            label="Anna Schmidt",
        )
        self.assertEqual(suggest_for_incoming(incoming, [other]), [])

    def test_unique_surname_without_dates_is_not_forced_onto_random_hits(self):
        incoming = {
            "given_name": "MergeTest",
            "surname": "Xylophone",
            "sex": "M",
            "birth_year": None,
            "names": [],
        }
        other = MatchPerson(
            pk=3,
            given_name="Martin",
            surname="Helmchen",
            sex="M",
            birth_year=1786,
            death_year=1862,
            label="Martin Helmchen",
        )
        self.assertEqual(suggest_for_incoming(incoming, [other]), [])


class GedcomExportScopeTests(TestCase):
    def setUp(self):
        self.tree = Tree.objects.create(name="Export Tree")
        self.grand = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Grand", surname="Root"
        )
        self.parent = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Parent", surname="Root"
        )
        self.person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Focus", surname="Root"
        )
        self.child = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Child", surname="Root"
        )
        fam_g = Family.objects.create(gedcom_tree=self.tree, husband=self.grand)
        ChildFamilyLink.objects.create(child=self.parent, family=fam_g)
        fam_p = Family.objects.create(gedcom_tree=self.tree, husband=self.parent)
        ChildFamilyLink.objects.create(child=self.person, family=fam_p)
        fam_x = Family.objects.create(gedcom_tree=self.tree, husband=self.person)
        ChildFamilyLink.objects.create(child=self.child, family=fam_x)
        _event(self.tree, individual=self.grand, tag="BIRT", name="Birth", when=date(1800, 1, 1))
        _event(self.tree, individual=self.parent, tag="BIRT", name="Birth", when=date(1830, 1, 1))
        _event(self.tree, individual=self.person, tag="BIRT", name="Birth", when=date(1860, 1, 1))
        _event(self.tree, individual=self.child, tag="BIRT", name="Birth", when=date(1890, 1, 1))

    def test_ancestor_ids_exclude_descendants(self):
        ids = collect_ancestor_ids(self.person)
        self.assertEqual(ids, {self.person.pk, self.parent.pk, self.grand.pk})

    def test_descendant_ids_exclude_ancestors(self):
        ids = collect_descendant_ids(self.person, include_spouses=True)
        self.assertEqual(ids, {self.person.pk, self.child.pk})

    def test_build_gedcom_contains_names(self):
        text = build_gedcom_text(
            self.tree,
            Individual.objects.filter(pk=self.person.pk),
            Family.objects.none(),
        )
        self.assertIn("Focus", text)
        self.assertIn("/Root/", text)
        self.assertIn("0 HEAD", text)
        self.assertIn("0 TRLR", text)


class GedcomExportViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(name="Public Export", is_public=True)
        self.member = User.objects.create_user("ged_member", password="password")
        self.outsider = User.objects.create_user("ged_outsider", password="password")
        TreeMembership.objects.create(
            user=self.member, gedcom_tree=self.tree, role=TreeMembership.Role.EDITOR
        )
        self.historic = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Old", surname="Public"
        )
        self.living = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Living", surname="Secret"
        )
        _event(
            self.tree,
            individual=self.historic,
            tag="BIRT",
            name="Birth",
            when=date(1800, 6, 1),
        )
        _event(
            self.tree,
            individual=self.living,
            tag="BIRT",
            name="Birth",
            when=date.today().replace(year=date.today().year - 20),
        )

    def test_anonymous_is_redirected(self):
        url = reverse("genview:tree-gedcom-export", kwargs={"tree_id": self.tree.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_member_export_includes_living(self):
        self.client.login(username="ged_member", password="password")
        url = reverse("genview:tree-gedcom-export", kwargs={"tree_id": self.tree.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        body = response.content.decode("utf-8")
        self.assertIn("Living", body)
        self.assertIn("Old", body)

    def test_public_viewer_export_hides_living(self):
        self.client.login(username="ged_outsider", password="password")
        url = reverse("genview:tree-gedcom-export", kwargs={"tree_id": self.tree.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Old", body)
        self.assertNotIn("Living", body)
        self.assertNotIn("Secret", body)

    def test_person_ancestor_export(self):
        parent = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Ancestor", surname="Public"
        )
        _event(self.tree, individual=parent, tag="BIRT", name="Birth", when=date(1760, 1, 1))
        fam = Family.objects.create(gedcom_tree=self.tree, husband=parent)
        ChildFamilyLink.objects.create(child=self.historic, family=fam)
        self.client.login(username="ged_member", password="password")
        url = reverse(
            "genview:individual-gedcom-export",
            kwargs={"tree_id": self.tree.pk, "pk": self.historic.pk},
        )
        response = self.client.get(url, {"scope": "ancestors"})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Ancestor", body)
        self.assertIn("Old", body)
        self.assertNotIn("Living", body)


class GedcomMergeViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tree = Tree.objects.create(name="Merge Tree")
        self.editor = User.objects.create_user("merge_editor", password="password")
        self.admin = User.objects.create_user("merge_admin", password="password")
        TreeMembership.objects.create(
            user=self.editor, gedcom_tree=self.tree, role=TreeMembership.Role.EDITOR
        )
        TreeMembership.objects.create(
            user=self.admin, gedcom_tree=self.tree, role=TreeMembership.Role.ADMIN
        )
        self.existing = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Johann", surname="Mueller"
        )
        _event(
            self.tree,
            individual=self.existing,
            tag="BIRT",
            name="Birth",
            when=date(1800, 1, 12),
        )

    def test_editor_cannot_open_merge(self):
        self.client.login(username="merge_editor", password="password")
        url = reverse("genview:gedcom-merge-upload", kwargs={"tree_id": self.tree.pk})
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_admin_upload_match_and_apply(self):
        self.client.login(username="merge_admin", password="password")
        upload = reverse("genview:gedcom-merge-upload", kwargs={"tree_id": self.tree.pk})
        ged = SimpleUploadedFile(
            "extra.ged", SAMPLE_GEDCOM.encode("utf-8"), content_type="text/plain"
        )
        response = self.client.post(upload, {"gedcom_file": ged}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Johann")
        self.assertContains(response, "Klaus")
        match_url = reverse("genview:gedcom-merge-match", kwargs={"tree_id": self.tree.pk})
        apply_response = self.client.post(
            match_url,
            {
                "map_I1": str(self.existing.pk),
                "map_I2": "",
            },
            follow=True,
        )
        self.assertEqual(apply_response.status_code, 200)
        self.assertEqual(Individual.objects.filter(gedcom_tree=self.tree).count(), 2)
        klaus = Individual.objects.get(gedcom_tree=self.tree, given_name="Klaus")
        self.assertTrue(
            ChildFamilyLink.objects.filter(child=klaus, family__husband=self.existing).exists()
        )

    def test_match_without_upload_redirects(self):
        self.client.login(username="merge_admin", password="password")
        url = reverse("genview:gedcom-merge-match", kwargs={"tree_id": self.tree.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
