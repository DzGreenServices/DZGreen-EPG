import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import re

URL = "https://elcinema.com/en/tvguide"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Accept-Language": "ar-DZ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://elcinema.com/",
}

response = requests.get(
    URL,
    headers=headers,
    timeout=30
)

response.raise_for_status()

print("HTTP:", response.status_code)

soup = BeautifulSoup(
    response.text,
    "html.parser"
)

channels = {}

for link in soup.find_all("a", href=True):

    href = link["href"].strip()

    if not re.search(r"/tvguide/\d+/?$", href):
        continue

    name = link.get_text(
        " ",
        strip=True
    )

    if not name:
        continue

    full_url = urljoin(
        URL,
        href
    )

    channels[full_url] = name


print()
print("===================================")
print("القنوات المكتشفة:", len(channels))
print("===================================")
print()

for url, name in sorted(
    channels.items(),
    key=lambda x: x[1].lower()
):

    print(
        name,
        "|",
        url
    )

print()
print("===================================")
print("انتهى الاكتشاف")
print("===================================")
