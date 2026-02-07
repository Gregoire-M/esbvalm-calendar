import requests
from bs4 import BeautifulSoup
from ics import Calendar, Event
from datetime import datetime, timedelta
from google.cloud import storage
import pytz
import yaml
from pathlib import Path
import os

url = "https://esbva-lm.com/equipe-pro/calendrier-resultat/"

# Disable SSL verification for local dev (set DISABLE_SSL_VERIFY=1 to activate)
# verify_ssl = os.getenv("DISABLE_SSL_VERIFY") != "1"
verify_ssl = False
response = requests.get(url, verify=verify_ssl)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")
matches = soup.select("table tbody tr")

# Load overrides from YAML file
overrides = {}
overrides_file = Path("overrides.yaml")
if overrides_file.exists():
    with open(overrides_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
        if config and "overrides" in config:
            overrides = config["overrides"]

cal = Calendar()

for match in matches:
    date_str = match.select_one("td:nth-child(1)").text.strip()
    home_team = match.select_one("td:nth-child(2)").text.strip()
    away_team = match.select_one("td:nth-child(4)").text.strip()

    try:
        match_datetime = datetime.strptime(date_str[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        continue

    event = Event()
    event.name = f"{home_team} vs {away_team}"
    
    # Check for date override (only if original date is within 7 days of override)
    if event.name in overrides:
        override_date_str = overrides[event.name]
        override_datetime = datetime.strptime(override_date_str, "%Y-%m-%d %H:%M")
        days_diff = abs((match_datetime - override_datetime).days)
        
        if days_diff <= 7:
            match_datetime = override_datetime
    
    event.begin = match_datetime.replace(tzinfo=pytz.timezone("Europe/Paris"))
    event.end = (match_datetime + timedelta(hours=2)).replace(tzinfo=pytz.timezone("Europe/Paris"))
    if home_team == 'ESBVA-LM':
        event.location = "Le Palacium, Villeneuve-d'Ascq"

    cal.events.add(event)

with open("esbva_lm_calendrier.ics", "w", encoding="utf-8") as f:
    f.writelines(cal)

client = storage.Client()
bucket = client.bucket("symfonic.fr")
blob = bucket.blob("esbva_lm_calendrier.ics")
blob.upload_from_string(str(cal), content_type="text/calendar")
