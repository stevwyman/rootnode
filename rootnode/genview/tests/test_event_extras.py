# genview/tests/test_event_extras.py
from datetime import date
from django.test import SimpleTestCase
from genview.templatetags.event_extras import display_event_date

class DisplayEventDateFilterTest(SimpleTestCase):
    def test_event_with_parsed_date(self):
        class Dummy:
            parsed_date = date(2020, 5, 17)
            raw_date = "ABT 2020"
        assert display_event_date(Dummy()) == "17.05.2020"

    def test_event_without_parsed_date(self):
        class Dummy:
            parsed_date = None
            raw_date = "ABT 1900"
        assert display_event_date(Dummy()) == "ABT 1900"

    def test_raw_string_input(self):
        assert display_event_date("12 JAN 1885") == "12 JAN 1885"

    def test_none_input(self):
        assert display_event_date(None) == "Unbekannt"