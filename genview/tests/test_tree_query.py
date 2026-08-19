from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse

from genview.models import (
    Tree,
    TreeMembership,
    Individual,
    Family,
    ChildFamilyLink,
    Event,
    EventType,
    Place,
)
from genview.tree_query import (
    walk_kinship,
    children_of,
    execute_tree_query,
    find_relation_path,
    describe_relation,
    describe_start_to_goal,
    TreeQueryError,
)


def _deat_type():
    tag = EventType.objects.filter(tag="DEAT").first()
    if tag:
        return tag
    return EventType.objects.create(tag="DEAT", name="Death", is_visible=True)


def _birt_type():
    tag = EventType.objects.filter(tag="BIRT").first()
    if tag:
        return tag
    return EventType.objects.create(tag="BIRT", name="Birth", is_visible=True)


def _mark_birth(tree, person, when, raw="", place=None):
    Event.objects.create(
        gedcom_tree=tree,
        individual=person,
        event_type=_birt_type(),
        parsed_date=when,
        raw_date=raw or (when.isoformat() if when else ""),
        place=place,
    )


def _mark_deceased(tree, person, year=1900, place=None):
    Event.objects.create(
        gedcom_tree=tree,
        individual=person,
        event_type=_deat_type(),
        parsed_date=date(year, 1, 1),
        raw_date=str(year),
        place=place,
    )


class TreeQueryExecutorTests(TestCase):
    def setUp(self):
        self.tree = Tree.objects.create(name="Query Tree")
        self.start = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Stefan", surname="Start"
        )
        self.mother = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Maria", surname="Mutter"
        )
        self.maternal_gm = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Anna", surname="Grossmutter"
        )
        self.maternal_gf = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Otto", surname="Grossvater"
        )
        self.uncle = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Karl", surname="Onkel"
        )
        self.father = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Hans", surname="Vater"
        )

        gm_family = Family.objects.create(
            gedcom_tree=self.tree,
            husband=self.maternal_gf,
            wife=self.maternal_gm,
        )
        ChildFamilyLink.objects.create(child=self.mother, family=gm_family)
        ChildFamilyLink.objects.create(child=self.uncle, family=gm_family)

        parent_family = Family.objects.create(
            gedcom_tree=self.tree,
            husband=self.father,
            wife=self.mother,
        )
        ChildFamilyLink.objects.create(child=self.start, family=parent_family)

        self.tree.starting_individual = self.start
        self.tree.save(update_fields=["starting_individual"])

        for person in (
            self.start,
            self.mother,
            self.father,
            self.maternal_gm,
            self.maternal_gf,
            self.uncle,
        ):
            _mark_deceased(self.tree, person, 1920)

    def test_walk_kinship_maternal_grandmother(self):
        found = walk_kinship(self.start, ["mother", "mother"])
        self.assertEqual(found, self.maternal_gm)

    def test_walk_missing_step_raises(self):
        lone = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Solo", surname="Person"
        )
        with self.assertRaises(TreeQueryError):
            walk_kinship(lone, ["father"])

    def test_count_children_of_grandmother(self):
        result = execute_tree_query(
            self.tree.id,
            {
                "intent": "count_children",
                "kinship_path": ["mother", "mother"],
            },
            apply_privacy=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["facts"]["children_count"], 2)
        self.assertIn("Anna Grossmutter", result["answer"])

    def test_list_children(self):
        result = execute_tree_query(
            self.tree.id,
            {"intent": "list_children", "kinship_path": ["mother", "mother"]},
            apply_privacy=False,
        )
        names = [c["display_name"] for c in result["facts"]["children"]]
        self.assertIn("Maria Mutter", names)
        self.assertIn("Karl Onkel", names)

    def test_resolve_fathers_father(self):
        paternal_gf = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Wilhelm", surname="Opa"
        )
        _mark_deceased(self.tree, paternal_gf, 1910)
        fam = Family.objects.create(
            gedcom_tree=self.tree, husband=paternal_gf, wife=None
        )
        ChildFamilyLink.objects.create(child=self.father, family=fam)

        result = execute_tree_query(
            self.tree.id,
            {"intent": "resolve_kinship", "kinship_path": ["father", "father"]},
            apply_privacy=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["facts"]["subject"]["id"], paternal_gf.pk)
        self.assertIn("Wilhelm", result["answer"])

    def test_relation_siblings(self):
        path = find_relation_path(self.mother, self.uncle)
        self.assertEqual(path, ["sibling"])
        self.assertIn("Geschwister", describe_relation(path))

        result = execute_tree_query(
            self.tree.id,
            {
                "intent": "relation_between",
                "person_id": self.mother.pk,
                "target_id": self.uncle.pk,
            },
            apply_privacy=False,
        )
        self.assertTrue(result["ok"])
        self.assertIn("Geschwister", result["answer"])

    def test_relation_nephew_of_aunt(self):
        self.start.sex = "M"
        self.start.save(update_fields=["sex"])
        self.uncle.sex = "M"
        self.uncle.save(update_fields=["sex"])
        self.mother.sex = "F"
        self.mother.save(update_fields=["sex"])

        path = find_relation_path(self.start, self.uncle)
        self.assertEqual(path, ["mother", "sibling"])

        nephew = execute_tree_query(
            self.tree.id,
            {
                "intent": "relation_between",
                "person_id": self.start.pk,
                "target_id": self.uncle.pk,
            },
            apply_privacy=False,
        )
        self.assertTrue(nephew["ok"])
        self.assertIn("Neffe", nephew["answer"])
        self.assertIn("Karl", nephew["answer"])

        aunt = execute_tree_query(
            self.tree.id,
            {
                "intent": "relation_between",
                "person_id": self.uncle.pk,
                "target_id": self.start.pk,
            },
            apply_privacy=False,
        )
        self.assertTrue(aunt["ok"])
        self.assertIn("Onkel", aunt["answer"])

    def test_mother_father_child_is_maternal_aunt_uncle(self):
        self.assertIn(
            "Onkel/Tante",
            describe_relation(["mother", "father", "child"]),
        )
        self.assertIn("Neffe", describe_start_to_goal(["mother", "father", "child"], self.start))

    def test_privacy_redacts_living_subject(self):
        living = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Lebend", surname="Kind"
        )
        self.tree.starting_individual = living
        self.tree.save(update_fields=["starting_individual"])
        result = execute_tree_query(
            self.tree.id,
            {"intent": "resolve_kinship", "kinship_path": []},
            apply_privacy=True,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["facts"]["subject"]["redacted"])
        self.assertIsNone(result["facts"]["subject"]["id"])
        self.assertNotIn("Lebend", result["answer"])

    def test_privacy_hides_child_count_for_living(self):
        living = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Lebend", surname="Eltern"
        )
        child = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Geheim", surname="Kind"
        )
        fam = Family.objects.create(gedcom_tree=self.tree, husband=living, wife=None)
        ChildFamilyLink.objects.create(child=child, family=fam)
        result = execute_tree_query(
            self.tree.id,
            {
                "intent": "count_children",
                "person_id": living.pk,
            },
            apply_privacy=True,
        )
        self.assertTrue(result["ok"])
        self.assertIsNone(result["facts"]["children_count"])
        self.assertNotIn("1", result["answer"])

    def test_privacy_hides_relation_if_living_endpoint(self):
        living = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Lebend", surname="Person"
        )
        result = execute_tree_query(
            self.tree.id,
            {
                "intent": "relation_between",
                "person_id": self.mother.pk,
                "target_id": living.pk,
            },
            apply_privacy=True,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["facts"]["relation_hidden"])
        self.assertNotIn("Lebend", result["answer"])

    def test_privacy_still_walks_to_public_grandmother(self):
        """Living start person may still resolve a deceased grandmother."""
        living_start = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Jetzt", surname="Lebend"
        )
        living_mother = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Noch", surname="Lebend"
        )
        parent_fam = Family.objects.create(
            gedcom_tree=self.tree, husband=None, wife=living_mother
        )
        ChildFamilyLink.objects.create(child=living_start, family=parent_fam)
        gm_fam = Family.objects.create(
            gedcom_tree=self.tree, husband=None, wife=self.maternal_gm
        )
        ChildFamilyLink.objects.create(child=living_mother, family=gm_fam)
        self.tree.starting_individual = living_start
        self.tree.save(update_fields=["starting_individual"])

        result = execute_tree_query(
            self.tree.id,
            {"intent": "resolve_kinship", "kinship_path": ["mother", "mother"]},
            apply_privacy=True,
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["facts"]["subject"]["redacted"])
        self.assertEqual(result["facts"]["subject"]["id"], self.maternal_gm.pk)
        self.assertNotIn("Jetzt", result["answer"])
        self.assertNotIn("Noch", result["answer"])
        self.assertIn("Anna", result["answer"])
        result = execute_tree_query(
            self.tree.id, {"intent": "hack_the_db"}, apply_privacy=False
        )
        self.assertFalse(result["ok"])

    def test_children_of_helper(self):
        kids = children_of(self.maternal_gm)
        self.assertEqual({k.pk for k in kids}, {self.mother.pk, self.uncle.pk})

    def test_name_resolution(self):
        result = execute_tree_query(
            self.tree.id,
            {
                "intent": "count_children",
                "person_name": "Anna Grossmutter",
            },
            apply_privacy=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["facts"]["children_count"], 2)

    def test_name_resolution_respects_privacy(self):
        living = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Lebend", surname="Geheim"
        )
        result = execute_tree_query(
            self.tree.id,
            {"intent": "resolve_kinship", "person_name": "Lebend Geheim"},
            apply_privacy=True,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["facts"], {})

    def test_person_facts_includes_calculated_age(self):
        _mark_birth(self.tree, self.maternal_gm, date(1833, 6, 15))
        # DEAT in setUp is 1920-01-01 → 86 years (birthday not yet reached)
        result = execute_tree_query(
            self.tree.id,
            {"intent": "person_facts", "kinship_path": ["mother", "mother"]},
            apply_privacy=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["facts"]["age_years"], 86)
        self.assertTrue(result["facts"]["age_at_death"])
        self.assertIn("86", result["answer"])
        self.assertIn("alt", result["answer"])

    def test_person_age_sentence(self):
        _mark_birth(self.tree, self.maternal_gm, date(1833, 1, 1))
        result = execute_tree_query(
            self.tree.id,
            {"intent": "person_age", "kinship_path": ["mother", "mother"]},
            apply_privacy=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["facts"]["age_years"], 87)
        self.assertIn("Anna Grossmutter", result["answer"])
        self.assertIn("87", result["answer"])
        self.assertNotIn("geboren", result["answer"])

    def test_person_age_year_only_is_approximate(self):
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=self.maternal_gm,
            event_type=_birt_type(),
            parsed_date=None,
            raw_date="ABT 1840",
        )
        result = execute_tree_query(
            self.tree.id,
            {"intent": "person_age", "person_id": self.maternal_gm.pk},
            apply_privacy=False,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["facts"]["age_years"], 80)
        self.assertTrue(result["facts"]["age_approximate"])
        self.assertIn("etwa 80", result["answer"])

    def test_person_age_unknown_without_birth(self):
        result = execute_tree_query(
            self.tree.id,
            {"intent": "person_age", "person_id": self.maternal_gm.pk},
            apply_privacy=False,
        )
        self.assertTrue(result["ok"])
        self.assertIsNone(result["facts"]["age_years"])
        self.assertIn("unbekannt", result["answer"])

    def test_list_grandfathers_of_named_person(self):
        charles = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Charles Philip Arthur",
            surname="Windsor",
        )
        father = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Philip", surname="Mountbatten"
        )
        mother = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Elizabeth", surname="Windsor"
        )
        paternal_gf = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Andreas", surname="Vatervater"
        )
        maternal_gf = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Georg", surname="Muttervater"
        )
        pgf_fam = Family.objects.create(
            gedcom_tree=self.tree, husband=paternal_gf, wife=None
        )
        ChildFamilyLink.objects.create(child=father, family=pgf_fam)
        mgf_fam = Family.objects.create(
            gedcom_tree=self.tree, husband=maternal_gf, wife=None
        )
        ChildFamilyLink.objects.create(child=mother, family=mgf_fam)
        parents = Family.objects.create(
            gedcom_tree=self.tree, husband=father, wife=mother
        )
        ChildFamilyLink.objects.create(child=charles, family=parents)

        result = execute_tree_query(
            self.tree.id,
            {
                "intent": "list_relatives",
                "kind": "grandfathers",
                "person_name": "Charles Philip Arthur Windsor",
            },
            apply_privacy=False,
        )
        self.assertTrue(result["ok"], result["answer"])
        self.assertEqual(result["intent"], "list_relatives")
        self.assertEqual(result["facts"]["subject"]["id"], charles.pk)
        names = [
            rel["person"]["display_name"]
            for rel in result["facts"]["relatives"]
            if rel.get("person")
        ]
        self.assertEqual(len(names), 2)
        self.assertIn("Andreas Vatervater", names)
        self.assertIn("Georg Muttervater", names)
        self.assertIn("väterlicherseits", result["answer"])
        self.assertIn("mütterlicherseits", result["answer"])
        self.assertNotIn("zweite Person", result["answer"].lower())

    def test_list_uncles_fans_out_siblings(self):
        charles = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Charles",
            surname="List",
        )
        father = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Philip", surname="List", sex="M"
        )
        grandfather = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Opa", surname="List", sex="M"
        )
        uncle = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Karl", surname="Onkel", sex="M"
        )
        aunt = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Helga", surname="Tante", sex="F"
        )
        gf_fam = Family.objects.create(
            gedcom_tree=self.tree, husband=grandfather, wife=None
        )
        ChildFamilyLink.objects.create(child=father, family=gf_fam)
        ChildFamilyLink.objects.create(child=uncle, family=gf_fam)
        ChildFamilyLink.objects.create(child=aunt, family=gf_fam)
        parents = Family.objects.create(
            gedcom_tree=self.tree, husband=father, wife=None
        )
        ChildFamilyLink.objects.create(child=charles, family=parents)

        result = execute_tree_query(
            self.tree.id,
            {
                "intent": "list_relatives",
                "kind": "uncles",
                "person_name": "Charles List",
            },
            apply_privacy=False,
        )
        self.assertTrue(result["ok"], result["answer"])
        names = [
            rel["person"]["display_name"]
            for rel in result["facts"]["relatives"]
            if rel.get("person")
        ]
        self.assertEqual(names, ["Karl Onkel"])
        self.assertIn("Karl", result["answer"])
        self.assertNotIn("Helga", result["answer"])

    def test_person_facts_selects_uncle_by_place_and_death(self):
        berlin = Place.objects.create(gedcom_tree=self.tree, name="Berlin")
        hamburg = Place.objects.create(gedcom_tree=self.tree, name="Hamburg")
        self.uncle.sex = Individual.Sex.MALE
        self.uncle.save(update_fields=["sex"])
        karl_death = self.uncle.events.filter(event_type__tag="DEAT").first()
        karl_death.parsed_date = date(1985, 4, 2)
        karl_death.raw_date = "2 APR 1985"
        karl_death.place = berlin
        karl_death.save(update_fields=["parsed_date", "raw_date", "place"])

        paternal_gf = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Wilhelm", surname="Opa", sex="M"
        )
        paternal_uncle = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Franz", surname="Onkel", sex="M"
        )
        _mark_deceased(self.tree, paternal_gf, 1910)
        _mark_deceased(self.tree, paternal_uncle, 1970, place=hamburg)
        pgf_fam = Family.objects.create(
            gedcom_tree=self.tree, husband=paternal_gf, wife=None
        )
        ChildFamilyLink.objects.create(child=self.father, family=pgf_fam)
        ChildFamilyLink.objects.create(child=paternal_uncle, family=pgf_fam)

        result = execute_tree_query(
            self.tree.id,
            {
                "intent": "person_facts",
                "kind": "uncles",
                "place_filter": "Berlin",
                "fact_focus": "death",
            },
            apply_privacy=False,
        )
        self.assertTrue(result["ok"], result["answer"])
        self.assertEqual(result["facts"]["subject"]["id"], self.uncle.pk)
        self.assertIn("Karl", result["answer"])
        self.assertIn("1985", result["answer"])
        self.assertIn("Berlin", result["answer"])
        self.assertIn("gestorben", result["answer"])
        self.assertNotIn("Franz", result["answer"])
        death_idx = result["answer"].find("gestorben")
        birth_idx = result["answer"].find("geboren")
        if birth_idx >= 0:
            self.assertLess(death_idx, birth_idx)

        listed = execute_tree_query(
            self.tree.id,
            {
                "intent": "list_relatives",
                "kind": "uncles",
                "place_filter": "Berlin",
            },
            apply_privacy=False,
        )
        self.assertTrue(listed["ok"], listed["answer"])
        names = [
            rel["person"]["display_name"]
            for rel in listed["facts"]["relatives"]
            if rel.get("person")
        ]
        self.assertEqual(names, ["Karl Onkel"])
        self.assertIn("Berlin", listed["answer"])
        self.assertNotIn("Franz", listed["answer"])


class TreeQueryViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="quser", password="password")
        self.tree = Tree.objects.create(name="View Tree")
        TreeMembership.objects.create(
            user=self.user, gedcom_tree=self.tree, role="EDITOR"
        )
        self.person = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Eva", surname="Beispiel"
        )
        _mark_deceased(self.tree, self.person, 1880)
        self.tree.starting_individual = self.person
        self.tree.save(update_fields=["starting_individual"])
        self.url = reverse("genview:tree-query", kwargs={"tree_id": self.tree.id})
        self.execute_url = reverse(
            "genview:tree-query-execute", kwargs={"tree_id": self.tree.id}
        )

    def test_page_loads(self):
        self.client.login(username="quser", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stammbaum fragen")

    def test_form_post_resolve(self):
        self.client.login(username="quser", password="password")
        response = self.client.post(
            self.url,
            {"intent": "resolve_kinship", "step_1": "", "step_2": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Eva Beispiel")

    def test_execute_json(self):
        self.client.login(username="quser", password="password")
        response = self.client.post(
            self.execute_url,
            data='{"intent": "person_facts", "kinship_path": []}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["facts"]["subject"]["id"], self.person.pk)

    def test_execute_denied_without_membership(self):
        User.objects.create_user(username="outsider", password="password")
        self.client.login(username="outsider", password="password")
        response = self.client.post(
            self.execute_url,
            data='{"intent": "resolve_kinship"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_natural_language_form_post(self):
        self.client.login(username="quser", password="password")
        response = self.client.post(
            self.url,
            {"question": "Wie viele Kinder hat die Startperson?"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "keine bekannten Kinder")

    def test_execute_json_with_question(self):
        self.client.login(username="quser", password="password")
        response = self.client.post(
            self.execute_url,
            data='{"question": "How many children did my grandmother on mothers side have?"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("parse_source"), "rules")
        self.assertFalse(payload["ok"])
        self.assertIn("Mutter", payload["answer"])

