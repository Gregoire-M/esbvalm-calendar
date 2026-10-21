import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytz
import requests
import yaml
from bs4 import BeautifulSoup
from ics import Calendar, Event


CALENDAR_URL = "https://billetterie.esbva-lm.com/fr/calendrier"
TEAM_NAME = "ESBVA-LM"
PAGE_SIZE = 10
EUROPE_PARIS = pytz.timezone("Europe/Paris")
SERVER_ACTION_REFERENCE = re.compile(
    r'createServerReference\)\("([0-9a-f]{40,64})"'
)
LOGGER = logging.getLogger(__name__)


def create_event(home_team, away_team, match_datetime):
    """Create a calendar event for a match."""
    event = Event()
    event.name = f"{home_team} vs {away_team}"
    if match_datetime.tzinfo is None:
        match_datetime = EUROPE_PARIS.localize(match_datetime)
    else:
        match_datetime = match_datetime.astimezone(EUROPE_PARIS)
    event.begin = match_datetime
    event.end = match_datetime + timedelta(hours=2)
    if home_team == TEAM_NAME:
        event.location = "Le Palacium, Villeneuve-d'Ascq"
    return event


def _get_calendar_action_id(page_soup):
    # The page serves its event list through a build-generated Next.js action.
    for script in page_soup.find_all("script", src=True):
        script_url = urljoin(CALENDAR_URL, script["src"])
        parsed_url = urlparse(script_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.netloc != urlparse(CALENDAR_URL).netloc
            or not parsed_url.path.startswith("/_next/static/chunks/")
        ):
            continue

        response = requests.get(script_url, timeout=30)
        response.raise_for_status()
        bundle = response.text
        marker = bundle.find("minStartDatetime")
        if marker == -1 or "homeOnSaleEvents" not in bundle:
            continue

        references = list(SERVER_ACTION_REFERENCE.finditer(bundle[:marker]))
        if references:
            return references[-1].group(1)

    raise RuntimeError("Could not locate the ticketing calendar data action.")


def _parse_action_response(response_text):
    for line in response_text.splitlines():
        _, separator, payload = line.partition(":")
        if not separator:
            continue
        try:
            result = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(result, dict) and (
            "data" in result or "errors" in result or "errorsValidation" in result
        ):
            return result
    raise ValueError("Ticketing calendar returned an unreadable data response.")


def _fetch_event_page(action_id, min_start_datetime, offset):
    payload = [
        {
            "inputs": {
                "minStartDatetime": min_start_datetime,
                "allowedLocations": None,
                "allowedMonths": "$undefined",
                "competitionIds": "$undefined",
                "fuzzySearch": "",
            },
            "paginateInputs": {
                "limit": PAGE_SIZE,
                "offset": offset,
                "sortBy": [
                    {"field": "event__start_datetime", "isAscending": True}
                ],
            },
        }
    ]
    response = requests.post(
        CALENDAR_URL,
        json=payload,
        headers={
            "Accept": "text/x-component",
            "Content-Type": "text/plain;charset=UTF-8",
            "Next-Action": action_id,
            "Origin": urlparse(CALENDAR_URL).scheme
            + "://"
            + urlparse(CALENDAR_URL).netloc,
            "Referer": CALENDAR_URL,
        },
        timeout=30,
    )
    response.raise_for_status()

    result = _parse_action_response(response.text)
    errors = result.get("errors") or result.get("errorsValidation")
    if errors:
        raise RuntimeError(f"Ticketing calendar data request failed: {errors}")

    data = result.get("data")
    if not isinstance(data, dict):
        raise ValueError("Ticketing calendar response did not contain event data.")
    events = data.get("events")
    total_count = data.get("totalCount")
    if not isinstance(events, list) or not isinstance(total_count, int):
        raise ValueError("Ticketing calendar response has an invalid event list.")
    return events, total_count


def _parse_match_datetime(value):
    if not isinstance(value, str):
        raise ValueError("Ticketing calendar event has no start datetime.")
    try:
        match_datetime = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            f"Ticketing calendar event has an invalid start datetime: {value!r}"
        ) from error
    if match_datetime.tzinfo is None:
        raise ValueError(
            f"Ticketing calendar event start datetime has no timezone: {value!r}"
        )
    return match_datetime.astimezone(EUROPE_PARIS).replace(tzinfo=None)


def _to_match(event_data):
    if not isinstance(event_data, dict):
        raise ValueError("Ticketing calendar returned an invalid event.")
    if event_data.get("hasUnknownStartDatetime"):
        LOGGER.warning(
            "Skipping ticketing event %s because its start time is unknown.",
            event_data.get("id", "<unknown>"),
        )
        return None

    home_team = event_data.get("homeTeam")
    opponent = event_data.get("opponent")
    if not isinstance(home_team, dict) or not isinstance(opponent, dict):
        raise ValueError(
            f"Ticketing event {event_data.get('id', '<unknown>')} has no team data."
        )
    home_name = home_team.get("displayName")
    away_name = opponent.get("displayName")
    if not isinstance(home_name, str) or not home_name.strip():
        raise ValueError(
            f"Ticketing event {event_data.get('id', '<unknown>')} has no home team."
        )
    if not isinstance(away_name, str) or not away_name.strip():
        raise ValueError(
            f"Ticketing event {event_data.get('id', '<unknown>')} has no opponent."
        )

    if home_name.strip() != TEAM_NAME:
        return None
    return (
        TEAM_NAME,
        away_name.strip(),
        _parse_match_datetime(event_data.get("startDatetime")),
    )


def fetch_matches(now=None):
    """Fetch the upcoming ESBVA-LM home fixtures from the ticketing calendar."""
    page_response = requests.get(CALENDAR_URL, timeout=30)
    page_response.raise_for_status()
    page_soup = BeautifulSoup(page_response.text, "html.parser")
    action_id = _get_calendar_action_id(page_soup)

    if now is None:
        now = datetime.now(EUROPE_PARIS)
    elif now.tzinfo is None:
        now = EUROPE_PARIS.localize(now)
    min_start = now.astimezone(EUROPE_PARIS).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    min_start_datetime = (
        min_start.astimezone(pytz.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )

    matches = []
    offset = 0
    total_count = None
    while total_count is None or offset < total_count:
        event_page, page_total = _fetch_event_page(
            action_id, min_start_datetime, offset
        )
        if total_count is not None and page_total != total_count:
            LOGGER.info(
                "Ticketing calendar total changed from %s to %s during pagination.",
                total_count,
                page_total,
            )
        total_count = page_total
        if not event_page and offset < total_count:
            raise ValueError(
                f"Ticketing calendar returned no events at offset {offset} "
                f"of {total_count}."
            )
        for event_data in event_page:
            match = _to_match(event_data)
            if match is not None:
                matches.append(match)
        offset += len(event_page)

    return matches


def load_match_config():
    """Load manual date overrides and additional matches from YAML."""
    overrides = {}
    additional_matches = []
    overrides_file = Path("overrides.yaml")
    if overrides_file.exists():
        with overrides_file.open("r", encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
        if config:
            overrides = config.get("overrides", {})
            additional_matches = config.get("additional_matches", []) or []
    return overrides, additional_matches


def apply_overrides(matches, overrides):
    adjusted_matches = []
    for home_team, away_team, match_datetime in matches:
        match_name = f"{home_team} vs {away_team}"
        if match_name in overrides:
            override_datetime = datetime.strptime(
                overrides[match_name], "%Y-%m-%d %H:%M"
            )
            if abs((match_datetime - override_datetime).days) <= 7:
                match_datetime = override_datetime
        adjusted_matches.append((home_team, away_team, match_datetime))
    return adjusted_matches


def build_calendar():
    overrides, additional_matches = load_match_config()
    matches = apply_overrides(fetch_matches(), overrides)
    for match_config in additional_matches:
        home_team = match_config.get("home_team")
        away_team = match_config.get("away_team")
        date_str = match_config.get("date")
        if not all([home_team, away_team, date_str]):
            continue
        try:
            match_datetime = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        matches.append((home_team, away_team, match_datetime))

    calendar = Calendar()
    for home_team, away_team, match_datetime in matches:
        calendar.events.add(create_event(home_team, away_team, match_datetime))
    return calendar


def main():
    calendar = build_calendar()
    with open("esbva_lm_calendrier.ics", "w", encoding="utf-8") as calendar_file:
        calendar_file.writelines(calendar)

    from google.cloud import storage

    bucket_name = "symfonic.fr"
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob("esbva_lm_calendrier.ics")
    blob.upload_from_string(str(calendar), content_type="text/calendar")


if __name__ == "__main__":
    main()
