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


BASE_URL = "https://elcinema.com"
GUIDE_URL = "https://elcinema.com/en/tvguide"

OUTPUT_XML = "ElCinema-EPG.xml"
OUTPUT_CSV = "ElCinema-Channel-Mapping.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
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


def clean_text(text):
    if not text:
        return ""

    return " ".join(
        str(text).split()
    )


def remove_diacritics(text):
    text = unicodedata.normalize(
        "NFKD",
        text
    )

    return "".join(
        character
        for character in text
        if not unicodedata.combining(character)
    )


def create_session():

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    return session


def get_page(
    session,
    url,
    retries=3
):

    for attempt in range(
        1,
        retries + 1
    ):

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


def discover_channels(session):

    print()
    print(
        "==================================="
    )
    print(
        "اكتشاف قنوات ElCinema"
    )
    print(
        "==================================="
    )

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

        href = link[
            "href"
        ].strip()

        match = re.search(
            r"/tvguide/(\d+)/?$",
            href
        )

        if not match:

            continue

        channel_id = match.group(
            1
        )

        name = ""

        title_value = link.get(
            "title"
        )

        if title_value:

            name = clean_text(
                title_value
            )

        if not name:

            aria_value = link.get(
                "aria-label"
            )

            if aria_value:

                name = clean_text(
                    aria_value
                )

        if not name:

            image = link.find(
                "img"
            )

            if image:

                name = clean_text(
                    image.get(
                        "alt",
                        ""
                    )
                )

                if not name:

                    name = clean_text(
                        image.get(
                            "title",
                            ""
                        )
                    )

        if not name:

            name = clean_text(
                link.get_text(
                    " ",
                    strip=True
                )
            )

        if not name:

            continue

        channels[
            channel_id
        ] = {
            "id": channel_id,
            "name": name,
            "url": urljoin(
                BASE_URL,
                href
            )
        }

    print(
        "القنوات المكتشفة:",
        len(channels)
    )

    return list(
        channels.values()
    )


def parse_date(text):

    text = clean_text(
        text
    )

    if not text:

        return None

    match = re.search(
        r"(\d{1,2})\s+([^\s]+)",
        text
    )

    if not match:

        return None

    day = int(
        match.group(
            1
        )
    )

    month_name = match.group(
        2
    )

    month = MONTHS.get(
        month_name
    )

    if not month:

        return None

    now = datetime.now()

    # ElCinema displays the schedule around the current date.
    # We choose the closest matching year.
    possible = []

    for year in (
        now.year - 1,
        now.year,
        now.year + 1
    ):

        try:

            value = datetime(
                year,
                month,
                day
            )

            possible.append(
                value
            )

        except ValueError:

            pass

    if not possible:

        return None

    result = min(
        possible,
        key=lambda value: abs(
            (value - now).total_seconds()
        )
    )

    return result


def parse_time(text):

    text = clean_text(
        text
    )

    if not text:

        return None

    # English format:
    # 10:30 AM / 10:30 PM
    match = re.search(
        r"(\d{1,2}):(\d{2})\s*(AM|PM)",
        text,
        re.IGNORECASE
    )

    if match:

        hour = int(
            match.group(
                1
            )
        )

        minute = int(
            match.group(
                2
            )
        )

        meridiem = match.group(
            3
        ).upper()

        if meridiem == "AM":

            if hour == 12:

                hour = 0

        else:

            if hour != 12:

                hour += 12

        return (
            hour,
            minute
        )

    # 24-hour fallback:
    # 10:30
    match = re.search(
        r"\b(\d{1,2}):(\d{2})\b",
        text
    )

    if match:

        hour = int(
            match.group(
                1
            )
        )

        minute = int(
            match.group(
                2
            )
        )

        if (
            0 <= hour <= 23
            and
            0 <= minute <= 59
        ):

            return (
                hour,
                minute
            )

    return None


def parse_duration(text):

    text = clean_text(
        text
    )

    if not text:

        return None

    patterns = [
        r"(\d+)\s*minutes?",
        r"(\d+)\s*mins?",
        r"(\d+)\s*دقيقة",
        r"(\d+)\s*دقائق"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            re.IGNORECASE
        )

        if match:

            value = int(
                match.group(
                    1
                )
            )

            if (
                value > 0
                and
                value <= 1440
            ):

                return value

    # Fallback: first integer.
    match = re.search(
        r"(\d+)",
        text
    )

    if match:

        value = int(
            match.group(
                1
            )
        )

        if (
            value > 0
            and
            value <= 1440
        ):

            return value

    return None


def is_date_candidate(
    text
):

    text = clean_text(
        text
    )

    if not text:

        return False

    if not re.search(
        r"\d{1,2}\s+[^\s]+",
        text
    ):

        return False

    return parse_date(
        text
    ) is not None


def find_date_before(
    node
):

    # Search backwards for the closest visible date.
    previous_nodes = node.find_all_previous(
        [
            "div",
            "p",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6"
        ],
        limit=120
    )

    for previous in previous_nodes:

        text = clean_text(
            previous.get_text(
                " ",
                strip=True
            )
        )

        if not is_date_candidate(
            text
        ):

            continue

        value = parse_date(
            text
        )

        if value:

            return value

    return None


def extract_programs(
    soup,
    channel_id
):

    programs = []

    seen = set()

    # -------------------------------------------------
    # ElCinema TV Grid
    # -------------------------------------------------

    tvgrid = soup.select_one(
        "div.tvgrid"
    )

    if not tvgrid:

        # Some pages may use a class combination.
        tvgrid = soup.select_one(
            ".tvgrid"
        )

    if not tvgrid:

        print(
            "تحذير: لم يتم العثور على tvgrid |",
            channel_id
        )

        return programs

    current_date = None

    previous_start = None

    # Process the grid in document order.
    for node in tvgrid.find_all(
        True
    ):

        classes = node.get(
            "class",
            []
        )

        # -------------------------------------------------
        # Date separator
        # -------------------------------------------------

        if (
            node.name == "div"
            and
            "dates" in classes
        ):

            date_value = parse_date(
                node.get_text(
                    " ",
                    strip=True
                )
            )

            if date_value:

                current_date = date_value
                previous_start = None

            continue

        if not current_date:

            continue

        # -------------------------------------------------
        # Only top-level program cards
        # -------------------------------------------------

        is_program_card = any(
            str(item).startswith(
                "boxed-category-"
            )
            for item in classes
        )

        if not is_program_card:

            continue

        # Do not process nested cards.
        parent = node.parent

        nested = False

        while (
            parent is not None
            and
            parent is not tvgrid
        ):

            parent_classes = parent.get(
                "class",
                []
            )

            if any(
                str(item).startswith(
                    "boxed-category-"
                )
                for item in parent_classes
            ):

                nested = True

                break

            parent = parent.parent

        if nested:

            continue

        # -------------------------------------------------
        # TITLE
        # -------------------------------------------------

        title = ""

        title_link = node.select_one(
            "a[href^='/work/']"
        )

        if title_link:

            title = clean_text(
                title_link.get_text(
                    " ",
                    strip=True
                )
            )

        if not title:

            title_node = node.select_one(
                "ul.unstyled.no-margin li:first-child"
            )

            if title_node:

                title = clean_text(
                    title_node.get_text(
                        " ",
                        strip=True
                    )
                )

        if not title:

            continue

        # -------------------------------------------------
        # TIME
        # -------------------------------------------------

        time_value = None

        time_node = node.select_one(
            "ul.unstyled.text-center li:first-child"
        )

        if time_node:

            time_value = parse_time(
                time_node.get_text(
                    " ",
                    strip=True
                )
            )

        # Special first card layout.
        if not time_value:

            items = node.select(
                "ul.unstyled.no-margin li"
            )

            if len(items) >= 2:

                time_value = parse_time(
                    items[1].get_text(
                        " ",
                        strip=True
                    )
                )

        # Whole-card fallback.
        if not time_value:

            card_text = clean_text(
                node.get_text(
                    " ",
                    strip=True
                )
            )

            time_match = re.search(
                r"\d{1,2}:\d{2}\s*(?:AM|PM)",
                card_text,
                re.IGNORECASE
            )

            if time_match:

                time_value = parse_time(
                    time_match.group(
                        0
                    )
                )

        if not time_value:

            continue

        # -------------------------------------------------
        # DURATION
        # -------------------------------------------------

        duration = None

        duration_node = node.select_one(
            "span.subheader"
        )

        if duration_node:

            duration = parse_duration(
                duration_node.get_text(
                    " ",
                    strip=True
                )
            )

        if not duration:

            card_text = clean_text(
                node.get_text(
                    " ",
                    strip=True
                )
            )

            duration_match = re.search(
                r"\b(\d+)\s*(?:minutes?|mins?|دقيقة|دقائق)\b",
                card_text,
                re.IGNORECASE
            )

            if duration_match:

                duration = int(
                    duration_match.group(
                        1
                    )
                )

        if not duration:

            continue

        # -------------------------------------------------
        # DATETIME
        # -------------------------------------------------

        hour, minute = time_value

        start = current_date.replace(
            hour=hour,
            minute=minute,
            second=0,
            microsecond=0
        )

        # Midnight rollover.
        if (
            previous_start is not None
            and
            start < previous_start
        ):

            start += timedelta(
                days=1
            )

        stop = (
            start
            + timedelta(
                minutes=duration
            )
        )

        previous_start = start

        # -------------------------------------------------
        # DUPLICATES
        # -------------------------------------------------

        key = (
            start,
            stop,
            title
        )

        if key in seen:

            continue

        seen.add(
            key
        )

        programs.append({
            "channel_id": channel_id,
            "title": title,
            "start": start,
            "stop": stop
        })

    programs.sort(
        key=lambda item: item["start"]
    )

    return programs


def extract_logo(
    soup
):

    # Prefer the TV Guide logo.
    for image in soup.find_all(
        "img"
    ):

        src = image.get(
            "src"
        )

        if not src:

            continue

        if "/tvguide/" in src:

            return urljoin(
                BASE_URL,
                src
            )

    return ""


def suggested_tvg_id(
    channel_id
):

    return (
        "elcinema."
        + str(channel_id)
    )


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


def create_mapping_csv(
    channels,
    results
):

    rows = []

    for channel in channels:

        channel_id = channel[
            "id"
        ]

        result = results.get(
            channel_id,
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

        rows.append({
            "ElCinema_ID": channel_id,
            "Channel_Name": channel[
                "name"
            ],
            "Suggested_tvg_id": suggested_tvg_id(
                channel_id
            ),
            "Logo": logo,
            "Guide_URL": channel[
                "url"
            ],
            "Programs": len(
                programs
            ),
            "Status": (
                "HAS_PROGRAMS"
                if programs
                else "NO_PROGRAMS"
            )
        })

    rows.sort(
        key=lambda row: (
            row["Channel_Name"].lower(),
            int(row["ElCinema_ID"])
        )
    )

    with open(
        OUTPUT_CSV,
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
                "Status"
            ]
        )

        writer.writeheader()

        writer.writerows(
            rows
        )


def create_xml(
    channels,
    results
):

    tv = ET.Element(
        "tv",
        {
            "generator-info-name":
                "DZGreen ElCinema EPG",
            "generator-info-url":
                "https://github.com/DzGreenServices/DZGreen-EPG",
            "source-info-url":
                "https://elcinema.com/en/tvguide"
        }
    )

    active_channels = []

    total_programs = 0

    # -------------------------------------------------
    # CHANNELS FIRST
    # -------------------------------------------------

    for channel in channels:

        channel_id = channel[
            "id"
        ]

        result = results.get(
            channel_id,
            {}
        )

        programs = result.get(
            "programs",
            []
        )

        if not programs:

            continue

        active_channels.append(
            (
                channel,
                result
            )
        )

        tvg_id = suggested_tvg_id(
            channel_id
        )

        channel_element = ET.SubElement(
            tv,
            "channel",
            {
                "id": tvg_id
            }
        )

        display_name = ET.SubElement(
            channel_element,
            "display-name",
            {
                "lang": "en"
            }
        )

        display_name.text = channel[
            "name"
        ]

        logo = result.get(
            "logo",
            ""
        )

        if logo:

            ET.SubElement(
                channel_element,
                "icon",
                {
                    "src": logo
                }
            )

    # -------------------------------------------------
    # PROGRAMMES AFTER ALL CHANNELS
    # -------------------------------------------------

    for channel, result in active_channels:

        tvg_id = suggested_tvg_id(
            channel["id"]
        )

        programs = result.get(
            "programs",
            []
        )

        for program in programs:

            total_programs += 1

            programme = ET.SubElement(
                tv,
                "programme",
                {
                    "channel": tvg_id,
                    "start": program[
                        "start"
                    ].strftime(
                        "%Y%m%d%H%M%S +0100"
                    ),
                    "stop": program[
                        "stop"
                    ].strftime(
                        "%Y%m%d%H%M%S +0100"
                    )
                }
            )

            title = ET.SubElement(
                programme,
                "title",
                {
                    "lang": "en"
                }
            )

            title.text = program[
                "title"
            ]

    if total_programs == 0:

        raise RuntimeError(
            "لم يتم استخراج أي برنامج."
        )

    ET.indent(
        tv,
        space="  "
    )

    temporary_file = (
        OUTPUT_XML
        + ".tmp"
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
        OUTPUT_XML
    )

    return (
        len(active_channels),
        total_programs
    )


def main():

    print()
    print(
        "==================================="
    )
    print(
        "DZGreen ElCinema MASTER EPG"
    )
    print(
        "==================================="
    )

    session = create_session()

    channels = discover_channels(
        session
    )

    if not channels:

        raise RuntimeError(
            "لم يتم العثور على قنوات."
        )

    results = {}

    successful_channels = 0

    total_programs = 0

    total_channels = len(
        channels
    )

    for index, channel in enumerate(
        channels,
        start=1
    ):

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

            results[
                channel["id"]
            ] = result

            count = len(
                result["programs"]
            )

            print(
                "البرامج:",
                count
            )

            if count > 0:

                successful_channels += 1

                total_programs += count

        except Exception as error:

            print(
                "خطأ:",
                error
            )

            results[
                channel["id"]
            ] = {
                "logo": "",
                "programs": []
            }

        time.sleep(
            0.5
        )

    create_mapping_csv(
        channels,
        results
    )

    channels_count, xml_programs = create_xml(
        channels,
        results
    )

    print()
    print(
        "==================================="
    )
    print(
        "النتيجة النهائية"
    )
    print(
        "==================================="
    )

    print(
        "القنوات المكتشفة:",
        total_channels
    )

    print(
        "القنوات التي لها برامج:",
        successful_channels
    )

    print(
        "القنوات داخل XML:",
        channels_count
    )

    print(
        "إجمالي البرامج:",
        xml_programs
    )

    print(
        "CSV:",
        OUTPUT_CSV
    )

    print(
        "XML:",
        OUTPUT_XML
    )

    print(
        "==================================="
    )


if __name__ == "__main__":

    main()
