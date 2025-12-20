import requests
from datetime import datetime

API_KEY = "e8a7347e89a344f9af7755ffb78cb559"
competition = "EPL"  # tai esim. "1" (Champions League), "MLS", "3", jne.
date = datetime.today().strftime("%Y-%m-%d")

url = f"https://api.sportsdata.io/v4/soccer/odds/json/GameOddsByDate/{competition}/{date}"
headers = {"Ocp-Apim-Subscription-Key": API_KEY}

response = requests.get(url, headers=headers)

if response.status_code == 200:
    for match in response.json():
        print(match["HomeTeam"], "vs", match["AwayTeam"])
else:
    print("❌ Virhe:", response.status_code, response.text)
