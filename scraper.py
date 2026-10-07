import requests
from bs4 import BeautifulSoup

URL = "https://elcinema.com/en/tvguide/1132/"

response = requests.get(URL, timeout=30)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

dates = soup.find_all("div", class_="dates")

print("عدد التواريخ:", len(dates))

for date in dates:
    print(date.get_text(" ", strip=True))
