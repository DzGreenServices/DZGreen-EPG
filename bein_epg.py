
import csv
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


# ============================================================
# CONFIGURATION
# ============================================================

PAGE_URL = "https://www.bein.com/en/tv-guide/?c=dz&"

OUTPUT_DIR = Path("docs")
XML_FILE = OUTPUT_DIR / "BeIN-EPG.xml"
CSV_FILE = OUTPUT_DIR / "BeIN-Channel-Mapping.csv"

CATEGORIES = ("sports", "entertainment")
DAYS_TO_FETCH = 4

CONTENT_TIMEOUT_MS = 20000
PAGE_TIMEOUT_MS = 60000


# ============================================================
# TEXT AND DATE HELPERS
# ============================================================

def clean_text(value):
    """Clean text extracted from HTML."""

    if value is None:
        return ""

    value = BeautifulSoup(str(value), "html.parser").get_text(
        separator=" ",
        strip=True,
    )

    return re.sub(r"\s+", " ", value).strip()


def timestamp_to_xmltv(value):
    """Convert a Unix timestamp in milliseconds to XMLTV UTC."""

    try:
        timestamp_ms = int(value)

        dt = datetime.fromtimestamp(
            timestamp_ms / 1000,
            tz=timezone.utc,
        )

        return dt.strftime("%Y%m%d%H%M%S +0000")

    except (ValueError, TypeError, OverflowError, OSError):
        return None


def get_channel_id(url):
    """Generate a stable channel ID from its URL."""

    path = urlparse(url).path.strip("/")

    if not path:
        return ""

    slug = path.split("/")[-1].lower()

    slug = re.sub(r"[^a-z0-9._-]+", "", slug)

    return slug


def get_channel_name(anchor, image, url):
    """Extract the best available channel name."""

    candidates = [
        anchor.get("data-full-title"),
        anchor.get("title"),
        anchor.get("aria-label"),
        image.get("alt") if image else None,
        anchor.get_text(" ", strip=True),
    ]

    for candidate in candidates:
        name = clean_text(candidate)

        if name:
            return name

    slug = urlparse(url).path.strip("/").split("/")[-1]

    return slug.replace("_", " ").replace("-", " ").title()


# ============================================================
# CHANNEL AND PROGRAMME EXTRACTION
# ============================================================

def parse_channel_rows(html, category, date_value):
    """Extract channels and programmes from the current page."""

    soup = BeautifulSoup(html, "html.parser")

    rows = soup.select("#channelRows .channel-row")

    channels = []

    for row in rows:
        anchor = row.select_one(".channel-col a")

        if anchor is None:
            anchor = row.select_one("a[href]")

        if anchor is None:
            continue

        channel_url = urljoin(
            PAGE_URL,
            anchor.get("href", "").strip(),
        )

        channel_id = get_channel_id(channel_url)

        if not channel_id:
            continue

        image = anchor.select_one("img")

        logo_url = ""

        if image:
            logo_url = (
                image.get("src")
                or image.get("data-src")
                or image.get("data-lazy-src")
                or ""
            )

            if logo_url:
                logo_url = urljoin(PAGE_URL, logo_url)

        channel_name = get_channel_name(
            anchor,
            image,
            channel_url,
        )

        programmes = []

        blocks = row.select(
            '.prog-block[data-start-ms][data-end-ms]'
        )

        for block in blocks:
            title = clean_text(
                block.get("data-full-title")
                or block.get("title")
                or block.get_text(" ", strip=True)
            )

            start_value = block.get("data-start-ms")
            end_value = block.get("data-end-ms")

            if not title or not start_value or not end_value:
                continue

            try:
                start_ms = int(start_value)
                end_ms = int(end_value)
            except (ValueError, TypeError):
                continue

            if end_ms <= start_ms:
                continue

            start_xmltv = timestamp_to_xmltv(start_ms)
            end_xmltv = timestamp_to_xmltv(end_ms)

            if not start_xmltv or not end_xmltv:
                continue

            programme_category = clean_text(
                block.get("data-full-category")
                or category
            )

            description = clean_text(
                block.get("data-full-description")
                or block.get("data-description")
                or ""
            )

            programmes.append({
                "start": start_xmltv,
                "stop": end_xmltv,
                "title": title,
                "category": programme_category,
                "description": description,
                "start_ms": start_ms,
                "end_ms": end_ms,
            })

        channels.append({
            "id": channel_id,
            "name": channel_name,
            "url": channel_url,
            "logo": logo_url,
            "category": category,
            "date": date_value,
            "programmes": programmes,
        })

    return channels


# ============================================================
# WAIT FOR PAGE CONTENT
# ============================================================

def wait_for_content_change(
    page,
    previous_html,
    active_selector,
    attribute,
    value,
    timeout_ms,
):
    """Wait until the selected control is active and content changes."""

    page.wait_for_function(
        """arg => {
            const active = document.querySelector(arg.selector);
            const root = document.querySelector("#channelRows");

            if (!active || !root) {
                return false;
            }

            if (!active.classList.contains("active")) {
                return false;
            }

            if (active.getAttribute(arg.attribute) !== arg.value) {
                return false;
            }

            const rows = root.querySelectorAll(".channel-row");

            const programmes = root.querySelectorAll(
                '.prog-block[data-start-ms][data-end-ms]'
            );

            if (rows.length === 0 || programmes.length === 0) {
                return false;
            }

            return root.innerHTML !== arg.previousHtml;
        }""",
        arg={
            "selector": active_selector,
            "attribute": attribute,
            "value": value,
            "previousHtml": previous_html,
        },
        timeout=timeout_ms,
    )


# ============================================================
# CATEGORY ACTIVATION
# ============================================================

def activate_category(page, category):
    """Activate Sports or Entertainment and verify its content."""

    selector = f'.category-tab[data-category="{category}"]'

    button = page.locator(selector)

    if button.count() == 0:
        raise RuntimeError(
            f"Category button not found: {category}"
        )

    root = page.locator("#channelRows")

    previous_html = root.inner_html()

    button_class = button.get_attribute("class") or ""

    was_active = "active" in button_class.split()

    if not was_active:
        button.click()

    try:
        if not was_active:
            wait_for_content_change(
                page=page,
                previous_html=previous_html,
                active_selector=selector,
                attribute="data-category",
                value=category,
                timeout_ms=CONTENT_TIMEOUT_MS,
            )
        else:
            page.wait_for_function(
                """arg => {
                    const button = document.querySelector(arg.selector);
                    const root = document.querySelector("#channelRows");

                    return button
                        && button.classList.contains("active")
                        && root
                        && root.querySelectorAll(".channel-row").length > 0
                        && root.querySelector(
                            '.prog-block[data-start-ms][data-end-ms]'
                        );
                }""",
                arg={"selector": selector},
                timeout=CONTENT_TIMEOUT_MS,
            )

    except PlaywrightTimeoutError:
        print(
            f"Warning: normal category wait timed out for {category}. "
            "Checking current page content...",
            flush=True,
        )

        page.wait_for_function(
            """arg => {
                const button = document.querySelector(arg.selector);
                const root = document.querySelector("#channelRows");

                if (
                    !button ||
                    !button.classList.contains("active") ||
                    !root
                ) {
                    return false;
                }

                return root.querySelectorAll(".channel-row").length > 0
                    && root.querySelector(
                        '.prog-block[data-start-ms][data-end-ms]'
                    );
            }""",
            arg={"selector": selector},
            timeout=CONTENT_TIMEOUT_MS,
        )

    channel_count = root.locator(".channel-row").count()

    programme_count = root.locator(
        '.prog-block[data-start-ms][data-end-ms]'
    ).count()

    print(
        f"Category activated: {category} | "
        f"Channels: {channel_count} | "
        f"Programmes: {programme_count}",
        flush=True,
    )


# ============================================================
# AVAILABLE DATES
# ============================================================

def get_available_dates(page):
    """Read the dates displayed in the date strip."""

    dates = page.locator(
        "#dayStrip .day-cell[data-date]"
    ).evaluate_all(
        """elements => elements.map(
            element => element.getAttribute("data-date")
        ).filter(Boolean)"""
    )

    dates = list(dict.fromkeys(dates))

    if not dates:
        raise RuntimeError(
            "No available dates found in #dayStrip."
        )

    return dates[:DAYS_TO_FETCH]


def activate_date(page, date_value):
    """Select a date and wait for its programmes to load."""

    selector = f'.day-cell[data-date="{date_value}"]'

    cell = page.locator(selector)

    if cell.count() == 0:
        raise RuntimeError(
            f"Date not found on page: {date_value}"
        )

    root = page.locator("#channelRows")

    previous_html = root.inner_html()

    cell_class = cell.get_attribute("class") or ""

    was_active = "active" in cell_class.split()

    if not was_active:
        cell.click()

        try:
            wait_for_content_change(
                page=page,
                previous_html=previous_html,
                active_selector=selector,
                attribute="data-date",
                value=date_value,
                timeout_ms=CONTENT_TIMEOUT_MS,
            )

        except PlaywrightTimeoutError:
            print(
                f"Warning: content-change wait timed out for "
                f"{date_value}; checking current content...",
                flush=True,
            )

            page.wait_for_function(
                """arg => {
                    const cell = document.querySelector(arg.selector);
                    const root = document.querySelector("#channelRows");

                    return cell
                        && cell.classList.contains("active")
                        && root
                        && root.querySelectorAll(".channel-row").length > 0
                        && root.querySelector(
                            '.prog-block[data-start-ms][data-end-ms]'
                        );
                }""",
                arg={"selector": selector},
                timeout=CONTENT_TIMEOUT_MS,
            )

    else:
        page.wait_for_function(
            """arg => {
                const cell = document.querySelector(arg.selector);
                const root = document.querySelector("#channelRows");

                return cell
                    && cell.classList.contains("active")
                    && root
                    && root.querySelectorAll(".channel-row").length > 0
                    && root.querySelector(
                        '.prog-block[data-start-ms][data-end-ms]'
                    );
            }""",
            arg={"selector": selector},
            timeout=CONTENT_TIMEOUT_MS,
        )

    print(
        f"Date activated: {date_value}",
        flush=True,
    )


# ============================================================
# XMLTV GENERATION
# ============================================================

def write_xml(channels):
    """Write the XMLTV EPG file."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    tv = ET.Element("tv", {
        "generator-info-name": "DZGreen beIN EPG",
        "generator-info-url": PAGE_URL,
    })

    unique_channels = {}

    for channel in channels:
        channel_id = channel["id"]

        if channel_id not in unique_channels:
            unique_channels[channel_id] = channel

    for channel_id, channel in sorted(
        unique_channels.items(),
        key=lambda item: item[0],
    ):
        channel_element = ET.SubElement(
            tv,
            "channel",
            {"id": channel_id},
        )

        display_name = ET.SubElement(
            channel_element,
            "display-name",
            {"lang": "en"},
        )
        display_name.text = channel["name"]

        if channel["logo"]:
            ET.SubElement(
                channel_element,
                "icon",
                {"src": channel["logo"]},
            )

    seen_programmes = set()

    for channel in channels:
        channel_id = channel["id"]

        for programme in channel["programmes"]:
            unique_key = (
                channel_id,
                programme["start"],
                programme["stop"],
                programme["title"],
            )

            if unique_key in seen_programmes:
                continue

            seen_programmes.add(unique_key)

            programme_element = ET.SubElement(
                tv,
                "programme",
                {
                    "start": programme["start"],
                    "stop": programme["stop"],
                    "channel": channel_id,
                },
            )

            title_element = ET.SubElement(
                programme_element,
                "title",
                {"lang": "en"},
            )
            title_element.text = programme["title"]

            if programme["category"]:
                category_element = ET.SubElement(
                    programme_element,
                    "category",
                    {"lang": "en"},
                )
                category_element.text = programme["category"]

            if programme["description"]:
                description_element = ET.SubElement(
                    programme_element,
                    "desc",
                    {"lang": "en"},
                )
                description_element.text = programme["description"]

    tree = ET.ElementTree(tv)

    ET.indent(tree, space="  ")

    tree.write(
        XML_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    print(
        f"XMLTV saved: {XML_FILE} | "
        f"Channels: {len(unique_channels)} | "
        f"Programmes: {len(seen_programmes)}",
        flush=True,
    )


# ============================================================
# CSV MAPPING GENERATION
# ============================================================

def write_csv(channels):
    """Write the channel mapping CSV file."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    unique_channels = {}

    for channel in channels:
        channel_id = channel["id"]

        if channel_id not in unique_channels:
            unique_channels[channel_id] = channel

    with CSV_FILE.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as csv_file:

        writer = csv.writer(csv_file)

        writer.writerow([
            "channel_id",
            "channel_name",
            "channel_url",
            "logo_url",
            "category",
        ])

        for channel_id, channel in sorted(
            unique_channels.items(),
            key=lambda item: item[0],
        ):
            writer.writerow([
                channel_id,
                channel["name"],
                channel["url"],
                channel["logo"],
                channel["category"],
            ])

    print(
        f"CSV saved: {CSV_FILE} | "
        f"Channels: {len(unique_channels)}",
        flush=True,
    )


# ============================================================
# MAIN
# ============================================================

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_channels = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
        )

        page = browser.new_page(
            locale="en-US",
        )

        page.set_default_timeout(CONTENT_TIMEOUT_MS)

        print(
            "Opening the beIN TV Guide...",
            flush=True,
        )

        page.goto(
            PAGE_URL,
            wait_until="domcontentloaded",
            timeout=PAGE_TIMEOUT_MS,
        )

        page.wait_for_selector(
            "#channelRows .channel-row",
            timeout=PAGE_TIMEOUT_MS,
        )

        page.wait_for_selector(
            '.category-tab[data-category="sports"]',
            timeout=PAGE_TIMEOUT_MS,
        )

        page.wait_for_selector(
            '.category-tab[data-category="entertainment"]',
            timeout=PAGE_TIMEOUT_MS,
        )

        available_dates = get_available_dates(page)

        print(
            "Available dates: " + ", ".join(available_dates),
            flush=True,
        )

        for category in CATEGORIES:
            print(
                f"\n=== {category.upper()} ===",
                flush=True,
            )

            activate_category(page, category)

            for index, date_value in enumerate(
                available_dates,
                start=1,
            ):
                print(
                    f"[{index}/{len(available_dates)}] "
                    f"{category} - {date_value}",
                    flush=True,
                )

                if index > 1:
                    activate_date(page, date_value)

                html = page.locator(
                    "#channelRows"
                ).inner_html()

                extracted_channels = parse_channel_rows(
                    html,
                    category,
                    date_value,
                )

                channel_count = len(extracted_channels)

                programme_count = sum(
                    len(channel["programmes"])
                    for channel in extracted_channels
                )

                print(
                    f"  Channels: {channel_count} | "
                    f"Programmes: {programme_count}",
                    flush=True,
                )

                if channel_count == 0:
                    raise RuntimeError(
                        f"No channels extracted for "
                        f"{category} on {date_value}."
                    )

                if programme_count == 0:
                    raise RuntimeError(
                        f"No programmes extracted for "
                        f"{category} on {date_value}."
                    )

                all_channels.extend(extracted_channels)

        browser.close()

    if not all_channels:
        raise RuntimeError(
            "No channels were extracted. Output files were not generated."
        )

    write_xml(all_channels)
    write_csv(all_channels)

    print(
        "\nEPG generation completed successfully.",
        flush=True,
    )

    return 0


# ============================================================
# ENTRY POINT AND FULL ERROR TRACEBACK
# ============================================================

if __name__ == "__main__":
    try:
        result = main()
        sys.exit(result)

    except BaseException:
        print(
            "\n========== FULL ERROR TRACEBACK ==========",
            file=sys.stderr,
            flush=True,
        )

        traceback.print_exc(
            file=sys.stderr,
        )

        print(
            "========== END ERROR TRACEBACK ==========\n",
            file=sys.stderr,
            flush=True,
        )

        raise
