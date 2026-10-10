
import csv
import re
import sys
import time
import requests
import xml.etree.ElementTree as ET

from datetime import datetime, date, time as dt_time, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://elcinema.com"
GUIDE_URL = "https://elcinema.com/en/tvguide/"

OUTPUT_DIR = Path("docs")
XML_FILE = OUTPUT_DIR / "ElCinema-EPG.xml"
CSV_FILE = OUTPUT_DIR / "ElCinema-Channel-Mapping.csv"

MAX_WORKERS = 12
REQUEST_TIMEOUT = 12

EGYPT_TZ = ZoneInfo("Africa/Cairo")
ALGERIA_TZ = ZoneInfo("Africa/Algiers")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

CHANNEL_URL_RE = re.compile(
    r"/(?:en/)?tvguide/(\d+)/?"
)

TIME_RE = re.compile(
    r"\b(\d{1,2}:\d{2}\s*(?:AM|PM))\b",
    re.IGNORECASE,
)

DURATION_RE = re.compile(
    r"\[(\d+)\s*minutes?\]",
    re.IGNORECASE,
)

DATE_RE = re.compile(
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?"
    r"\s*,?\s*(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)",
    re.IGNORECASE,
)


# ============================================================
# HTTP
# ============================================================

def fetch_page(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        response.encoding = response.apparent_encoding

        return {
            "url": url,
            "final_url": response.url,
            "status": response.status_code,
            "html": response.text if response.ok else "",
            "error": None,
        }

    except requests.RequestException as exc:
        return {
            "url": url,
            "final_url": url,
            "status": 0,
            "html": "",
            "error": str(exc),
        }


# ============================================================
# CHANNEL DISCOVERY
# ============================================================

def discover_channels():
    print("[ElCinema EPG] Reading main TV guide...", flush=True)

    result = fetch_page(GUIDE_URL)

    if not result["html"]:
        raise RuntimeError(
            "Could not download the main TV guide. "
            f"HTTP status: {result['status']}. "
            f"Error: {result['error']}"
        )

    soup = BeautifulSoup(result["html"], "html.parser")
    channels = {}

    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        absolute_url = urljoin(BASE_URL, href)
        path = urlparse(absolute_url).path

        match = CHANNEL_URL_RE.fullmatch(path)

        if not match:
            continue

        channel_number = match.group(1)

        # Normalize every channel URL.
        channel_url = (
            f"{BASE_URL}/en/tvguide/{channel_number}/"
        )

        channels[channel_number] = channel_url

    print(
        f"[ElCinema EPG] HTTP {result['status']} | "
        f"HTML size: {len(result['html'])} characters",
        flush=True,
    )

    print(
        f"[ElCinema EPG] Channels discovered: {len(channels)}",
        flush=True,
    )

    if not channels:
        raise RuntimeError(
            "No channel links were found on the main guide page. "
            "The website HTML may have changed or blocked the request."
        )

    return channels


# ============================================================
# DATE PARSING
# ============================================================

def parse_guide_date(text):
    match = DATE_RE.search(text or "")

    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(2).title()

    today = datetime.now(EGYPT_TZ).date()
    candidates = []

    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidate = datetime.strptime(
                f"{day} {month_name} {year}",
                "%d %B %Y",
            ).date()

            candidates.append(candidate)

        except ValueError:
            continue

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda candidate: abs((candidate - today).days),
    )


# ============================================================
# PROGRAM CARD PARSING
# ============================================================

def get_card_title(card):
    link = card.select_one('a[href*="/work/"]')

    if not link:
        return None, None

    title = link.get_text(" ", strip=True)
    url = urljoin(BASE_URL, link.get("href", ""))

    if not title:
        return None, None

    return title, url


def get_program_time(card):
    text = card.get_text(" ", strip=True)
    match = TIME_RE.search(text)

    if not match:
        # Some page versions may place the time in a separate element.
        for selector in (
            ".time",
            ".date",
            "time",
        ):
            element = card.select_one(selector)

            if element:
                match = TIME_RE.search(
                    element.get_text(" ", strip=True)
                )

                if match:
                    break

    if not match:
        return None

    try:
        return datetime.strptime(
            re.sub(r"\s+", " ", match.group(1).strip()).upper(),
            "%I:%M %p",
        ).time()

    except ValueError:
        return None


def get_duration(card):
    text = card.get_text(" ", strip=True)
    match = DURATION_RE.search(text)

    if match:
        return int(match.group(1))

    return None


def get_description(card):
    # The supplied HTML uses a hidden span for the full description.
    hidden = card.select_one("span.hide")

    if hidden:
        description = hidden.get_text(" ", strip=True)

        if description:
            return description

    read_more = card.select_one("a#read-more")

    if read_more:
        parent = read_more.find_parent("li")

        if parent:
            description = parent.get_text(" ", strip=True)
            description = re.sub(
                r"\bread more\b",
                "",
                description,
                flags=re.IGNORECASE,
            ).strip()

            if description:
                return description

    return ""


def get_rating(card):
    element = card.select_one(".stars-rating-lg")

    if not element:
        return ""

    rating_text = (
        element.get("title")
        or element.get_text(" ", strip=True)
    )

    match = re.search(r"(\d+(?:\.\d+)?)", rating_text)

    return match.group(1) if match else ""


def get_actors(card):
    actors = []

    for link in card.select('a[href*="/person/"]'):
        name = link.get_text(" ", strip=True)

        if name and name not in actors:
            actors.append(name)

    return ", ".join(actors)


def get_category(card):
    for item in card.find_all("li"):
        text = item.get_text(" ", strip=True)

        if re.search(
            r"\b(?:Movie|Series|TV Show|Program|Episode)\b",
            text,
            re.IGNORECASE,
        ):
            return text

    return ""


def get_program_image(card):
    image = card.select_one("img[src]")

    if not image:
        return ""

    return urljoin(BASE_URL, image.get("src", ""))


# ============================================================
# CHANNEL PAGE PARSING
# ============================================================

def parse_channel_page(channel_number, url, result):
    channel_id = f"elcinema.{channel_number}"

    page_info = {
        "id": channel_id,
        "name": f"ElCinema {channel_number}",
        "url": url,
        "program_count": 0,
        "status": result["status"],
        "html_size": len(result["html"]),
        "error": result["error"] or "",
    }

    if not result["html"]:
        return page_info, []

    soup = BeautifulSoup(result["html"], "html.parser")

    heading = soup.select_one("h1")

    if heading:
        heading_text = heading.get_text(" ", strip=True)

        if heading_text:
            page_info["name"] = heading_text

    elif soup.title:
        title_text = soup.title.get_text(" ", strip=True)

        if title_text:
            page_info["name"] = title_text

    # These selectors come from the HTML supplied by the user.
    cards = soup.select("div.boxed-category-1.padded-half")
    date_elements = soup.select("div.dates")

    # Keep date headings and program cards in their document order.
    ordered_elements = soup.select(
        "div.dates, div.boxed-category-1.padded-half"
    )

    current_date = None
    programmes = []

    for element in ordered_elements:

        if "dates" in element.get("class", []):
            parsed_date = parse_guide_date(
                element.get_text(" ", strip=True)
            )

            if parsed_date:
                current_date = parsed_date

            continue

        title, program_url = get_card_title(element)

        if not title:
            continue

        program_time = get_program_time(element)

        if not program_time or not current_date:
            continue

        duration = get_duration(element)

        if not duration or duration <= 0:
            continue

        start_local = datetime.combine(
            current_date,
            program_time,
            tzinfo=EGYPT_TZ,
        )

        stop_local = start_local + timedelta(minutes=duration)

        # Convert actual local datetimes, not a fixed time difference.
        start_algeria = start_local.astimezone(ALGERIA_TZ)
        stop_algeria = stop_local.astimezone(ALGERIA_TZ)

        programmes.append({
            "channel": channel_id,
            "title": title,
            "start": start_algeria,
            "stop": stop_algeria,
            "description": get_description(element),
            "category": get_category(element),
            "rating": get_rating(element),
            "actors": get_actors(element),
            "icon": get_program_image(element),
            "url": program_url,
        })

    page_info["program_count"] = len(programmes)

    if not programmes:
        print(
            f"[ElCinema EPG] No programmes: {url} | "
            f"HTTP {result['status']} | "
            f"HTML {len(result['html'])} chars | "
            f"dates={len(date_elements)} | cards={len(cards)}",
            flush=True,
        )

        if result["final_url"] != url:
            print(
                f"[ElCinema EPG] Redirected to: "
                f"{result['final_url']}",
                flush=True,
            )

    else:
        print(
            f"[ElCinema EPG] {page_info['name']}: "
            f"{len(programmes)} programmes",
            flush=True,
        )

    return page_info, programmes


# ============================================================
# XMLTV OUTPUT
# ============================================================

def xmltv_timestamp(value):
    return value.strftime("%Y%m%d%H%M%S %z")


def add_text_element(parent, tag, value):
    if value is None:
        return

    value = str(value).strip()

    if value:
        ET.SubElement(parent, tag).text = value


def write_xml(channels, programmes):
    tv = ET.Element(
        "tv",
        {
            "source-info-name": "ElCinema",
            "source-info-url": GUIDE_URL,
            "generator-info-name": "DZGreen ElCinema EPG",
        },
    )

    for channel in channels:
        element = ET.SubElement(
            tv,
            "channel",
            {"id": channel["id"]},
        )

        ET.SubElement(
            element,
            "display-name",
            {"lang": "en"},
        ).text = channel["name"]

    for programme in sorted(
        programmes,
        key=lambda item: item["start"],
    ):
        element = ET.SubElement(
            tv,
            "programme",
            {
                "start": xmltv_timestamp(programme["start"]),
                "stop": xmltv_timestamp(programme["stop"]),
                "channel": programme["channel"],
            },
        )

        add_text_element(
            element,
            "title",
            programme["title"],
        )

        add_text_element(
            element,
            "desc",
            programme["description"],
        )

        add_text_element(
            element,
            "category",
            programme["category"],
        )

        if programme["rating"]:
            rating = ET.SubElement(element, "rating")

            ET.SubElement(
                rating,
                "value",
            ).text = programme["rating"]

        if programme["actors"]:
            credits = ET.SubElement(element, "credits")

            for actor in programme["actors"].split(", "):
                add_text_element(credits, "actor", actor)

        if programme["icon"]:
            ET.SubElement(
                element,
                "icon",
                {"src": programme["icon"]},
            )

        if programme["url"]:
            add_text_element(
                element,
                "url",
                programme["url"],
            )

    tree = ET.ElementTree(tv)

    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass

    tree.write(
        XML_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )


# ============================================================
# CSV OUTPUT
# ============================================================

def write_csv(channels):
    with CSV_FILE.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as csv_file:

        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "tvg-id",
                "channel_name",
                "source_url",
                "program_count",
                "http_status",
            ],
        )

        writer.writeheader()

        for channel in channels:
            writer.writerow({
                "tvg-id": channel["id"],
                "channel_name": channel["name"],
                "source_url": channel["url"],
                "program_count": channel["program_count"],
                "http_status": channel["status"],
            })


# ============================================================
# MAIN
# ============================================================

def main():
    started = time.monotonic()

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    channel_map = discover_channels()
    channel_items = list(channel_map.items())

    all_channels = []
    all_programmes = []

    print(
        f"[ElCinema EPG] Fetching {len(channel_items)} pages "
        f"with {MAX_WORKERS} workers...",
        flush=True,
    )

    results = {}

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        future_to_channel = {
            executor.submit(fetch_page, url): (
                channel_number,
                url,
            )
            for channel_number, url in channel_items
        }

        completed = 0
        total = len(future_to_channel)

        for future in as_completed(future_to_channel):
            channel_number, url = future_to_channel[future]
            result = future.result()

            results[channel_number] = (url, result)

            completed += 1

            print(
                f"[ElCinema EPG] Downloaded "
                f"{completed}/{total}",
                flush=True,
            )

    for channel_number, url in channel_items:
        actual_url, result = results[channel_number]

        channel_info, programmes = parse_channel_page(
            channel_number,
            actual_url,
            result,
        )

        all_channels.append(channel_info)
        all_programmes.extend(programmes)

    if not all_programmes:
        print(
            "[ElCinema EPG] ERROR: No programmes were extracted.",
            flush=True,
        )

        print(
            "[ElCinema EPG] Check the HTTP statuses, HTML sizes, "
            "date counts and card counts printed above.",
            flush=True,
        )

        sys.exit(1)

    write_xml(all_channels, all_programmes)
    write_csv(all_channels)

    elapsed = time.monotonic() - started

    print("[ElCinema EPG] Completed successfully.", flush=True)
    print(f"[ElCinema EPG] Channels: {len(all_channels)}", flush=True)
    print(f"[ElCinema EPG] Programmes: {len(all_programmes)}", flush=True)
    print(f"[ElCinema EPG] XML: {XML_FILE}", flush=True)
    print(f"[ElCinema EPG] CSV: {CSV_FILE}", flush=True)
    print(f"[ElCinema EPG] Runtime: {elapsed:.1f} seconds", flush=True)


if __name__ == "__main__":
    main()
