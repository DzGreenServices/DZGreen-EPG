
import csv
import re
import sys
import time
import requests
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta
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
REQUEST_TIMEOUT = 15

EGYPT_TZ = ZoneInfo("Africa/Cairo")
ALGERIA_TZ = ZoneInfo("Africa/Algiers")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
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
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|"
    r"Saturday|Sunday)?\s*,?\s*"
    r"(\d{1,2})\s+"
    r"(January|February|March|April|May|June|July|"
    r"August|September|October|November|December)",
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
# DISCOVER CHANNELS
# ============================================================

def discover_channels():
    print(
        "[ElCinema EPG] Reading main TV guide...",
        flush=True,
    )

    result = fetch_page(GUIDE_URL)

    if not result["html"]:
        raise RuntimeError(
            "Cannot download the main TV guide. "
            f"HTTP={result['status']}, "
            f"error={result['error']}"
        )

    soup = BeautifulSoup(result["html"], "html.parser")
    channels = {}

    for link in soup.find_all("a", href=True):
        absolute_url = urljoin(BASE_URL, link["href"])
        path = urlparse(absolute_url).path

        match = CHANNEL_URL_RE.fullmatch(path)

        if not match:
            continue

        channel_number = match.group(1)

        channels[channel_number] = (
            f"{BASE_URL}/en/tvguide/{channel_number}/"
        )

    print(
        f"[ElCinema EPG] Main page HTTP: {result['status']}",
        flush=True,
    )

    print(
        f"[ElCinema EPG] Main page HTML: "
        f"{len(result['html'])} characters",
        flush=True,
    )

    print(
        f"[ElCinema EPG] Channels found: {len(channels)}",
        flush=True,
    )

    if not channels:
        raise RuntimeError(
            "No channel links found on the main guide page."
        )

    return channels


# ============================================================
# PARSE DATE
# ============================================================

def parse_guide_date(text):
    match = DATE_RE.search(text or "")

    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(2).title()

    today = datetime.now(EGYPT_TZ).date()
    candidates = []

    for year in (
        today.year - 1,
        today.year,
        today.year + 1,
    ):
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
        key=lambda candidate: abs(
            (candidate - today).days
        ),
    )


# ============================================================
# FIND PROGRAM CARD
# ============================================================

def find_program_card(link):
    """
    Find the closest ancestor containing both a time
    and a duration.

    This avoids relying on one fixed CSS class.
    """

    parent = link

    for _ in range(10):
        parent = parent.parent

        if parent is None:
            break

        text = parent.get_text(" ", strip=True)

        has_time = TIME_RE.search(text) is not None
        has_duration = DURATION_RE.search(text) is not None

        if has_time and has_duration:
            return parent

    return None


# ============================================================
# PROGRAM DETAILS
# ============================================================

def get_card_title(card):
    link = card.select_one('a[href*="/work/"]')

    if not link:
        return None, None

    title = link.get_text(" ", strip=True)
    program_url = urljoin(
        BASE_URL,
        link.get("href", ""),
    )

    if not title:
        return None, None

    return title, program_url


def get_program_time(card):
    text = card.get_text(" ", strip=True)
    match = TIME_RE.search(text)

    if not match:
        return None

    value = re.sub(
        r"\s+",
        " ",
        match.group(1).strip(),
    ).upper()

    try:
        return datetime.strptime(
            value,
            "%I:%M %p",
        ).time()

    except ValueError:
        return None


def get_duration(card):
    text = card.get_text(" ", strip=True)
    match = DURATION_RE.search(text)

    if not match:
        return None

    return int(match.group(1))


def get_description(card):
    hidden = card.select_one("span.hide")

    if hidden:
        description = hidden.get_text(" ", strip=True)

        if description:
            return description

    read_more = card.select_one("a#read-more")

    if read_more:
        parent = read_more.find_parent("li")

        if parent:
            description = parent.get_text(
                " ",
                strip=True,
            )

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

    match = re.search(
        r"(\d+(?:\.\d+)?)",
        rating_text,
    )

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
            r"\b(Movie|Series|TV Show|Program|Episode)\b",
            text,
            re.IGNORECASE,
        ):
            return text

    return ""


def get_program_image(card):
    image = card.select_one("img[src]")

    if not image:
        return ""

    return urljoin(
        BASE_URL,
        image.get("src", ""),
    )


# ============================================================
# PARSE CHANNEL PAGE
# ============================================================

def parse_channel_page(channel_number, url, result):
    channel_id = f"elcinema.{channel_number}"

    channel_info = {
        "id": channel_id,
        "name": f"ElCinema {channel_number}",
        "url": url,
        "program_count": 0,
        "status": result["status"],
        "html_size": len(result["html"]),
    }

    if not result["html"]:
        print(
            f"[ElCinema EPG] Download failed: {url} "
            f"| HTTP={result['status']} "
            f"| {result['error']}",
            flush=True,
        )

        return channel_info, []

    soup = BeautifulSoup(
        result["html"],
        "html.parser",
    )

    heading = soup.select_one("h1")

    if heading:
        heading_text = heading.get_text(
            " ",
            strip=True,
        )

        if heading_text:
            channel_info["name"] = heading_text

    elif soup.title:
        title_text = soup.title.get_text(
            " ",
            strip=True,
        )

        if title_text:
            channel_info["name"] = title_text

    date_elements = soup.select("div.dates")
    work_links = soup.select('a[href*="/work/"]')

    print(
        f"[ElCinema EPG] Diagnosing {channel_number}: "
        f"HTTP={result['status']}, "
        f"HTML={len(result['html'])}, "
        f"dates={len(date_elements)}, "
        f"work_links={len(work_links)}",
        flush=True,
    )

    ordered_elements = soup.select(
        "div.dates, a[href*='/work/']"
    )

    current_date = None
    programmes = []
    processed_cards = set()

    for element in ordered_elements:

        # Date heading
        if (
            element.name == "div"
            and "dates" in element.get("class", [])
        ):
            parsed_date = parse_guide_date(
                element.get_text(" ", strip=True)
            )

            if parsed_date:
                current_date = parsed_date

            continue

        # Work link
        if element.name != "a":
            continue

        if not current_date:
            continue

        card = find_program_card(element)

        if card is None:
            continue

        card_key = id(card)

        if card_key in processed_cards:
            continue

        title, program_url = get_card_title(card)

        if not title:
            continue

        program_time = get_program_time(card)
        duration = get_duration(card)

        if not program_time:
            continue

        if not duration or duration <= 0:
            continue

        processed_cards.add(card_key)

        # ElCinema uses Egypt local time.
        start_egypt = datetime.combine(
            current_date,
            program_time,
            tzinfo=EGYPT_TZ,
        )

        stop_egypt = start_egypt + timedelta(
            minutes=duration,
        )

        # Convert both timestamps to Algeria local time.
        start_algeria = start_egypt.astimezone(
            ALGERIA_TZ,
        )

        stop_algeria = stop_egypt.astimezone(
            ALGERIA_TZ,
        )

        programmes.append({
            "channel": channel_id,
            "title": title,
            "start": start_algeria,
            "stop": stop_algeria,
            "description": get_description(card),
            "category": get_category(card),
            "rating": get_rating(card),
            "actors": get_actors(card),
            "icon": get_program_image(card),
            "url": program_url,
        })

    channel_info["program_count"] = len(programmes)

    if not programmes:
        print(
            f"[ElCinema EPG] No programmes: {url} "
            f"| dates={len(date_elements)} "
            f"| work_links={len(work_links)}",
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
            f"[ElCinema EPG] {channel_info['name']}: "
            f"{len(programmes)} programmes",
            flush=True,
        )

    return channel_info, programmes


# ============================================================
# XMLTV
# ============================================================

def xmltv_timestamp(value):
    return value.strftime("%Y%m%d%H%M%S %z")


def add_text_element(parent, tag, value):
    if value is None:
        return

    value = str(value).strip()

    if value:
        ET.SubElement(
            parent,
            tag,
        ).text = value


def write_xml(channels, programmes):
    tv = ET.Element(
        "tv",
        {
            "source-info-name": "ElCinema",
            "source-info-url": GUIDE_URL,
            "generator-info-name": "DZGreen ElCinema EPG",
        },
    )

    # Channels
    for channel in channels:
        channel_element = ET.SubElement(
            tv,
            "channel",
            {"id": channel["id"]},
        )

        ET.SubElement(
            channel_element,
            "display-name",
            {"lang": "en"},
        ).text = channel["name"]

    # Programmes
    for programme in sorted(
        programmes,
        key=lambda item: item["start"],
    ):
        element = ET.SubElement(
            tv,
            "programme",
            {
                "start": xmltv_timestamp(
                    programme["start"]
                ),
                "stop": xmltv_timestamp(
                    programme["stop"]
                ),
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
            rating = ET.SubElement(
                element,
                "rating",
            )

            add_text_element(
                rating,
                "value",
                programme["rating"],
            )

        if programme["actors"]:
            credits = ET.SubElement(
                element,
                "credits",
            )

            for actor in programme["actors"].split(", "):
                add_text_element(
                    credits,
                    "actor",
                    actor,
                )

        if programme["icon"]:
            ET.SubElement(
                element,
                "icon",
                {"src": programme["icon"]},
            )

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
# CSV
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

    print(
        f"[ElCinema EPG] Downloading "
        f"{len(channel_items)} channel pages "
        f"using {MAX_WORKERS} workers...",
        flush=True,
    )

    results = {}

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS,
    ) as executor:

        future_to_channel = {
            executor.submit(
                fetch_page,
                url,
            ): (channel_number, url)
            for channel_number, url in channel_items
        }

        total = len(future_to_channel)
        completed = 0

        for future in as_completed(future_to_channel):
            channel_number, url = future_to_channel[future]

            try:
                result = future.result()

            except Exception as exc:
                result = {
                    "url": url,
                    "final_url": url,
                    "status": 0,
                    "html": "",
                    "error": str(exc),
                }

            results[channel_number] = (
                url,
                result,
            )

            completed += 1

            print(
                f"[ElCinema EPG] Downloaded "
                f"{completed}/{total}",
                flush=True,
            )

    all_channels = []
    all_programmes = []

    # Parse in channel order for predictable logs and CSV.
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
            "[ElCinema EPG] ERROR: No programmes extracted.",
            flush=True,
        )

        print(
            "[ElCinema EPG] Check the diagnostic counts "
            "for dates and work_links above.",
            flush=True,
        )

        sys.exit(1)

    write_xml(
        all_channels,
        all_programmes,
    )

    write_csv(all_channels)

    elapsed = time.monotonic() - started

    print(
        "[ElCinema EPG] Completed successfully.",
        flush=True,
    )

    print(
        f"[ElCinema EPG] Channels: {len(all_channels)}",
        flush=True,
    )

    print(
        f"[ElCinema EPG] Programmes: {len(all_programmes)}",
        flush=True,
    )

    print(
        f"[ElCinema EPG] XML: {XML_FILE}",
        flush=True,
    )

    print(
        f"[ElCinema EPG] CSV: {CSV_FILE}",
        flush=True,
    )

    print(
        f"[ElCinema EPG] Runtime: {elapsed:.1f} seconds",
        flush=True,
    )


if __name__ == "__main__":
    main()
