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

CATEGORIES = [
    ("sports", "رياضة"),
    ("entertainment", "ترفيه"),
]

# beIN guide times are displayed in UTC+03:00.
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
    """Extract the final path component from a beIN CONNECT URL."""
    if not href:
        return ""

    try:
        path = unquote(urlparse(href).path).strip("/")
        if path:
            return path.split("/")[-1].strip()
    except Exception:
        pass

    return ""


def get_channel_name_from_logo(logo_url):
    """Create a readable fallback channel ID/name from a logo filename."""
    if not logo_url:
        return ""

    try:
        filename = unquote(urlparse(logo_url).path.split("/")[-1])
        filename = re.sub(
            r"\.(png|jpg|jpeg|webp|svg)$",
            "",
            filename,
            flags=re.IGNORECASE,
        )
        filename = re.sub(r"^2023_", "", filename, flags=re.IGNORECASE)
        filename = re.sub(
            r"(_Digital_Mono|_DIGITAL_Mono|_Mono|_Digital)$",
            "",
            filename,
            flags=re.IGNORECASE,
        )
        return clean_text(filename.replace("_", " "))
    except Exception:
        return ""


def get_channel_identity(row):
    """Read a channel identity from one .channel-row element."""
    channel_id = ""
    channel_name = ""
    bein_url = ""
    logo_url = ""

    try:
        img = row.locator(".channel-col img").first
        if img.count() > 0:
            logo_url = img.get_attribute("src", timeout=5000) or ""
    except Exception:
        logo_url = ""

    try:
        link = row.locator(".channel-col a[href]").first
        if link.count() > 0:
            href = link.get_attribute("href", timeout=5000) or ""
            if href:
                bein_url = href
                channel_id = get_channel_id_from_href(href)
    except Exception:
        pass

    if not channel_id:
        channel_name = get_channel_name_from_logo(logo_url)
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
    """Convert a millisecond Unix timestamp to UTC+03:00."""
    return datetime.fromtimestamp(
        int(ms) / 1000,
        tz=timezone.utc,
    ).astimezone(BEIN_TZ)


def xmltv_datetime(dt):
    """Return XMLTV date/time format, e.g. 20261009200000 +0300."""
    return dt.strftime("%Y%m%d%H%M%S %z")


# ============================================================
# XML Writer
# ============================================================

def create_xml(channels, programs):
    print("Creating:", OUTPUT_XML)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<tv generator-info-name="DZGreen Official beIN EPG" '
        'source-info-name="beIN">',
    ]

    for channel_id in sorted(channels):
        channel = channels[channel_id]
        lines.append(f'  <channel id="{xml_escape(channel_id)}">')
        lines.append(
            f'    <display-name>{xml_escape(channel["channel_name"])}</display-name>'
        )
        if channel.get("logo_url"):
            lines.append(
                f'    <icon src="{xml_escape(channel["logo_url"])}"/>'
            )
        lines.append("  </channel>")

    for program in sorted(
        programs,
        key=lambda item: (item["channel_id"], item["start_ms"]),
    ):
        start_dt = ms_to_datetime(program["start_ms"])
        end_dt = ms_to_datetime(program["end_ms"])
        start = xmltv_datetime(start_dt)
        stop = xmltv_datetime(end_dt)

        channel_id = program["channel_id"]
        title = program["title"]

        # XMLTV uses "stop" for the programme end time.
        lines.append(
            f'  <programme start="{start}" '
            f'stop="{stop}" '
            f'channel="{xml_escape(channel_id)}">'
        )
        lines.append(
            f'    <title lang="ar">{xml_escape(title)}</title>'
        )
        lines.append("  </programme>")

    lines.append("</tv>")
    Path(OUTPUT_XML).write_text("\n".join(lines) + "\n", encoding="utf-8")
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
        encoding="utf-8-sig",
    ) as file:
        writer = csv.writer(file)
        writer.writerow([
            "channel_name",
            "tvg_id",
            "logo_url",
            "bein_url",
            "category",
        ])

        for channel_id in sorted(channels):
            channel = channels[channel_id]
            writer.writerow([
                channel["channel_name"],
                channel_id,
                channel.get("logo_url", ""),
                channel.get("bein_url", ""),
                channel.get("category", ""),
            ])

    print("CSV created successfully.")


# ============================================================
# Safe programme attribute reader
# ============================================================

def read_program_attributes(prog):
    """
    Read programme attributes safely.
    If the website replaces/removes a programme element while the page is
    updating, return None instead of stopping the whole workflow.
    """
    try:
        title = clean_text(
            prog.get_attribute("data-full-title", timeout=5000) or ""
        )
        category = clean_text(
            prog.get_attribute("data-full-category", timeout=5000) or ""
        )
        start_ms = prog.get_attribute("data-start-ms", timeout=5000)
        end_ms = prog.get_attribute("data-end-ms", timeout=5000)

        if not title or not start_ms or not end_ms:
            return None

        start_ms_int = int(start_ms)
        end_ms_int = int(end_ms)

        if end_ms_int <= start_ms_int:
            return None

        return {
            "title": title,
            "category": category,
            "start_ms": start_ms_int,
            "end_ms": end_ms_int,
        }

    except Exception as error:
        print(f"WARNING: Could not read a programme block: {error}")
        return None


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
    program_keys = set()
    dates = []

    with sync_playwright() as playwright:
        print("Launching Chromium...")
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1600, "height": 1000},
        )
        page.set_default_timeout(10000)

        try:
            print("Opening official beIN website...")
            page.goto(
                BASE_URL,
                wait_until="networkidle",
                timeout=120000,
            )
            print("Page loaded.")

            print("Reading available dates...")
            page.wait_for_selector(".day-cell", timeout=60000)
            date_cells = page.locator(".day-cell")

            for index in range(date_cells.count()):
                try:
                    date_value = date_cells.nth(index).get_attribute(
                        "data-date",
                        timeout=5000,
                    )
                    if date_value:
                        dates.append(date_value)
                except Exception as error:
                    print(f"WARNING: Could not read date cell: {error}")

            dates = list(dict.fromkeys(dates))
            print("Days found:", len(dates))
            print("Available dates:")
            for date_value in dates:
                print("  -", date_value)

            if not dates:
                raise RuntimeError(
                    "No available dates found; the beIN page structure may have changed."
                )

            for category_code, category_name in CATEGORIES:
                print()
                print("#" * 60)
                print(f"CATEGORY: {category_name} ({category_code})")
                print("#" * 60)

                category_button = page.locator(
                    f'.category-tab[data-category="{category_code}"]'
                ).first

                if category_button.count() == 0:
                    print("WARNING: Category button not found:", category_code)
                    continue

                category_button.click()
                page.wait_for_timeout(1500)
                print("Category loaded:", category_name)

                category_channel_count_before = len(channels)
                category_program_count_before = len(programs)

                for date_value in dates:
                    print("=" * 60)
                    print("Selecting date:", date_value)
                    print("=" * 60)

                    date_cell = page.locator(
                        f'.day-cell[data-date="{date_value}"]'
                    ).first

                    if date_cell.count() == 0:
                        print("WARNING: Date not found:", date_value)
                        continue

                    try:
                        date_cell.click(timeout=10000)
                    except Exception as error:
                        print(f"WARNING: Could not select {date_value}: {error}")
                        continue

                    # Give the guide time to update after selecting a date.
                    page.wait_for_timeout(1500)

                    rows = page.locator(".channel-row")
                    row_count = rows.count()

                    print(f"Extracting: {category_name} | {date_value}")
                    print("Channel rows:", row_count)

                    new_channels_this_date = 0
                    programs_this_date = 0

                    for row_index in range(row_count):
                        try:
                            row = rows.nth(row_index)
                            identity = get_channel_identity(row)
                        except Exception as error:
                            print(
                                f"WARNING: Could not read channel row "
                                f"{row_index + 1}: {error}"
                            )
                            continue

                        channel_id = identity["channel_id"]
                        if not channel_id:
                            print(
                                f"WARNING: Could not identify channel row "
                                f"{row_index + 1}"
                            )
                            continue

                        if channel_id not in channels:
                            channels[channel_id] = {
                                "channel_name": identity["channel_name"],
                                "logo_url": identity["logo_url"],
                                "bein_url": identity["bein_url"],
                                "category": category_name,
                            }
                            new_channels_this_date += 1
                        else:
                            existing = channels[channel_id]
                            if not existing.get("logo_url") and identity["logo_url"]:
                                existing["logo_url"] = identity["logo_url"]
                            if not existing.get("bein_url") and identity["bein_url"]:
                                existing["bein_url"] = identity["bein_url"]

                        try:
                            prog_blocks = row.locator(".prog-block")
                            prog_count = prog_blocks.count()
                        except Exception as error:
                            print(
                                f"WARNING: Could not list programmes for "
                                f"{channel_id}: {error}"
                            )
                            continue

                        for prog_index in range(prog_count):
                            try:
                                prog = prog_blocks.nth(prog_index)
                                data = read_program_attributes(prog)
                            except Exception as error:
                                print(
                                    f"WARNING: Skipping unreadable programme "
                                    f"channel={channel_id}, index={prog_index + 1}: "
                                    f"{error}"
                                )
                                continue

                            if not data:
                                continue

                            program_key = (
                                channel_id,
                                data["start_ms"],
                                data["end_ms"],
                                data["title"],
                            )

                            if program_key in program_keys:
                                continue

                            program_keys.add(program_key)
                            programs.append({
                                "channel_id": channel_id,
                                "title": data["title"],
                                "category": data["category"],
                                "start_ms": data["start_ms"],
                                "end_ms": data["end_ms"],
                            })
                            programs_this_date += 1

                    print("New channels:", new_channels_this_date)
                    print("Programs extracted:", programs_this_date)

                print("CATEGORY SUMMARY:", category_name)
                print(
                    "Channels added:",
                    len(channels) - category_channel_count_before,
                )
                print(
                    "Programs added:",
                    len(programs) - category_program_count_before,
                )

        finally:
            browser.close()

    print()
    print("=" * 60)
    print("EXTRACTION SUMMARY")
    print("=" * 60)
    print("Channels :", len(channels))
    print("Programs :", len(programs))
    print("Dates    :", len(dates))

    if not channels:
        raise RuntimeError("No channels were extracted; output files were not created.")
    if not programs:
        raise RuntimeError("No programmes were extracted; output files were not created.")

    create_xml(channels, programs)
    create_csv(channels)

    print("=" * 60)
    print("SUCCESS")
    print("=" * 60)
    print("Created:", OUTPUT_XML)
    print("Created:", OUTPUT_CSV)
    print(f"Execution time: {time.time() - start_time:.2f} seconds")


if __name__ == "__main__":
    main()
