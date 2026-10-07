import csv
import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://elcinema.com"
GUIDE_URL = "https://elcinema.com/en/tvguide"

M3U_CHANNELS_FILE = "epg_channels.json"
OUTPUT_FILE = "DZGreen-EPG.xml"
MAPPING_REPORT = "epg_mapping_report.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-DZ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://elcinema.com/",
}

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
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}

QUALITY_WORDS = {
    "4k", "8k", "2k", "uhd", "fhd", "hd", "sd",
    "hdr", "hevc", "h265", "h264", "x265", "x264",
    "vip", "premium", "backup", "test", "plus"
}

REMOVE_WORDS = {
    "channel", "channels"
}

DATE_WEEKDAYS = {
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
    "الاثنين", "الثلاثاء", "الأربعاء", "الاربعاء",
    "الخميس", "الجمعة", "السبت", "الأحد", "الاحد"
}

# Safe aliases for known ElCinema naming differences/truncations.
NAME_ALIASES = {
    "al kahera wal n": "al kahera wal nas",
    "kahera wal n": "kahera wal nas",
    "mbc masr": "mbc egypt",
}


def clean_text(text):
    if not text:
        return ""
    return " ".join(text.split())


def strip_diacritics(text):
    text = unicodedata.normalize("NFKD", text)
    return "".join(
        ch for ch in text
        if not unicodedata.combining(ch)
    )


def normalize_name(text):
    text = strip_diacritics(
        clean_text(text)
    ).lower()

    text = (
        text.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ة", "ه")
        .replace("ـ", "")
    )

    text = text.replace("&", " and ")
    text = text.replace("+", " plus ")
    text = re.sub(r"[’'`]", "", text)
    text = re.sub(
        r"[^0-9a-zA-Z\u0600-\u06FF]+",
        " ",
        text
    )

    tokens = []

    for token in text.split():
        if token in QUALITY_WORDS:
            continue

        if token in REMOVE_WORDS:
            continue

        tokens.append(token)

    result = " ".join(tokens).strip()

    # "MBC2" -> "MBC 2" equivalence is handled by compact_key.
    return NAME_ALIASES.get(
        result,
        result
    )


def compact_key(text):
    return re.sub(
        r"\s+",
        "",
        normalize_name(text)
    )


def create_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def get_page(session, url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                url,
                timeout=30
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
            time.sleep(3)

    return None


def load_user_channels():
    if not os.path.exists(
        M3U_CHANNELS_FILE
    ):
        raise RuntimeError(
            f"الملف {M3U_CHANNELS_FILE} غير موجود."
        )

    with open(
        M3U_CHANNELS_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        data = json.load(file)

    channels = data.get(
        "channels",
        []
    )

    grouped = {}

    for item in channels:
        tvg_id = clean_text(
            item.get("tvg-id", "")
        )
        name = clean_text(
            item.get("name", "")
        )

        if not tvg_id or not name:
            continue

        if tvg_id not in grouped:
            grouped[tvg_id] = {
                "tvg_id": tvg_id,
                "name": name,
                "names": []
            }

        if name not in grouped[tvg_id]["names"]:
            grouped[tvg_id]["names"].append(
                name
            )

    result = list(
        grouped.values()
    )

    print()
    print(
        "M3U tvg-id الفريدة:",
        len(result)
    )

    return result


def discover_elcinema_channels(session):
    print()
    print("===================================")
    print("اكتشاف قنوات ElCinema")
    print("===================================")

    response = get_page(
        session,
        GUIDE_URL
    )

    if not response:
        raise RuntimeError(
            "تعذر الوصول إلى دليل ElCinema."
        )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    channels = {}

    for link in soup.find_all(
        "a",
        href=True
    ):
        href = link["href"].strip()

        match = re.search(
            r"/tvguide/(\d+)/?$",
            href
        )

        if not match:
            continue

        channel_id = match.group(1)

        # Prefer full accessible attributes over truncated visible text.
        name = (
            link.get("title")
            or link.get("aria-label")
            or ""
        )

        if not name:
            image = link.find("img")
            if image:
                name = (
                    image.get("alt")
                    or image.get("title")
                    or ""
                )

        if not name:
            name = link.get_text(
                " ",
                strip=True
            )

        name = clean_text(name)

        if not name:
            continue

        channels[channel_id] = {
            "id": channel_id,
            "name": name,
            "url": urljoin(
                BASE_URL,
                href
            )
        }

    print(
        "قنوات ElCinema المكتشفة:",
        len(channels)
    )

    return list(
        channels.values()
    )


def build_user_indexes(user_channels):
    exact = {}
    compact = {}

    for channel in user_channels:
        keys = set()

        for name in channel["names"]:
            normalized = normalize_name(name)

            if normalized:
                keys.add(normalized)

            compact_value = compact_key(name)

            if compact_value:
                keys.add(
                    "__COMPACT__" + compact_value
                )

        for key in keys:
            target = (
                exact
                if not key.startswith("__COMPACT__")
                else compact
            )

            actual_key = (
                key
                if not key.startswith("__COMPACT__")
                else key.replace(
                    "__COMPACT__",
                    "",
                    1
                )
            )

            target.setdefault(
                actual_key,
                []
            ).append(channel)

    return exact, compact


def find_user_matches(source_name, exact, compact):
    source_normalized = normalize_name(
        source_name
    )

    direct = exact.get(
        source_normalized,
        []
    )

    if direct:
        return direct, "EXACT"

    source_compact = compact_key(
        source_name
    )

    compact_matches = compact.get(
        source_compact,
        []
    )

    if compact_matches:
        return compact_matches, "COMPACT"

    return [], ""


def build_matches(
    user_channels,
    elcinema_channels
):
    print()
    print("===================================")
    print("مطابقة آمنة")
    print("===================================")

    exact_index, compact_index = build_user_indexes(
        user_channels
    )

    matches = []
    matched_tvg_ids = set()

    for source in elcinema_channels:
        user_matches, method = find_user_matches(
            source["name"],
            exact_index,
            compact_index
        )

        for user_channel in user_matches:
            tvg_id = user_channel["tvg_id"]

            unique_key = (
                tvg_id,
                source["id"]
            )

            if unique_key in matched_tvg_ids:
                continue

            matched_tvg_ids.add(
                unique_key
            )

            matches.append({
                "tvg_id": tvg_id,
                "user_name": user_channel["name"],
                "all_user_names": " | ".join(
                    user_channel["names"]
                ),
                "elcinema_id": source["id"],
                "elcinema_name": source["name"],
                "method": method,
                "url": source["url"]
            })

    source_ids = {
        item["elcinema_id"]
        for item in matches
    }

    user_tvg_ids = {
        item["tvg_id"]
        for item in matches
    }

    unmatched_user = [
        {
            "tvg_id": item["tvg_id"],
            "user_name": item["name"]
        }
        for item in user_channels
        if item["tvg_id"] not in user_tvg_ids
    ]

    unmatched_source = [
        {
            "elcinema_id": item["id"],
            "elcinema_name": item["name"],
            "url": item["url"]
        }
        for item in elcinema_channels
        if item["id"] not in source_ids
    ]

    print(
        "مطابقات آمنة:",
        len(matches)
    )
    print(
        "قنوات ElCinema التي لم نجد لها tvg-id:",
        len(unmatched_source)
    )
    print(
        "tvg-id في M3U بدون ElCinema:",
        len(unmatched_user)
    )

    print()
    print("بعض المطابقات:")

    for item in matches[:40]:
        print(
            item["tvg_id"],
            "|",
            item["user_name"],
            "=>",
            item["elcinema_name"],
            "|",
            item["method"]
        )

    return matches, unmatched_user, unmatched_source


def save_mapping_report(
    matches,
    unmatched_user,
    unmatched_source
):
    with open(
        MAPPING_REPORT,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.writer(
            file
        )

        writer.writerow([
            "type",
            "tvg-id",
            "user_name",
            "elcinema_id",
            "elcinema_name",
            "method",
            "url"
        ])

        for item in matches:
            writer.writerow([
                "MATCHED",
                item["tvg_id"],
                item["user_name"],
                item["elcinema_id"],
                item["elcinema_name"],
                item["method"],
                item["url"]
            ])

        for item in unmatched_user:
            writer.writerow([
                "USER_UNMATCHED",
                item["tvg_id"],
                item["user_name"],
                "",
                "",
                "",
                ""
            ])

        for item in unmatched_source:
            writer.writerow([
                "ELCINEMA_UNMATCHED",
                "",
                "",
                item["elcinema_id"],
                item["elcinema_name"],
                "",
                item["url"]
            ])

    print(
        "تقرير المطابقة:",
        MAPPING_REPORT
    )


def parse_date(date_text):
    date_text = clean_text(
        date_text
    )

    match = re.search(
        r"(\d{1,2})\s+([^\s]+)",
        date_text
    )

    if not match:
        return None

    day = int(
        match.group(1)
    )

    month_name = match.group(2)

    month = MONTHS.get(
        month_name
    )

    if not month:
        return None

    now = datetime.now()
    year = now.year

    try:
        result = datetime(
            year,
            month,
            day
        )
    except ValueError:
        return None

    if result < now - timedelta(
        days=180
    ):
        result = result.replace(
            year=year + 1
        )

    return result


def parse_time(time_text):
    time_text = clean_text(
        time_text
    )

    match = re.search(
        r"(\d{1,2}):(\d{2})\s*(AM|PM)",
        time_text,
        re.IGNORECASE
    )

    if not match:
        return None

    hour = int(
        match.group(1)
    )

    minute = int(
        match.group(2)
    )

    meridiem = match.group(3).upper()

    if meridiem == "AM":
        if hour == 12:
            hour = 0
    else:
        if hour != 12:
            hour += 12

    return hour, minute


def parse_duration(duration_text):
    duration_text = clean_text(
        duration_text
    )

    match = re.search(
        r"(\d+)\s*(?:minutes|min)",
        duration_text,
        re.IGNORECASE
    )

    if not match:
        match = re.search(
            r"(\d+)",
            duration_text
        )

    if not match:
        return None

    return int(
        match.group(1)
    )


def is_date_text(text):
    text = clean_text(
        text
    )

    if not text:
        return False

    pattern = (
        r"^(?:(?:"
        r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
        r"الاثنين|الثلاثاء|الأربعاء|الاربعاء|الخميس|الجمعة|السبت|الأحد|الاحد"
        r")\s+)?"
        r"\d{1,2}\s+[^\s]+$"
    )

    return bool(
        re.match(
            pattern,
            text,
            re.IGNORECASE
        )
    )


def find_program_container(title_link):
    # Walk upward and choose the nearest ancestor that contains
    # both a start time and a duration.
    current = title_link

    for _ in range(8):
        if not current:
            break

        text = clean_text(
            current.get_text(
                " ",
                strip=True
            )
        )

        has_time = re.search(
            r"\d{1,2}:\d{2}\s*(?:AM|PM)",
            text,
            re.IGNORECASE
        )

        has_duration = re.search(
            r"\b\d+\s*(?:minutes|min)\b",
            text,
            re.IGNORECASE
        )

        if has_time and has_duration:
            return current

        current = current.parent

    return title_link.parent


def find_previous_date(node):
    candidates = node.find_all_previous(
        [
            "h1", "h2", "h3", "h4", "h5",
            "h6", "div", "section", "p"
        ],
        limit=80
    )

    for candidate in candidates:
        text = clean_text(
            candidate.get_text(
                " ",
                strip=True
            )
        )

        if is_date_text(text):
            parsed = parse_date(
                text
            )

            if parsed:
                return parsed

    return None


def extract_programs_legacy(
    soup,
    channel_id
):
    programs_output = []

    dates = soup.select(
        "div.dates"
    )

    for date_node in dates:
        current_date = parse_date(
            date_node.get_text(
                " ",
                strip=True
            )
        )

        if not current_date:
            continue

        container = date_node.find_next(
            "div",
            class_="columns small-12"
        )

        if not container:
            continue

        programs = container.select(
            "div.boxed-category-0.padded-half, "
            "div.boxed-category-1.padded-half"
        )

        previous_start = None

        for program in programs:
            title = ""

            title_link = program.select_one(
                "a[href^='/work/']"
            )

            if title_link:
                title = clean_text(
                    title_link.get_text(
                        " ",
                        strip=True
                    )
                )
            else:
                title_item = program.select_one(
                    "ul.unstyled.no-margin li:first-child"
                )

                if title_item:
                    title = clean_text(
                        title_item.get_text(
                            " ",
                            strip=True
                        )
                    )

            if not title:
                continue

            time_item = program.select_one(
                "ul.unstyled.text-center li:first-child"
            )

            if not time_item:
                items = program.select(
                    "ul.unstyled.no-margin li"
                )

                if len(items) >= 2:
                    time_item = items[1]

            if not time_item:
                continue

            time_value = parse_time(
                time_item.get_text(
                    " ",
                    strip=True
                )
            )

            duration_item = program.select_one(
                "span.subheader"
            )

            if not duration_item:
                continue

            duration = parse_duration(
                duration_item.get_text(
                    " ",
                    strip=True
                )
            )

            if not time_value or not duration:
                continue

            hour, minute = time_value

            start = current_date.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0
            )

            if previous_start and start < previous_start:
                start += timedelta(
                    days=1
                )

            stop = start + timedelta(
                minutes=duration
            )

            previous_start = start

            programs_output.append({
                "channel_id": channel_id,
                "title": title,
                "start": start,
                "stop": stop
            })

    return programs_output


def extract_programs_generic(
    soup,
    channel_id
):
    programs_output = []

    seen = set()

    title_links = soup.select(
        "a[href^='/work/']"
    )

    for title_link in title_links:
        title = clean_text(
            title_link.get_text(
                " ",
                strip=True
            )
        )

        if not title:
            continue

        container = find_program_container(
            title_link
        )

        container_text = clean_text(
            container.get_text(
                " ",
                strip=True
            )
        )

        time_match = re.search(
            r"(\d{1,2}:\d{2}\s*(?:AM|PM))",
            container_text,
            re.IGNORECASE
        )

        duration_match = re.search(
            r"(\d+\s*(?:minutes|min))",
            container_text,
            re.IGNORECASE
        )

        if not time_match or not duration_match:
            continue

        current_date = find_previous_date(
            title_link
        )

        if not current_date:
            continue

        time_value = parse_time(
            time_match.group(1)
        )

        duration = parse_duration(
            duration_match.group(1)
        )

        if not time_value or not duration:
            continue

        hour, minute = time_value

        start = current_date.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0
        )

        stop = start + timedelta(
            minutes=duration
        )

        key = (
            start,
            stop,
            title.lower()
        )

        if key in seen:
            continue

        seen.add(key)

        programs_output.append({
            "channel_id": channel_id,
            "title": title,
            "start": start,
            "stop": stop
        })

    programs_output.sort(
        key=lambda item: item["start"]
    )

    # Correct midnight rollover.
    corrected = []
    previous_start = None

    for item in programs_output:
        start = item["start"]
        stop = item["stop"]

        if previous_start and start < previous_start:
            start += timedelta(
                days=1
            )
            stop += timedelta(
                days=1
            )

        previous_start = start

        item["start"] = start
        item["stop"] = stop

        corrected.append(item)

    return corrected


def extract_programs(
    soup,
    channel_id
):
    legacy = extract_programs_legacy(
        soup,
        channel_id
    )

    if legacy:
        return legacy

    # Some ElCinema channel pages use a newer layout.
    return extract_programs_generic(
        soup,
        channel_id
    )


def extract_logo(soup):
    for image in soup.find_all(
        "img"
    ):
        src = image.get("src")

        if not src:
            continue

        if "/tvguide/" in src:
            return urljoin(
                BASE_URL,
                src
            )

    return ""


def process_elcinema_channel(
    session,
    channel
):
    response = get_page(
        session,
        channel["url"]
    )

    if not response:
        return {
            "logo": "",
            "programs": []
        }

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    logo = extract_logo(
        soup
    )

    programs = extract_programs(
        soup,
        channel["id"]
    )

    return {
        "logo": logo,
        "programs": programs
    }


def create_xml(
    matches,
    source_results
):
    tv = ET.Element(
        "tv"
    )

    total_programs = 0
    channels_with_programs = 0

    # One source channel can feed multiple user tvg-id variants
    # such as HD / FHD / 4K when they have the same normalized name.
    grouped = {}

    for match in matches:
        grouped.setdefault(
            match["elcinema_id"],
            []
        ).append(match)

    for source_id, source_matches in grouped.items():
        result = source_results.get(
            source_id,
            {}
        )

        programs = result.get(
            "programs",
            []
        )

        logo = result.get(
            "logo",
            ""
        )

        if not programs:
            continue

        for match in source_matches:
            channels_with_programs += 1
            total_programs += len(
                programs
            )

            channel_element = ET.SubElement(
                tv,
                "channel",
                id=match["tvg_id"]
            )

            display_name = ET.SubElement(
                channel_element,
                "display-name"
            )

            display_name.text = match[
                "user_name"
            ]

            if logo:
                ET.SubElement(
                    channel_element,
                    "icon",
                    src=logo
                )

            for item in programs:
                programme = ET.SubElement(
                    tv,
                    "programme",
                    start=item["start"].strftime(
                        "%Y%m%d%H%M%S +0100"
                    ),
                    stop=item["stop"].strftime(
                        "%Y%m%d%H%M%S +0100"
                    ),
                    channel=match["tvg_id"]
                )

                title = ET.SubElement(
                    programme,
                    "title",
                    lang="ar"
                )

                title.text = item["title"]

    if total_programs == 0:
        raise RuntimeError(
            "لم يتم استخراج أي برنامج. "
            "لن يتم استبدال ملف XML الحالي."
        )

    ET.indent(
        tv,
        space="  "
    )

    temporary_file = (
        OUTPUT_FILE + ".tmp"
    )

    tree = ET.ElementTree(
        tv
    )

    tree.write(
        temporary_file,
        encoding="utf-8",
        xml_declaration=True
    )

    os.replace(
        temporary_file,
        OUTPUT_FILE
    )

    print()
    print("===================================")
    print("النتيجة النهائية")
    print("===================================")
    print(
        "قنوات M3U مرتبطة:",
        channels_with_programs
    )
    print(
        "إجمالي البرامج:",
        total_programs
    )
    print(
        "الملف:",
        OUTPUT_FILE
    )
    print("===================================")


def main():
    print()
    print("===================================")
    print("DZGreen EPG")
    print("M3U tvg-id + ElCinema")
    print("===================================")

    session = create_session()

    user_channels = load_user_channels()

    elcinema_channels = discover_elcinema_channels(
        session
    )

    matches, unmatched_user, unmatched_source = build_matches(
        user_channels,
        elcinema_channels
    )

    save_mapping_report(
        matches,
        unmatched_user,
        unmatched_source
    )

    if not matches:
        raise RuntimeError(
            "لم يتم العثور على أي مطابقة آمنة."
        )

    source_results = {}

    unique_sources = {}

    for match in matches:
        unique_sources[
            match["elcinema_id"]
        ] = {
            "id": match["elcinema_id"],
            "name": match["elcinema_name"],
            "url": match["url"]
        }

    print()
    print(
        "تحميل قنوات ElCinema المطابقة:",
        len(unique_sources)
    )

    for index, channel in enumerate(
        unique_sources.values(),
        start=1
    ):
        print(
            "[",
            index,
            "/",
            len(unique_sources),
            "]",
            channel["name"],
            "|",
            channel["id"]
        )

        try:
            source_results[
                channel["id"]
            ] = process_elcinema_channel(
                session,
                channel
            )

        except Exception as error:
            print(
                "خطأ:",
                error
            )

            source_results[
                channel["id"]
            ] = {
                "logo": "",
                "programs": []
            }

        time.sleep(0.5)

    create_xml(
        matches,
        source_results
    )


if __name__ == "__main__":
    main()
