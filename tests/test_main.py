import unittest
from datetime import datetime
from unittest.mock import Mock, call, patch

from bs4 import BeautifulSoup

import main


class CalendarSourceTests(unittest.TestCase):
    def test_finds_calendar_action_in_matching_bundle(self):
        action_id = "a" * 42
        page = BeautifulSoup(
            '<script src="/_next/static/chunks/calendar.js"></script>'
            '<script src="https://third-party.example/script.js"></script>',
            "html.parser",
        )
        response = Mock()
        response.text = (
            f'createServerReference)("{action_id}");'
            "minStartDatetime homeOnSaleEvents"
        )

        with patch("main.requests.get", return_value=response) as get:
            self.assertEqual(main._get_calendar_action_id(page), action_id)

        get.assert_called_once_with(
            "https://billetterie.esbva-lm.com/_next/static/chunks/calendar.js",
            timeout=30,
        )
        response.raise_for_status.assert_called_once_with()

    def test_parses_server_action_response(self):
        response_text = (
            '0:{"a":"$@1","f":"","q":"","i":false}\n'
            '1:{"data":{"events":[],"totalCount":0},"errors":null,'
            '"errorsValidation":null}'
        )

        self.assertEqual(
            main._parse_action_response(response_text),
            {
                "data": {"events": [], "totalCount": 0},
                "errors": None,
                "errorsValidation": None,
            },
        )

    def test_maps_home_event_to_local_time(self):
        event_data = {
            "id": 4,
            "startDatetime": "2026-11-01T14:30:00+00:00",
            "homeTeam": {"displayName": "ESBVA-LM"},
            "opponent": {"displayName": "Angers "},
        }

        self.assertEqual(
            main._to_match(event_data),
            ("ESBVA-LM", "Angers", datetime(2026, 11, 1, 15, 30)),
        )

    def test_create_event_uses_paris_timezone(self):
        event = main.create_event(
            "ESBVA-LM", "Angers", datetime(2026, 11, 1, 15, 30)
        )

        self.assertEqual(event.begin.datetime.utcoffset().total_seconds(), 3600)
        self.assertEqual(event.location, "Le Palacium, Villeneuve-d'Ascq")

    def test_ignores_away_event(self):
        event_data = {
            "id": 9,
            "startDatetime": "2026-11-01T14:30:00+00:00",
            "homeTeam": {"displayName": "Angers"},
            "opponent": {"displayName": "ESBVA-LM"},
        }

        self.assertIsNone(main._to_match(event_data))

    def test_skips_event_with_unknown_start_time(self):
        with self.assertLogs(main.LOGGER, level="WARNING"):
            self.assertIsNone(
                main._to_match(
                    {
                        "id": 12,
                        "hasUnknownStartDatetime": True,
                    }
                )
            )

    def test_fetches_all_pages_and_only_returns_home_matches(self):
        page_response = Mock()
        page_response.text = "<html></html>"
        first_page = [
            {
                "id": index,
                "startDatetime": "2026-11-01T14:30:00+00:00",
                "homeTeam": {"displayName": "ESBVA-LM"},
                "opponent": {"displayName": f"Opponent {index}"},
            }
            for index in range(main.PAGE_SIZE)
        ]
        second_page = [
            {
                "id": 11,
                "startDatetime": "2026-11-22T14:30:00+00:00",
                "homeTeam": {"displayName": "ESBVA-LM"},
                "opponent": {"displayName": "Final opponent"},
            },
            {
                "id": 12,
                "startDatetime": "2026-11-23T14:30:00+00:00",
                "homeTeam": {"displayName": "Away team"},
                "opponent": {"displayName": "ESBVA-LM"},
            },
        ]
        now = main.EUROPE_PARIS.localize(datetime(2026, 10, 21, 12))

        with (
            patch("main.requests.get", return_value=page_response),
            patch("main._get_calendar_action_id", return_value="action-id"),
            patch(
                "main._fetch_event_page",
                side_effect=[(first_page, 11), (second_page, 11)],
            ) as fetch_page,
        ):
            matches = main.fetch_matches(now=now)

        self.assertEqual(len(matches), 11)
        self.assertEqual(matches[-1][1], "Final opponent")
        self.assertEqual(
            fetch_page.call_args_list,
            [
                call("action-id", "2026-10-20T22:00:00.000Z", 0),
                call("action-id", "2026-10-20T22:00:00.000Z", 10),
            ],
        )

    def test_rejects_event_without_start_datetime(self):
        with self.assertRaisesRegex(ValueError, "no start datetime"):
            main._to_match(
                {
                    "id": 13,
                    "homeTeam": {"displayName": "ESBVA-LM"},
                    "opponent": {"displayName": "Opponent"},
                }
            )

    def test_build_calendar_keeps_overrides_and_manual_matches(self):
        matches = [
            ("ESBVA-LM", "Angers", datetime(2026, 11, 1, 15, 30)),
        ]
        config = (
            {"ESBVA-LM vs Angers": "2026-11-02 18:00"},
            [
                {
                    "home_team": "ESBVA-LM",
                    "away_team": "Manual opponent",
                    "date": "2026-11-03 20:00",
                }
            ],
        )

        with (
            patch("main.fetch_matches", return_value=matches),
            patch("main.load_match_config", return_value=config),
        ):
            calendar = main.build_calendar()

        events = {event.name: event for event in calendar.events}
        self.assertEqual(
            set(events),
            {"ESBVA-LM vs Angers", "ESBVA-LM vs Manual opponent"},
        )
        self.assertEqual(events["ESBVA-LM vs Angers"].begin.datetime.hour, 18)


if __name__ == "__main__":
    unittest.main()
