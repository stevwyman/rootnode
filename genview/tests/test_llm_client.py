import json
import os
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase

from genview.llm_client import extract_json_object, parse_question_via_llm
from genview.models import Individual, Tree
from genview.tree_query import execute_tree_query
from genview.tree_query_parse import (
    parse_natural_language_question,
    parse_question_rules,
    sanitize_llm_plan,
)


class ExtractJsonTests(SimpleTestCase):
    def test_plain_object(self):
        plan = extract_json_object('{"intent": "resolve_kinship"}')
        self.assertEqual(plan["intent"], "resolve_kinship")

    def test_fenced_markdown(self):
        plan = extract_json_object(
            '```json\n{"intent": "count_children", "kinship_path": ["mother"]}\n```'
        )
        self.assertEqual(plan["kinship_path"], ["mother"])

    def test_embedded_object(self):
        plan = extract_json_object('Sure, here: {"intent": "person_facts"} thanks')
        self.assertEqual(plan["intent"], "person_facts")


class RuleParserTests(SimpleTestCase):
    def test_maternal_grandmother_children(self):
        plan = parse_question_rules(
            "Wie viele Kinder hatte die Großmutter mütterlicherseits?"
        )
        self.assertEqual(plan["intent"], "count_children")
        self.assertEqual(plan["kinship_path"], ["mother", "mother"])

    def test_english_fathers_father(self):
        plan = parse_question_rules("what is the name of my fathers father")
        self.assertEqual(plan["intent"], "resolve_kinship")
        self.assertEqual(plan["kinship_path"], ["father", "father"])

    def test_relation_between(self):
        plan = parse_question_rules(
            "Was ist die Beziehung zwischen Anna Müller und Bernd Beispiel?"
        )
        self.assertEqual(plan["intent"], "relation_between")
        self.assertEqual(plan["person_name"], "Anna Müller")
        self.assertEqual(plan["target_name"], "Bernd Beispiel")

    def test_relation_between_strips_quotes(self):
        plan = parse_question_rules(
            'Wie sind "August Philip Hawke Brooksbank" und '
            '"Beatrice Elizabeth Mary Windsor " verwandt?'
        )
        self.assertEqual(plan["intent"], "relation_between")
        self.assertEqual(plan["person_name"], "August Philip Hawke Brooksbank")
        self.assertEqual(plan["target_name"], "Beatrice Elizabeth Mary Windsor")

    def test_unknown_returns_none(self):
        self.assertIsNone(parse_question_rules("Erzähl mir etwas Lustiges über Kekse"))

    def test_how_old_maternal_grandmother(self):
        plan = parse_question_rules(
            "Wie alt war meine Großmutter mütterlicherseits?"
        )
        self.assertEqual(plan["intent"], "person_age")
        self.assertEqual(plan["kinship_path"], ["mother", "mother"])

    def test_how_old_english(self):
        plan = parse_question_rules("how old was my grandmother")
        self.assertEqual(plan["intent"], "person_age")
        self.assertEqual(plan["kinship_path"], ["mother", "mother"])

    def test_how_old_named_person(self):
        plan = parse_question_rules(
            'Wie alt ist "Beatrice Elizabeth Mary Windsor"?'
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["intent"], "person_age")
        self.assertEqual(plan["kinship_path"], [])
        self.assertEqual(plan["person_name"], "Beatrice Elizabeth Mary Windsor")

    def test_how_old_named_person_english(self):
        plan = parse_question_rules("How old is Jane Doe?")
        self.assertEqual(plan["intent"], "person_age")
        self.assertEqual(plan["person_name"], "Jane Doe")

    def test_grandfathers_of_named_person(self):
        plan = parse_question_rules(
            "wie heißen die Großväter von Charles Philip Arthur Windsor?"
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["intent"], "list_relatives")
        self.assertEqual(plan["kind"], "grandfathers")
        self.assertEqual(plan["person_name"], "Charles Philip Arthur Windsor")

    def test_uncles_of_named_person(self):
        plan = parse_question_rules("Wie heißen die Onkel von Charles Windsor?")
        self.assertEqual(plan["intent"], "list_relatives")
        self.assertEqual(plan["kind"], "uncles")
        self.assertEqual(plan["person_name"], "Charles Windsor")

    def test_children_of_named_person_are_listed(self):
        plan = parse_question_rules("Wie heißen die Kinder von Charles Windsor?")
        self.assertEqual(plan["intent"], "list_relatives")
        self.assertEqual(plan["kind"], "children")
        self.assertEqual(plan["person_name"], "Charles Windsor")

    def test_father_of_named_person(self):
        plan = parse_question_rules(
            "Wer ist der Vater von Eugenie Victoria Helena Windsor?"
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["intent"], "resolve_kinship")
        self.assertEqual(plan["kinship_path"], ["father"])
        self.assertEqual(plan["person_name"], "Eugenie Victoria Helena Windsor")

    def test_genitive_father_of_named_person(self):
        plan = parse_question_rules(
            "Wie heißt der Vater von Eugenie Victoria Helena Windsor?"
        )
        self.assertEqual(plan["kinship_path"], ["father"])
        plan = parse_question_rules(
            "Wie lautet der Name des Vaters von Eugenie Victoria Helena Windsor?"
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["kinship_path"], ["father"])
        self.assertEqual(plan["person_name"], "Eugenie Victoria Helena Windsor")

    def test_english_mother_of_named_person(self):
        plan = parse_question_rules("Who is the mother of Jane Doe?")
        self.assertEqual(plan["intent"], "resolve_kinship")
        self.assertEqual(plan["kinship_path"], ["mother"])
        self.assertEqual(plan["person_name"], "Jane Doe")

    def test_other_grandmother_is_paternal(self):
        plan = parse_question_rules(
            "Wie viele Kinder hatte meine andere Großmutter?"
        )
        self.assertIsNotNone(plan)
        self.assertEqual(plan["intent"], "count_children")
        self.assertEqual(plan["kinship_path"], ["father", "mother"])

    def test_count_children_named_person_is_not_start_person(self):
        self.assertIsNone(parse_question_rules("Wie viele Kinder hatte Otto?"))

    def test_children_of_grandmother_via_kind_and_path(self):
        plan = parse_question_rules(
            "Welche Kinder hatte die Großmutter mütterlicherseits?"
        )
        self.assertEqual(plan["intent"], "list_relatives")
        self.assertEqual(plan["kind"], "children")
        self.assertEqual(plan["kinship_path"], ["mother", "mother"])

    def test_count_own_children(self):
        plan = parse_question_rules("Wie viele Kinder habe ich?")
        self.assertEqual(plan["intent"], "count_children")
        self.assertEqual(plan["kinship_path"], [])

    def test_uncle_from_berlin_death_fills_template(self):
        for question in (
            "When did my uncle from Berlin die",
            "When did my uncle from Berlin died",
            "Wann ist mein Onkel aus Berlin gestorben?",
        ):
            plan = parse_question_rules(question)
            self.assertIsNotNone(plan, question)
            self.assertEqual(plan["intent"], "person_facts", question)
            self.assertEqual(plan["kind"], "uncles", question)
            self.assertEqual(plan["place_filter"], "Berlin", question)
            self.assertEqual(plan["fact_focus"], "death", question)
            self.assertEqual(plan["kinship_path"], [], question)
            self.assertEqual(plan["person_name"], "", question)


class NaturalLanguagePipelineTests(TestCase):
    def setUp(self):
        self.tree = Tree.objects.create(name="NL Tree")
        self.start = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Stefan", surname="Start"
        )
        self.mother = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Maria", surname="Mutter"
        )
        self.gm = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Anna", surname="Grossmutter"
        )
        from genview.models import ChildFamilyLink, Family

        gm_fam = Family.objects.create(
            gedcom_tree=self.tree, husband=None, wife=self.gm
        )
        ChildFamilyLink.objects.create(child=self.mother, family=gm_fam)
        parent_fam = Family.objects.create(
            gedcom_tree=self.tree, husband=None, wife=self.mother
        )
        ChildFamilyLink.objects.create(child=self.start, family=parent_fam)
        self.tree.starting_individual = self.start
        self.tree.save(update_fields=["starting_individual"])
        from datetime import date
        from genview.models import Event, EventType

        deat = EventType.objects.filter(tag="DEAT").first() or EventType.objects.create(
            tag="DEAT", name="Death", is_visible=True
        )
        for person in (self.start, self.mother, self.gm):
            Event.objects.create(
                gedcom_tree=self.tree,
                individual=person,
                event_type=deat,
                parsed_date=date(1920, 1, 1),
            )

    def test_rules_then_execute(self):
        parsed = parse_natural_language_question(
            "Wie viele Kinder hatte die Großmutter mütterlicherseits?"
        )
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["source"], "rules")
        result = execute_tree_query(self.tree.id, parsed["plan"], apply_privacy=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["facts"]["children_count"], 1)

    def test_other_grandmother_is_not_the_start_person(self):
        from genview.models import ChildFamilyLink, Family

        father = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Hans", surname="Vater"
        )
        paternal_gm = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Else", surname="Vaterseite"
        )
        paternal_uncle = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Paul", surname="Onkel"
        )
        pgm_fam = Family.objects.create(
            gedcom_tree=self.tree, husband=None, wife=paternal_gm
        )
        ChildFamilyLink.objects.create(child=father, family=pgm_fam)
        ChildFamilyLink.objects.create(child=paternal_uncle, family=pgm_fam)
        parent_fam = Family.objects.get(wife=self.mother)
        parent_fam.husband = father
        parent_fam.save()

        start_fam = Family.objects.create(
            gedcom_tree=self.tree, husband=self.start, wife=None
        )
        for idx in range(3):
            child = Individual.objects.create(
                gedcom_tree=self.tree,
                given_name=f"Kind{idx}",
                surname="Start",
            )
            ChildFamilyLink.objects.create(child=child, family=start_fam)

        parsed = parse_natural_language_question(
            "Wie viele Kinder hatte meine andere Großmutter?"
        )
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["plan"]["kinship_path"], ["father", "mother"])
        result = execute_tree_query(self.tree.id, parsed["plan"], apply_privacy=False)
        self.assertTrue(result["ok"])
        self.assertEqual(result["facts"]["subject"]["id"], paternal_gm.pk)
        self.assertEqual(result["facts"]["children_count"], 2)
        self.assertIn("Else", result["answer"])
        self.assertNotIn("Stefan", result["answer"])

    def test_named_persons_grandfathers_via_question(self):
        from genview.models import ChildFamilyLink, Family

        charles = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Charles Philip Arthur",
            surname="Windsor",
        )
        father = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Philip", surname="Vater"
        )
        mother = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Elizabeth", surname="Mutter"
        )
        pgf = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Andreas", surname="Opa"
        )
        mgf = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Georg", surname="Opa"
        )
        ChildFamilyLink.objects.create(
            child=father,
            family=Family.objects.create(
                gedcom_tree=self.tree, husband=pgf, wife=None
            ),
        )
        ChildFamilyLink.objects.create(
            child=mother,
            family=Family.objects.create(
                gedcom_tree=self.tree, husband=mgf, wife=None
            ),
        )
        ChildFamilyLink.objects.create(
            child=charles,
            family=Family.objects.create(
                gedcom_tree=self.tree, husband=father, wife=mother
            ),
        )

        parsed = parse_natural_language_question(
            "wie heißen die Großväter von Charles Philip Arthur Windsor?"
        )
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["source"], "rules")
        self.assertEqual(parsed["plan"]["intent"], "list_relatives")
        self.assertEqual(parsed["plan"]["kind"], "grandfathers")
        result = execute_tree_query(self.tree.id, parsed["plan"], apply_privacy=False)
        self.assertTrue(result["ok"], result["answer"])
        self.assertIn("Andreas", result["answer"])
        self.assertIn("Georg", result["answer"])
        self.assertIn("Charles", result["answer"])

    def test_father_of_named_person_via_question(self):
        from genview.models import ChildFamilyLink, Family

        eugenie = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Eugenie Victoria Helena",
            surname="Windsor",
        )
        andrew = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Andrew", surname="Windsor"
        )
        ChildFamilyLink.objects.create(
            child=eugenie,
            family=Family.objects.create(
                gedcom_tree=self.tree, husband=andrew, wife=None
            ),
        )

        parsed = parse_natural_language_question(
            "Wer ist der Vater von Eugenie Victoria Helena Windsor?"
        )
        self.assertTrue(parsed["ok"], parsed.get("error"))
        self.assertEqual(parsed["source"], "rules")
        self.assertEqual(parsed["plan"]["intent"], "resolve_kinship")
        self.assertEqual(parsed["plan"]["kinship_path"], ["father"])
        result = execute_tree_query(self.tree.id, parsed["plan"], apply_privacy=False)
        self.assertTrue(result["ok"], result["answer"])
        self.assertIn("Andrew", result["answer"])

    @patch("genview.tree_query_parse.parse_question_via_llm")
    def test_llm_empty_path_does_not_count_start_person(self, mock_llm):
        mock_llm.return_value = {
            "plan": {
                "intent": "count_children",
                "kinship_path": [],
                "person_name": "",
                "target_name": "",
            },
            "raw": "{}",
            "error": None,
        }
        parsed = parse_natural_language_question(
            "Wie viele Kinder hatte die Oma väterlicherseits?"
        )
        self.assertFalse(parsed["ok"])
        self.assertIsNone(parsed["plan"])
        mock_llm.assert_called_once()

    def test_mama_von_startperson_uses_rules(self):
        parsed = parse_natural_language_question(
            "Kannst du mir sagen, wie die Mama von der Startperson heißt?"
        )
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["source"], "rules")
        self.assertEqual(parsed["plan"]["intent"], "resolve_kinship")
        self.assertEqual(parsed["plan"]["kinship_path"], ["mother"])

    @patch.dict(os.environ, {"TREE_QUERY_LLM_URL": "off"})
    def test_uncle_from_berlin_death_without_llm(self):
        from datetime import date

        from genview.models import (
            ChildFamilyLink,
            Event,
            EventType,
            Family,
            Place,
        )

        berlin = Place.objects.create(gedcom_tree=self.tree, name="Berlin")
        hamburg = Place.objects.create(gedcom_tree=self.tree, name="Hamburg")
        father = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Hans", surname="Vater", sex="M"
        )
        maternal_uncle = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Karl", surname="Onkel", sex="M"
        )
        paternal_uncle = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Franz", surname="Onkel", sex="M"
        )
        gm_fam = Family.objects.get(wife=self.gm)
        ChildFamilyLink.objects.create(child=maternal_uncle, family=gm_fam)
        pgf = Individual.objects.create(
            gedcom_tree=self.tree, given_name="Wilhelm", surname="Opa", sex="M"
        )
        pgf_fam = Family.objects.create(
            gedcom_tree=self.tree, husband=pgf, wife=None
        )
        ChildFamilyLink.objects.create(child=father, family=pgf_fam)
        ChildFamilyLink.objects.create(child=paternal_uncle, family=pgf_fam)
        parent_fam = Family.objects.get(wife=self.mother)
        parent_fam.husband = father
        parent_fam.save()

        deat = EventType.objects.filter(tag="DEAT").first()
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=maternal_uncle,
            event_type=deat,
            parsed_date=date(1985, 4, 2),
            place=berlin,
        )
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=paternal_uncle,
            event_type=deat,
            parsed_date=date(1970, 1, 1),
            place=hamburg,
        )

        parsed = parse_natural_language_question(
            "When did my uncle from Berlin died"
        )
        self.assertTrue(parsed["ok"], parsed.get("error"))
        self.assertEqual(parsed["source"], "rules")
        self.assertEqual(parsed["plan"]["intent"], "person_facts")
        self.assertEqual(parsed["plan"]["kind"], "uncles")
        self.assertEqual(parsed["plan"]["place_filter"], "Berlin")
        self.assertEqual(parsed["plan"]["fact_focus"], "death")
        result = execute_tree_query(self.tree.id, parsed["plan"], apply_privacy=False)
        self.assertTrue(result["ok"], result["answer"])
        self.assertIn("Karl", result["answer"])
        self.assertIn("1985", result["answer"])
        self.assertIn("gestorben", result["answer"])
        self.assertNotIn("Franz", result["answer"])

    @patch("genview.tree_query_parse.parse_question_via_llm")
    def test_uncertain_place_query_sends_draft_to_llm(self, mock_llm):
        mock_llm.return_value = {
            "plan": {
                "intent": "person_facts",
                "kind": "uncles",
                "place_filter": "Berlin",
                "fact_focus": "death",
                "kinship_path": [],
                "person_name": "",
                "person_id": 999999,
            },
            "raw": "{}",
            "error": None,
        }
        parsed = parse_natural_language_question(
            "When did my uncle from Berlin died"
        )
        self.assertTrue(parsed["ok"], parsed.get("error"))
        self.assertEqual(parsed["source"], "llm")
        self.assertIsNone(parsed["plan"]["person_id"])
        self.assertEqual(parsed["plan"]["intent"], "person_facts")
        self.assertEqual(parsed["plan"]["kind"], "uncles")
        self.assertEqual(parsed["plan"]["place_filter"], "Berlin")
        self.assertEqual(parsed["plan"]["fact_focus"], "death")
        mock_llm.assert_called_once()
        draft = mock_llm.call_args.kwargs["draft"]
        self.assertEqual(draft["intent"], "person_facts")
        self.assertEqual(draft["kind"], "uncles")
        self.assertEqual(draft["place_filter"], "Berlin")
        self.assertEqual(draft["fact_focus"], "death")

    @patch("genview.tree_query_parse.parse_question_via_llm")
    def test_llm_list_relatives_upgraded_for_death_question(self, mock_llm):
        mock_llm.return_value = {
            "plan": {
                "intent": "list_relatives",
                "kind": "uncles",
                "kinship_path": [],
            },
            "raw": "{}",
            "error": None,
        }
        parsed = parse_natural_language_question(
            "When did my uncle from Berlin died"
        )
        self.assertTrue(parsed["ok"], parsed.get("error"))
        self.assertEqual(parsed["plan"]["intent"], "person_facts")
        self.assertEqual(parsed["plan"]["kind"], "uncles")
        self.assertEqual(parsed["plan"]["place_filter"], "Berlin")
        self.assertEqual(parsed["plan"]["fact_focus"], "death")

    @patch("genview.tree_query_parse.parse_question_via_llm")
    def test_llm_cannot_invent_child_count_for_death_question(self, mock_llm):
        mock_llm.return_value = {
            "plan": {
                "intent": "count_children",
                "kinship_path": [],
                "person_name": "Elizabeth",
            },
            "raw": "{}",
            "error": None,
        }
        parsed = parse_natural_language_question(
            "When did my uncle from Berlin died"
        )
        self.assertTrue(parsed["ok"], parsed.get("error"))
        self.assertEqual(parsed["source"], "rules")
        self.assertEqual(parsed["plan"]["intent"], "person_facts")
        self.assertEqual(parsed["plan"]["kind"], "uncles")
        self.assertEqual(parsed["plan"]["place_filter"], "Berlin")
        mock_llm.assert_called_once()

    @patch.dict(os.environ, {"TREE_QUERY_LLM_URL": "off"})
    def test_unmatched_without_llm(self):
        parsed = parse_natural_language_question("Wer backt den besten Kuchen im Dorf?")
        self.assertFalse(parsed["ok"])
        self.assertIsNone(parsed["source"])

    @patch("genview.tree_query_parse.parse_question_via_llm")
    def test_off_topic_does_not_count_children(self, mock_llm):
        parsed = parse_natural_language_question("wieviele Personen sind ausgeblendet?")
        self.assertFalse(parsed["ok"])
        self.assertIsNone(parsed["plan"])
        self.assertIn("Verwandtschaft", parsed["error"])
        mock_llm.assert_not_called()


class LlmClientTests(SimpleTestCase):
    def test_ollama_chat_payload(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "message": {
                "content": json.dumps(
                    {"intent": "count_children", "kinship_path": ["mother", "mother"]}
                )
            }
        }
        with patch.dict(os.environ, {"TREE_QUERY_LLM_URL": "http://ollama.local:11434"}):
            with patch("genview.llm_client.requests.post", return_value=mock_response) as post:
                result = parse_question_via_llm(
                    "When did my uncle from Berlin died",
                    draft={
                        "intent": "person_facts",
                        "kind": "uncles",
                        "place_filter": "Berlin",
                    },
                )
        self.assertIsNone(result["error"])
        self.assertEqual(result["plan"]["intent"], "count_children")
        body = post.call_args.kwargs["json"]
        self.assertTrue(body["format"] == "json")
        self.assertIn("/api/chat", post.call_args.args[0])
        system = body["messages"][0]["content"]
        self.assertIn("place_filter", system)
        self.assertIn("fact_focus", system)
        self.assertIn("Draft plan", body["messages"][1]["content"])

    def test_wrapper_parse_endpoint(self):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "plan": {"intent": "person_facts", "kinship_path": ["father"]}
        }
        with patch.dict(os.environ, {"TREE_QUERY_LLM_URL": "http://llmnode:8000/parse"}):
            with patch("genview.llm_client.requests.post", return_value=mock_response) as post:
                result = parse_question_via_llm(
                    "Wann wurde mein Vater geboren?",
                    draft={"intent": "person_facts", "kinship_path": ["father"]},
                )
        self.assertEqual(result["plan"]["intent"], "person_facts")
        self.assertTrue(post.call_args.args[0].endswith("/parse"))
        body = post.call_args.kwargs["json"]
        self.assertIn("template", body)
        self.assertIn("draft", body)
        self.assertIn("capabilities_prompt", body)

    def test_connection_error(self):
        import requests as req

        with patch.dict(os.environ, {"TREE_QUERY_LLM_URL": "http://localhost:11434"}):
            with patch(
                "genview.llm_client.requests.post",
                side_effect=req.exceptions.ConnectionError(),
            ):
                result = parse_question_via_llm("Wer ist mein Vater?")
        self.assertIn("nicht erreichbar", result["error"])

    def test_sanitize_drops_ids(self):
        cleaned = sanitize_llm_plan(
            {"intent": "resolve_kinship", "person_id": 12, "kinship_path": ["father"]}
        )
        self.assertIsNone(cleaned["person_id"])
        self.assertEqual(cleaned["kinship_path"], ["father"])
