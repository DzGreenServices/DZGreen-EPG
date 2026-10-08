import csv
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests


CHANNEL_API = (
    "https://www.beinsports.com/api/opta/tv-channel"
)

EVENT_API = (
    "https://www.beinsports.com/api/opta/tv-event"
)

OUTPUT_XML = "BeIN-EPG-test.xml"
OUTPUT_CSV = "BeIN-Channel-Mapping-test.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.beinsports.com/en-mena/tv-guide",
}


def clean_text(text):
    if not text:
        return ""

    return " ".join(
        str(text).split()
    )


def get_json(
    session,
    url,
    params=None,
    retries=3
):
    for attempt in range(
        1,
        retries + 1
    ):
        try:

            response = session.get(
                url,
                params=params,
                timeout=30
            )

            print(
                "HTTP:",
                response.status_code,
                "|",
                response.url
            )

            if response.status_code == 200:

                try:
                    return response.json()

                except ValueError:

                    print(
                        "Réponse non JSON"
                    )

                    return None

        except requests.RequestException as error:

            print(
                "Erreur:",
                error
            )

        if attempt < retries:
            time.sleep(2)

    return None


def get_channels(session):

    print()
    print(
        "==================================="
    )
    print(
        "Récupération des chaînes beIN"
    )
    print(
        "==================================="
    )

    data = get_json(
        session,
        CHANNEL_API,
        params={
            "region": "en-MENA"
        }
    )

    if not data:

        raise RuntimeError(
            "Impossible de récupérer les chaînes."
        )

    rows = data.get(
        "rows",
        []
    )

    channels = []

    for item in rows:

        channel_id = item.get(
            "id"
        )

        name = clean_text(
            item.get(
                "name",
                ""
            )
        )

        if not channel_id:
            continue

        if not name:
            continue

        channels.append({
            "id": str(channel_id),
            "name": name
        })

    return channels


def get_events_for_channel(
    session,
    channel_id,
    days=2
):

    now = datetime.now(
        timezone.utc
    )

    start = (
        now
        .replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )
    )

    end = (
        start
        + timedelta(
            days=days
        )
    )

    params = {
        "startBefore": end.strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        ),
        "endAfter": start.strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        ),
        "channelIds": channel_id
    }

    data = get_json(
        session,
        EVENT_API,
        params=params
    )

    if not data:
        return []

    rows = data.get(
        "rows",
        []
    )

    programs = []

    for item in rows:

        title = clean_text(
            item.get(
                "title",
                ""
            )
        )

        start_text = item.get(
            "startDate"
        )

        end_text = item.get(
            "endDate"
        )

        if not title:
            continue

        if not start_text:
            continue

        if not end_text:
            continue

        try:

            start_date = datetime.fromisoformat(
                start_text.replace(
                    "Z",
                    "+00:00"
                )
            )

            end_date = datetime.fromisoformat(
                end_text.replace(
                    "Z",
                    "+00:00"
                )
            )

        except ValueError:

            continue

        programs.append({
            "title": title,
            "start": start_date,
            "stop": end_date
        })

    programs.sort(
        key=lambda item: item["start"]
    )

    return programs


def make_tvg_id(name):

    normalized = clean_text(
        name
    ).lower()

    normalized = normalized.replace(
        "beIN".lower(),
        "bein"
    )

    normalized = re.sub(
        r"[^a-z0-9]+",
        ".",
        normalized
    )

    normalized = normalized.strip(
        "."
    )

    return (
        "bein."
        + normalized
    )


def create_xml(
    channels,
    all_programs
):

    tv = ET.Element(
        "tv"
    )

    total_programs = 0
    channels_with_programs = 0

    for channel in channels:

        channel_id = channel["id"]

        programs = all_programs.get(
            channel_id,
            []
        )

        if not programs:
            continue

        channels_with_programs += 1

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

        for program in programs:

            total_programs += 1

            programme = ET.SubElement(
                tv,
                "programme",
                start=program[
                    "start"
                ].astimezone(
                    timezone(
                        timedelta(
                            hours=1
                        )
                    )
                ).strftime(
                    "%Y%m%d%H%M%S +0100"
                ),
                stop=program[
                    "stop"
                ].astimezone(
                    timezone(
                        timedelta(
                            hours=1
                        )
                    )
                ).strftime(
                    "%Y%m%d%H%M%S +0100"
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

    if total_programs == 0:

        raise RuntimeError(
            "Aucun programme récupéré."
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
        channels_with_programs,
        total_programs
    )


def create_csv(
    channels,
    all_programs
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
            "beIN site ID",
            "Channel Name",
            "Suggested tvg-id",
            "Programs"
        ])

        for channel in channels:

            channel_id = channel["id"]

            programs = all_programs.get(
                channel_id,
                []
            )

            writer.writerow([
                channel_id,
                channel["name"],
                make_tvg_id(
                    channel["name"]
                ),
                len(programs)
            ])


def main():

    print()
    print(
        "==================================="
    )
    print(
        "DZGreen - beIN OFFICIAL TEST"
    )
    print(
        "==================================="
    )

    session = requests.Session()

    session.headers.update(
        HEADERS
    )

    channels = get_channels(
        session
    )

    print()
    print(
        "Nombre de chaînes:",
        len(channels)
    )

    all_programs = {}

    for index, channel in enumerate(
        channels,
        start=1
    ):

        print()
        print(
            "[",
            index,
            "/",
            len(channels),
            "]",
            channel["name"]
        )

        try:

            programs = get_events_for_channel(
                session,
                channel["id"],
                days=2
            )

            all_programs[
                channel["id"]
            ] = programs

            print(
                "Programmes:",
                len(programs)
            )

        except Exception as error:

            print(
                "Erreur:",
                error
            )

            all_programs[
                channel["id"]
            ] = []

        time.sleep(
            0.3
        )

    channels_with_programs, total_programs = create_xml(
        channels,
        all_programs
    )

    create_csv(
        channels,
        all_programs
    )

    print()
    print(
        "==================================="
    )
    print(
        "RESULTAT"
    )
    print(
        "==================================="
    )

    print(
        "Chaînes:",
        len(channels)
    )

    print(
        "Chaînes avec programmes:",
        channels_with_programs
    )

    print(
        "Programmes:",
        total_programs
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
