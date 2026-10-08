import csv
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, unquote

from playwright.sync_api import sync_playwright


# ============================================================
# DZGreen - Official beIN EPG
# Source: Official beIN TV Guide
# ============================================================

BASE_URL = "https://www.bein.com/ar/%d8%ac%d8%af%d9%88%d9%84-%d8%a7%d9%84%d8%a8%d8%ab/?c=dz&"

OUTPUT_XML = "BeIN-EPG.xml"
OUTPUT_CSV = "BeIN-Channels.csv"

# Official beIN guide categories
CATEGORIES = [
    ("sports", "رياضة"),
    ("entertainment", "ترفيه"),
]

# Official beIN guide timezone:
# displayed times are Qatar/Makkah = UTC+03:00
BEIN_TZ = timezone(timedelta(hours=3))


# ============================================================
# Helpers
# ============================================================

def clean_text(value):
    if not value:
        return ""
    return " ".join(value.split()).strip()


def xml_escape(value):
    value = str(value or "")
    return (
        value.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&apos;")
    )


def get_channel_id_from_href(href):
    """
    Extract channel ID from official beIN CONNECT URL.

    Example:
    https://beinconnect.app/beINSPORTS1
    -> beINSPORTS1
    """

    if not href:
        return ""

    try:
        parsed = urlparse(href)
        path = unquote(parsed.path).strip("/")

        if not path:
            return ""

        channel_id = path.split("/")[-1].strip()

        if channel_id:
            return channel_id

    except Exception:
        pass

    return ""


def get_channel_name_from_logo(logo_url):
    """
    Extract a readable channel name from the official logo filename.

    Examples:

    2023_Alkass_7.png
    -> Alkass 7

    2023_Alkass_2.png
    -> Alkass 2

    beIN_SPORTS1_ENGLISH_Digital_Mono.png
    -> beIN SPORTS1 ENGLISH
    """

    if not logo_url:
        return ""

    try:
        filename = unquote(urlparse(logo_url).path.split("/")[-1])

        # Remove extension
        filename = re.sub(r"\.(png|jpg|jpeg|webp|svg)$", "", filename,
                          flags=re.IGNORECASE)

        # Remove common year prefix
        filename = re.sub(r"^2023_", "", filename, flags=re.IGNORECASE)

        # Remove common logo suffixes
        filename = re.sub(
            r"(_Digital_Mono|_DIGITAL_Mono|_Mono|_Digital)$",
            "",
            filename,
            flags=re.IGNORECASE
        )

        # Alkass -> AlKass style readable name
        filename = filename.replace("_", " ")

        # Clean multiple spaces
        filename = clean_text(filename)

        return filename

    except Exception:
        return ""


def get_channel_identity(row):
    """
    Determine channel identity.

    Priority:
    1. Official channel href
    2. Official logo filename

    Returns:
        channel_id
        channel_name
        logo_url
        bein_url
    """

    channel_id = ""
    channel_name = ""
    bein_url = ""
    logo_url = ""

    # --------------------------------------------------------
    # Logo
    # --------------------------------------------------------

    img = row.locator(".channel-col img").first

    if img.count() > 0:
        logo_url = img.get_attribute("src") or ""

    # --------------------------------------------------------
    # Official href
    # --------------------------------------------------------

    link = row.locator(".channel-col a[href]").first

    if link.count() > 0:
        href = link.get_attribute("href") or ""

        channel_id = get_channel_id_from_href(href)

        if href:
            bein_url = href

    # --------------------------------------------------------
    # If no href, use logo filename
    # --------------------------------------------------------

    if not channel_id:
        channel_name = get_channel_name_from_logo(logo_url)

        if channel_name:
            channel_id = channel_name

    else:
        channel_name = channel_id

    return {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "logo_url": logo_url,
        "bein_url": bein_url,
    }


def ms_to_datetime(ms):
    """
    Convert official data-start-ms/data-end-ms
    to timezone-aware datetime in UTC+03:00.
    """

    return datetime.fromtimestamp(
        int(ms) / 1000,
        tz=timezone.utc
    ).astimezone(BEIN_TZ)


def xmltv_datetime(dt):
    """
    XMLTV format:
    YYYYMMDDHHMMSS +0300
    """

    return dt.strftime("%Y%m%d%H%M%S %z")


# ============================================================
# XML Writer
# ============================================================

def create_xml(channels, programs):
    print("Creating:", OUTPUT_XML)

    lines = []

    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(
        '<tv generator-info-name="DZGreen Official beIN EPG" '
        'source-info-name="beIN">'
    )

    # --------------------------------------------------------
    # Channels
    # --------------------------------------------------------

    for channel_id in sorted(channels.keys()):
        channel = channels[channel_id]

        lines.append(
            f'  <channel id="{xml_escape(channel_id)}">'
        )

        lines.append(
            f'    <display-name>{xml_escape(channel["channel_name"])}</display-name>'
        )

        if channel["logo_url"]:
            lines.append(
                f'    <icon src="{xml_escape(channel["logo_url"])}"/>'
            )

        lines.append("  </channel>")

    # --------------------------------------------------------
    # Programs
    # --------------------------------------------------------

    for program in sorted(
        programs,
        key=lambda x: (
            x["channel_id"],
            x["start_ms"]
        )
    ):
        start_dt = ms_to_datetime(program["start_ms"])
        end_dt = ms_to_datetime(program["end_ms"])

        start = xmltv_datetime(start_dt)
        end = xmltv_datetime(end_dt)

        channel_id = program["channel_id"]
        title = program["title"]

        lines.append(
            f'  <programme start="{start}" '
            f'end="{end}" '
            f'channel="{xml_escape(channel_id)}">'
        )

        lines.append(
            f'    <title lang="ar">{xml_escape(title)}</title>'
        )

        lines.append("  </programme>")

    lines.append("</tv>")

    Path(OUTPUT_XML).write_text(
        "\n".join(lines),
        encoding="utf-8"
    )

    print("XML created successfully.")


# ============================================================
# CSV Writer
# ============================================================

def create_csv(channels):
    print("Creating:", OUTPUT_CSV)

    with open(
        OUTPUT_CSV,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "channel_name",
            "tvg_id",
            "logo_url",
            "bein_url",
            "category",
        ])

        for channel_id in sorted(channels.keys()):

            channel = channels[channel_id]

            writer.writerow([
                channel["channel_name"],
                channel_id,
                channel["logo_url"],
                channel["bein_url"],
                channel["category"],
            ])

    print("CSV created successfully.")


# ============================================================
# Main scraper
# ============================================================

def main():

    start_time = time.time()

    print("=" * 60)
    print("DZGreen - Official beIN EPG")
    print("Sports + Entertainment")
    print("=" * 60)

    channels = {}
    programs = []

    # Used to avoid duplicate programs
    program_keys = set()

    with sync_playwright() as p:

        print("Launching Chromium...")

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            viewport={
                "width": 1600,
                "height": 1000
            }
        )

        print("Opening official beIN website...")

        page.goto(
            BASE_URL,
            wait_until="networkidle",
            timeout=120000
        )

        print("Page loaded.")

        # ----------------------------------------------------
        # Read dates
        # ----------------------------------------------------

        print("Reading available dates...")

        page.wait_for_selector(
            ".day-cell",
            timeout=60000
        )

        date_cells = page.locator(".day-cell")

        date_count = date_cells.count()

        print("Days found:", date_count)

        dates = []

        for i in range(date_count):

            cell = date_cells.nth(i)

            date_value = cell.get_attribute("data-date")

            if date_value:
                dates.append(date_value)

        dates = list(dict.fromkeys(dates))

        print("Available dates:")

        for d in dates:
            print("  -", d)

        # ====================================================
        # Categories
        # ====================================================

        for category_code, category_name in CATEGORIES:

            print()
            print("#" * 60)
            print(
                f"CATEGORY: {category_name} ({category_code})"
            )
            print("#" * 60)

            # ------------------------------------------------
            # Select category
            # ------------------------------------------------

            print("-" * 60)
            print(
                f"Selecting category: "
                f"{category_name} ({category_code})"
            )
            print("-" * 60)

            category_button = page.locator(
                f'.category-tab[data-category="{category_code}"]'
            ).first

            if category_button.count() == 0:

                print(
                    "WARNING: Category button not found:",
                    category_code
                )

                continue

            category_button.click()

            page.wait_for_timeout(1500)

            print(
                "Category loaded:",
                category_name
            )

            category_channel_count_before = len(channels)
            category_program_count_before = len(programs)

            # ================================================
            # Dates
            # ================================================

            for date_value in dates:

                print("=" * 60)
                print(
                    "Selecting date:",
                    date_value
                )
                print("=" * 60)

                # --------------------------------------------
                # Re-find date cell after category change
                # --------------------------------------------

                date_cell = page.locator(
                    f'.day-cell[data-date="{date_value}"]'
                ).first

                if date_cell.count() == 0:

                    print(
                        "WARNING: Date not found:",
                        date_value
                    )

                    continue

                date_cell.click()

                page.wait_for_timeout(1200)

                print(
                    "Date loaded:",
                    date_value
                )

                # --------------------------------------------
                # Channel rows
                # --------------------------------------------

                rows = page.locator(
                    ".channel-row"
                )

                row_count = rows.count()

                print(
                    f"Extracting: "
                    f"{category_name} | {date_value}"
                )

                print(
                    "Channel rows:",
                    row_count
                )

                new_channels_this_date = 0
                programs_this_date = 0

                # ============================================
                # Process every channel-row
                # ============================================

                for row_index in range(row_count):

                    row = rows.nth(row_index)

                    identity = get_channel_identity(row)

                    channel_id = identity["channel_id"]

                    # ------------------------------------------------
                    # IMPORTANT:
                    # Do NOT skip the row simply because there is
                    # no <a href>.
                    # Logo is now accepted as fallback identity.
                    # ------------------------------------------------

                    if not channel_id:

                        print(
                            f"WARNING: Could not identify "
                            f"channel row {row_index + 1}"
                        )

                        continue

                    # ------------------------------------------------
                    # Save channel
                    # ------------------------------------------------

                    if channel_id not in channels:

                        channels[channel_id] = {
                            "channel_name": identity["channel_name"],
                            "logo_url": identity["logo_url"],
                            "bein_url": identity["bein_url"],
                            "category": category_name,
                        }

                        new_channels_this_date += 1

                    else:

                        # Update missing information if available
                        existing = channels[channel_id]

                        if (
                            not existing["logo_url"]
                            and identity["logo_url"]
                        ):
                            existing["logo_url"] = identity["logo_url"]

                        if (
                            not existing["bein_url"]
                            and identity["bein_url"]
                        ):
                            existing["bein_url"] = identity["bein_url"]

                    # ============================================
                    # Programs
                    # ============================================

                    prog_blocks = row.locator(
                        ".prog-block"
                    )

                    prog_count = prog_blocks.count()

                    for prog_index in range(prog_count):

                        prog = prog_blocks.nth(prog_index)

                        title = clean_text(
                            prog.get_attribute(
                                "data-full-title"
                            )
                            or ""
                        )

                        category = clean_text(
                            prog.get_attribute(
                                "data-full-category"
                            )
                            or ""
                        )

                        start_ms = prog.get_attribute(
                            "data-start-ms"
                        )

                        end_ms = prog.get_attribute(
                            "data-end-ms"
                        )

                        if not title:
                            continue

                        if not start_ms or not end_ms:
                            continue

                        try:
                            start_ms_int = int(start_ms)
                            end_ms_int = int(end_ms)

                        except ValueError:
                            continue

                        # ------------------------------------------------
                        # Unique program key
                        # ------------------------------------------------

                        program_key = (
                            channel_id,
                            start_ms_int,
                            end_ms_int,
                            title,
                        )

                        if program_key in program_keys:
                            continue

                        program_keys.add(program_key)

                        programs.append({
                            "channel_id": channel_id,
                            "title": title,
                            "category": category,
                            "start_ms": start_ms_int,
                            "end_ms": end_ms_int,
                        })

                        programs_this_date += 1

                print(
                    "New channels:",
                    new_channels_this_date
                )

                print(
                    "Programs extracted:",
                    programs_this_date
                )

            # ----------------------------------------------------
            # Category summary
            # ----------------------------------------------------

            print(
                "CATEGORY SUMMARY:",
                category_name
            )

            print(
                "Channels added:",
                len(channels) - category_channel_count_before
            )

            print(
                "Programs added:",
                len(programs) - category_program_count_before
            )

        browser.close()

    # ========================================================
    # Final output
    # ========================================================

    print()
    print("=" * 60)
    print("EXTRACTION SUMMARY")
    print("=" * 60)

    print(
        "Channels :",
        len(channels)
    )

    print(
        "Programs :",
        len(programs)
    )

    print(
        "Dates    :",
        len(dates)
    )

    create_xml(
        channels,
        programs
    )

    create_csv(
        channels
    )

    print("=" * 60)
    print("SUCCESS")
    print("=" * 60)

    print(
        "Created:",
        OUTPUT_XML
    )

    print(
        "Created:",
        OUTPUT_CSV
    )

    execution_time = time.time() - start_time

    print(
        f"Execution time: {execution_time:.2f} seconds"
    )


if __name__ == "__main__":
    main()
