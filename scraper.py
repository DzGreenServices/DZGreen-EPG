
import csv
import re
import time
import logging
from datetime import datetime, date, time as dt_time, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import local
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://elcinema.com"
GUIDE_URL = f"{BASE_URL}/en/tvguide/"

OUTPUT_DIR = Path(__file__).resolve().parent / "docs"
XML_FILE = OUTPUT_DIR / "ElCinema-EPG.xml"
CSV_FILE = OUTPUT_DIR / "ElCinema-Channel-Mapping.csv"

MAX_WORKERS = 3
TIMEOUT = 20

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

CAIRO = ZoneInfo("Africa/Cairo")
ALGIERS = ZoneInfo("Africa/Algiers")

TIME_RE = re.compile(
    r"(\d{1,2}:\d{2})\s*"
    r"(AM|PM|صباحًا|صباحاً|مساءً|مساءاً|صباحا|مساء)?",
    re.IGNORECASE,
)

DURATION_RE = re.compile(
    r"\[\s*(\d+)\s*(?:minutes?|دقيقة|دقائق)\s*\]",
    re.IGNORECASE,
)

WORK_RE = re.compile(r"/(?:en/)?work/(\d+)/?")
CHANNEL_RE = re.compile(r"/(?:en/)?tvguide/(\d+)/?")
DATE_RE = re.compile(
    r"(\d{1,2})\s+([A-Za-z]+)",
    re.IGNORECASE,
)

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

log = logging.getLogger("ElCinemaEPG")
_thread_local = local()


# ============================================================
# HTTP
# ============================================================

def get_session():
    session = getattr(_thread_local, "session", None)

    if session is None:
        session = requests.Session()

        retry = Retry(
            total=3,
            connect=3,
            read=2,
            backoff_factor=0.7,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
            respect_retry_after_header=True,
        )

        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=4,
            pool_maxsize=4,
        )

        session.mount("https://", adapter)
        session.mount("http://", adapter)

        session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        })

        _thread_local.session = session

    return session


def fetch_page(url):
    try:
        response = get_session().get(
            url,
            timeout=TIMEOUT,
        )

        if response.status_code != 200:
            log.warning(
                "HTTP %s: %s",
                response.status_code,
                url,
            )
            return None

        return response.text

    except requests.RequestException as exc:
        log.warning(
            "Request failed: %s | %s",
            url,
            exc,
        )
        return None


# ============================================================
# CHANNEL DISCOVERY
# ============================================================

def discover_channels():
    log.info("Reading ElCinema TV guide...")

    html = fetch_page(GUIDE_URL)

    if not html:
        raise RuntimeError(
            "Cannot download the main TV guide."
        )

    soup = BeautifulSoup(html, "html.parser")
    channels = {}

    for link in soup.select("a[href]"):
        href = link.get("href", "")
        match = CHANNEL_RE.search(href)

        if not match:
            continue

        channel_number = match.group(1)

        channels[channel_number] = (
            f"{BASE_URL}/en/tvguide/{channel_number}/"
        )

    log.info(
        "Channels discovered: %s",
        len(channels),
    )

    return channels


# ============================================================
# DATE AND TIME
# ============================================================

def parse_guide_date(text):
    match = DATE_RE.search(text.strip())

    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(2).lower()
    month = MONTHS.get(month_name)

    if not month:
        return None

    today = datetime.now(CAIRO).date()
    candidates = []

    for year in (
        today.year - 1,
        today.year,
        today.year + 1,
    ):
        try:
            candidates.append(
                date(year, month, day)
            )
        except ValueError:
            pass

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda candidate: abs(
            (candidate - today).days
        ),
    )


def parse_start_datetime(date_value, time_text):
    match = TIME_RE.search(time_text)

    if not match or not date_value:
        return None

    hour_minute = match.group(1)
    period = (match.group(2) or "").strip().lower()

    try:
        parsed_time = datetime.strptime(
            hour_minute,
            "%I:%M",
        ).time()
    except ValueError:
        return None

    hour = parsed_time.hour
    minute = parsed_time.minute

    if period in ("pm", "مساءً", "مساءاً", "مساء"):
        if hour < 12:
            hour += 12

    elif period in ("am", "صباحًا", "صباحاً", "صباحا"):
        if hour == 12:
            hour = 0

    return datetime.combine(
        date_value,
        dt_time(hour, minute),
        tzinfo=CAIRO,
    )


def xmltv_timestamp(value):
    return value.strftime("%Y%m%d%H%M%S %z")


# ============================================================
# PROGRAM CARD
# ============================================================

def find_program_card(link):
    card = link.find_parent(
        "div",
        class_="row",
    )

    if not card:
        return None

    time_element = card.select_one(
        "ul.unstyled.text-center > li:first-child"
    )

    duration_element = card.select_one(
        "ul.unstyled.text-center span.subheader"
    )

    if not time_element or not duration_element:
        return None

    if not TIME_RE.search(
        time_element.get_text(" ", strip=True)
    ):
        return None

    if not DURATION_RE.search(
        duration_element.get_text(" ", strip=True)
    ):
        return None

    return card


def parse_program_card(card, guide_date):
    time_element = card.select_one(
        "ul.unstyled.text-center > li:first-child"
    )

    duration_element = card.select_one(
        "ul.unstyled.text-center span.subheader"
    )

    if not time_element or not duration_element:
        return None

    time_text = time_element.get_text(
        " ",
        strip=True,
    )

    duration_text = duration_element.get_text(
        " ",
        strip=True,
    )

    duration_match = DURATION_RE.search(duration_text)

    if not duration_match:
        return None

    duration = int(duration_match.group(1))

    if duration <= 0 or duration > 1440:
        return None

    start_cairo = parse_start_datetime(
        guide_date,
        time_text,
    )

    if not start_cairo:
        return None

    # Use the text link, not the poster link.
    title_link = None

    for link in card.select("a[href*='/work/']"):
        if link.get_text(" ", strip=True):
            title_link = link
            break

    if not title_link:
        return None

    title = title_link.get_text(
        " ",
        strip=True,
    )

    if not title:
        return None

    # Convert Cairo time to Algeria time.
    # Calculate the end in UTC to handle DST transitions.
    start_utc = start_cairo.astimezone(timezone.utc)

    end_utc = start_utc + timedelta(
        minutes=duration
    )

    start_algiers = start_utc.astimezone(ALGIERS)
    end_algiers = end_utc.astimezone(ALGIERS)

    return {
        "title": title,
        "start": start_algiers,
        "end": end_algiers,
    }


# ============================================================
# CHANNEL PARSING
# ============================================================

def parse_channel_page(item):
    channel_number, url = item

    html = fetch_page(url)

    if not html:
        return {
            "number": channel_number,
            "url": url,
            "name": channel_number,
            "programmes": [],
        }

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    heading = soup.select_one(
        "div.panel.jumbo h1"
    )

    channel_name = (
        heading.get_text(" ", strip=True)
        if heading
        else channel_number
    )

    programmes = []
    seen = set()
    current_date = None

    # Process date headings and programme rows
    # in document order.
    for element in soup.select(
        "div.dates, div.row"
    ):
        if "dates" in element.get("class", []):
            parsed_date = parse_guide_date(
                element.get_text(" ", strip=True)
            )

            if parsed_date:
                current_date = parsed_date

            continue

        if current_date is None:
            continue

        time_element = element.select_one(
            "ul.unstyled.text-center > li:first-child"
        )

        duration_element = element.select_one(
            "ul.unstyled.text-center span.subheader"
        )

        title_link = next(
            (
                link
                for link in element.select(
                    "a[href*='/work/']"
                )
                if link.get_text(" ", strip=True)
            ),
            None,
        )

        if (
            not time_element
            or not duration_element
            or not title_link
        ):
            continue

        if not TIME_RE.search(
            time_element.get_text(" ", strip=True)
        ):
            continue

        if not DURATION_RE.search(
            duration_element.get_text(" ", strip=True)
        ):
            continue

        programme = parse_program_card(
            element,
            current_date,
        )

        if not programme:
            continue

        unique_key = (
            programme["title"],
            programme["start"].isoformat(),
        )

        if unique_key in seen:
            continue

        seen.add(unique_key)
        programmes.append(programme)

    programmes.sort(
        key=lambda programme: programme["start"]
    )

    log.info(
        "%s | %s programmes",
        channel_name,
        len(programmes),
    )

    return {
        "number": channel_number,
        "url": url,
        "name": channel_name,
        "programmes": programmes,
    }


# ============================================================
# XMLTV GENERATION - SIMPLIFIED
# ============================================================

def add_text(parent, tag, value, **attributes):
    element = ET.SubElement(
        parent,
        tag,
        attributes,
    )

    if value:
        element.text = str(value)

    return element


def write_xmltv(channel_results):
    tv = ET.Element("tv", {
        "source-info-name": "ElCinema",
        "generator-info-name": "DZGreen-EPG",
    })

    # 1. Write ALL channel definitions first.
    for channel in channel_results:
        channel_id = (
            f"elcinema.{channel['number']}"
        )

        channel_element = ET.SubElement(
            tv,
            "channel",
            {"id": channel_id},
        )

        add_text(
            channel_element,
            "display-name",
            channel["name"],
            lang="en",
        )

    # 2. Write programmes with only:
    # start, stop, channel and title.
    for channel in channel_results:
        channel_id = (
            f"elcinema.{channel['number']}"
        )

        for programme in channel["programmes"]:
            ET.SubElement(
                tv,
                "programme",
                {
                    "start": xmltv_timestamp(
                        programme["start"]
                    ),
                    "stop": xmltv_timestamp(
                        programme["end"]
                    ),
                    "channel": channel_id,
                },
            )

            # The only programme metadata retained is the title.
            title_element = tv[-1]
            add_text(
                title_element,
                "title",
                programme["title"],
                lang="en",
            )

    ET.indent(
        tv,
        space="  ",
    )

    tree = ET.ElementTree(tv)

    tree.write(
        XML_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    # Verify that the generated XML can be parsed.
    ET.parse(XML_FILE)

    log.info(
        "XML validation passed: %s",
        XML_FILE,
    )


# ============================================================
# CSV MAPPING
# ============================================================

def write_csv(channel_results):
    with CSV_FILE.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "channel_id",
                "channel_name",
                "tvguide_url",
                "xmltv_id",
                "programmes_count",
            ],
        )

        writer.writeheader()

        for channel in channel_results:
            writer.writerow({
                "channel_id": channel["number"],
                "channel_name": channel["name"],
                "tvguide_url": channel["url"],
                "xmltv_id": (
                    f"elcinema.{channel['number']}"
                ),
                "programmes_count": len(
                    channel["programmes"]
                ),
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

    channels = discover_channels()

    if not channels:
        raise RuntimeError(
            "No channels found."
        )

    results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:
        futures = {
            executor.submit(
                parse_channel_page,
                item,
            ): item[0]
            for item in channels.items()
        }

        for future in as_completed(futures):
            try:
                results.append(
                    future.result()
                )

            except Exception:
                log.exception(
                    "Channel processing failed: %s",
                    futures[future],
                )

    results.sort(
        key=lambda channel: int(
            channel["number"]
        )
    )

    total_programmes = sum(
        len(channel["programmes"])
        for channel in results
    )

    if total_programmes == 0:
        raise RuntimeError(
            "No programmes extracted. "
            "XMLTV was not generated."
        )

    write_xmltv(results)
    write_csv(results)

    elapsed = time.monotonic() - started

    log.info("")
    log.info("Finished successfully.")
    log.info(
        "Channels processed: %s",
        len(results),
    )
    log.info(
        "Programmes extracted: %s",
        total_programmes,
    )
    log.info(
        "XMLTV: %s",
        XML_FILE,
    )
    log.info(
        "CSV: %s",
        CSV_FILE,
    )
    log.info(
        "Elapsed: %.1f seconds",
        elapsed,
    )


if __name__ == "__main__":
    main()
