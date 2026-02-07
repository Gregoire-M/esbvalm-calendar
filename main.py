import requests
from bs4 import BeautifulSoup
from ics import Calendar, Event
from datetime import datetime, timedelta
from google.cloud import storage
import pytz
import yaml
from pathlib import Path
import os

def create_event(home_team, away_team, match_datetime):
    """Create a calendar event for a match"""
    event = Event()
    event.name = f"{home_team} vs {away_team}"
    event.begin = match_datetime.replace(tzinfo=pytz.timezone("Europe/Paris"))
    event.end = (match_datetime + timedelta(hours=2)).replace(tzinfo=pytz.timezone("Europe/Paris"))
    if home_team == 'ESBVA-LM':
        event.location = "Le Palacium, Villeneuve-d'Ascq"
    return event

url = "https://esbva-lm.com/equipe-pro/calendrier-resultat/"

# Disable SSL verification for local dev (set DISABLE_SSL_VERIFY=1 to activate)
# verify_ssl = os.getenv("DISABLE_SSL_VERIFY") != "1"
verify_ssl = False
response = requests.get(url, verify=verify_ssl)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")
matches = soup.select("table tbody tr")

# Load overrides and additional matches from YAML file
overrides = {}
additional_matches = []
overrides_file = Path("overrides.yaml")
if overrides_file.exists():
    with open(overrides_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        if config:
            if "overrides" in config:
                overrides = config["overrides"]
            if "additional_matches" in config and config["additional_matches"]:
                additional_matches = config["additional_matches"]

cal = Calendar()

for match in matches:
    date_str = match.select_one("td:nth-child(1)").text.strip()
    home_team = match.select_one("td:nth-child(2)").text.strip()
    away_team = match.select_one("td:nth-child(4)").text.strip()

    try:
        match_datetime = datetime.strptime(date_str[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        continue

    # Check for date override (only if original date is within 7 days of override)
    match_name = f"{home_team} vs {away_team}"
    if match_name in overrides:
        override_date_str = overrides[match_name]
        override_datetime = datetime.strptime(override_date_str, "%Y-%m-%d %H:%M")
        days_diff = abs((match_datetime - override_datetime).days)
        
        if days_diff <= 7:
            match_datetime = override_datetime
    
    event = create_event(home_team, away_team, match_datetime)
    cal.events.add(event)

# Add additional matches from config
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
    
    event = create_event(home_team, away_team, match_datetime)
    cal.events.add(event)

with open("esbva_lm_calendrier.ics", "w", encoding="utf-8") as f:
    f.writelines(cal)

client = storage.Client()
bucket = client.bucket("symfonic.fr")
blob = bucket.blob("esbva_lm_calendrier.ics")
blob.upload_from_string(str(cal), content_type="text/calendar")
