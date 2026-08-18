from datetime import date, timedelta

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from genview.models import (
    ChildFamilyLink,
    Event,
    EventType,
    Family,
    Individual,
    Tree,
    TreeMembership,
)
from genview.privacy import BIRTH_PRIVACY_YEARS, DEATH_PRIVACY_YEARS, MARRIAGE_PRIVACY_YEARS
from genview.tree_calendar import (
    collect_upcoming_birthdays,
    collect_upcoming_anniversaries,
    _days_until_annual,
)


def _etype(tag, name, category=EventType.Category.INDIVIDUAL):
    et, _ = EventType.objects.get_or_create(
        tag=tag, defaults={"name": name, "category": category}
    )
    return et


class TreeCalendarTests(TestCase):
    def test_days_until_annual_same_year(self):
        today = date(2026, 8, 15)
        nxt, days = _days_until_annual(8, 20, today)
        self.assertEqual(nxt, date(2026, 8, 20))
        self.assertEqual(days, 5)

    def test_days_until_annual_next_year(self):
        today = date(2026, 12, 1)
        nxt, days = _days_until_annual(1, 15, today)
        self.assertEqual(nxt, date(2027, 1, 15))
        self.assertEqual(days, 45)

    def test_upcoming_birthday_within_horizon(self):
        tree = Tree.objects.create(name="Cal Tree")
        person = Individual.objects.create(
            gedcom_tree=tree,
            given_name="Birthday",
            surname="Soon",
        )
        birt = _etype("BIRT", "Birth")
        today = date.today()
        target = today + timedelta(days=5)
        Event.objects.create(
            gedcom_tree=tree,
            individual=person,
            event_type=birt,
            parsed_date=date(1986, target.month, target.day),
        )
        items = collect_upcoming_birthdays(tree.id, apply_privacy=False, horizon_days=30)
        self.assertTrue(any(i.title == person.full_name() for i in items))

    def test_deceased_birthday_is_omitted(self):
        tree = Tree.objects.create(name="Cal Tree")
        person = Individual.objects.create(
            gedcom_tree=tree, given_name="Late", surname="Person"
        )
        today = date.today()
        target = today + timedelta(days=4)
        Event.objects.create(
            gedcom_tree=tree,
            individual=person,
            event_type=_etype("BIRT", "Birth"),
            parsed_date=date(1920, target.month, target.day),
        )
        Event.objects.create(
            gedcom_tree=tree,
            individual=person,
            event_type=_etype("DEAT", "Death"),
            parsed_date=date(1980, 1, 1),
        )
        items = collect_upcoming_birthdays(tree.id, apply_privacy=False, horizon_days=30)
        self.assertFalse(any("Late" in i.title for i in items))

    def test_living_birthday_hidden_when_privacy_applies(self):
        tree = Tree.objects.create(name="Cal Tree")
        person = Individual.objects.create(
            gedcom_tree=tree, given_name="Young", surname="Person"
        )
        today = date.today()
        Event.objects.create(
            gedcom_tree=tree,
            individual=person,
            event_type=_etype("BIRT", "Birth"),
            parsed_date=date(today.year - 10, today.month, today.day),
        )
        items = collect_upcoming_birthdays(tree.id, apply_privacy=True, horizon_days=30)
        self.assertFalse(any("Young" in i.title for i in items))

    def test_historical_living_birthday_visible_when_privacy_applies(self):
        tree = Tree.objects.create(name="Cal Tree")
        person = Individual.objects.create(
            gedcom_tree=tree, given_name="Ancient", surname="Living"
        )
        today = date.today()
        target = today + timedelta(days=6)
        Event.objects.create(
            gedcom_tree=tree,
            individual=person,
            event_type=_etype("BIRT", "Birth"),
            parsed_date=date(today.year - BIRTH_PRIVACY_YEARS - 5, target.month, target.day),
        )
        items = collect_upcoming_birthdays(tree.id, apply_privacy=True, horizon_days=30)
        self.assertTrue(any(i.title == person.full_name() for i in items))

    def test_anniversary_with_living_child_hidden_when_privacy_applies(self):
        tree = Tree.objects.create(name="Cal Tree")
        today = date.today()
        target = today + timedelta(days=7)
        husband = Individual.objects.create(
            gedcom_tree=tree, given_name="Old", surname="Father"
        )
        wife = Individual.objects.create(
            gedcom_tree=tree, given_name="Old", surname="Mother"
        )
        for person, year in ((husband, 1850), (wife, 1852)):
            Event.objects.create(
                gedcom_tree=tree,
                individual=person,
                event_type=_etype("BIRT", "Birth"),
                parsed_date=date(year, 1, 1),
            )
            Event.objects.create(
                gedcom_tree=tree,
                individual=person,
                event_type=_etype("DEAT", "Death"),
                parsed_date=date(year + DEATH_PRIVACY_YEARS + 10, 1, 1),
            )
        family = Family.objects.create(gedcom_tree=tree, husband=husband, wife=wife)
        Event.objects.create(
            gedcom_tree=tree,
            family=family,
            event_type=_etype("MARR", "Marriage", EventType.Category.FAMILY),
            parsed_date=date(today.year - MARRIAGE_PRIVACY_YEARS - 10, target.month, target.day),
        )
        child = Individual.objects.create(
            gedcom_tree=tree, given_name="Living", surname="Child"
        )
        ChildFamilyLink.objects.create(family=family, child=child)
        Event.objects.create(
            gedcom_tree=tree,
            individual=child,
            event_type=_etype("BIRT", "Birth"),
            parsed_date=date(today.year - 10, 1, 1),
        )
        hidden = collect_upcoming_anniversaries(tree.id, apply_privacy=True, horizon_days=30)
        self.assertFalse(any("Old" in i.title for i in hidden))
        shown = collect_upcoming_anniversaries(tree.id, apply_privacy=False, horizon_days=30)
        self.assertTrue(any("Old Father" in i.title and "Old Mother" in i.title for i in shown))

    def test_birthday_query_count_does_not_scale_with_people(self):
        tree = Tree.objects.create(name="Cal Tree")
        today = date.today()
        target = today + timedelta(days=2)
        birt = _etype("BIRT", "Birth")
        for i in range(25):
            person = Individual.objects.create(
                gedcom_tree=tree, given_name=f"P{i}", surname="Living"
            )
            Event.objects.create(
                gedcom_tree=tree,
                individual=person,
                event_type=birt,
                parsed_date=date(1990, target.month, target.day),
            )
        with CaptureQueriesContext(connection) as ctx:
            items = collect_upcoming_birthdays(tree.id, apply_privacy=True, horizon_days=30)
        self.assertEqual(items, [])
        self.assertLess(len(ctx.captured_queries), 8)

        with CaptureQueriesContext(connection) as ctx:
            items = collect_upcoming_birthdays(tree.id, apply_privacy=False, horizon_days=30)
        self.assertEqual(len(items), 25)
        self.assertLess(len(ctx.captured_queries), 8)


class TreeOverviewViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="overview_user", password="password")
        self.tree = Tree.objects.create(name="Overview Tree")
        TreeMembership.objects.create(
            user=self.user, gedcom_tree=self.tree, role="VIEWER"
        )
        self.url = reverse("genview:tree-overview", kwargs={"tree_id": self.tree.id})

    def test_overview_loads_with_stats(self):
        Individual.objects.create(
            gedcom_tree=self.tree, given_name="A", surname="B"
        )
        self.client.login(username="overview_user", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stat_individuals"], 1)
        self.assertTemplateUsed(response, "genview/tree_overview.html")

    def test_confidential_birthday_hidden_for_public_guest(self):
        self.tree.is_public = True
        self.tree.show_living_people = False
        self.tree.save(update_fields=["is_public", "show_living_people"])
        person = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Young",
            surname="Person",
        )
        today = date.today()
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=person,
            event_type=_etype("BIRT", "Birth"),
            parsed_date=date(today.year - 10, today.month, today.day),
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        all_cal = response.context["calendar_today"] + response.context["calendar_upcoming"]
        self.assertFalse(any("Young" in i.title for i in all_cal))

    def test_guest_overview_query_count_does_not_scale_with_living_people(self):
        self.tree.is_public = True
        self.tree.show_living_people = False
        self.tree.save(update_fields=["is_public", "show_living_people"])
        today = date.today()
        target = today + timedelta(days=3)
        birt = _etype("BIRT", "Birth")
        marr = _etype("MARR", "Marriage", EventType.Category.FAMILY)
        for i in range(20):
            person = Individual.objects.create(
                gedcom_tree=self.tree, given_name=f"Live{i}", surname="Person"
            )
            Event.objects.create(
                gedcom_tree=self.tree,
                individual=person,
                event_type=birt,
                parsed_date=date(today.year - 20, target.month, target.day),
            )
            spouse = Individual.objects.create(
                gedcom_tree=self.tree, given_name=f"Spouse{i}", surname="Person"
            )
            family = Family.objects.create(gedcom_tree=self.tree, husband=person, wife=spouse)
            Event.objects.create(
                gedcom_tree=self.tree,
                family=family,
                event_type=marr,
                parsed_date=date(today.year - 5, target.month, target.day),
            )
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["stat_individuals"], 0)
        all_cal = response.context["calendar_today"] + response.context["calendar_upcoming"]
        self.assertEqual(all_cal, [])
        self.assertLess(len(ctx.captured_queries), 45)
