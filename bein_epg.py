import csv
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://www.bein.com/ar/%d8%ac%d8%af%d9%88%d9%84-%d8%a7%d9%84%d8%a8%d8%ab/?c=dz&"

XML_OUTPUT = "docs/BeIN-EPG.xml"
CSV_OUTPUT = "docs/BeIN-Channels.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar-DZ,ar;q=0.9,en;q=0.8",
}


# ============================================================
# HELPERS
# ============================================================

def get_channel_id(href):
    """
    Extract the channel ID from:

    https://beinconnect.app/beINSPORTS1

    Result:
    beINSPORTS1
    """

    if not href:
        return None

    path = urlparse(href).path.strip("/")

    if not path:
        return None

    return path.split("/")[-1]


def clean_text(value):
    if not value:
        return ""

    return " ".join(value.split())


def epoch_to_xmltv(milliseconds):
    """
    Convert Unix milliseconds to XMLTV timestamp.

    We keep the timezone represented by the official timestamp.
    """

    dt = datetime.fromtimestamp(
        int(milliseconds) / 1000,
        tz=timezone.utc
    )

    return dt.strftime("%Y%m%d%H%M%S +0000")


def format_channel_name(channel_id):
    """
    Fallback name based only on the official channel ID.

    This is used only if the page does not expose
    another visible channel name.
    """

    if not channel_id:
        return "Unknown"

    name = channel_id

    name = re.sub(
        r"^beINSPORTS",
        "beIN SPORTS ",
        name,
        flags=re.IGNORECASE
    )

    name = name.replace("MAX", "MAX ")
    name = name.replace("XTRA", "XTRA ")

    name = re.sub(r"\s+", " ", name).strip()

    return name


# ============================================================
# DOWNLOAD OFFICIAL PAGE
# ============================================================

def download_page():
    print("Downloading official beIN TV guide...")

    response = requests.get(
        BASE_URL,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    print("HTTP:", response.status_code)
    print("Page size:", len(response.text))

    return response.text


# ============================================================
# PARSE CHANNELS AND PROGRAMS
# ============================================================

def parse_schedule(html):

    soup = BeautifulSoup(html, "html.parser")

    channel_rows = soup.select("div.channel-row")

    print("Channel rows found:", len(channel_rows))

    if not channel_rows:
        raise RuntimeError(
            "No channel-row elements found. "
            "The beIN page may require JavaScript."
        )

    channels = {}
    programmes = []

    # --------------------------------------------------------
    # Read every channel
    # --------------------------------------------------------

    for row in channel_rows:

        channel_link = row.select_one(".channel-col a[href]")

        if not channel_link:
            continue

        href = channel_link.get("href", "").strip()

        channel_id = get_channel_id(href)

        if not channel_id:
            continue

        # Official logo
        img = channel_link.select_one("img")

        logo_url = ""

        if img:
            logo_url = (
                img.get("src")
                or img.get("data-src")
                or ""
            ).strip()

        channel_name = format_channel_name(channel_id)

        channels[channel_id] = {
            "id": channel_id,
            "name": channel_name,
            "logo": logo_url,
            "bein_url": href,
        }

        # ----------------------------------------------------
        # Programs of this channel
        # ----------------------------------------------------

        program_blocks = row.select(
            ".row-timeline-track .prog-block"
        )

        for block in program_blocks:

            title = clean_text(
                block.get("data-full-title", "")
            )

            category = clean_text(
                block.get("data-full-category", "")
            )

            start_ms = block.get("data-start-ms")
            end_ms = block.get("data-end-ms")

            if not title:
                continue

            if not start_ms or not end_ms:
                continue

            try:
                start_ms = int(start_ms)
                end_ms = int(end_ms)
            except ValueError:
                continue

            if end_ms <= start_ms:
                continue

            programmes.append({
                "channel": channel_id,
                "title": title,
                "category": category,
                "start_ms": start_ms,
                "end_ms": end_ms,
            })

    return channels, programmes


# ============================================================
# REMOVE DUPLICATES
# ============================================================

def remove_duplicates(programmes):

    unique = {}

    for program in programmes:

        key = (
            program["channel"],
            program["start_ms"],
            program["end_ms"],
            program["title"],
        )

        unique[key] = program

    result = list(unique.values())

    result.sort(
        key=lambda x: (
            x["channel"],
            x["start_ms"]
        )
    )

    return result


# ============================================================
# WRITE XMLTV
# ============================================================

def write_xml(channels, programmes):

    print("Writing:", XML_OUTPUT)

    tv = ET.Element(
        "tv",
        {
            "generator-info-name": "DZGreen beIN EPG",
            "source-info-name": "beIN official TV Guide",
        }
    )

    # --------------------------------------------------------
    # Channels
    # --------------------------------------------------------

    for channel_id in sorted(channels):

        channel = channels[channel_id]

        channel_element = ET.SubElement(
            tv,
            "channel",
            {
                "id": channel["id"]
            }
        )

        display_name = ET.SubElement(
            channel_element,
            "display-name"
        )

        display_name.text = channel["name"]

        if channel["logo"]:

            ET.SubElement(
                channel_element,
                "icon",
                {
                    "src": channel["logo"]
                }
            )

    # --------------------------------------------------------
    # Programs
    # --------------------------------------------------------

    for program in programmes:

        programme_element = ET.SubElement(
            tv,
            "programme",
            {
                "start": epoch_to_xmltv(
                    program["start_ms"]
                ),
                "stop": epoch_to_xmltv(
                    program["end_ms"]
                ),
                "channel": program["channel"],
            }
        )

        title_element = ET.SubElement(
            programme_element,
            "title"
        )

        title_element.text = program["title"]

        # The official page provides category.
        # No invented description is added.

        if program["category"]:

            category_element = ET.SubElement(
                programme_element,
                "category"
            )

            category_element.text = program["category"]

    tree = ET.ElementTree(tv)

    ET.indent(tree, space="    ")

    tree.write(
        XML_OUTPUT,
        encoding="utf-8",
        xml_declaration=True
    )


# ============================================================
# WRITE CSV
# ============================================================

def write_csv(channels):

    print("Writing:", CSV_OUTPUT)

    with open(
        CSV_OUTPUT,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.writer(file)

        writer.writerow([
            "channel_name",
            "tvg_id",
            "logo_url",
            "bein_url",
        ])

        for channel_id in sorted(channels):

            channel = channels[channel_id]

            writer.writerow([
                channel["name"],
                channel["id"],
                channel["logo"],
                channel["bein_url"],
            ])


# ============================================================
# MAIN
# ============================================================

def main():

    start_time = time.time()

    print("=" * 60)
    print("DZGreen - Official beIN EPG")
    print("=" * 60)

    html = download_page()

    channels, programmes = parse_schedule(html)

    programmes = remove_duplicates(programmes)

    print()
    print("Channels :", len(channels))
    print("Programs :", len(programmes))
    print()

    if not channels:
        raise RuntimeError(
            "No channels were extracted."
        )

    if not programmes:
        raise RuntimeError(
            "No programs were extracted."
        )

    write_xml(
        channels,
        programmes
    )

    write_csv(
        channels
    )

    elapsed = time.time() - start_time

    print()
    print("=" * 60)
    print("DONE")
    print("=" * 60)
    print("XML :", XML_OUTPUT)
    print("CSV :", CSV_OUTPUT)
    print("Time:", round(elapsed, 2), "seconds")


if __name__ == "__main__":
    main()
