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
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

programs = soup.select("div.boxed-category-0.padded-half, div.boxed-category-1.padded-half")

print("عدد البرامج:", len(programs))
print()

for program in programs:

    title = ""

    title_link = program.select_one("a[href^='/work/']")
    if title_link:
        title = title_link.get_text(" ", strip=True)
    else:
        title_item = program.select_one("ul.unstyled.no-margin li")
        if title_item:
            title = title_item.get_text(" ", strip=True)

    time_item = program.select_one("ul.unstyled.text-center li:first-child")

    if not time_item:
        items = program.select("ul.unstyled.no-margin li")
        if len(items) >= 2:
            time_item = items[1]

    time = time_item.get_text(" ", strip=True) if time_item else ""

    duration_item = program.select_one("span.subheader")
    duration = duration_item.get_text(" ", strip=True) if duration_item else ""

    print(f"{time} | {title} | {duration}")
