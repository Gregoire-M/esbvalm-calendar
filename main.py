import requests
from bs4 import BeautifulSoup
from ics import Calendar, Event
from datetime import datetime, timedelta
import pytz

url = "https://esbva-lm.com/equipe-pro/calendrier-resultat/"

response = requests.get(url)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")
matches = soup.select("table tbody tr")

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
    event.begin = match_datetime.replace(tzinfo=pytz.timezone("Europe/Paris"))
    event.end = (match_datetime + timedelta(hours=2)).replace(tzinfo=pytz.timezone("Europe/Paris"))
    if home_team == 'ESBVA-LM':
        event.location = "Le Palacium, Villeneuve-d'Ascq"

    cal.events.add(event)

with open("esbva_lm_calendrier.ics", "w", encoding="utf-8") as f:
    f.writelines(cal)
