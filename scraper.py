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


def clean_text(text):
    if not text:
        return ""

    return " ".join(text.split())


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
                attempt
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

        href = link["href"].strip()

        match = re.search(
            r"/tvguide/(\d+)/?$",
            href
        )

        if not match:
            continue

        channel_id = match.group(1)

        name = ""

        # Try title.
        name = clean_text(
            link.get(
                "title",
                ""
            )
        )

        # Try aria-label.
        if not name:
            name = clean_text(
                link.get(
                    "aria-label",
                    ""
                )
            )

        # Try image alt/title.
        if not name:

            image = link.find("img")

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

        # Finally use visible text.
        if not name:
            name = clean_text(
                link.get_text(
                    " ",
                    strip=True
                )
            )

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

    match = re.search(
        r"(\d{1,2})\s+([^\s]+)",
        text
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

    # ElCinema sometimes displays dates around
    # the end/beginning of the year.
    if result < now - timedelta(
        days=180
    ):

        result = result.replace(
            year=year + 1
        )

    return result


def parse_time(text):

    text = clean_text(
        text
    )

    match = re.search(
        r"(\d{1,2}):(\d{2})\s*(AM|PM)",
        text,
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


def parse_duration(text):

    text = clean_text(
        text
    )

    match = re.search(
        r"(\d+)\s*(?:minutes|min|دقيقة|دقائق)",
        text,
        re.IGNORECASE
    )

    if not match:

        match = re.search(
            r"(\d+)",
            text
        )

    if not match:
        return None

    duration = int(
        match.group(1)
    )

    if duration <= 0:
        return None

    if duration > 1440:
        return None

    return duration


def add_program(
    output,
    seen,
    channel_id,
    current_date,
    title,
    start_time,
    duration
):

    if not current_date:
        return

    if not title:
        return

    if not start_time:
        return

    if not duration:
        return

    hour, minute = start_time

    start = current_date.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0
    )

    stop = (
        start
        + timedelta(minutes=duration)
    )

    key = (
        start,
        stop,
        title
    )

    if key in seen:
        return

    seen.add(
        key
    )

    output.append({
        "channel_id": channel_id,
        "title": title,
        "start": start,
        "stop": stop
    })


def extract_programs_legacy(
    soup,
    channel_id
):

    programs = []

    seen = set()

    date_nodes = soup.select(
        "div.dates"
    )

    for date_node in date_nodes:

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

        blocks = container.select(
            "div.boxed-category-0.padded-half, "
            "div.boxed-category-1.padded-half"
        )

        for block in blocks:

            title = ""

            title_link = block.select_one(
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

                title_node = block.select_one(
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

            time_node = block.select_one(
                "ul.unstyled.text-center li:first-child"
            )

            if not time_node:

                items = block.select(
                    "ul.unstyled.no-margin li"
                )

                if len(items) >= 2:
                    time_node = items[1]

            duration_node = block.select_one(
                "span.subheader"
            )

            if not time_node:
                continue

            if not duration_node:
                continue

            start_time = parse_time(
                time_node.get_text(
                    " ",
                    strip=True
                )
            )

            duration = parse_duration(
                duration_node.get_text(
                    " ",
                    strip=True
                )
            )

            add_program(
                programs,
                seen,
                channel_id,
                current_date,
                title,
                start_time,
                duration
            )

    programs.sort(
        key=lambda item: item["start"]
    )

    return programs


def extract_programs_generic(
    soup,
    channel_id
):

    programs = []

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

        current_node = title_link

        container = None

        for _ in range(8):

            if not current_node:
                break

            text = clean_text(
                current_node.get_text(
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
                r"\d+\s*(?:minutes|min|دقيقة|دقائق)",
                text,
                re.IGNORECASE
            )

            if has_time and has_duration:

                container = current_node

                break

            current_node = current_node.parent

        if not container:
            continue

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
            r"(\d+\s*(?:minutes|min|دقيقة|دقائق))",
            container_text,
            re.IGNORECASE
        )

        if not time_match:
            continue

        if not duration_match:
            continue

        current_date = None

        previous_nodes = title_link.find_all_previous(
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
            limit=100
        )

        for previous in previous_nodes:

            candidate = clean_text(
                previous.get_text(
                    " ",
                    strip=True
                )
            )

            parsed = parse_date(
                candidate
            )

            if parsed:

                current_date = parsed

                break

        if not current_date:
            continue

        start_time = parse_time(
            time_match.group(1)
        )

        duration = parse_duration(
            duration_match.group(1)
        )

        add_program(
            programs,
            seen,
            channel_id,
            current_date,
            title,
            start_time,
            duration
        )

    programs.sort(
        key=lambda item: item["start"]
    )

    return programs


def extract_programs(
    soup,
    channel_id
):

    # First try the known ElCinema TV-guide structure.
    programs = extract_programs_legacy(
        soup,
        channel_id
    )

    if programs:
        return programs

    # Fallback for pages with another layout.
    return extract_programs_generic(
        soup,
        channel_id
    )


def extract_logo(soup):

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

    # Stable ID generated from ElCinema's own
    # channel number.
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

        channel_id = channel["id"]

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
            "Channel_Name": channel["name"],
            "Suggested_tvg_id": suggested_tvg_id(
                channel_id
            ),
            "Logo": logo,
            "Guide_URL": channel["url"],
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

    print()
    print(
        "جدول المطابقة:",
        OUTPUT_CSV
    )


def create_xml(
    channels,
    results
):

    tv = ET.Element(
        "tv"
    )

    total_programs = 0
    channels_with_programs = 0

    for channel in channels:

        channel_id = channel["id"]

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

        if programs:
            channels_with_programs += 1

        channel_element = ET.SubElement(
            tv,
            "channel",
            id=suggested_tvg_id(
                channel_id
            )
        )

        display_name = ET.SubElement(
            channel_element,
            "display-name"
        )

        display_name.text = channel[
            "name"
        ]

        if logo:

            ET.SubElement(
                channel_element,
                "icon",
                src=logo
            )

        for program in programs:

            total_programs += 1

            programme = ET.SubElement(
                tv,
                "programme",
                start=program[
                    "start"
                ].strftime(
                    "%Y%m%d%H%M%S +0100"
                ),
                stop=program[
                    "stop"
                ].strftime(
                    "%Y%m%d%H%M%S +0100"
                ),
                channel=suggested_tvg_id(
                    channel_id
                )
            )

            title = ET.SubElement(
                programme,
                "title",
                lang="ar"
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

    print()
    print(
        "ملف XML:",
        OUTPUT_XML
    )

    print(
        "القنوات التي لها برامج:",
        channels_with_programs
    )

    print(
        "إجمالي البرامج:",
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

    total = len(
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
            total,
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

            print(
                "البرامج:",
                len(
                    result["programs"]
                )
            )

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

        # Be polite to the website.
        time.sleep(0.5)

    create_mapping_csv(
        channels,
        results
    )

    create_xml(
        channels,
        results
    )

    print()
    print(
        "==================================="
    )
    print(
        "اكتمل الاستخراج"
    )
    print(
        "==================================="
    )

    print(
        "القنوات:",
        len(channels)
    )

    print(
        "الـ CSV:",
        OUTPUT_CSV
    )

    print(
        "الـ XML:",
        OUTPUT_XML
    )


if __name__ == "__main__":
    main()
