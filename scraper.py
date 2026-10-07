import requests
from bs4 import BeautifulSoup

URL = "https://elcinema.com/en/tvguide/1132/"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ar-DZ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://elcinema.com/",
}

response = requests.get(URL, headers=headers, timeout=30)

print("HTTP:", response.status_code)

response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

dates = soup.find_all("div", class_="dates")

print("عدد التواريخ:", len(dates))

for date in dates:
    print(date.get_text(" ", strip=True))
