
import csv
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


# =========================================================
# DZGreen ElCinema EPG
# =========================================================

BASE_URL = "https://elcinema.com"
GUIDE_URL = "https://elcinema.com/en/tvguide"

OUTPUT_XML = "ElCinema-EPG.xml"
OUTPUT_CSV = "ElCinema-Channel-Mapping.csv"

TIMEZONE = "+0100"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    "Referer": "https://elcinema.com/",
}

REQUEST_DELAY = 0.5

# Cache work descriptions during this execution.
DESCRIPTION_CACHE = {}


MONTHS = {
    "يناير": 1,
    "فبراير": 2,
    "مارس": 3,
    "أبريل": 4,
    "ابريل": 4,
    "مايو": 5,
    "يونيو": 6,
    "يوليو": 7,
    "أغسطس": 8,
    "اغسطس": 8,
    "سبتمبر": 9,
    "أكتوبر": 10,
    "اكتوبر": 10,
    "نوفمبر": 11,
    "ديسمبر": 12,

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


# =========================================================
# TEXT UTILITIES
# =========================================================

def clean_text(text):
    if not text:
        return ""

    text = unicodedata.normalize("NFC", str(text))
    return " ".join(text.split())


def remove_diacritics(text):
    text = unicodedata.normalize("NFKD", text)

    return "".join(
        char for char in text
        if not unicodedata.combining(char)
    )


def create_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


# =========================================================
# HTTP
# =========================================================

def get_page(session, url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                url,
                timeout=25
            )

            if response.status_code == 200:
                return response

            print(
                "HTTP",
                response.status_code,
                "| محاولة",
                attempt,
                "|",
                url
            )

        except requests.RequestException as error:
            print(
                "خطأ اتصال | محاولة",
                attempt,
                "|",
                error
            )

        if attempt < retries:
            time.sleep(2)

    return None


# =========================================================
# CHANNEL DISCOVERY
# =========================================================

def discover_channels(session):
    print()
    print("===================================")
    print("اكتشاف قنوات ElCinema")
    print("===================================")

    response = get_page(session, GUIDE_URL)

    if not response:
        raise RuntimeError(
            "تعذر الوصول إلى دليل ElCinema."
        )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    channels = {}

    for link in soup.find_all("a", href=True):
        href = link["href"].strip()

        match = re.search(
            r"/tvguide/(\d+)/?$",
            href
        )

        if not match:
            continue

        channel_id = match.group(1)

        name = clean_text(
            link.get("title", "")
        )

        if not name:
            name = clean_text(
                link.get("aria-label", "")
            )

        if not name:
            image = link.find("img")

            if image:
                name = clean_text(
                    image.get("alt", "")
                )

                if not name:
                    name = clean_text(
                        image.get("title", "")
                    )

        if not name:
            name = clean_text(
                link.get_text(" ", strip=True)
            )

        if not name:
            continue

        channels[channel_id] = {
            "id": channel_id,
            "name": name,
            "url": urljoin(BASE_URL, href)
        }

    result = list(channels.values())

    print("القنوات المكتشفة:", len(result))

    return result


# =========================================================
# DATE AND TIME PARSING
# =========================================================

def parse_date(text):
    text = clean_text(text)

    if not text:
        return None

    normalized = remove_diacritics(text).lower()

    match = re.search(
        r"(\d{1,2})\s+([^\s,]+)",
        normalized
    )

    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(2).strip(".,،")

    month = MONTHS.get(month_name)

    if not month:
        return None

    now = datetime.now()
    candidates = []

    for year in (
        now.year - 1,
        now.year,
        now.year + 1
    ):
        try:
            candidates.append(
                datetime(year, month, day)
            )
        except ValueError:
            pass

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda value: abs(
            (value - now).total_seconds()
        )
    )


def parse_time(text):
    text = clean_text(text)

    if not text:
        return None

    match = re.search(
        r"\b(\d{1,2}):(\d{2})\s*(AM|PM)\b",
        text,
        re.IGNORECASE
    )

    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        meridiem = match.group(3).upper()

        if not (1 <= hour <= 12 and 0 <= minute <= 59):
            return None

        if meridiem == "AM":
            hour = 0 if hour == 12 else hour
        else:
            hour = hour if hour == 12 else hour + 12

        return hour, minute

    match = re.search(
        r"\b(\d{1,2}):(\d{2})\b",
        text
    )

    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))

        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute

    return None


def parse_duration(text):
    text = clean_text(text)

    if not text:
        return None

    patterns = [
        r"(\d+)\s*hours?\s*(\d+)\s*minutes?",
        r"(\d+)\s*hrs?\s*(\d+)\s*mins?",
        r"(\d+)\s*minutes?",
        r"(\d+)\s*mins?",
        r"(\d+)\s*دقيقة",
        r"(\d+)\s*دقائق",
    ]

    for index, pattern in enumerate(patterns):
        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if not match:
            continue

        if index in (0, 1):
            value = (
                int(match.group(1)) * 60
                + int(match.group(2))
            )
        else:
            value = int(match.group(1))

        if 0 < value <= 1440:
            return value

    return None


# =========================================================
# DESCRIPTION EXTRACTION
# =========================================================

def extract_work_description(session, url):
    """
    Retrieve a fuller description from a work page.
    Results, including empty results, are cached for this run.
    """

    if not url:
        return ""

    if url in DESCRIPTION_CACHE:
        return DESCRIPTION_CACHE[url]

    response = get_page(session, url)

    if not response:
        DESCRIPTION_CACHE[url] = ""
        return ""

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    description = ""

    # First: metadata descriptions.
    for selector in (
        'meta[property="og:description"]',
        'meta[name="description"]',
        'meta[name="twitter:description"]',
    ):
        element = soup.select_one(selector)

        if not element:
            continue

        candidate = clean_text(
            element.get("content", "")
        )

        if len(candidate) > len(description):
            description = candidate

    # Second: visible description areas.
    selectors = [
        "[itemprop='description']",
        ".movie-summary",
        ".work-summary",
        ".story .description",
        ".description",
        ".synopsis",
        "article p",
    ]

    for selector in selectors:
        for element in soup.select(selector):
            candidate = clean_text(
                element.get_text(" ", strip=True)
            )

            if len(candidate) > len(description):
                description = candidate

    # Remove common read-more labels.
    description = re.sub(
        r"\s*(?:Read more|اقرأ المزيد)\s*",
        " ",
        description,
        flags=re.IGNORECASE
    )

    description = clean_text(description)

    DESCRIPTION_CACHE[url] = description

    return description


def extract_program_description(session, node, title):
    """
    Try the guide card first, then the linked work page.
    """

    candidates = []

    selectors = [
        "[class*='description']",
        "[class*='synopsis']",
        "[class*='summary']",
        "[itemprop='description']",
        "p",
    ]

    for selector in selectors:
        for element in node.select(selector):
            candidate = clean_text(
                element.get_text(" ", strip=True)
            )

            candidate = re.sub(
                r"\s*(?:Read more|اقرأ المزيد)\s*",
                " ",
                candidate,
                flags=re.IGNORECASE
            )

            candidate = clean_text(candidate)

            if not candidate:
                continue

            if candidate.casefold() == title.casefold():
                continue

            # Ignore short fragments likely to be labels.
            if len(candidate) >= 40:
                candidates.append(candidate)

    description = (
        max(candidates, key=len)
        if candidates
        else ""
    )

    # If no useful description was found, or it is short,
    # try retrieving details from the work page.
    if len(description) < 100:
        link = node.select_one("a[href*='/work/']")

        if link:
            work_url = urljoin(
                BASE_URL,
                link.get("href", "")
            )

            full_description = extract_work_description(
                session,
                work_url
            )

            if len(full_description) > len(description):
                description = full_description

    return clean_text(description)


# =========================================================
# PROGRAM EXTRACTION
# =========================================================

def extract_programs(soup, channel_id, session):
    programs = []
    seen = set()

    tvgrid = soup.select_one(".tvgrid")

    if not tvgrid:
        print(
            "تحذير: لم يتم العثور على tvgrid |",
            channel_id
        )
        return programs

    current_date = None
    previous_start = None

    for node in tvgrid.find_all(True):
        classes = node.get("class", [])

        # Date separators.
        if node.name == "div" and "dates" in classes:
            date_value = parse_date(
                node.get_text(" ", strip=True)
            )

            if date_value:
                current_date = date_value
                previous_start = None

            continue

        if not current_date:
            continue

        # Identify programme cards.
        is_program_card = any(
            str(item).startswith("boxed-category-")
            for item in classes
        )

        if not is_program_card:
            continue

        # Skip nested programme cards.
        parent = node.parent
        nested = False

        while parent is not None and parent is not tvgrid:
            parent_classes = parent.get("class", [])

            if any(
                str(item).startswith("boxed-category-")
                for item in parent_classes
            ):
                nested = True
                break

            parent = parent.parent

        if nested:
            continue

        # TITLE
        title_link = node.select_one("a[href*='/work/']")

        title = ""

        if title_link:
            title = clean_text(
                title_link.get_text(" ", strip=True)
            )

        if not title:
            title_node = node.select_one(
                "ul.unstyled.no-margin li:first-child"
            )

            if title_node:
                title = clean_text(
                    title_node.get_text(" ", strip=True)
                )

        if not title:
            continue

        # TIME
        time_value = None

        time_node = node.select_one(
            "ul.unstyled.text-center li:first-child"
        )

        if time_node:
            time_value = parse_time(
                time_node.get_text(" ", strip=True)
            )

        if not time_value:
            items = node.select(
                "ul.unstyled.no-margin li"
            )

            for item in items:
                time_value = parse_time(
                    item.get_text(" ", strip=True)
                )

                if time_value:
                    break

        if not time_value:
            card_text = clean_text(
                node.get_text(" ", strip=True)
            )

            time_match = re.search(
                r"\d{1,2}:\d{2}\s*(?:AM|PM)",
                card_text,
                re.IGNORECASE
            )

            if time_match:
                time_value = parse_time(
                    time_match.group(0)
                )

        if not time_value:
            continue

        # DURATION
        duration = None

        duration_node = node.select_one("span.subheader")

        if duration_node:
            duration = parse_duration(
                duration_node.get_text(" ", strip=True)
            )

        if not duration:
            card_text = clean_text(
                node.get_text(" ", strip=True)
            )

            duration = parse_duration(card_text)

        if not duration:
            continue

        # DATETIME
        hour, minute = time_value

        start = current_date.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0
        )

        # Handle midnight rollover.
        if previous_start is not None and start < previous_start:
            start += timedelta(days=1)

        stop = start + timedelta(minutes=duration)

        previous_start = start

        # DESCRIPTION
        description = extract_program_description(
            session,
            node,
            title
        )

        # DUPLICATE CHECK
        key = (
            start,
            stop,
            title.casefold()
        )

        if key in seen:
            continue

        seen.add(key)

        # IMPORTANT: append stays inside the card loop.
        programs.append({
            "channel_id": channel_id,
            "title": title,
            "description": description,
            "start": start,
            "stop": stop,
        })

    programs.sort(
        key=lambda item: item["start"]
    )

    return programs


# =========================================================
# CHANNEL LOGO
# =========================================================

def extract_logo(soup):
    for image in soup.find_all("img"):
        src = image.get("src")

        if not src:
            continue

        if "/tvguide/" in src:
            return urljoin(BASE_URL, src)

    # Fallback: Open Graph image.
    og_image = soup.select_one(
        'meta[property="og:image"]'
    )

    if og_image:
        src = og_image.get("content", "")

        if src:
            return urljoin(BASE_URL, src)

    return ""


def suggested_tvg_id(channel_id):
    return "elcinema." + str(channel_id)


# =========================================================
# PROCESS ONE CHANNEL
# =========================================================

def process_channel(session, channel):
    response = get_page(
        session,
        channel["url"]
    )

    if not response:
        return {
            "logo": "",
            "programs": [],
        }

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    logo = extract_logo(soup)

    programs = extract_programs(
        soup,
        channel["id"],
        session
    )

    return {
        "logo": logo,
        "programs": programs,
    }


# =========================================================
# CHANNEL MAPPING CSV
# =========================================================

def create_mapping_csv(channels, results):
    rows = []

    for channel in channels:
        channel_id = channel["id"]
        result = results.get(channel_id, {})
        programs = result.get("programs", [])

        rows.append({
            "ElCinema_ID": channel_id,
            "Channel_Name": channel["name"],
            "Suggested_tvg_id": suggested_tvg_id(channel_id),
            "Logo": result.get("logo", ""),
            "Guide_URL": channel["url"],
            "Programs": len(programs),
            "Programs_With_Description": sum(
                1 for program in programs
                if program.get("description")
            ),
            "Status": (
                "HAS_PROGRAMS"
                if programs
                else "NO_PROGRAMS"
            ),
        })

    rows.sort(
        key=lambda row: (
            row["Channel_Name"].lower(),
            int(row["ElCinema_ID"])
        )
    )

    temporary_file = OUTPUT_CSV + ".tmp"

    with open(
        temporary_file,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "ElCinema_ID",
                "Channel_Name",
                "Suggested_tvg_id",
                "Logo",
                "Guide_URL",
                "Programs",
                "Programs_With_Description",
                "Status",
            ]
        )

        writer.writeheader()
        writer.writerows(rows)

    os.replace(temporary_file, OUTPUT_CSV)


# =========================================================
# XMLTV GENERATION
# =========================================================

def create_xml(channels, results):
    tv = ET.Element(
        "tv",
        {
            "generator-info-name": "DZGreen ElCinema EPG",
            "generator-info-url":
                "https://github.com/DzGreenServices/DZGreen-EPG",
            "source-info-url":
                "https://elcinema.com/en/tvguide",
        }
    )

    active_channels = []
    total_programs = 0
    total_descriptions = 0

    # Add channels that have programmes.
    for channel in channels:
        channel_id = channel["id"]
        result = results.get(channel_id, {})
        programs = result.get("programs", [])

        if not programs:
            continue

        active_channels.append((channel, result))

        channel_element = ET.SubElement(
            tv,
            "channel",
            {"id": suggested_tvg_id(channel_id)}
        )

        display_name = ET.SubElement(
            channel_element,
            "display-name",
            {"lang": "en"}
        )

        display_name.text = channel["name"]

        logo = result.get("logo", "")

        if logo:
            ET.SubElement(
                channel_element,
                "icon",
                {"src": logo}
            )

    # Add programmes only after all channels.
    for channel, result in active_channels:
        tvg_id = suggested_tvg_id(channel["id"])

        for program in result.get("programs", []):
            start = program.get("start")
            stop = program.get("stop")
            program_title = clean_text(
                program.get("title", "")
            )

            if not start or not stop or not program_title:
                continue

            if stop <= start:
                continue

            programme = ET.SubElement(
                tv,
                "programme",
                {
                    "channel": tvg_id,
                    "start": start.strftime(
                        "%Y%m%d%H%M%S " + TIMEZONE
                    ),
                    "stop": stop.strftime(
                        "%Y%m%d%H%M%S " + TIMEZONE
                    ),
                }
            )

            title_element = ET.SubElement(
                programme,
                "title",
                {"lang": "en"}
            )

            title_element.text = program_title

            description = clean_text(
                program.get("description", "")
            )

            if description:
                desc_element = ET.SubElement(
                    programme,
                    "desc",
                    {"lang": "en"}
                )

                desc_element.text = description
                total_descriptions += 1

            total_programs += 1

    # Never replace the previous XML with an empty guide.
    if total_programs == 0:
        raise RuntimeError(
            "لم يتم استخراج أي برنامج. "
            "لم يتم استبدال ملف XML السابق."
        )

    ET.indent(tv, space="  ")

    temporary_file = OUTPUT_XML + ".tmp"

    try:
        tree = ET.ElementTree(tv)

        tree.write(
            temporary_file,
            encoding="utf-8",
            xml_declaration=True
        )

        # Verify the temporary XML before replacing the old file.
        ET.parse(temporary_file)

        os.replace(
            temporary_file,
            OUTPUT_XML
        )

    finally:
        if os.path.exists(temporary_file):
            os.remove(temporary_file)

    return (
        len(active_channels),
        total_programs,
        total_descriptions
    )


# =========================================================
# MAIN
# =========================================================

def main():
    DESCRIPTION_CACHE.clear()

    print()
    print("===================================")
    print("DZGreen ElCinema MASTER EPG")
    print("===================================")

    session = create_session()

    channels = discover_channels(session)

    if not channels:
        raise RuntimeError(
            "لم يتم العثور على قنوات."
        )

    results = {}

    total_channels = len(channels)
    successful_channels = 0

    for index, channel in enumerate(channels, start=1):
        print()
        print(
            "[",
            index,
            "/",
            total_channels,
            "]",
            channel["name"],
            "|",
            channel["id"]
        )

        try:
            result = process_channel(
                session,
                channel
            )

            results[channel["id"]] = result

            programs = result["programs"]
            count = len(programs)

            descriptions = sum(
                1 for program in programs
                if program.get("description")
            )

            print("البرامج:", count)
            print("البرامج ذات الوصف:", descriptions)

            if count:
                successful_channels += 1

        except Exception as error:
            print("خطأ:", error)

            results[channel["id"]] = {
                "logo": "",
                "programs": [],
            }

        time.sleep(REQUEST_DELAY)

    create_mapping_csv(
        channels,
        results
    )

    channels_count, xml_programs, xml_descriptions = create_xml(
        channels,
        results
    )

    print()
    print("===================================")
    print("النتيجة النهائية")
    print("===================================")

    print("القنوات المكتشفة:", total_channels)
    print("القنوات التي لها برامج:", successful_channels)
    print("القنوات داخل XML:", channels_count)
    print("إجمالي البرامج:", xml_programs)
    print("البرامج التي لها وصف:", xml_descriptions)
    print("صفحات الأعمال المخزنة مؤقتًا:", len(DESCRIPTION_CACHE))
    print("CSV:", OUTPUT_CSV)
    print("XML:", OUTPUT_XML)

    if xml_descriptions == 0:
        print()
        print(
            "تنبيه: لم يتم استخراج أي وصف. "
            "يجب فحص HTML الخاص ببطاقات البرامج وصفحات الأعمال."
        )

    print("===================================")

    session.close()


if __name__ == "__main__":
    main()
