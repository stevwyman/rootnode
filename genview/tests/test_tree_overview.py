from datetime import date, timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User

from genview.models import Tree, TreeMembership, Individual, Event, EventType
from genview.tree_calendar import (
    collect_upcoming_birthdays,
    collect_upcoming_anniversaries,
    merge_upcoming,
    _days_until_annual,
)


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
        birt = EventType.objects.filter(tag="BIRT").first()
        if not birt:
            birt = EventType.objects.create(tag="BIRT", name="Birth", is_visible=True)
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

    def test_confidential_birthday_hidden_for_viewer(self):
        person = Individual.objects.create(
            gedcom_tree=self.tree,
            given_name="Young",
            surname="Person",
        )
        birt = EventType.objects.filter(tag="BIRT").first()
        if not birt:
            birt = EventType.objects.create(tag="BIRT", name="Birth", is_visible=True)
        today = date.today()
        Event.objects.create(
            gedcom_tree=self.tree,
            individual=person,
            event_type=birt,
            parsed_date=date(today.year - 10, today.month, today.day),
        )
        self.client.login(username="overview_user", password="password")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        all_cal = response.context["calendar_today"] + response.context["calendar_upcoming"]
        self.assertFalse(any("Young" in i.title for i in all_cal))
