from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import csv
import os
import time


# ============================================================
# CONFIGURATION
# ============================================================

URL = (
    "https://www.bein.com/ar/"
    "%d8%ac%d8%af%d9%88%d9%84-%d8%a7%d9%84%d8%a8%d8%ab/"
    "?c=dz&"
)

XML_OUTPUT = "BeIN-EPG.xml"
CSV_OUTPUT = "BeIN-Channels.csv"

# Official beIN guide time = UTC+03:00
SOURCE_TIMEZONE = timezone(timedelta(hours=3))

# Official categories
CATEGORIES = [
    ("sports", "رياضة"),
    ("entertainment", "ترفيه"),
]


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):
    if not value:
        return ""

    return " ".join(value.split()).strip()


def get_channel_id(href):
    """
    Example:
    https://beinconnect.app/beINSPORTS1

    Result:
    beINSPORTS1
    """

    if not href:
        return ""

    path = urlparse(href).path.strip("/")

    if not path:
        return ""

    return path.split("/")[-1].strip()


def epoch_to_xmltv(milliseconds):
    """
    Convert official Unix milliseconds
    to XMLTV time in UTC+03:00.
    """

    utc_dt = datetime.fromtimestamp(
        int(milliseconds) / 1000,
        tz=timezone.utc
    )

    local_dt = utc_dt.astimezone(
        SOURCE_TIMEZONE
    )

    return local_dt.strftime(
        "%Y%m%d%H%M%S +0300"
    )


# ============================================================
# GET AVAILABLE DAYS
# ============================================================

def get_available_days(page):

    print()
    print("Reading available dates...")

    page.wait_for_selector(
        ".day-cell",
        timeout=60000
    )

    day_elements = page.locator(
        ".day-cell"
    )

    count = day_elements.count()

    print(
        "Days found:",
        count
    )

    dates = []

    for i in range(count):

        element = day_elements.nth(i)

        date_value = element.get_attribute(
            "data-date"
        )

        if date_value:

            date_value = date_value.strip()

            if date_value not in dates:

                dates.append(
                    date_value
                )

    print()
    print("Available dates:")

    for date_value in dates:

        print(
            "  -",
            date_value
        )

    return dates


# ============================================================
# SELECT DAY
# ============================================================

def select_day(page, date_value):

    print()
    print("=" * 60)
    print(
        "Selecting date:",
        date_value
    )
    print("=" * 60)

    selector = (
        f'.day-cell[data-date="{date_value}"]'
    )

    day = page.locator(
        selector
    )

    if day.count() == 0:

        print(
            "Date not found:",
            date_value
        )

        return False

    try:

        day.scroll_into_view_if_needed()

        day.click(
            timeout=30000
        )

    except Exception as error:

        print(
            "Could not click date:",
            error
        )

        return False

    # Allow JavaScript to update the guide
    page.wait_for_timeout(
        2500
    )

    try:

        page.wait_for_selector(
            ".channel-row",
            timeout=60000
        )

        page.wait_for_selector(
            ".prog-block",
            timeout=60000
        )

    except PlaywrightTimeoutError:

        print(
            "TV guide did not load for:",
            date_value
        )

        return False

    print(
        "Date loaded:",
        date_value
    )

    return True


# ============================================================
# SELECT CATEGORY
# ============================================================

def select_category(page, category_id, category_name):

    print()
    print("-" * 60)
    print(
        "Selecting category:",
        category_name,
        "(" + category_id + ")"
    )
    print("-" * 60)

    selector = (
        f'.category-tab[data-category="{category_id}"]'
    )

    button = page.locator(
        selector
    )

    if button.count() == 0:

        print(
            "Category button not found:",
            category_id
        )

        return False

    try:

        button.scroll_into_view_if_needed()

        button.click(
            timeout=30000
        )

    except Exception as error:

        print(
            "Could not click category:",
            error
        )

        return False

    # Allow JavaScript to change the guide
    page.wait_for_timeout(
        2500
    )

    try:

        page.wait_for_selector(
            ".channel-row",
            timeout=60000
        )

    except PlaywrightTimeoutError:

        print(
            "No channel rows after selecting:",
            category_name
        )

        return False

    print(
        "Category loaded:",
        category_name
    )

    return True


# ============================================================
# PARSE CURRENT CATEGORY / DAY
# ============================================================

def parse_current_view(
    page,
    date_value,
    category_id,
    category_name,
    channels,
    programmes
):

    print()
    print(
        "Extracting:",
        category_name,
        "|",
        date_value
    )

    rows = page.locator(
        ".channel-row"
    )

    row_count = rows.count()

    print(
        "Channel rows:",
        row_count
    )

    day_program_count = 0
    new_channel_count = 0

    for row_index in range(row_count):

        row = rows.nth(
            row_index
        )

        # ----------------------------------------------------
        # CHANNEL LINK
        # ----------------------------------------------------

        channel_link = row.locator(
            ".channel-col a[href]"
        ).first

        if channel_link.count() == 0:
            continue

        href = channel_link.get_attribute(
            "href"
        )

        channel_id = get_channel_id(
            href
        )

        if not channel_id:
            continue

        # ----------------------------------------------------
        # LOGO
        # ----------------------------------------------------

        logo_url = ""

        image = channel_link.locator(
            "img"
        ).first

        if image.count() > 0:

            logo_url = (
                image.get_attribute("src")
                or image.get_attribute("data-src")
                or ""
            ).strip()

        # ----------------------------------------------------
        # SAVE CHANNEL
        # ----------------------------------------------------

        if channel_id not in channels:

            channels[channel_id] = {
                "id": channel_id,
                "name": channel_id,
                "logo": logo_url,
                "bein_url": href,
                "category": category_id,
            }

            new_channel_count += 1

        else:

            if (
                not channels[channel_id]["logo"]
                and logo_url
            ):

                channels[channel_id]["logo"] = logo_url

        # ----------------------------------------------------
        # PROGRAMS
        # ----------------------------------------------------

        blocks = row.locator(
            ".row-timeline-track .prog-block"
        )

        block_count = blocks.count()

        for block_index in range(block_count):

            block = blocks.nth(
                block_index
            )

            title = clean_text(
                block.get_attribute(
                    "data-full-title"
                )
            )

            category = clean_text(
                block.get_attribute(
                    "data-full-category"
                )
            )

            start_ms = block.get_attribute(
                "data-start-ms"
            )

            end_ms = block.get_attribute(
                "data-end-ms"
            )

            # ------------------------------------------------
            # Validate official fields
            # ------------------------------------------------

            if not title:
                continue

            if not start_ms or not end_ms:
                continue

            try:

                start_ms = int(
                    start_ms
                )

                end_ms = int(
                    end_ms
                )

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

                "date": date_value,

                "guide_category":
                    category_id,

            })

            day_program_count += 1

    print(
        "New channels:",
        new_channel_count
    )

    print(
        "Programs extracted:",
        day_program_count
    )


# ============================================================
# REMOVE DUPLICATES
# ============================================================

def remove_duplicates(programmes):

    unique = {}

    for programme in programmes:

        key = (
            programme["channel"],
            programme["start_ms"],
            programme["end_ms"],
            programme["title"],
            programme["category"],
        )

        unique[key] = programme

    result = list(
        unique.values()
    )

    result.sort(
        key=lambda item: (
            item["start_ms"],
            item["channel"],
            item["title"],
        )
    )

    return result


# ============================================================
# WRITE XMLTV
# ============================================================

def write_xml(channels, programmes):

    print()
    print(
        "Creating:",
        XML_OUTPUT
    )

    tv = ET.Element(
        "tv",
        {
            "generator-info-name":
                "DZGreen Official beIN EPG",

            "source-info-name":
                "beIN Official TV Guide",
        }
    )

    # --------------------------------------------------------
    # CHANNELS
    # --------------------------------------------------------

    for channel_id in sorted(channels):

        channel = channels[
            channel_id
        ]

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

        # Official channel ID from beIN
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
    # PROGRAMMES
    # --------------------------------------------------------

    for programme in programmes:

        programme_element = ET.SubElement(
            tv,
            "programme",
            {
                "start":
                    epoch_to_xmltv(
                        programme["start_ms"]
                    ),

                "stop":
                    epoch_to_xmltv(
                        programme["end_ms"]
                    ),

                "channel":
                    programme["channel"],
            }
        )

        # Official title
        title_element = ET.SubElement(
            programme_element,
            "title"
        )

        title_element.text = (
            programme["title"]
        )

        # Official category
        if programme["category"]:

            category_element = ET.SubElement(
                programme_element,
                "category"
            )

            category_element.text = (
                programme["category"]
            )

    tree = ET.ElementTree(
        tv
    )

    ET.indent(
        tree,
        space="    "
    )

    tree.write(
        XML_OUTPUT,
        encoding="utf-8",
        xml_declaration=True
    )

    print(
        "XML created successfully."
    )


# ============================================================
# WRITE CSV
# ============================================================

def write_csv(channels):

    print()
    print(
        "Creating:",
        CSV_OUTPUT
    )

    with open(
        CSV_OUTPUT,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.writer(
            file
        )

        writer.writerow([
            "channel_name",
            "tvg_id",
            "logo_url",
            "bein_url",
            "category",
        ])

        for channel_id in sorted(
            channels
        ):

            channel = channels[
                channel_id
            ]

            writer.writerow([
                channel["name"],
                channel["id"],
                channel["logo"],
                channel["bein_url"],
                channel["category"],
            ])

    print(
        "CSV created successfully."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    start_time = time.time()

    print("=" * 60)
    print(
        "DZGreen - Official beIN EPG"
    )
    print(
        "Sports + Entertainment"
    )
    print("=" * 60)

    channels = {}

    programmes = []

    with sync_playwright() as p:

        print()
        print(
            "Launching Chromium..."
        )

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page(
            locale="ar-DZ"
        )

        print(
            "Opening official beIN website..."
        )

        page.goto(
            URL,
            wait_until="domcontentloaded",
            timeout=120000
        )

        print(
            "Page loaded."
        )

        # Give JavaScript time to build guide
        page.wait_for_timeout(
            10000
        )

        # ----------------------------------------------------
        # AVAILABLE DAYS
        # ----------------------------------------------------

        dates = get_available_days(
            page
        )

        if not dates:

            browser.close()

            raise RuntimeError(
                "No dates found on the official beIN TV guide."
            )

        # ====================================================
        # PROCESS SPORTS + ENTERTAINMENT
        # ====================================================

        for category_id, category_name in CATEGORIES:

            print()
            print("#" * 60)
            print(
                "CATEGORY:",
                category_name,
                "(" + category_id + ")"
            )
            print("#" * 60)

            category_loaded = select_category(
                page,
                category_id,
                category_name
            )

            if not category_loaded:

                print(
                    "Skipping category:",
                    category_name
                )

                continue

            category_channels_before = len(
                channels
            )

            category_programmes_before = len(
                programmes
            )

            # ------------------------------------------------
            # Every available date
            # ------------------------------------------------

            for date_value in dates:

                success = select_day(
                    page,
                    date_value
                )

                if not success:

                    print(
                        "Skipping date:",
                        date_value
                    )

                    continue

                # IMPORTANT:
                # After selecting the day, ensure the
                # requested category remains active.
                active_button = page.locator(
                    '.category-tab.active'
                ).first

                active_category = ""

                if active_button.count() > 0:

                    active_category = (
                        active_button.get_attribute(
                            "data-category"
                        )
                        or ""
                    )

                if active_category != category_id:

                    print(
                        "Category changed after date selection."
                    )

                    print(
                        "Re-selecting:",
                        category_name
                    )

                    if not select_category(
                        page,
                        category_id,
                        category_name
                    ):
                        continue

                    page.wait_for_timeout(
                        1500
                    )

                parse_current_view(
                    page,
                    date_value,
                    category_id,
                    category_name,
                    channels,
                    programmes
                )

            category_channels_after = len(
                channels
            )

            category_programmes_after = len(
                programmes
            )

            print()
            print(
                "CATEGORY SUMMARY:",
                category_name
            )

            print(
                "Channels added:",
                category_channels_after
                - category_channels_before
            )

            print(
                "Programs added:",
                category_programmes_after
                - category_programmes_before
            )

    # ========================================================
    # REMOVE DUPLICATES
    # ========================================================

    programmes = remove_duplicates(
        programmes
    )

    # ========================================================
    # FINAL SUMMARY
    # ========================================================

    print()
    print("=" * 60)
    print(
        "EXTRACTION SUMMARY"
    )
    print("=" * 60)

    print(
        "Channels :",
        len(channels)
    )

    print(
        "Programs :",
        len(programmes)
    )

    print(
        "Dates    :",
        len(dates)
    )

    # --------------------------------------------------------
    # Safety checks
    # --------------------------------------------------------

    if not channels:

        raise RuntimeError(
            "No channels were extracted."
        )

    if not programmes:

        raise RuntimeError(
            "No programs were extracted."
        )

    # ========================================================
    # CREATE FILES
    # ========================================================

    write_xml(
        channels,
        programmes
    )

    write_csv(
        channels
    )

    elapsed = (
        time.time() - start_time
    )

    print()
    print("=" * 60)
    print(
        "SUCCESS"
    )
    print("=" * 60)

    print(
        "Created:",
        XML_OUTPUT
    )

    print(
        "Created:",
        CSV_OUTPUT
    )

    print(
        "Execution time:",
        round(elapsed, 2),
        "seconds"
    )


if __name__ == "__main__":

    main()
