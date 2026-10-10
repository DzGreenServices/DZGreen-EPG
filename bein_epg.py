
import csv
import re
import sys
import traceback
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


PAGE_URL = "https://www.bein.com/en/tv-guide/?c=dz&"

OUTPUT_DIR = Path("docs")
XML_FILE = OUTPUT_DIR / "BeIN-EPG.xml"
CSV_FILE = OUTPUT_DIR / "BeIN-Channel-Mapping.csv"

CATEGORIES = ("sports", "entertainment")
DAYS_TO_FETCH = 4

CONTENT_TIMEOUT_MS = 20000
PAGE_TIMEOUT_MS = 60000


def clean_text(value):
    """Clean whitespace from text."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def timestamp_to_xmltv(value):
    """Convert Unix milliseconds to XMLTV UTC timestamp."""
    timestamp_ms = int(value)

    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    ).strftime("%Y%m%d%H%M%S +0000")


def get_channel_id(url):
    """Create a stable channel ID from the channel URL."""
    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1] if path else ""

    slug = re.sub(r"[^a-zA-Z0-9_.-]", "", slug)

    return slug.lower()


def get_channel_name(anchor, image, url):
    """Extract the channel name from available page elements."""
    for element in (anchor, image):
        if element is None:
            continue

        for attribute in (
            "data-full-title",
            "title",
            "aria-label",
        ):
            value = clean_text(element.get(attribute, ""))

            if value and value.lower() != "channel logo":
                return value

    path = urlparse(url).path.rstrip("/")
    slug = path.split("/")[-1] if path else ""

    if slug:
        return slug

    return get_channel_id(url)


def parse_channel_rows(html, category, date_value):
    """Parse channel rows and every programme block from the page."""
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(".channel-row")

    channels = []

    for row in rows:
        anchor = row.select_one(".channel-col a[href]")

        if anchor is None:
            continue

        channel_url = urljoin(
            PAGE_URL,
            anchor.get("href", ""),
        )

        image = anchor.select_one("img")
        logo = ""

        if image is not None:
            logo = (
                image.get("src")
                or image.get("data-src")
                or image.get("data-lazy-src")
                or ""
            )

            logo = urljoin(PAGE_URL, logo) if logo else ""

        channel_id = get_channel_id(channel_url)

        if not channel_id:
            continue

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

            start_ms = block.get("data-start-ms")
            end_ms = block.get("data-end-ms")

            if not title or not start_ms or not end_ms:
                continue

            try:
                start = timestamp_to_xmltv(start_ms)
                stop = timestamp_to_xmltv(end_ms)

                start_dt = datetime.fromtimestamp(
                    int(start_ms) / 1000,
                    tz=timezone.utc,
                )
                stop_dt = datetime.fromtimestamp(
                    int(end_ms) / 1000,
                    tz=timezone.utc,
                )

                if stop_dt <= start_dt:
                    print(
                        f"WARNING: Invalid programme duration: "
                        f"{channel_id} - {title}",
                        flush=True,
                    )
                    continue

            except (ValueError, TypeError, OverflowError):
                print(
                    f"WARNING: Invalid timestamp for "
                    f"{channel_id}: {title}",
                    flush=True,
                )
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

            programmes.append(
                {
                    "start": start,
                    "stop": stop,
                    "title": title,
                    "category": programme_category,
                    "description": description,
                }
            )

        channels.append(
            {
                "id": channel_id,
                "name": channel_name,
                "url": channel_url,
                "logo": logo,
                "category": category,
                "date": date_value,
                "programmes": programmes,
            }
        )

    total_programmes = sum(
        len(channel["programmes"])
        for channel in channels
    )

    print(
        f"Parsed: category={category}, date={date_value}, "
        f"channels={len(channels)}, programmes={total_programmes}",
        flush=True,
    )

    # Extra diagnostics for beIN Movies 1.
    for channel in channels:
        if channel["id"] == "beinmovies1":
            programmes = sorted(
                channel["programmes"],
                key=lambda item: item["start"],
            )

            if programmes:
                first = programmes[0]
                last = programmes[-1]

                print(
                    f"DEBUG beINMOVIES1 [{category} / {date_value}]: "
                    f"first={first['start']} "
                    f"{first['title']}; "
                    f"last={last['start']} "
                    f"{last['title']}; "
                    f"count={len(programmes)}",
                    flush=True,
                )
            else:
                print(
                    f"WARNING: beINMOVIES1 has no programmes "
                    f"for {category} / {date_value}",
                    flush=True,
                )

    return channels


def wait_for_content_change(
    page,
    previous_html,
    active_selector,
    attribute,
    value,
    timeout_ms,
):
    """Wait for the selected control and refreshed programme content."""

    page.wait_for_function(
        """arg => {
            const active = document.querySelector(arg.activeSelector);
            const root = document.querySelector("#channelRows");

            if (!active || !root) {
                return false;
            }

            const isActive = active.classList.contains("active");
            const attributeMatches =
                active.getAttribute(arg.attribute) === arg.value;

            const hasRows =
                root.querySelectorAll(".channel-row").length > 0;

            const hasProgrammes =
                root.querySelector(
                    '.prog-block[data-start-ms][data-end-ms]'
                ) !== null;

            const contentChanged =
                root.innerHTML !== arg.previousHtml;

            return isActive
                && attributeMatches
                && hasRows
                && hasProgrammes
                && contentChanged;
        }""",
        arg={
            "previousHtml": previous_html,
            "activeSelector": active_selector,
            "attribute": attribute,
            "value": value,
        },
        timeout=timeout_ms,
    )


def verify_page_content(page, control_selector, attribute, value):
    """Verify that the selected control and programme content exist."""

    page.wait_for_function(
        """arg => {
            const control = document.querySelector(arg.selector);
            const root = document.querySelector("#channelRows");

            return control
                && control.classList.contains("active")
                && control.getAttribute(arg.attribute) === arg.value
                && root
                && root.querySelectorAll(".channel-row").length > 0
                && root.querySelector(
                    '.prog-block[data-start-ms][data-end-ms]'
                );
        }""",
        arg={
            "selector": control_selector,
            "attribute": attribute,
            "value": value,
        },
        timeout=CONTENT_TIMEOUT_MS,
    )


def activate_category(page, category):
    """Select a category and reject stale programme content."""

    selector = f'.category-tab[data-category="{category}"]'
    button = page.locator(selector)

    if button.count() == 0:
        raise RuntimeError(
            f"Category button not found: {category}"
        )

    root = page.locator("#channelRows")
    previous_html = root.inner_html()

    classes = (button.get_attribute("class") or "").split()
    was_active = "active" in classes

    if not was_active:
        print(
            f"Selecting category: {category}",
            flush=True,
        )

        button.click()

        try:
            wait_for_content_change(
                page=page,
                previous_html=previous_html,
                active_selector=selector,
                attribute="data-category",
                value=category,
                timeout_ms=CONTENT_TIMEOUT_MS,
            )
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                f"Category '{category}' did not refresh the "
                "programme content. Stopping to prevent stale "
                "EPG data from being saved."
            ) from exc

    verify_page_content(
        page,
        selector,
        "data-category",
        category,
    )

    print(
        f"Category verified: {category}",
        flush=True,
    )


def get_available_dates(page):
    """Read the available dates from the date strip."""

    page.wait_for_function(
        """() => {
            const strip = document.querySelector("#dayStrip");

            return strip
                && strip.querySelectorAll(".day-cell[data-date]").length > 0;
        }""",
        timeout=CONTENT_TIMEOUT_MS,
    )

    dates = page.locator(
        "#dayStrip .day-cell[data-date]"
    ).evaluate_all(
        """cells => cells.map(cell =>
            cell.getAttribute("data-date")
        ).filter(Boolean)"""
    )

    dates = list(dict.fromkeys(dates))[:DAYS_TO_FETCH]

    if not dates:
        raise RuntimeError(
            "No available dates were found in #dayStrip."
        )

    print(
        f"Available dates: {', '.join(dates)}",
        flush=True,
    )

    return dates


def activate_date(page, date_value):
    """Select a date and verify that its programme data refreshes."""

    selector = f'.day-cell[data-date="{date_value}"]'
    cell = page.locator(selector)

    if cell.count() == 0:
        raise RuntimeError(
            f"Date not found: {date_value}"
        )

    root = page.locator("#channelRows")
    previous_html = root.inner_html()

    cell_class = cell.get_attribute("class") or ""
    was_active = "active" in cell_class.split()

    if not was_active:
        print(
            f"Selecting date: {date_value}",
            flush=True,
        )

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
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(
                f"Date {date_value} became active, but the "
                "programme content did not refresh. Stopping "
                "to avoid saving stale EPG data."
            ) from exc

    verify_page_content(
        page,
        selector,
        "data-date",
        date_value,
    )

    print(
        f"Date verified: {date_value}",
        flush=True,
    )


def write_xml(channels):
    """Write the XMLTV file, avoiding duplicate channels and programmes."""

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    tv = ET.Element("tv")

    channel_info = {}
    programme_records = {}

    for channel in channels:
        channel_id = channel["id"]

        if channel_id not in channel_info:
            channel_info[channel_id] = channel

        for programme in channel["programmes"]:
            key = (
                channel_id,
                programme["start"],
                programme["stop"],
                programme["title"],
            )

            # Keep the first occurrence of each programme.
            if key not in programme_records:
                programme_records[key] = programme

    # Write channel definitions.
    for channel_id in sorted(channel_info):
        channel = channel_info[channel_id]

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

    # Write programmes in chronological order.
    sorted_programmes = sorted(
        programme_records.items(),
        key=lambda item: (
            item[0][0],
            item[0][1],
            item[0][2],
            item[0][3],
        ),
    )

    for key, programme in sorted_programmes:
        channel_id, start, stop, title = key

        programme_element = ET.SubElement(
            tv,
            "programme",
            {
                "start": start,
                "stop": stop,
                "channel": channel_id,
            },
        )

        title_element = ET.SubElement(
            programme_element,
            "title",
            {"lang": "en"},
        )
        title_element.text = title

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

    ET.indent(
        tree,
        space="  ",
    )

    tree.write(
        XML_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )

    print(
        f"XML saved: {XML_FILE}",
        flush=True,
    )
    print(
        f"XML channels: {len(channel_info)}",
        flush=True,
    )
    print(
        f"XML programmes: {len(programme_records)}",
        flush=True,
    )


def write_csv(channels):
    """Write the channel mapping CSV file."""

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    unique_channels = {}

    for channel in channels:
        channel_id = channel["id"]

        if channel_id not in unique_channels:
            unique_channels[channel_id] = channel

        else:
            existing = unique_channels[channel_id]

            if not existing["logo"] and channel["logo"]:
                existing["logo"] = channel["logo"]

    fieldnames = [
        "tvg-id",
        "tvg-name",
        "tvg-logo",
        "channel-url",
        "category",
    ]

    with CSV_FILE.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for channel_id in sorted(unique_channels):
            channel = unique_channels[channel_id]

            writer.writerow(
                {
                    "tvg-id": channel["id"],
                    "tvg-name": channel["name"],
                    "tvg-logo": channel["logo"],
                    "channel-url": channel["url"],
                    "category": channel["category"],
                }
            )

    print(
        f"CSV saved: {CSV_FILE}",
        flush=True,
    )
    print(
        f"CSV channels: {len(unique_channels)}",
        flush=True,
    )


def main():
    """Run the scraper for all categories and available dates."""

    all_channels = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
        )

        page = browser.new_page(
            locale="en-US",
            timezone_id="Africa/Algiers",
        )

        page.set_default_timeout(
            CONTENT_TIMEOUT_MS,
        )

        print(
            f"Opening: {PAGE_URL}",
            flush=True,
        )

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
            '.category-tab[data-category="sports"]',
            timeout=CONTENT_TIMEOUT_MS,
        )

        page.wait_for_selector(
            '.category-tab[data-category="entertainment"]',
            timeout=CONTENT_TIMEOUT_MS,
        )

        dates = get_available_dates(page)

        for category in CATEGORIES:
            print(
                f"\n========== CATEGORY: {category} ==========",
                flush=True,
            )

            activate_category(
                page,
                category,
            )

            # IMPORTANT: explicitly activate EVERY date,
            # including the first date in the list.
            for date_value in dates:
                print(
                    f"\n--- {category} / {date_value} ---",
                    flush=True,
                )

                activate_date(
                    page,
                    date_value,
                )

                page.wait_for_selector(
                    "#channelRows .channel-row",
                    timeout=CONTENT_TIMEOUT_MS,
                )

                html = page.locator(
                    "#channelRows"
                ).inner_html()

                parsed_channels = parse_channel_rows(
                    html=html,
                    category=category,
                    date_value=date_value,
                )

                if not parsed_channels:
                    raise RuntimeError(
                        f"No channels parsed for "
                        f"{category} on {date_value}."
                    )

                total_programmes = sum(
                    len(channel["programmes"])
                    for channel in parsed_channels
                )

                if total_programmes == 0:
                    raise RuntimeError(
                        f"No programmes parsed for "
                        f"{category} on {date_value}."
                    )

                all_channels.extend(
                    parsed_channels
                )

        browser.close()

    if not all_channels:
        raise RuntimeError(
            "No channels were collected. Output files were not written."
        )

    write_xml(all_channels)
    write_csv(all_channels)

    print(
        "\nEPG scraping completed successfully.",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()

    except Exception:
        print(
            "\nERROR: EPG scraping failed.",
            file=sys.stderr,
            flush=True,
        )

        traceback.print_exc()

        sys.exit(1)
