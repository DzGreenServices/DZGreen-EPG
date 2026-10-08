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


# ==========================================================
# Official / corrected channel names
# Key = (category, site_id)
# ==========================================================

CHANNEL_NAMES = {

    # ------------------------------------------------------
    # SPORTS
    # ------------------------------------------------------

    ("sports", "1"):
        "beIN SPORTS 1",

    ("sports", "2"):
        "beIN SPORTS 2",

    ("sports", "3"):
        "beIN SPORTS 3",

    ("sports", "4"):
        "beIN SPORTS 4",

    ("sports", "5"):
        "beIN SPORTS 5",

    ("sports", "6"):
        "beIN SPORTS 6",

    ("sports", "7"):
        "beIN SPORTS 7",

    ("sports", "8"):
        "beIN SPORTS 8",

    ("sports", "9"):
        "beIN SPORTS 9",

    ("sports", "10"):
        "beIN SPORTS NEWS",

    ("sports", "11"):
        "beIN SPORTS",

    ("sports", "12"):
        "beIN SPORTS EN 1",

    ("sports", "13"):
        "beIN SPORTS EN 2",

    ("sports", "14"):
        "beIN SPORTS FR 1",

    ("sports", "15"):
        "beIN SPORTS FR 2",

    # Site ID 16 is currently not identified safely.
    ("sports", "16"):
        "beIN SPORTS - Site 16",

    ("sports", "17"):
        "beIN SPORTS XTRA 1",

    ("sports", "18"):
        "beIN SPORTS XTRA 2",

    ("sports", "19"):
        "beIN SPORTS XTRA 3",

    ("sports", "20"):
        "beIN SPORTS XTRA 4",

    ("sports", "21"):
        "beIN SPORTS XTRA 5",

    ("sports", "22"):
        "beIN SPORTS XTRA 6",

    ("sports", "23"):
        "beIN SPORTS XTRA 7",

    ("sports", "24"):
        "beIN SPORTS XTRA 8",

    ("sports", "25"):
        "beIN SPORTS XTRA 9",

    ("sports", "26"):
        "beIN 4K HDR",

    ("sports", "27"):
        "beIN SPORTS MAX 1",

    ("sports", "28"):
        "beIN SPORTS MAX 2",

    ("sports", "29"):
        "beIN SPORTS MAX 3",

    ("sports", "30"):
        "beIN SPORTS MAX 4",

    ("sports", "31"):
        "beIN SPORTS MAX 5",

    ("sports", "32"):
        "beIN SPORTS MAX 6",

    ("sports", "33"):
        "Alkass One HD",

    ("sports", "34"):
        "Alkass Two HD",

    ("sports", "35"):
        "Alkass Three HD",

    ("sports", "36"):
        "Alkass Four HD",

    ("sports", "37"):
        "Alkass Five HD",

    ("sports", "38"):
        "Alkass Six HD",

    ("sports", "39"):
        "Alkass Seven HD",

    ("sports", "40"):
        "Alkass Eight HD",


    # ------------------------------------------------------
    # ENTERTAINMENT
    # ------------------------------------------------------

    ("entertainment", "1"):
        "beIN MOVIES 1",

    ("entertainment", "2"):
        "beIN MOVIES 2",

    ("entertainment", "3"):
        "beIN MOVIES 3",

    ("entertainment", "4"):
        "beIN MOVIES 4",

    ("entertainment", "5"):
        "FOX Movies Middle East",

    ("entertainment", "6"):
        "FOX Action Movies MENA",

    ("entertainment", "7"):
        "Star Movies Middle East",

    # Site ID 8 is not safely identified.
    ("entertainment", "8"):
        "Entertainment - Site 8",

    ("entertainment", "9"):
        "beIN SERIES 1",

    ("entertainment", "10"):
        "beIN SERIES 2",

    ("entertainment", "11"):
        "beIN DRAMA",

    ("entertainment", "12"):
        "beIN GOURMET",

    ("entertainment", "13"):
        "FOX Arabia",

    ("entertainment", "14"):
        "Food Network",

    ("entertainment", "15"):
        "HGTV Arabia",

    ("entertainment", "16"):
        "Star World",

    ("entertainment", "17"):
        "Fatafeat",

    ("entertainment", "18"):
        "FOX Life",

    ("entertainment", "19"):
        "MTV 80s",

    ("entertainment", "20"):
        "MTV 90s",

    ("entertainment", "21"):
        "Club MTV",

    ("entertainment", "22"):
        "Bloomberg TV",

    ("entertainment", "23"):
        "beIN JUNIOR",

    ("entertainment", "24"):
        "Jeem TV",

    ("entertainment", "25"):
        "Baraem TV",

    ("entertainment", "26"):
        "CNN Arabic",

    ("entertainment", "27"):
        "Euronews English",

    ("entertainment", "28"):
        "Discovery Channel",

    ("entertainment", "29"):
        "beJunior",

    ("entertainment", "30"):
        "Jeem TV - Site 30",

    # Site ID 31 is not safely identified.
    ("entertainment", "31"):
        "Entertainment - Site 31",
}


# ==========================================================
# Stable suggested tvg-id
# ==========================================================

CHANNEL_TVG_IDS = {

    # SPORTS

    ("sports", "1"):
        "beINSports1.qa@MENA",

    ("sports", "2"):
        "beINSports2.qa@MENA",

    ("sports", "3"):
        "beINSports3.qa@MENA",

    ("sports", "4"):
        "beINSPORT4.qa@MENA",

    ("sports", "5"):
        "beINSports5.qa@MENA",

    ("sports", "6"):
        "beINSports6.qa@MENA",

    ("sports", "7"):
        "beINSports7.qa@MENA",

    ("sports", "8"):
        "beINSports8.qa@MENA",

    ("sports", "9"):
        "beINSports9.qa@MENA",

    ("sports", "10"):
        "beINSportsNews.qa@SD",

    ("sports", "11"):
        "beINSports.qa@MENA",

    ("sports", "12"):
        "beinsports1en.qa",

    ("sports", "13"):
        "beinsports2en.qa",

    ("sports", "14"):
        "beinsports1fr.qa",

    ("sports", "15"):
        "beinsports2fr.qa",

    ("sports", "16"):
        "bein.sports.16",

    ("sports", "17"):
        "beINSportsXtra1.qa@SD",

    ("sports", "18"):
        "beINSportsXtra2.qa@SD",

    ("sports", "19"):
        "beINSportsXtra3.qa",

    ("sports", "20"):
        "beINSportsXtra4.qa",

    ("sports", "21"):
        "beINSportsXtra5.qa",

    ("sports", "22"):
        "beINSportsXtra6.qa",

    ("sports", "23"):
        "beINSportsXtra7.qa",

    ("sports", "24"):
        "beINSportsXtra8.qa",

    ("sports", "25"):
        "beINSportsXtra9.qa",

    ("sports", "26"):
        "beINSports4KHDR.qa",

    ("sports", "27"):
        "beINSportsMax1.qa@MENA",

    ("sports", "28"):
        "beINSportsMax2.qa@MENA",

    ("sports", "29"):
        "beINSportsMax3.qa@MENA",

    ("sports", "30"):
        "beINSportsMax4.qa@MENA",

    ("sports", "31"):
        "beINSportsMax5.qa@MENA",

    ("sports", "32"):
        "beINSportsMax6.qa@MENA",

    ("sports", "33"):
        "AlkassOne.qa@SD",

    ("sports", "34"):
        "AlkassTwo.qa@SD",

    ("sports", "35"):
        "AlkassThree.qa@SD",

    ("sports", "36"):
        "AlkassFour.qa@SD",

    ("sports", "37"):
        "AlkassFive.qa@SD",

    ("sports", "38"):
        "AlkassSix.qa@SD",

    ("sports", "39"):
        "AlkassSeven.qa@SD",

    ("sports", "40"):
        "AlkassEight.qa@SD",


    # ENTERTAINMENT

    ("entertainment", "1"):
        "beINMovies1Premiere.qa@SD",

    ("entertainment", "2"):
        "beINMovies2Action.qa@SD",

    ("entertainment", "3"):
        "beINMovies3Drama.qa@SD",

    ("entertainment", "4"):
        "beINMovies4Family.qa@SD",

    ("entertainment", "5"):
        "FoxMoviesMiddleEast.us@SD",

    ("entertainment", "6"):
        "FoxActionMoviesMENA.hk@SD",

    ("entertainment", "7"):
        "StarMoviesMiddleEast.ae@SD",

    ("entertainment", "8"):
        "bein.entertainment.8",

    ("entertainment", "9"):
        "beINSERIES1",

    ("entertainment", "10"):
        "beINSERIES2",

    ("entertainment", "11"):
        "beINDRAMA",

    ("entertainment", "12"):
        "beINGOURMET",

    ("entertainment", "13"):
        "FoxArabia.ae@SD",

    ("entertainment", "14"):
        "FoodNetworkEMEA.us@SD",

    ("entertainment", "15"):
        "HGTVArabia.us@SD",

    ("entertainment", "16"):
        "StarWorldMiddleEast.ae@SD",

    ("entertainment", "17"):
        "Fatafeat.ae@SD",

    ("entertainment", "18"):
        "FoxLifeMiddleEast.ae@SD",

    ("entertainment", "19"):
        "MTV80s.uk@SD",

    ("entertainment", "20"):
        "MTV90s.uk@SD",

    ("entertainment", "21"):
        "ClubMTVEurope.uk@SD",

    ("entertainment", "22"):
        "BloombergTV.us@MiddleEast",

    ("entertainment", "23"):
        "beINJUNIOR",

    ("entertainment", "24"):
        "JeemTV.qa@SD",

    ("entertainment", "25"):
        "Baraem.qa@SD",

    ("entertainment", "26"):
        "CNNArabic.ae@SD",

    ("entertainment", "27"):
        "EuronewsEnglish.fr@SD",

    ("entertainment", "28"):
        "DiscoveryChannelMiddleEastAfrica.us@SD",

    ("entertainment", "29"):
        "BeJunior.qa@SD",

    # Separate ID to avoid collision with site 24.
    ("entertainment", "30"):
        "JeemTV.qa@SD.site30",

    ("entertainment", "31"):
        "bein.entertainment.31",
}


def clean_text(text):

    if not text:
        return ""

    return " ".join(
        str(text).split()
    )


def get_channel_name(
    category,
    site_id,
    fallback_name
):

    key = (
        category,
        str(site_id)
    )

    if key in CHANNEL_NAMES:
        return CHANNEL_NAMES[key]

    return clean_text(
        fallback_name
    )


def get_tvg_id(
    category,
    site_id,
    channel_name
):

    key = (
        category,
        str(site_id)
    )

    if key in CHANNEL_TVG_IDS:
        return CHANNEL_TVG_IDS[key]

    value = clean_text(
        channel_name
    ).lower()

    value = re.sub(
        r"[^a-z0-9]+",
        ".",
        value
    )

    value = value.strip(".")

    return (
        "bein."
        + category
        + "."
        + value
    )


def get_day(
    session,
    category,
    date_value
):

    params = {
        "action": "epg_fetch",
        "offset": "0",
        "category": category,
        "serviceidentity": "bein.net",
        "mins": "00",
        "cdate": date_value.strftime(
            "%Y-%m-%d"
        ),
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


def discover_channels(
    html,
    category
):

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

        site_id = match.group(1)

        link = element.find(
            "a",
            href=True
        )

        discovered_name = ""

        if link:

            discovered_name = clean_text(
                link.get_text(
                    " ",
                    strip=True
                )
            )

            if not discovered_name:

                href = link.get(
                    "href",
                    ""
                )

                parts = [
                    part
                    for part in href.split("/")
                    if part
                ]

                if parts:

                    discovered_name = parts[-1]

        if not discovered_name:

            discovered_name = site_id

        channel_name = get_channel_name(
            category,
            site_id,
            discovered_name
        )

        key = (
            category,
            site_id
        )

        channels[key] = {
            "category": category,
            "site_id": site_id,
            "name": channel_name
        }

    return channels


def parse_time(text):

    match = re.search(
        r"(\d{1,2}):(\d{2})",
        clean_text(text)
    )

    if not match:
        return None

    hour = int(
        match.group(1)
    )

    minute = int(
        match.group(2)
    )

    if hour < 0 or hour > 23:
        return None

    if minute < 0 or minute > 59:
        return None

    return (
        hour,
        minute
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

        site_id = match.group(1)

        items = element.select(
            ".slider > ul:first-child > li"
        )

        channel_programs = []

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
                site_id
            )

            programs[key] = channel_programs

    return programs


def write_csv(
    channels,
    programs
):

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

        for key in sorted(
            channels.keys(),
            key=lambda item: (
                item[0],
                int(item[1])
            )
        ):

            channel = channels[key]

            program_list = programs.get(
                key,
                []
            )

            tvg_id = get_tvg_id(
                channel["category"],
                channel["site_id"],
                channel["name"]
            )

            writer.writerow([
                channel["category"],
                channel["site_id"],
                channel["name"],
                tvg_id,
                len(program_list)
            ])


def write_xml(
    channels,
    programs
):
    tv = ET.Element(
        "tv",
        {
            "generator-info-name": "DZGreen beIN Official EPG",
            "source-info-url": "https://www.beinsports.com/en-mena/tv-guide"
        }
    )

    active_channels = []

    used_tvg_ids = set()

    # -------------------------------------------------
    # 1. ALL channels first
    # -------------------------------------------------

    for key in sorted(
        channels.keys(),
        key=lambda item: (
            item[0],
            int(item[1])
        )
    ):

        channel = channels[key]

        program_list = programs.get(
            key,
            []
        )

        if not program_list:
            continue

        tvg_id = get_tvg_id(
            channel["category"],
            channel["site_id"],
            channel["name"]
        )

        # Prevent duplicate IDs.
        if tvg_id in used_tvg_ids:

            tvg_id = (
                tvg_id
                + "."
                + channel["site_id"]
            )

        used_tvg_ids.add(
            tvg_id
        )

        active_channels.append(
            (
                channel,
                program_list,
                tvg_id
            )
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

    # -------------------------------------------------
    # 2. ALL programmes after ALL channels
    # -------------------------------------------------

    total_programs = 0

    for channel, program_list, tvg_id in active_channels:

        for program in program_list:

            total_programs += 1

            programme = ET.SubElement(
                tv,
                "programme",
                {
                    "channel": tvg_id,
                    "start": program[
                        "start"
                    ].strftime(
                        "%Y%m%d%H%M%S +0300"
                    ),
                    "stop": program[
                        "stop"
                    ].strftime(
                        "%Y%m%d%H%M%S +0300"
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
            "No beIN programs were extracted."
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

    return (
        len(active_channels),
        total_programs
    )

    tv = ET.Element(
        "tv"
    )

    total = 0
    channel_count = 0

    used_tvg_ids = set()

    for key in sorted(
        channels.keys(),
        key=lambda item: (
            item[0],
            int(item[1])
        )
    ):

        channel = channels[key]

        program_list = programs.get(
            key,
            []
        )

        if not program_list:
            continue

        tvg_id = get_tvg_id(
            channel["category"],
            channel["site_id"],
            channel["name"]
        )

        # Guarantee unique XMLTV IDs.
        if tvg_id in used_tvg_ids:

            tvg_id = (
                tvg_id
                + "."
                + channel["site_id"]
            )

        used_tvg_ids.add(
            tvg_id
        )

        channel_count += 1

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

        for program in program_list:

            total += 1

            programme = ET.SubElement(
                tv,
                "programme",
                start=program[
                    "start"
                ].strftime(
                    "%Y%m%d%H%M%S +0300"
                ),
                stop=program[
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

            title.text = program[
                "title"
            ]

    if total == 0:

        raise RuntimeError(
            "No beIN programs were extracted."
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

    return (
        channel_count,
        total
    )


def main():

    print()
    print(
        "==================================="
    )
    print(
        "DZGreen - beIN OFFICIAL TEST"
    )
    print(
        "Corrected channel names"
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

    for day_index in range(
        DAYS
    ):

        date_value = (
            today
            + timedelta(
                days=day_index
            )
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
                ).extend(
                    items
                )

            time.sleep(
                0.5
            )

    # Remove duplicate programs.
    for key in list(
        all_programs.keys()
    ):

        seen = set()
        unique = []

        for item in all_programs[key]:

            item_key = (
                item["start"],
                item["stop"],
                item["title"]
            )

            if item_key in seen:
                continue

            seen.add(
                item_key
            )

            unique.append(
                item
            )

        unique.sort(
            key=lambda item: item["start"]
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
            if all_programs.get(key)
        )
    )

    print(
        "إجمالي البرامج:",
        sum(
            len(items)
            for items in all_programs.values()
        )
    )

    print(
        "==================================="
    )

    print()

    print(
        "القنوات المصححة:"
    )

    for key in sorted(
        channels.keys(),
        key=lambda item: (
            item[0],
            int(item[1])
        )
    ):

        channel = channels[key]

        count = len(
            all_programs.get(
                key,
                []
            )
        )

        print(
            channel["category"],
            "|",
            channel["site_id"],
            "|",
            channel["name"],
            "|",
            get_tvg_id(
                channel["category"],
                channel["site_id"],
                channel["name"]
            ),
            "|",
            count
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
        "==================================="
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
        "XML:",
        OUTPUT_XML
    )

    print(
        "CSV:",
        OUTPUT_CSV
    )

    print(
        "==================================="
    )


if __name__ == "__main__":
    main()
