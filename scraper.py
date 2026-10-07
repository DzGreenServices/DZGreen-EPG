import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import urljoin
import re
import xml.etree.ElementTree as ET
import time
import os


BASE_URL = "https://elcinema.com"
GUIDE_URL = "https://elcinema.com/en/tvguide"

OUTPUT_FILE = "DZGreen-EPG.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Accept-Language": "ar-DZ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://elcinema.com/",
}


MONTHS = {
    "يناير": 1,
    "فبراير": 2,
    "مارس": 3,
    "أبريل": 4,
    "ابريل": 4,
    "مايو": 5,
    "يونيو": 6,
    "يوليو": 7,
    "أغسطس": 8,
    "اغسطس": 8,
    "سبتمبر": 9,
    "أكتوبر": 10,
    "اكتوبر": 10,
    "نوفمبر": 11,
    "ديسمبر": 12,

    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12
}


def clean_text(text):
    if not text:
        return ""

    return " ".join(text.split())


def create_session():
    session = requests.Session()
    session.headers.update(HEADERS)

    return session


def get_page(session, url, retries=3):

    for attempt in range(1, retries + 1):

        try:

            response = session.get(
                url,
                timeout=30
            )

            if response.status_code == 200:
                return response

            print(
                "HTTP",
                response.status_code,
                "| محاولة",
                attempt,
                "|",
                url
            )

        except requests.RequestException as error:

            print(
                "خطأ اتصال | محاولة",
                attempt,
                "|",
                error
            )

        if attempt < retries:
            time.sleep(3)

    return None


def discover_channels(session):

    print()
    print("===================================")
    print("اكتشاف القنوات")
    print("===================================")

    response = get_page(
        session,
        GUIDE_URL
    )

    if not response:
        raise RuntimeError(
            "تعذر الوصول إلى دليل ElCinema"
        )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    channels = {}

    for link in soup.find_all(
        "a",
        href=True
    ):

        href = link["href"].strip()

        match = re.search(
            r"/tvguide/(\d+)/?$",
            href
        )

        if not match:
            continue

        channel_number = match.group(1)

        name = clean_text(
            link.get_text(" ", strip=True)
        )

        if not name:
            continue

        full_url = urljoin(
            BASE_URL,
            href
        )

        channels[channel_number] = {
            "id": channel_number,
            "name": name,
            "url": full_url
        }

    print(
        "القنوات المكتشفة:",
        len(channels)
    )

    return list(channels.values())


def parse_date(date_text):

    date_text = clean_text(
        date_text
    )

    match = re.search(
        r"(\d{1,2})\s+([^\s]+)",
        date_text
    )

    if not match:
        return None

    day = int(
        match.group(1)
    )

    month_name = match.group(2)

    month = MONTHS.get(
        month_name
    )

    if not month:
        return None

    now = datetime.now()

    year = now.year

    try:

        result = datetime(
            year,
            month,
            day
        )

    except ValueError:
        return None

    if result < now - timedelta(days=180):
        result = result.replace(
            year=year + 1
        )

    return result


def parse_time(time_text):

    time_text = clean_text(
        time_text
    )

    match = re.search(
        r"(\d{1,2}):(\d{2})\s*(AM|PM)",
        time_text,
        re.IGNORECASE
    )

    if not match:
        return None

    hour = int(
        match.group(1)
    )

    minute = int(
        match.group(2)
    )

    meridiem = match.group(3).upper()

    if meridiem == "AM":

        if hour == 12:
            hour = 0

    else:

        if hour != 12:
            hour += 12

    return hour, minute


def parse_duration(duration_text):

    duration_text = clean_text(
        duration_text
    )

    match = re.search(
        r"(\d+)",
        duration_text
    )

    if not match:
        return None

    return int(
        match.group(1)
    )


def extract_programs(
    soup,
    channel_name,
    channel_number
):

    programs_output = []

    dates = soup.select(
        "div.dates"
    )

    for date_node in dates:

        current_date = parse_date(
            date_node.get_text(
                " ",
                strip=True
            )
        )

        if not current_date:
            continue

        container = date_node.find_next(
            "div",
            class_="columns small-12"
        )

        if not container:
            continue

        programs = container.select(
            "div.boxed-category-0.padded-half, "
            "div.boxed-category-1.padded-half"
        )

        previous_start = None

        for program in programs:

            title = ""

            title_link = program.select_one(
                "a[href^='/work/']"
            )

            if title_link:

                title = clean_text(
                    title_link.get_text(
                        " ",
                        strip=True
                    )
                )

            else:

                title_item = program.select_one(
                    "ul.unstyled.no-margin li"
                )

                if title_item:

                    title = clean_text(
                        title_item.get_text(
                            " ",
                            strip=True
                        )
                    )

            if not title:
                continue

            time_item = program.select_one(
                "ul.unstyled.text-center li:first-child"
            )

            if not time_item:

                items = program.select(
                    "ul.unstyled.no-margin li"
                )

                if len(items) >= 2:
                    time_item = items[1]

            if not time_item:
                continue

            time_value = parse_time(
                time_item.get_text(
                    " ",
                    strip=True
                )
            )

            if not time_value:
                continue

            duration_item = program.select_one(
                "span.subheader"
            )

            if not duration_item:
                continue

            duration = parse_duration(
                duration_item.get_text(
                    " ",
                    strip=True
                )
            )

            if not duration:
                continue

            hour, minute = time_value

            start = current_date.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0
            )

            if previous_start and start < previous_start:

                start += timedelta(
                    days=1
                )

            stop = start + timedelta(
                minutes=duration
            )

            previous_start = start

            programs_output.append({
                "channel_id": channel_number,
                "channel_name": channel_name,
                "title": title,
                "start": start,
                "stop": stop
            })

    return programs_output


def extract_logo(soup):

    images = soup.find_all(
        "img"
    )

    for image in images:

        src = image.get(
            "src"
        )

        if not src:
            continue

        if "/tvguide/" in src:

            return urljoin(
                BASE_URL,
                src
            )

    return ""


def process_channel(
    session,
    channel
):

    channel_id = channel["id"]
    channel_name = channel["name"]
    channel_url = channel["url"]

    print()
    print(
        "-----------------------------------"
    )
    print(
        channel_name,
        "|",
        channel_id
    )
    print(
        "-----------------------------------"
    )

    response = get_page(
        session,
        channel_url
    )

    if not response:

        print(
            "فشل تحميل القناة"
        )

        return {
            "channel": channel,
            "logo": "",
            "programs": []
        }

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    logo = extract_logo(
        soup
    )

    programs = extract_programs(
        soup,
        channel_name,
        channel_id
    )

    print(
        "البرامج:",
        len(programs)
    )

    if logo:
        print(
            "الشعار:",
            logo
        )
    else:
        print(
            "الشعار: غير موجود"
        )

    return {
        "channel": channel,
        "logo": logo,
        "programs": programs
    }


def create_xml(results):

    tv = ET.Element(
        "tv"
    )

    total_programs = 0
    successful_channels = 0

    for result in results:

        channel = result["channel"]

        channel_id = channel["id"]
        channel_name = channel["name"]
        logo = result["logo"]
        programs = result["programs"]

        if programs:
            successful_channels += 1

        total_programs += len(
            programs
        )

        channel_element = ET.SubElement(
            tv,
            "channel",
            id=channel_id
        )

        display_name = ET.SubElement(
            channel_element,
            "display-name"
        )

        display_name.text = channel_name

        if logo:

            ET.SubElement(
                channel_element,
                "icon",
                src=logo
            )

        for item in programs:

            programme = ET.SubElement(
                tv,
                "programme",
                start=item["start"].strftime(
                    "%Y%m%d%H%M%S +0100"
                ),
                stop=item["stop"].strftime(
                    "%Y%m%d%H%M%S +0100"
                ),
                channel=channel_id
            )

            title = ET.SubElement(
                programme,
                "title",
                lang="ar"
            )

            title.text = item["title"]

    tree = ET.ElementTree(
        tv
    )

    ET.indent(
        tree,
        space="  "
    )

    temporary_file = OUTPUT_FILE + ".tmp"

    tree.write(
        temporary_file,
        encoding="utf-8",
        xml_declaration=True
    )

    if total_programs == 0:

        if os.path.exists(
            temporary_file
        ):
            os.remove(
                temporary_file
            )

        raise RuntimeError(
            "لم يتم استخراج أي برنامج. "
            "لن يتم استبدال ملف XML القديم."
        )

    os.replace(
        temporary_file,
        OUTPUT_FILE
    )

    print()
    print("===================================")
    print("نتيجة الاستخراج")
    print("===================================")
    print(
        "القنوات:",
        len(results)
    )
    print(
        "القنوات التي تحتوي برامج:",
        successful_channels
    )
    print(
        "إجمالي البرامج:",
        total_programs
    )
    print(
        "الملف:",
        OUTPUT_FILE
    )
    print("===================================")


def main():

    print()
    print("===================================")
    print("DZGreen EPG Scraper")
    print("ElCinema")
    print("===================================")

    session = create_session()

    channels = discover_channels(
        session
    )

    if not channels:

        raise RuntimeError(
            "لم يتم العثور على أي قناة"
        )

    results = []

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
            "]"
        )

        try:

            result = process_channel(
                session,
                channel
            )

            results.append(
                result
            )

        except Exception as error:

            print(
                "خطأ في القناة:",
                error
            )

            results.append({
                "channel": channel,
                "logo": "",
                "programs": []
            })

        time.sleep(0.5)

    create_xml(
        results
    )


if __name__ == "__main__":
    main()
