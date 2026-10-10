
import argparse
import csv
import re
import sys
import time
import traceback
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

XML_OUTPUT = "BeIN-EPG.xml"
CSV_OUTPUT = "BeIN-Channel-Mapping.csv"

CATEGORIES = ("sports", "entertainment")

DAYS_TO_FETCH = 4
DEFAULT_TIMEOUT = 60

CONTENT_STABLE_CHECKS = 3
CONTENT_CHECK_INTERVAL_MS = 700
MAX_CONTENT_WAIT_SECONDS = 20


# ============================================================
# GENERAL HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    return re.sub(r"\s+", " ", str(value)).strip()


def get_channel_id(url):
    if not url:
        return ""

    path = urlparse(url).path.rstrip("/")
    last_segment = path.split("/")[-1] if path else ""

    return re.sub(
        r"[^a-zA-Z0-9_.-]",
        "",
        last_segment,
    ).lower()


def timestamp_to_xmltv(milliseconds):
    try:
        timestamp = float(milliseconds) / 1000.0
        dt = datetime.fromtimestamp(
            timestamp,
            tz=timezone.utc,
        )
        return dt.strftime("%Y%m%d%H%M%S +0000")

    except (ValueError, TypeError, OverflowError, OSError):
        return ""


def get_attribute(element, attribute):
    if element is None:
        return ""

    return clean_text(element.get(attribute, ""))


# ============================================================
# PAGE INITIALIZATION
# ============================================================

def wait_for_page_ready(page, timeout_ms):
    page.wait_for_load_state(
        "domcontentloaded",
        timeout=timeout_ms,
    )

    page.locator(".category-tab").first.wait_for(
        state="attached",
        timeout=timeout_ms,
    )

    page.locator(".day-cell").first.wait_for(
        state="attached",
        timeout=timeout_ms,
    )

    page.locator("#channelRows").wait_for(
        state="attached",
        timeout=timeout_ms,
    )


# ============================================================
# CONTENT WAITING
# ============================================================

def wait_for_content_change(
    page,
    previous_html,
    active_selector,
    attribute,
    value,
    timeout_ms,
):
    try:
        page.wait_for_function(
            """arg => {
                const active = document.querySelector(
                    arg.activeSelector
                );

                if (!active) {
                    return false;
                }

                const activeValue = active.getAttribute(
                    arg.attribute
                );

                const isActive =
                    activeValue === arg.value ||
                    active.classList.contains("active") ||
                    active.getAttribute("aria-selected") === "true";

                const root = document.querySelector("#channelRows");

                if (!root) {
                    return false;
                }

                const htmlChanged =
                    root.innerHTML !== arg.previousHtml;

                return isActive && htmlChanged;
            }""",
            arg={
                "activeSelector": active_selector,
                "attribute": attribute,
                "value": value,
                "previousHtml": previous_html,
            },
            timeout=timeout_ms,
        )

    except PlaywrightTimeoutError:
        print(
            f"WARNING: Content did not visibly change for "
            f"{attribute}={value}. Checking current DOM.",
            flush=True,
        )


def wait_for_stable_content(page):
    deadline = (
        time.monotonic() + MAX_CONTENT_WAIT_SECONDS
    )

    previous_html = None
    stable_checks = 0

    while time.monotonic() < deadline:
        current_html = page.locator(
            "#channelRows"
        ).inner_html()

        if current_html == previous_html:
            stable_checks += 1
        else:
            stable_checks = 0
            previous_html = current_html

        if stable_checks >= CONTENT_STABLE_CHECKS:
            return current_html

        page.wait_for_timeout(
            CONTENT_CHECK_INTERVAL_MS
        )

    print(
        "WARNING: Maximum wait reached before "
        "DOM stability was confirmed.",
        flush=True,
    )

    return page.locator("#channelRows").inner_html()


# ============================================================
# CATEGORY SELECTION
# ============================================================

def activate_category(page, category, timeout_ms):
    selector = (
        f'.category-tab[data-category="{category}"]'
    )

    tab = page.locator(selector).first

    if tab.count() == 0:
        raise RuntimeError(
            f"Category tab not found: {category}"
        )

    previous_html = page.locator(
        "#channelRows"
    ).inner_html()

    tab.click()

    wait_for_content_change(
        page=page,
        previous_html=previous_html,
        active_selector=selector,
        attribute="data-category",
        value=category,
        timeout_ms=timeout_ms,
    )

    page.wait_for_timeout(1000)

    html = wait_for_stable_content(page)

    print(
        f"\nCATEGORY SELECTED: {category}",
        flush=True,
    )

    return html


# ============================================================
# AVAILABLE DATES
# ============================================================

def get_available_dates(page):
    dates = page.locator(
        ".day-cell"
    ).evaluate_all(
        """elements => elements.map(element => ({
            date: element.getAttribute("data-date") || "",
            text: (element.innerText || "").trim()
        }))"""
    )

    result = []

    for item in dates:
        date_value = clean_text(
            item.get("date", "")
        )

        if date_value and date_value not in result:
            result.append(date_value)

    return result


# ============================================================
# DATE SELECTION
# ============================================================

def activate_date(page, date_value, timeout_ms):
    selector = (
        f'.day-cell[data-date="{date_value}"]'
    )

    day = page.locator(selector).first

    if day.count() == 0:
        print(
            f"WARNING: Date not found: {date_value}",
            flush=True,
        )

        return page.locator(
            "#channelRows"
        ).inner_html()

    previous_html = page.locator(
        "#channelRows"
    ).inner_html()

    try:
        day.click(timeout=timeout_ms)

    except PlaywrightTimeoutError:
        print(
            f"WARNING: Could not click date {date_value}.",
            flush=True,
        )

    wait_for_content_change(
        page=page,
        previous_html=previous_html,
        active_selector=selector,
        attribute="data-date",
        value=date_value,
        timeout_ms=timeout_ms,
    )

    page.wait_for_timeout(1000)

    html = wait_for_stable_content(page)

    print(
        f"\nDATE SELECTED: {date_value}",
        flush=True,
    )

    return html


# ============================================================
# CHANNEL DIAGNOSTIC
# ============================================================

def diagnose_target_channels(page):
    """
    Wait five seconds and inspect the actual browser DOM
    for BaraemTV, JeemTV, and beINJUNIOR.
    """
    page.wait_for_timeout(5000)

    result = page.evaluate(
        """() => {
            const rows = [
                ...document.querySelectorAll(
                    "#channelRows .channel-row"
                )
            ];

            return rows
                .filter(row => {
                    const link = row.querySelector(
                        ".channel-col a[href]"
                    );

                    return link && (
                        /BaraemTV/i.test(link.href) ||
                        /JeemTV/i.test(link.href) ||
                        /beINJUNIOR/i.test(link.href)
                    );
                })
                .map(row => ({
                    url: row.querySelector(
                        ".channel-col a[href]"
                    )?.href || "",

                    html: row.outerHTML,

                    trackHTML: row.querySelector(
                        ".row-timeline-track"
                    )?.innerHTML || "",

                    blocks: row.querySelectorAll(
                        ".prog-block"
                    ).length
                }));
        }"""
    )

    print(
        "CHANNEL CHECK AFTER 5 SECONDS:",
        result,
        flush=True,
    )


# ============================================================
# CHANNEL AND PROGRAMME EXTRACTION
# ============================================================

def parse_channel_rows(html, category, date_value):
    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    rows = soup.select(".channel-row")

    channels = []
    programmes = []

    zero_programme_channels = 0
    rows_without_links = 0
    total_blocks = 0
    timed_blocks = 0

    print(
        f"\nPARSE DIAGNOSTIC | "
        f"category={category} | date={date_value}",
        flush=True,
    )

    print(
        f"HTML channel rows: {len(rows)}",
        flush=True,
    )

    for index, row in enumerate(rows, start=1):
        link = row.select_one(
            ".channel-col a[href]"
        )

        if link is None:
            rows_without_links += 1
            continue

        channel_url = clean_text(
            link.get("href", "")
        )

        if channel_url.startswith("/"):
            channel_url = (
                "https://www.bein.com" + channel_url
            )

        channel_id = get_channel_id(channel_url)

        if not channel_id:
            print(
                f"WARNING: Could not determine channel ID "
                f"for row {index}: {channel_url}",
                flush=True,
            )
            continue

        image = row.select_one("img")

        channel_name = ""

        if image is not None:
            channel_name = clean_text(
                image.get("alt", "")
            )

        if not channel_name:
            channel_name = clean_text(
                link.get("aria-label", "")
            )

        if not channel_name:
            channel_name = clean_text(
                link.get_text(" ", strip=True)
            )

        if not channel_name:
            channel_name = channel_id

        all_blocks = row.select(".prog-block")

        valid_blocks = row.select(
            ".prog-block[data-start-ms][data-end-ms]"
        )

        total_blocks += len(all_blocks)
        timed_blocks += len(valid_blocks)

        channel_programme_count = 0

        channels.append(
            {
                "channel_id": channel_id,
                "channel_name": channel_name,
                "channel_url": channel_url,
                "category": category,
            }
        )

        for block in valid_blocks:
            start_ms = get_attribute(
                block,
                "data-start-ms",
            )

            end_ms = get_attribute(
                block,
                "data-end-ms",
            )

            start = timestamp_to_xmltv(start_ms)
            stop = timestamp_to_xmltv(end_ms)

            if not start or not stop:
                continue

            try:
                if float(end_ms) <= float(start_ms):
                    continue

            except (ValueError, TypeError):
                continue

            title = (
                get_attribute(
                    block,
                    "data-full-title",
                )
                or clean_text(
                    block.get_text(" ", strip=True)
                )
            )

            programme_category = get_attribute(
                block,
                "data-full-category",
            )

            if not title:
                title = "Programme"

            programmes.append(
                {
                    "channel": channel_id,
                    "start": start,
                    "stop": stop,
                    "title": title,
                    "category": programme_category,
                    "date": date_value,
                }
            )

            channel_programme_count += 1

        if channel_programme_count == 0:
            zero_programme_channels += 1

            print(
                f"ZERO PROGRAMMES | {channel_name} | "
                f"id={channel_id} | "
                f"all blocks={len(all_blocks)} | "
                f"timed blocks={len(valid_blocks)} | "
                f"url={channel_url}",
                flush=True,
            )

        else:
            print(
                f"OK | {channel_name} | "
                f"id={channel_id} | "
                f"programmes={channel_programme_count}",
                flush=True,
            )

    print("\nPARSE SUMMARY", flush=True)
    print(f"Category: {category}", flush=True)
    print(f"Date: {date_value}", flush=True)
    print(f"Channels: {len(channels)}", flush=True)

    print(
        f"All programme blocks: {total_blocks}",
        flush=True,
    )

    print(
        f"Blocks with timestamps: {timed_blocks}",
        flush=True,
    )

    print(
        f"Extracted programmes: {len(programmes)}",
        flush=True,
    )

    print(
        f"Channels with zero programmes: "
        f"{zero_programme_channels}",
        flush=True,
    )

    print(
        f"Rows without channel links: "
        f"{rows_without_links}",
        flush=True,
    )

    return channels, programmes


# ============================================================
# XMLTV OUTPUT
# ============================================================

def write_xml(channels, programmes, output_path):
    tv = ET.Element(
        "tv",
        {
            "generator-info-name": "BeIN EPG Scraper",
            "generator-info-url": PAGE_URL,
        },
    )

    unique_channels = {}

    for channel in channels:
        channel_id = channel["channel_id"]

        if channel_id not in unique_channels:
            unique_channels[channel_id] = channel

    for channel_id, channel in unique_channels.items():
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

        display_name.text = channel["channel_name"]

        if channel.get("channel_url"):
            url_element = ET.SubElement(
                channel_element,
                "url",
            )

            url_element.text = channel["channel_url"]

    seen_programmes = set()

    for programme in programmes:
        key = (
            programme["channel"],
            programme["start"],
            programme["stop"],
            programme["title"],
        )

        if key in seen_programmes:
            continue

        seen_programmes.add(key)

        programme_element = ET.SubElement(
            tv,
            "programme",
            {
                "channel": programme["channel"],
                "start": programme["start"],
                "stop": programme["stop"],
            },
        )

        title_element = ET.SubElement(
            programme_element,
            "title",
            {"lang": "en"},
        )

        title_element.text = programme["title"]

        if programme.get("category"):
            category_element = ET.SubElement(
                programme_element,
                "category",
                {"lang": "en"},
            )

            category_element.text = programme["category"]

    ET.indent(tv, space="  ")

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tree = ET.ElementTree(tv)

    tree.write(
        output_path,
        encoding="utf-8",
        xml_declaration=True,
    )

    print(
        f"\nXML saved: {output_path}",
        flush=True,
    )

    print(
        f"XML channels: {len(unique_channels)}",
        flush=True,
    )

    print(
        f"XML programmes: {len(seen_programmes)}",
        flush=True,
    )


# ============================================================
# CSV OUTPUT
# ============================================================

def write_csv(channels, output_path):
    unique_channels = {}

    for channel in channels:
        channel_id = channel["channel_id"]

        if channel_id not in unique_channels:
            unique_channels[channel_id] = channel

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "channel_id",
        "channel_name",
        "channel_url",
        "category",
    ]

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for channel in unique_channels.values():
            writer.writerow(
                {
                    field: channel.get(field, "")
                    for field in fieldnames
                }
            )

    print(
        f"CSV saved: {output_path}",
        flush=True,
    )

    print(
        f"CSV channels: {len(unique_channels)}",
        flush=True,
    )


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Extract EPG from the official beIN TV Guide."
        ),
    )

    parser.add_argument(
        "--days",
        type=int,
        default=DAYS_TO_FETCH,
        help="Number of dates to extract (1-14).",
    )

    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Optional first date in YYYY-MM-DD format.",
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(OUTPUT_DIR),
        help="Output directory.",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Page timeout in seconds.",
    )

    args = parser.parse_args()

    if not 1 <= args.days <= 14:
        parser.error(
            "--days must be between 1 and 14."
        )

    if args.timeout < 10:
        parser.error(
            "--timeout must be at least 10 seconds."
        )

    start_date = None

    if args.start_date:
        try:
            start_date = datetime.strptime(
                args.start_date,
                "%Y-%m-%d",
            ).date()

        except ValueError:
            parser.error(
                "--start-date must use YYYY-MM-DD format."
            )

    output_dir = Path(args.output_dir)

    xml_path = output_dir / XML_OUTPUT
    csv_path = output_dir / CSV_OUTPUT

    timeout_ms = args.timeout * 1000

    all_channels = []
    all_programmes = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
            )

            context = browser.new_context(
                viewport={
                    "width": 1600,
                    "height": 1200,
                },
                locale="en-US",
                timezone_id="Africa/Algiers",
            )

            page = context.new_page()

            page.set_default_timeout(timeout_ms)

            print(
                f"Opening: {PAGE_URL}",
                flush=True,
            )

            page.goto(
                PAGE_URL,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )

            wait_for_page_ready(
                page,
                timeout_ms,
            )

            page.wait_for_timeout(1500)

            for category in CATEGORIES:
                print(
                    "\n"
                    + "=" * 65
                    + f"\nCATEGORY: {category}\n"
                    + "=" * 65,
                    flush=True,
                )

                activate_category(
                    page,
                    category,
                    timeout_ms,
                )

                available_dates = get_available_dates(
                    page
                )

                if not available_dates:
                    print(
                        f"WARNING: No dates found for "
                        f"{category}.",
                        flush=True,
                    )
                    continue

                if start_date:
                    requested_dates = [
                        date_value
                        for date_value in available_dates
                        if datetime.strptime(
                            date_value,
                            "%Y-%m-%d",
                        ).date() >= start_date
                    ]

                else:
                    requested_dates = available_dates

                requested_dates = requested_dates[
                    :args.days
                ]

                print(
                    f"Available dates: {available_dates}",
                    flush=True,
                )

                print(
                    f"Dates to process: {requested_dates}",
                    flush=True,
                )

                for date_value in requested_dates:
                    try:
                        activate_date(
                            page,
                            date_value,
                            timeout_ms,
                        )

                        # ==================================================
                        # NEW DIAGNOSTIC:
                        # WAIT 5 SECONDS AND CHECK TARGET CHANNELS
                        # ==================================================

                        if category == "entertainment":
                            diagnose_target_channels(page)

                        # Read the current browser DOM after the
                        # diagnostic wait.
                        browser_html = page.locator(
                            "#channelRows"
                        ).inner_html()

                        # Extra diagnostic for all entertainment rows.
                        if category == "entertainment":
                            diagnostic = page.locator(
                                "#channelRows"
                            ).evaluate(
                                """root => {
                                    const rows = [
                                        ...root.querySelectorAll(
                                            ".channel-row"
                                        )
                                    ];

                                    return rows.map((row, index) => {
                                        const link = row.querySelector(
                                            ".channel-col a[href]"
                                        );

                                        const blocks =
                                            row.querySelectorAll(
                                                ".prog-block"
                                            );

                                        return {
                                            index,
                                            url: link ? link.href : "",
                                            rowBlocks: blocks.length,
                                            timedBlocks:
                                                row.querySelectorAll(
                                                    ".prog-block" +
                                                    "[data-start-ms]" +
                                                    "[data-end-ms]"
                                                ).length,
                                            firstTitle: blocks.length
                                                ? blocks[0].getAttribute(
                                                    "data-full-title"
                                                )
                                                : ""
                                        };
                                    });
                                }"""
                            )

                            print(
                                "BROWSER DOM DIAGNOSTIC:",
                                diagnostic,
                                flush=True,
                            )

                            baraem_rows = page.locator(
                                "#channelRows .channel-row"
                            ).evaluate_all(
                                """rows => rows
                                    .filter(row => {
                                        const link = row.querySelector(
                                            ".channel-col a[href]"
                                        );

                                        return link &&
                                            /baraem/i.test(link.href);
                                    })
                                    .map(row => row.outerHTML)"""
                            )

                            if baraem_rows:
                                print(
                                    "BARAEM ROW HTML:",
                                    baraem_rows[0][:3000],
                                    flush=True,
                                )

                        channels, programmes = parse_channel_rows(
                            browser_html,
                            category,
                            date_value,
                        )

                        all_channels.extend(channels)
                        all_programmes.extend(programmes)

                    except Exception as exc:
                        print(
                            f"ERROR processing "
                            f"{category} / {date_value}: {exc}",
                            flush=True,
                        )

                        traceback.print_exc()

            context.close()
            browser.close()

        if not all_channels:
            print(
                "ERROR: No channels were extracted.",
                file=sys.stderr,
                flush=True,
            )
            return 1

        write_xml(
            all_channels,
            all_programmes,
            xml_path,
        )

        write_csv(
            all_channels,
            csv_path,
        )

        print(
            "\nEPG scraping completed successfully.",
            flush=True,
        )

        return 0

    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())

    except KeyboardInterrupt:
        print(
            "\nProcess interrupted by user.",
            file=sys.stderr,
        )
        sys.exit(130)
