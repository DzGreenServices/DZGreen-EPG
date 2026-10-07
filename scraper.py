import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import xml.etree.ElementTree as ET

URL = "https://elcinema.com/en/tvguide/1132/"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Accept-Language": "ar-DZ,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://elcinema.com/",
}

response = requests.get(URL, headers=headers, timeout=30)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

# XMLTV
tv = ET.Element("tv")

channel_id = "MBCMAX"

channel = ET.SubElement(tv, "channel", id=channel_id)

display_name = ET.SubElement(channel, "display-name")
display_name.text = "MBC MAX"

icon = ET.SubElement(
    channel,
    "icon",
    src="https://media0106.elcinema.com/tvguide/1132_1.png"
)

# نبحث عن كل التواريخ
dates = soup.select("div.dates")

print("عدد التواريخ:", len(dates))

for date_node in dates:

    date_text = date_node.get_text(" ", strip=True)

    # استخراج اليوم والشهر
    match = re.search(r"(\d{1,2})\s+([^\s]+)", date_text)

    if not match:
        continue

    day = int(match.group(1))
    month_name = match.group(2)

    months = {
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

    month = months.get(month_name)

    if not month:
        continue

    year = datetime.now().year

    current_date = datetime(year, month, day)

    # الـ div الذي يحتوي البرامج يأتي بعد التاريخ
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

        title_link = program.select_one("a[href^='/work/']")

        if title_link:
            title = title_link.get_text(" ", strip=True)

        else:
            title_item = program.select_one(
                "ul.unstyled.no-margin li"
            )

            if title_item:
                title = title_item.get_text(
                    " ",
                    strip=True
                )

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

        time_text = time_item.get_text(
            " ",
            strip=True
        )

        duration_item = program.select_one(
            "span.subheader"
        )

        if not duration_item:
            continue

        duration_text = duration_item.get_text(
            " ",
            strip=True
        )

        time_match = re.search(
            r"(\d{1,2}):(\d{2})\s*(AM|PM)",
            time_text,
            re.IGNORECASE
        )

        duration_match = re.search(
            r"(\d+)",
            duration_text
        )

        if not time_match or not duration_match:
            continue

        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        meridiem = time_match.group(3).upper()

        duration = int(duration_match.group(1))

        if meridiem == "AM":
            if hour == 12:
                hour = 0
        else:
            if hour != 12:
                hour += 12

        start = current_date.replace(
            hour=hour,
            minute=minute
        )

        # إذا رجع الوقت إلى ما قبل البرنامج السابق
        # فهذا يعني أننا دخلنا اليوم التالي
        if previous_start and start < previous_start:
            start += timedelta(days=1)

        stop = start + timedelta(minutes=duration)

        previous_start = start

        start_text = start.strftime(
            "%Y%m%d%H%M%S +0100"
        )

        stop_text = stop.strftime(
            "%Y%m%d%H%M%S +0100"
        )

        programme = ET.SubElement(
            tv,
            "programme",
            start=start_text,
            stop=stop_text,
            channel=channel_id
        )

        title_element = ET.SubElement(
            programme,
            "title",
            lang="ar"
        )

        title_element.text = title

        print(
            start_text,
            "->",
            stop_text,
            "|",
            title
        )

# حفظ XML
tree = ET.ElementTree(tv)

tree.write(
    "DZGreen-EPG.xml",
    encoding="utf-8",
    xml_declaration=True
)

print()
print("تم إنشاء DZGreen-EPG.xml")
