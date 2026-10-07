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
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4,
    "مايو": 5, "يونيو": 6, "يوليو": 7, "أغسطس": 8, "اغسطس": 8,
    "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5,
    "June": 6, "July": 7, "August": 8, "September": 9, "October": 10,
    "November": 11, "December": 12,
}

# These are technical quality markers, NOT channel identity.
QUALITY_WORDS = {
    "4k", "8k", "2k", "uhd", "fhd", "hd", "sd",
    "hdr", "hevc", "h265", "h264", "x265", "x264",
    "vip", "premium", "backup", "test"
}

# Safe spelling aliases. They do not erase meaningful channel numbers or "plus".
ALIASES = {
    "alkass": "al kass",
    "alkas": "al kass",
    "beinsports": "bein sports",
    "beinxtra": "bein xtra",
    "beinsportsxtra": "bein sports xtra",
    "osnyahala": "osn ya hala",
    "osnyahalatv": "osn ya hala",
    "osnyahalacinima": "osn ya hala afl am",
    "cinima": "cinema",
    "masr": "masr",
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
    """
    Identity normalization:
    - removes only technical quality markers (4K/HD/etc.)
    - preserves meaningful words and numbers
    - preserves '+' as the semantic word 'plus'
    - turns MBC2 into MBC 2
    """
    text = strip_diacritics(clean_text(text)).lower()

    text = (
        text.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ة", "ه")
        .replace("ـ", "")
    )

    # Plus is meaningful for channel identity.
    text = text.replace("+", " plus ")

    # Separate letters/numbers: MBC2 -> MBC 2.
    text = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", text)

    # Normalize punctuation and separators.
    text = re.sub(r"[’'`]", "", text)
    text = re.sub(r"[^0-9a-zA-Z\u0600-\u06FF]+", " ", text)

    tokens = []
    for token in text.split():
        # Quality markers are removable.
        if token in QUALITY_WORDS:
            continue

        # A literal "plus" is KEPT because it changes MBC Drama
        # into MBC Drama Plus.
        tokens.append(token)

    normalized = " ".join(tokens)

    # Compact spelling aliases.
    compact = normalized.replace(" ", "")
    if compact in ALIASES:
        normalized = ALIASES[compact]

    return normalized.strip()


def compact_key(text):
    return re.sub(r"\s+", "", normalize_name(text))


def parse_date(text):
    text = clean_text(text)
    match = re.search(r"(\d{1,2})\s+([^\s]+)", text)
    if not match:
        return None

    day = int(match.group(1))
    month = MONTHS.get(match.group(2))
    if not month:
        return None

    now = datetime.now()

    try:
        result = datetime(now.year, month, day)
    except ValueError:
        return None

    if result < now - timedelta(days=180):
        result = result.replace(year=now.year + 1)

    return result


def parse_time(text):
    match = re.search(
        r"(\d{1,2}):(\d{2})\s*(AM|PM)",
        clean_text(text),
        re.I
    )
    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))
    meridiem = match.group(3).upper()

    if meridiem == "AM":
        if hour == 12:
            hour = 0
    elif hour != 12:
        hour += 12

    return hour, minute


def parse_duration(text):
    text = clean_text(text)

    match = re.search(r"(\d+)\s*(?:minutes|min)", text, re.I)
    if not match:
        match = re.search(r"(\d+)", text)

    if not match:
        return None

    return int(match.group(1))


def create_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def get_page(session, url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=30)

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
    if not os.path.exists(M3U_CHANNELS_FILE):
        raise RuntimeError(
            f"الملف {M3U_CHANNELS_FILE} غير موجود."
        )

    with open(
        M3U_CHANNELS_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        data = json.load(file)

    grouped = {}

    for item in data.get("channels", []):
        tvg_id = clean_text(item.get("tvg-id", ""))
        name = clean_text(item.get("name", ""))

        if not tvg_id or not name:
            continue

        if tvg_id not in grouped:
            grouped[tvg_id] = {
                "tvg_id": tvg_id,
                "name": name,
                "names": []
            }

        if name not in grouped[tvg_id]["names"]:
            grouped[tvg_id]["names"].append(name)

    result = list(grouped.values())

    print("M3U tvg-id الفريدة:", len(result))
    return result


def discover_elcinema_channels(session):
    print()
    print("===================================")
    print("اكتشاف قنوات ElCinema")
    print("===================================")

    response = get_page(session, GUIDE_URL)

    if not response:
        raise RuntimeError(
            "تعذر الوصول إلى دليل ElCinema."
        )

    soup = BeautifulSoup(response.text, "html.parser")
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
            "url": urljoin(BASE_URL, href)
        }

    print(
        "قنوات ElCinema المكتشفة:",
        len(channels)
    )

    return list(channels.values())


def build_indexes(user_channels):
    exact = {}
    compact = {}

    for channel in user_channels:
        for name in channel["names"]:
            normalized = normalize_name(name)
            compact_name = compact_key(name)

            if normalized:
                exact.setdefault(
                    normalized, []
                ).append(channel)

            if compact_name:
                compact.setdefault(
                    compact_name, []
                ).append(channel)

    return exact, compact


def unique_channels(items):
    result = []
    seen = set()

    for item in items:
        if item["tvg_id"] in seen:
            continue

        seen.add(item["tvg_id"])
        result.append(item)

    return result


def build_matches(user_channels, elcinema_channels):
    """
    Important rule:
    One user tvg-id can map to ONE ElCinema channel only.
    One ElCinema channel may feed multiple user tvg-id variants
    (for example HD and 4K versions).
    """
    exact_index, compact_index = build_indexes(user_channels)

    matches = []
    used_pairs = set()
    used_user_source = set()

    for source in elcinema_channels:
        source_name = source["name"]
        normalized = normalize_name(source_name)
        compact = compact_key(source_name)

        candidates = []

        for channel in exact_index.get(normalized, []):
            candidates.append(
                (channel, "EXACT")
            )

        if not candidates:
            for channel in compact_index.get(compact, []):
                candidates.append(
                    (channel, "COMPACT")
                )

        # Deduplicate candidates by tvg-id.
        candidate_map = {}
        for channel, method in candidates:
            candidate_map[channel["tvg_id"]] = (
                channel,
                method
            )

        for tvg_id, (channel, method) in candidate_map.items():
            pair = (
                tvg_id,
                source["id"]
            )

            if pair in used_pairs:
                continue

            # This is the critical protection:
            # same tvg-id cannot be assigned to two source channels.
            if tvg_id in used_user_source:
                continue

            used_pairs.add(pair)
            used_user_source.add(tvg_id)

            matches.append({
                "tvg_id": tvg_id,
                "user_name": channel["name"],
                "all_user_names": " | ".join(
                    channel["names"]
                ),
                "elcinema_id": source["id"],
                "elcinema_name": source["name"],
                "method": method,
                "url": source["url"]
            })

    matched_tvg_ids = {
        item["tvg_id"]
        for item in matches
    }

    matched_source_ids = {
        item["elcinema_id"]
        for item in matches
    }

    unmatched_user = [
        {
            "tvg_id": channel["tvg_id"],
            "user_name": channel["name"]
        }
        for channel in user_channels
        if channel["tvg_id"] not in matched_tvg_ids
    ]

    unmatched_source = [
        {
            "elcinema_id": channel["id"],
            "elcinema_name": channel["name"],
            "url": channel["url"]
        }
        for channel in elcinema_channels
        if channel["id"] not in matched_source_ids
    ]

    print()
    print("===================================")
    print("مطابقة القنوات")
    print("===================================")
    print(
        "مطابقات آمنة:",
        len(matches)
    )
    print(
        "ElCinema بدون tvg-id مطابق:",
        len(unmatched_source)
    )
    print(
        "tvg-id بدون ElCinema:",
        len(unmatched_user)
    )

    print()
    print("المطابقات:")
    for item in matches:
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
        writer = csv.writer(file)

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


def extract_logo(soup):
    for image in soup.find_all("img"):
        src = image.get("src")

        if src and "/tvguide/" in src:
            return urljoin(
                BASE_URL,
                src
            )

    return ""


def parse_program_block(
    program,
    current_date,
    channel_id
):
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
        return None

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
        return None

    duration_item = program.select_one(
        "span.subheader"
    )

    if not duration_item:
        return None

    time_value = parse_time(
        time_item.get_text(
            " ",
            strip=True
        )
    )

    duration = parse_duration(
        duration_item.get_text(
            " ",
            strip=True
        )
    )

    if not time_value or not duration:
        return None

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

    return {
        "channel_id": channel_id,
        "title": title,
        "start": start,
        "stop": stop
    }


def extract_programs(soup, channel_id):
    programs_output = []

    dates = soup.select("div.dates")

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
            item = parse_program_block(
                program,
                current_date,
                channel_id
            )

            if not item:
                continue

            start = item["start"]

            if previous_start and start < previous_start:
                item["start"] += timedelta(days=1)
                item["stop"] += timedelta(days=1)

            previous_start = item["start"]
            programs_output.append(item)

    # Fallback parser for pages with a slightly different layout.
    if not programs_output:
        for title_link in soup.select(
            "a[href^='/work/']"
        ):
            title = clean_text(
                title_link.get_text(
                    " ",
                    strip=True
                )
            )

            if not title:
                continue

            parent = title_link
            for _ in range(8):
                parent = parent.parent
                if not parent:
                    break

                text = clean_text(
                    parent.get_text(
                        " ",
                        strip=True
                    )
                )

                time_match = re.search(
                    r"(\d{1,2}:\d{2}\s*(?:AM|PM))",
                    text,
                    re.I
                )

                duration_match = re.search(
                    r"(\d+)\s*(?:minutes|min)",
                    text,
                    re.I
                )

                if time_match and duration_match:
                    break

            if not parent:
                continue

            current_date = None
            for previous in title_link.find_all_previous(
                ["div", "h1", "h2", "h3", "h4", "h5", "h6"],
                limit=80
            ):
                possible = clean_text(
                    previous.get_text(
                        " ",
                        strip=True
                    )
                )

                current_date = parse_date(
                    possible
                )

                if current_date:
                    break

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

            programs_output.append({
                "channel_id": channel_id,
                "title": title,
                "start": start,
                "stop": stop
            })

    # Remove duplicates.
    unique = {}
    for item in programs_output:
        key = (
            item["start"],
            item["stop"],
            item["title"].strip().lower()
        )
        unique[key] = item

    result = list(unique.values())
    result.sort(
        key=lambda item: item["start"]
    )

    return result


def process_channel(
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

    return {
        "logo": extract_logo(soup),
        "programs": extract_programs(
            soup,
            channel["id"]
        )
    }


def create_xml(
    matches,
    source_results
):
    tv = ET.Element("tv")

    total_programs = 0
    channels_with_programs = 0

    # Same ElCinema source can safely feed multiple user tvg-id variants.
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
            total_programs += len(programs)

            channel_element = ET.SubElement(
                tv,
                "channel",
                id=match["tvg_id"]
            )

            display_name = ET.SubElement(
                channel_element,
                "display-name"
            )
            display_name.text = match["user_name"]

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
            "لن يتم استبدال XML."
        )

    ET.indent(
        tv,
        space="  "
    )

    temporary = OUTPUT_FILE + ".tmp"

    ET.ElementTree(tv).write(
        temporary,
        encoding="utf-8",
        xml_declaration=True
    )

    os.replace(
        temporary,
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
    elcinema_channels = discover_elcinema_channels(session)

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
            "لا توجد مطابقة آمنة."
        )

    source_channels = {}

    for match in matches:
        source_channels[
            match["elcinema_id"]
        ] = {
            "id": match["elcinema_id"],
            "name": match["elcinema_name"],
            "url": match["url"]
        }

    print()
    print(
        "قنوات ElCinema التي سيتم تحميلها:",
        len(source_channels)
    )

    source_results = {}

    for index, channel in enumerate(
        source_channels.values(),
        start=1
    ):
        print(
            "[",
            index,
            "/",
            len(source_channels),
            "]",
            channel["name"],
            "|",
            channel["id"]
        )

        try:
            source_results[
                channel["id"]
            ] = process_channel(
                session,
                channel
            )
        except Exception as error:
            print(
                "خطأ في القناة:",
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
