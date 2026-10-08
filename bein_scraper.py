import csv
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://www.bein.com"
EPG_URL = f"{BASE_URL}/en/epg-ajax-template/"

OUTPUT_XML = "BeIN-EPG-test.xml"
OUTPUT_CSV = "BeIN-Channel-Mapping-test.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bein.com/en/tv-guide/",
}

CATEGORIES = [
    "sports",
    "entertainment"
]

DAYS = 2


def clean_text(text):
    if not text:
        return ""

    return " ".join(
        text.split()
    )


def get_day(session, category, date_value):

    params = {
        "action": "epg_fetch",
        "offset": "0",
        "category": category,
        "serviceidentity": "bein.net",
        "mins": "00",
        "cdate": date_value.strftime("%Y-%m-%d"),
        "language": "EN",
        "postid": "25356",
        "loadindex": "0"
    }

    response = session.get(
        EPG_URL,
        params=params,
        timeout=30
    )

    print(
        "HTTP:",
        response.status_code,
        "|",
        category,
        "|",
        date_value
    )

    if response.status_code != 200:
        print(
            response.text[:300]
        )
        return None

    return response.text


def discover_channels(html, category):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    channels = {}

    for element in soup.select(
        ".container-tvguide > div"
    ):

        element_id = element.get(
            "id",
            ""
        )

        match = re.match(
            r"channels_(\d+)",
            element_id
        )

        if not match:
            continue

        channel_id = match.group(1)

        link = element.find(
            "a",
            href=True
        )

        name = ""

        if link:

            name = clean_text(
                link.get_text(
                    " ",
                    strip=True
                )
            )

            if not name:

                href = link.get(
                    "href",
                    ""
                )

                parts = [
                    p
                    for p in href.split("/")
                    if p
                ]

                if parts:
                    name = parts[-1]

        if not name:
            name = channel_id

        key = (
            category,
            channel_id
        )

        channels[key] = {
            "category": category,
            "site_id": channel_id,
            "name": name
        }

    return channels


def parse_time(text):

    match = re.search(
        r"^(\d{1,2}):(\d{2})",
        clean_text(text)
    )

    if not match:
        return None

    return (
        int(match.group(1)),
        int(match.group(2))
    )


def extract_programs(
    html,
    category,
    date_value
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    programs = {}

    for element in soup.select(
        ".container-tvguide > div"
    ):

        element_id = element.get(
            "id",
            ""
        )

        match = re.match(
            r"channels_(\d+)",
            element_id
        )

        if not match:
            continue

        channel_id = match.group(1)

        items = element.select(
            ".slider > ul:first-child > li"
        )

        channel_programs = []

        # The official parser used by EPG tools
        # starts from the previous day and rolls over
        # when a start time decreases.
        current_date = (
            date_value
            - timedelta(days=1)
        )

        previous_start = None

        for item in items:

            title_node = item.select_one(
                ".title"
            )

            if not title_node:
                continue

            title = clean_text(
                title_node.get_text(
                    " ",
                    strip=True
                )
            )

            if not title:
                continue

            time_node = item.select_one(
                ".time"
            )

            if not time_node:
                continue

            time_value = parse_time(
                time_node.get_text(
                    " ",
                    strip=True
                )
            )

            if not time_value:
                continue

            hour, minute = time_value

            start = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                hour,
                minute,
                tzinfo=timezone(
                    timedelta(hours=3)
                )
            )

            if (
                previous_start
                and start < previous_start
            ):
                current_date += timedelta(
                    days=1
                )

                start = datetime(
                    current_date.year,
                    current_date.month,
                    current_date.day,
                    hour,
                    minute,
                    tzinfo=timezone(
                        timedelta(hours=3)
                    )
                )

            previous_start = start

            channel_programs.append({
                "title": title,
                "start": start,
                "stop": None
            })

        # Stop = next program start.
        for index in range(
            len(channel_programs)
        ):

            current = channel_programs[
                index
            ]

            if (
                index + 1
                < len(channel_programs)
            ):

                current["stop"] = (
                    channel_programs[
                        index + 1
                    ]["start"]
                )

            else:

                current["stop"] = (
                    current["start"]
                    + timedelta(
                        minutes=30
                    )
                )

        if channel_programs:

            key = (
                category,
                channel_id
            )

            programs[key] = channel_programs

    return programs


def make_tvg_id(name):

    value = clean_text(
        name
    ).lower()

    value = re.sub(
        r"[^a-z0-9]+",
        ".",
        value
    )

    value = value.strip(
        "."
    )

    return (
        "bein."
        + value
    )


def write_csv(channels, programs):

    with open(
        OUTPUT_CSV,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.writer(
            file
        )

        writer.writerow([
            "Category",
            "beIN Site ID",
            "Channel Name",
            "Suggested tvg-id",
            "Programs"
        ])

        for channel in channels.values():

            key = (
                channel["category"],
                channel["site_id"]
            )

            writer.writerow([
                channel["category"],
                channel["site_id"],
                channel["name"],
                make_tvg_id(
                    channel["name"]
                ),
                len(
                    programs.get(
                        key,
                        []
                    )
                )
            ])


def write_xml(channels, programs):

    tv = ET.Element(
        "tv"
    )

    total = 0
    channel_count = 0

    for channel in channels.values():

        key = (
            channel["category"],
            channel["site_id"]
        )

        items = programs.get(
            key,
            []
        )

        if not items:
            continue

        channel_count += 1

        tvg_id = make_tvg_id(
            channel["name"]
        )

        channel_element = ET.SubElement(
            tv,
            "channel",
            id=tvg_id
        )

        display_name = ET.SubElement(
            channel_element,
            "display-name"
        )

        display_name.text = channel[
            "name"
        ]

        for item in items:

            total += 1

            programme = ET.SubElement(
                tv,
                "programme",
                start=item[
                    "start"
                ].strftime(
                    "%Y%m%d%H%M%S +0300"
                ),
                stop=item[
                    "stop"
                ].strftime(
                    "%Y%m%d%H%M%S +0300"
                ),
                channel=tvg_id
            )

            title = ET.SubElement(
                programme,
                "title",
                lang="en"
            )

            title.text = item[
                "title"
            ]

    if total == 0:

        raise RuntimeError(
            "لم يتم استخراج أي برنامج."
        )

    ET.indent(
        tv,
        space="  "
    )

    ET.ElementTree(tv).write(
        OUTPUT_XML,
        encoding="utf-8",
        xml_declaration=True
    )

    return channel_count, total


def main():

    print()
    print(
        "==================================="
    )
    print(
        "DZGreen - beIN OFFICIAL TEST"
    )
    print(
        "bein.com"
    )
    print(
        "==================================="
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    channels = {}
    all_programs = {}

    today = datetime.now().date()

    for offset in range(
        DAYS
    ):

        date_value = (
            today
            + timedelta(days=offset)
        )

        for category in CATEGORIES:

            print()
            print(
                "CATEGORY:",
                category,
                "| DATE:",
                date_value
            )

            html = get_day(
                session,
                category,
                date_value
            )

            if not html:
                continue

            discovered = discover_channels(
                html,
                category
            )

            for key, channel in discovered.items():
                channels[key] = channel

            day_programs = extract_programs(
                html,
                category,
                date_value
            )

            for key, items in day_programs.items():

                all_programs.setdefault(
                    key,
                    []
                ).extend(items)

            time.sleep(0.5)

    # Remove exact duplicates.
    for key in list(
        all_programs.keys()
    ):

        seen = set()
        unique = []

        for item in all_programs[key]:

            unique_key = (
                item["start"],
                item["stop"],
                item["title"]
            )

            if unique_key in seen:
                continue

            seen.add(
                unique_key
            )

            unique.append(
                item
            )

        unique.sort(
            key=lambda x: x["start"]
        )

        all_programs[key] = unique

    print()
    print(
        "==================================="
    )

    print(
        "القنوات المكتشفة:",
        len(channels)
    )

    print(
        "القنوات التي لها برامج:",
        sum(
            1
            for key in channels
            if all_programs.get(
                key
            )
        )
    )

    print(
        "إجمالي البرامج:",
        sum(
            len(items)
            for items in all_programs.values()
        )
    )

    write_csv(
        channels,
        all_programs
    )

    channel_count, total = write_xml(
        channels,
        all_programs
    )

    print()
    print(
        "XML:",
        OUTPUT_XML
    )

    print(
        "CSV:",
        OUTPUT_CSV
    )

    print(
        "Channels:",
        channel_count
    )

    print(
        "Programs:",
        total
    )

    print(
        "==================================="
    )


if __name__ == "__main__":
    main()
