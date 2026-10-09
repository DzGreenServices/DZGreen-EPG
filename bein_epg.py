
import csv
import re
import sys
import time
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

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
# TEXT AND TIME HELPERS
# ============================================================

def clean_text(value):
    if not value:
        return ""

    return re.sub(r"\s+", " ", str(value)).strip()


def timestamp_to_xmltv(timestamp_ms):
    """Convert a Unix timestamp in milliseconds to XMLTV UTC."""

    timestamp = int(timestamp_ms) / 1000

    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    ).strftime("%Y%m%d%H%M%S +0000")


def get_channel_id(url):
    """Create a stable channel ID from the channel URL."""

    path = urlparse(url).path.strip("/")
    slug = path.split("/")[-1] if path else ""

    slug = re.sub(r"[^a-zA-Z0-9._-]", "", slug)

    if not slug:
        return ""

    return slug.lower()


def get_channel_name(row, url):
    """Extract the channel name without using generic logo alt text."""

    link = row.select_one(".channel-col a")

    if link:
        name = clean_text(link.get_text(" ", strip=True))

        if name:
            return name

    image = row.select_one(".channel-col img")

    if image:
        alt = clean_text(image.get("alt", ""))

        if alt and alt.lower() != "channel logo":
            return alt

    path = urlparse(url).path.strip("/")
    slug = path.split("/")[-1] if path else ""

    return slug or get_channel_id(url)


# ============================================================
# HTML PARSING
# ============================================================

def parse_channel_rows(html, category, guide_date):
    """Extract channel information and programmes from the guide."""

    soup = BeautifulSoup(html, "html.parser")

    channels = {}
    programmes = []

    rows = soup.select("#channelRows .channel-row")

    if not rows:
        rows = soup.select(".channel-row")

    for row in rows:
        link = row.select_one(".channel-col a")

        if not link:
            continue

        channel_url = clean_text(link.get("href", ""))

        if not channel_url:
            continue

        channel_id = get_channel_id(channel_url)

        if not channel_id:
            continue

        channel_name = get_channel_name(row, channel_url)

        logo = ""
        image = row.select_one(".channel-col img")

        if image:
            logo = clean_text(
                image.get("src", "")
                or image.get("data-src", "")
            )

        channels[channel_id] = {
            "channel_id": channel_id,
            "channel_name": channel_name,
            "logo": logo,
            "url": channel_url,
            "category": category,
        }

        blocks = row.select(
            ".prog-block[data-start-ms][data-end-ms]"
        )

        for block in blocks:
            title = clean_text(
                block.get("data-full-title", "")
            )

            if not title:
                title_element = block.select_one(
                    ".prog-title-text"
                )

                if title_element:
                    title = clean_text(
                        title_element.get_text(" ", strip=True)
                    )

            if not title:
                continue

            start_ms = block.get("data-start-ms")
            end_ms = block.get("data-end-ms")

            try:
                start_ms = int(start_ms)
                end_ms = int(end_ms)
            except (TypeError, ValueError):
                continue

            if end_ms <= start_ms:
                continue

            programme_category = clean_text(
                block.get("data-full-category", "")
            )

            if not programme_category:
                category_element = block.select_one(
                    ".prog-category"
                )

                if category_element:
                    programme_category = clean_text(
                        category_element.get_text(" ", strip=True)
                    )

            programmes.append({
                "channel_id": channel_id,
                "title": title,
                "category": programme_category,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "guide_date": guide_date,
            })

    return channels, programmes


# ============================================================
# WAIT FOR DYNAMIC PAGE CONTENT
# ============================================================

def wait_for_content_change(
    page,
    previous_html,
    active_selector,
    attribute,
    value,
    timeout_ms,
):
    """Wait until the selected control is active and programmes exist."""

    page.wait_for_function(
        """arg => {
            const active = document.querySelector(
                arg.activeSelector
            );

            const root = document.querySelector("#channelRows");

            if (!active || !root) {
                return false;
            }

            const isActive = arg.attribute
                ? active.getAttribute(arg.attribute) === arg.value
                : active.classList.contains("active");

            const hasProgrammes = root.querySelector(
                '.prog-block[data-start-ms][data-end-ms]'
            );

            const changed = root.innerHTML !== arg.previousHtml;

            return isActive && hasProgrammes && changed;
        }""",
        arg={
            "activeSelector": active_selector,
            "attribute": attribute,
            "value": value,
            "previousHtml": previous_html,
        },
        timeout=timeout_ms,
    )


# ============================================================
# CATEGORY SELECTION
# ============================================================


def activate_category(page, category):
    """Activate a category and wait for valid programme data."""

    selector = f'.category-tab[data-category="{category}"]'
    button = page.locator(selector)

    if button.count() == 0:
        raise RuntimeError(
            f"Category button not found: {category}"
        )

    root = page.locator("#channelRows")

    previous_html = root.inner_html()

    is_active = "active" in (
        button.get_attribute("class") or ""
    ).split()

    if not is_active:
        button.click()

    try:
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

                const rows = root.querySelectorAll(".channel-row");
                const programmes = root.querySelectorAll(
                    '.prog-block[data-start-ms][data-end-ms]'
                );

                if (rows.length === 0 || programmes.length === 0) {
                    return false;
                }

                return button.dataset.category === arg.category
                    && (
                        arg.wasActive
                        || root.innerHTML !== arg.previousHtml
                    );
            }""",
            arg={
                "selector": selector,
                "category": category,
                "wasActive": is_active,
                "previousHtml": previous_html,
            },
            timeout=CONTENT_TIMEOUT_MS,
        )

    except PlaywrightTimeoutError:
        # If the category was already active, valid programmes
        # may already be loaded without an HTML change.
        if is_active:
            page.wait_for_function(
                """arg => {
                    const button = document.querySelector(arg.selector);
                    const root = document.querySelector("#channelRows");

                    return button
                        && button.classList.contains("active")
                        && root
                        && root.querySelector(
                            '.prog-block[data-start-ms][data-end-ms]'
                        );
                }""",
                arg={"selector": selector},
                timeout=CONTENT_TIMEOUT_MS,
            )
        else:
            raise

    print(
        f"Category activated: {category} | "
        f"Channels: {root.locator('.channel-row').count()} | "
        f"Programmes: {root.locator('.prog-block[data-start-ms][data-end-ms]').count()}",
        flush=True,
    )



# ============================================================
# DATE SELECTION
# ============================================================

def get_available_dates(page):
    """Read the dates exposed by the page's day strip."""

    cells = page.locator(
        "#dayStrip .day-cell[data-date]"
    )

    dates = []

    for index in range(cells.count()):
        date_value = cells.nth(index).get_attribute("data-date")

        if not date_value:
            continue

        try:
            datetime.strptime(date_value, "%Y-%m-%d")
        except ValueError:
            continue

        if date_value not in dates:
            dates.append(date_value)

    return dates[:DAYS_TO_FETCH]


def activate_date(page, guide_date):
    """Select a specific date and wait for programme updates."""

    if isinstance(guide_date, datetime):
        date_value = guide_date.strftime("%Y-%m-%d")
    else:
        date_value = str(guide_date)

    selector = f'.day-cell[data-date="{date_value}"]'
    day_cell = page.locator(selector)

    if day_cell.count() == 0:
        raise RuntimeError(
            f"Date not found in the day strip: {date_value}"
        )

    if "active" in (
        day_cell.get_attribute("class") or ""
    ).split():
        page.wait_for_selector(
            "#channelRows .channel-row",
            timeout=CONTENT_TIMEOUT_MS,
        )
        return

    previous_html = page.locator(
        "#channelRows"
    ).inner_html()

    day_cell.click()

    try:
        wait_for_content_change(
            page,
            previous_html,
            selector,
            None,
            None,
            CONTENT_TIMEOUT_MS,
        )

    except PlaywrightTimeoutError:
        # Check the selected date and ensure programme blocks exist.
        page.wait_for_function(
            """selector => {
                const day = document.querySelector(selector);
                const root = document.querySelector("#channelRows");

                return day
                    && day.classList.contains("active")
                    && root
                    && root.querySelector(
                        '.prog-block[data-start-ms][data-end-ms]'
                    );
            }""",
            arg=selector,
            timeout=CONTENT_TIMEOUT_MS,
        )

    print(f"Date activated: {date_value}")


# ============================================================
# XMLTV OUTPUT
# ============================================================

def write_xml(channels, programmes):
    """Write the XMLTV file."""

    tv = ET.Element("tv", {
        "generator-info-name": "beIN EPG Generator",
    })

    for channel_id in sorted(channels):
        channel = channels[channel_id]

        channel_element = ET.SubElement(
            tv,
            "channel",
            {"id": channel_id},
        )

        display_name = ET.SubElement(
            channel_element,
            "display-name",
        )
        display_name.text = channel["channel_name"]

        if channel["logo"]:
            ET.SubElement(
                channel_element,
                "icon",
                {"src": channel["logo"]},
            )

    seen = set()

    for programme in sorted(
        programmes,
        key=lambda item: (
            item["channel_id"],
            item["start_ms"],
            item["title"],
        ),
    ):
        unique_key = (
            programme["channel_id"],
            programme["start_ms"],
            programme["end_ms"],
            programme["title"],
        )

        if unique_key in seen:
            continue

        seen.add(unique_key)

        element = ET.SubElement(
            tv,
            "programme",
            {
                "channel": programme["channel_id"],
                "start": timestamp_to_xmltv(
                    programme["start_ms"]
                ),
                "stop": timestamp_to_xmltv(
                    programme["end_ms"]
                ),
            },
        )

        title_element = ET.SubElement(
            element,
            "title",
            {"lang": "en"},
        )
        title_element.text = programme["title"]

        if programme["category"]:
            category_element = ET.SubElement(
                element,
                "category",
                {"lang": "en"},
            )
            category_element.text = programme["category"]

    tree = ET.ElementTree(tv)

    ET.indent(tree, space="  ")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    tree.write(
        XML_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    print(f"XML written: {XML_FILE}")
    print(f"Channels: {len(channels)}")
    print(f"Programmes: {len(seen)}")


# ============================================================
# CSV OUTPUT
# ============================================================

def write_csv(channels):
    """Write the channel mapping CSV file."""

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with CSV_FILE.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "channel_id",
                "channel_name",
                "logo",
                "url",
                "category",
            ],
        )

        writer.writeheader()

        for channel_id in sorted(channels):
            writer.writerow(channels[channel_id])

    print(f"CSV written: {CSV_FILE}")


# ============================================================
# MAIN
# ============================================================

def main():
    all_channels = {}
    all_programmes = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
        )

        page = browser.new_page(
            locale="en-US",
        )

        page.set_default_timeout(CONTENT_TIMEOUT_MS)
        page.set_default_navigation_timeout(PAGE_TIMEOUT_MS)

        try:
            print("Opening the beIN TV Guide...")

            page.goto(
                PAGE_URL,
                wait_until="domcontentloaded",
                timeout=PAGE_TIMEOUT_MS,
            )

            page.wait_for_selector(
                "#channelRows .channel-row",
                timeout=CONTENT_TIMEOUT_MS,
            )

            page.wait_for_selector(
                "#dayStrip .day-cell[data-date]",
                timeout=CONTENT_TIMEOUT_MS,
            )

            available_dates = get_available_dates(page)

            if not available_dates:
                raise RuntimeError(
                    "No dates found in #dayStrip."
                )

            print("Available dates:", ", ".join(available_dates))

            for category in CATEGORIES:
                print(f"\n=== {category.upper()} ===")

                activate_category(page, category)

                for index, date_value in enumerate(
                    available_dates,
                    start=1,
                ):
                    print(
                        f"[{index}/{len(available_dates)}] "
                        f"{category} - {date_value}"
                    )

                    activate_date(page, date_value)

                    # Allow the page to finish any remaining rendering.
                    page.wait_for_selector(
                        "#channelRows .channel-row",
                        timeout=CONTENT_TIMEOUT_MS,
                    )

                    html = page.locator(
                        "#channelRows"
                    ).inner_html()

                    channels, programmes = parse_channel_rows(
                        html,
                        category,
                        date_value,
                    )

                    all_channels.update(channels)
                    all_programmes.extend(programmes)

                    print(
                        f"  Channels: {len(channels)}"
                        f" | Programmes: {len(programmes)}"
                    )

                    time.sleep(0.2)

            if not all_channels:
                raise RuntimeError(
                    "No channels were extracted. "
                    "Check the guide HTML and selectors."
                )

            if not all_programmes:
                raise RuntimeError(
                    "No programmes were extracted. "
                    "The page may not have loaded its programme data."
                )

            write_xml(all_channels, all_programmes)
            write_csv(all_channels)

            print("\nEPG generation completed successfully.")

        finally:
            browser.close()

    return 0




if __name__ == "__main__":
    import traceback

    try:
        result = main()
        sys.exit(result)

    except BaseException:
        print(
            "\n========== FULL ERROR TRACEBACK ==========",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exc(file=sys.stderr)
        print(
            "========== END ERROR TRACEBACK ==========\n",
            file=sys.stderr,
            flush=True,
        )
        raise

