
import csv
import re
import sys
import traceback
import xml.etree.ElementTree as ET

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://elcinema.com"
GUIDE_URL = "https://elcinema.com/en/tvguide"

# مجلد المشروع الحالي، مع وضع النتائج داخل docs
PROJECT_DIR = Path(__file__).resolve().parent
DOCS_DIR = PROJECT_DIR / "docs"

XML_OUTPUT = DOCS_DIR / "ElCinema-EPG.xml"
CSV_OUTPUT = DOCS_DIR / "ElCinema-Channel-Mapping.csv"

# المنطقة الزمنية التي يعرض بها الموقع البرامج
EGYPT_TZ = ZoneInfo("Africa/Cairo")

# المنطقة الزمنية التي نريد إظهار البرامج بها
ALGERIA_TZ = ZoneInfo("Africa/Algiers")

# إذا كان لديك رابط صفحة دليل قناة محددة، ضعه هنا.
# اترك القائمة فارغة لمحاولة اكتشاف روابط القنوات من صفحة الدليل.
CHANNEL_URLS = []

# مهلة انتظار تحميل الصفحة بالميلي ثانية
PAGE_TIMEOUT = 60000

# تشغيل المتصفح دون نافذة
HEADLESS = True

# معرّف ثابت اختياري لقناة واحدة عند استعمال PAGE_URL مباشرة.
# اتركه None إذا أردت استخراج المعرّف من رابط القناة.
SINGLE_CHANNEL_ID = None

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def log(message):
    print(f"[ElCinema EPG] {message}", flush=True)


def normalize_space(value):
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def absolute_url(url):
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return BASE_URL + url
    return url


def extract_channel_id(url):
    """
    يحاول استخراج رقم القناة من رابط الصفحة.
    يمكن تعديل هذه الدالة إذا كانت روابط القنوات في الموقع
    تستخدم صيغة مختلفة.
    """
    if SINGLE_CHANNEL_ID:
        return str(SINGLE_CHANNEL_ID)

    parsed = re.search(r"/tvguide/([^/?#]+)", url, re.I)
    if parsed:
        return parsed.group(1)

    parsed = re.search(r"/channel/([^/?#]+)", url, re.I)
    if parsed:
        return parsed.group(1)

    parsed = re.search(r"/(\d+)/?(?:\?.*)?$", url)
    if parsed:
        return parsed.group(1)

    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", url.rstrip("/"))


def make_xml_channel_id(channel_id):
    """
    يوحّد المعرّف الذي سيستخدمه XMLTV.
    حافظ على نفس المعرّف في قائمة M3U.
    """
    channel_id = normalize_space(str(channel_id))

    if not channel_id.startswith("elcinema."):
        channel_id = "elcinema." + channel_id

    return channel_id


# ============================================================
# BROADCAST DATE PARSING
# ============================================================

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def parse_broadcast_date(text, reference_date=None, previous_date=None):
    """
    يقرأ عناوين مثل:
        Saturday 10 October
        Sunday 11 October

    السنة غير موجودة في HTML، لذلك يتم تحديدها بالتحقق
    من يوم الأسبوع الفعلي.

    previous_date يساعد في إبقاء التواريخ متتابعة عند
    الانتقال بين الأيام أو نهاية السنة.
    """
    text = normalize_space(text).lower()

    match = re.search(
        r"\b(monday|tuesday|wednesday|thursday|friday|"
        r"saturday|sunday)\s+(\d{1,2})\s+"
        r"(january|february|march|april|may|june|july|"
        r"august|september|october|november|december)\b",
        text,
        re.I,
    )

    if not match:
        return None

    weekday_name = match.group(1).lower()
    day = int(match.group(2))
    month = MONTHS[match.group(3).lower()]
    expected_weekday = WEEKDAYS[weekday_name]

    reference_date = reference_date or date.today()

    candidates = []

    for year in range(reference_date.year - 1, reference_date.year + 3):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue

        if candidate.weekday() != expected_weekday:
            continue

        if previous_date is not None:
            # يسمح بالتواريخ المتتابعة عبر نهاية السنة.
            if candidate < previous_date:
                continue

            if (candidate - previous_date).days > 10:
                continue

            candidates.append(candidate)
        else:
            candidates.append(candidate)

    if not candidates:
        log(f"تعذر تحديد السنة للتاريخ: {text}")
        return None

    if previous_date is not None:
        return min(candidates, key=lambda d: abs((d - previous_date).days))

    # اختيار أقرب تاريخ مناسب إلى اليوم الحالي.
    return min(candidates, key=lambda d: abs((d - reference_date).days))


def find_date_sections(soup):
    """
    يعيد أقسام التاريخ حسب ترتيبها في الصفحة.
    يفترض أن كل عنوان .dates يتبعه محتوى برامج ذلك اليوم.
    """
    date_elements = soup.select("div.dates")
    sections = []

    for element in date_elements:
        sections.append({
            "element": element,
            "label": normalize_space(element.get_text(" ", strip=True)),
        })

    return sections


def assign_dates_to_cards(soup):
    """
    يربط البطاقات بالتاريخ الذي تظهر تحته في DOM.

    عند عدم وجود حاوية يوم واضحة، يعتمد على ترتيب عناوين
    .dates وبطاقات البرامج.
    """
    results = []
    current_date = None
    previous_date = None
    reference_date = date.today()

    # كل العناوين والبطاقات مرتبة بحسب ظهورها في الصفحة.
    items = soup.select("div.dates, div.boxed-category-1.padded-half")

    for item in items:
        classes = item.get("class", [])

        if "dates" in classes:
            label = normalize_space(item.get_text(" ", strip=True))

            parsed_date = parse_broadcast_date(
                label,
                reference_date=reference_date,
                previous_date=previous_date,
            )

            if parsed_date is not None:
                current_date = parsed_date
                previous_date = parsed_date

        elif (
            "boxed-category-1" in classes
            and current_date is not None
        ):
            results.append((item, current_date))

    return results


# ============================================================
# PROGRAM CARD EXTRACTION
# ============================================================

def extract_time_text(card):
    """
    وقت البرنامج عادةً موجود في أول li داخل أول عمود.
    """
    columns = card.select(":scope > .row > .columns")

    if columns:
        first_column = columns[0]
        first_li = first_column.select_one("li")

        if first_li:
            text = normalize_space(first_li.get_text(" ", strip=True))
            match = re.search(
                r"\b(\d{1,2}:\d{2}\s*(?:AM|PM))\b",
                text,
                re.I,
            )
            if match:
                return normalize_space(match.group(1)).upper()

    # احتياط إذا اختلف ترتيب الأعمدة.
    text = normalize_space(card.get_text(" ", strip=True))
    match = re.search(
        r"\b(\d{1,2}:\d{2}\s*(?:AM|PM))\b",
        text,
        re.I,
    )

    return normalize_space(match.group(1)).upper() if match else ""


def extract_duration(card):
    text = normalize_space(card.get_text(" ", strip=True))
    match = re.search(r"\[(\d+)\s*minutes?\]", text, re.I)

    if match:
        return int(match.group(1))

    return 0


def extract_title(card):
    link = card.select_one('a[href*="/work/"]')

    if link:
        title = normalize_space(link.get_text(" ", strip=True))
        if title:
            return title

    # احتياط إذا لم يظهر الرابط المتوقع.
    for selector in ("h1", "h2", "h3", "h4", ".title"):
        element = card.select_one(selector)
        if element:
            title = normalize_space(element.get_text(" ", strip=True))
            if title:
                return title

    return ""


def extract_image(card):
    image = card.select_one("img[src]")

    if image:
        return absolute_url(
            image.get("src")
            or image.get("data-src")
            or ""
        )

    image = card.select_one("img[data-src]")

    if image:
        return absolute_url(image.get("data-src", ""))

    return ""


def extract_year(card):
    text = normalize_space(card.get_text(" ", strip=True))

    # مثال: Movie (2023)
    match = re.search(r"\b(?:Movie|Series|Show)\s*\((\d{4})\)", text, re.I)

    if not match:
        match = re.search(r"\b(19\d{2}|20\d{2})\b", text)

    return match.group(1) if match else ""


def extract_rating(card):
    rating_element = card.select_one(".stars-rating-lg")

    if not rating_element:
        return ""

    possible_values = [
        rating_element.get("title", ""),
        rating_element.get("data-rating", ""),
        rating_element.get_text(" ", strip=True),
    ]

    for value in possible_values:
        match = re.search(
            r"(?:التقييم\s*:\s*)?(\d+(?:\.\d+)?)",
            value or "",
        )
        if match:
            return match.group(1)

    return ""


def extract_actors(card):
    actors = []
    seen = set()

    for link in card.select('a[href*="/person/"]'):
        name = normalize_space(link.get_text(" ", strip=True))

        if name and name not in seen:
            seen.add(name)
            actors.append(name)

    return ", ".join(actors)


def extract_description(card):
    """
    يقرأ الوصف الظاهر والمخفي في span.hide،
    ويحذف رابط read-more دون حذف النص المخفي.
    """
    candidates = card.select("li")

    for element in candidates:
        if not element.select_one("a#read-more"):
            continue

        clone = BeautifulSoup(str(element), "html.parser")

        for link in clone.select("a#read-more"):
            link.decompose()

        # إزالة العناصر غير النصية التي قد تضيف ضجيجًا.
        for node in clone.select("script, style"):
            node.decompose()

        return normalize_space(clone.get_text(" ", strip=True))

    # احتياط لبعض صفحات الموقع.
    for selector in (
        ".description",
        ".plot",
        ".synopsis",
        "[itemprop='description']",
    ):
        element = card.select_one(selector)
        if element:
            return normalize_space(element.get_text(" ", strip=True))

    return ""


def cairo_time_to_algeria(broadcast_date, time_text):
    """
    يحول وقت البث من توقيت مصر إلى توقيت الجزائر.

    يتم تحويل التاريخ والوقت معًا؛ لذلك قد يتغير يوم البث
    بعد التحويل.
    """
    if not broadcast_date or not time_text:
        return None

    try:
        naive_datetime = datetime.strptime(
            f"{broadcast_date.isoformat()} {time_text}",
            "%Y-%m-%d %I:%M %p",
        )
    except ValueError:
        log(f"وقت غير صالح: {broadcast_date} {time_text}")
        return None

    egypt_datetime = naive_datetime.replace(tzinfo=EGYPT_TZ)

    return egypt_datetime.astimezone(ALGERIA_TZ)


def extract_program(card, broadcast_date, channel_id, channel_name, page_url):
    title = extract_title(card)
    time_text = extract_time_text(card)
    duration = extract_duration(card)

    if not title:
        return None

    if not time_text:
        log(f"تجاهل برنامج بلا وقت: {title}")
        return None

    if duration <= 0:
        log(f"تجاهل برنامج بلا مدة صحيحة: {title}")
        return None

    start = cairo_time_to_algeria(broadcast_date, time_text)

    if start is None:
        return None

    stop = start + timedelta(minutes=duration)

    return {
        "channel_id": channel_id,
        "channel_name": channel_name,
        "title": title,
        "start": start,
        "stop": stop,
        "duration_minutes": duration,
        "description": extract_description(card),
        "image": extract_image(card),
        "year": extract_year(card),
        "rating": extract_rating(card),
        "actors": extract_actors(card),
        "source_url": page_url,
        "source_time": time_text,
        "source_date": broadcast_date.isoformat(),
    }


def extract_channel_name(soup, page_url):
    for selector in (
        "h1",
        ".channel-name",
        ".tv-channel-name",
        "[itemprop='name']",
    ):
        element = soup.select_one(selector)

        if element:
            name = normalize_space(element.get_text(" ", strip=True))
            if name:
                return name

    return extract_channel_id(page_url)


def extract_programs_from_page(soup, page_url):
    channel_id = make_xml_channel_id(extract_channel_id(page_url))
    channel_name = extract_channel_name(soup, page_url)

    programs = []
    dated_cards = assign_dates_to_cards(soup)

    if not dated_cards:
        log(
            "لم أجد بطاقات مرتبطة بعناوين تاريخ "
            f"في الصفحة: {page_url}"
        )
        return channel_name, channel_id, programs

    for card, broadcast_date in dated_cards:
        program = extract_program(
            card=card,
            broadcast_date=broadcast_date,
            channel_id=channel_id,
            channel_name=channel_name,
            page_url=page_url,
        )

        if program:
            programs.append(program)

    return channel_name, channel_id, programs


# ============================================================
# CHANNEL DISCOVERY
# ============================================================

def discover_channel_urls(page, guide_url):
    """
    يحاول اكتشاف روابط صفحات القنوات من صفحة الدليل.
    إذا لم تتوافق روابط الموقع مع المرشحات أدناه، ضع الروابط
    الصحيحة يدويًا في CHANNEL_URLS.
    """
    log(f"فتح صفحة الدليل: {guide_url}")
    page.goto(
        guide_url,
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT,
    )

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        pass

    page.wait_for_timeout(2000)

    links = page.locator("a[href]").evaluate_all(
        """
        elements => elements.map(a => ({
            href: a.href,
            text: (a.innerText || a.textContent || '').trim()
        }))
        """
    )

    urls = []
    seen = set()

    for item in links:
        href = item.get("href", "")

        if not href.startswith(BASE_URL):
            continue

        if "/tvguide/" not in href and "/channel/" not in href:
            continue

        href = href.split("#", 1)[0]

        if href not in seen:
            seen.add(href)
            urls.append(href)

    return urls


# ============================================================
# XMLTV OUTPUT
# ============================================================

def xmltv_timestamp(value):
    """
    يكتب الوقت المحلي للجزائر مع الإزاحة الصحيحة.
    XMLTV format: YYYYMMDDHHMMSS +0100
    """
    local_value = value.astimezone(ALGERIA_TZ)
    offset = local_value.strftime("%z")

    return local_value.strftime("%Y%m%d%H%M%S ") + offset


def add_text_element(parent, tag, text, attributes=None):
    element = ET.SubElement(parent, tag, attributes or {})
    element.text = text or ""
    return element


def write_xmltv(programs, channel_names, output_path):
    tv = ET.Element("tv", {
        "generator-info-name": "DZGreen ElCinema EPG",
        "generator-info-url": GUIDE_URL,
    })

    for channel_id, channel_name in sorted(channel_names.items()):
        channel = ET.SubElement(tv, "channel", {"id": channel_id})

        add_text_element(channel, "display-name", channel_name, {
            "lang": "en"
        })

    # منع تكرار البرنامج نفسه للقناة والوقت نفسيهما.
    seen = set()

    for program in sorted(
        programs,
        key=lambda item: (item["start"], item["channel_id"], item["title"]),
    ):
        key = (
            program["channel_id"],
            program["start"].isoformat(),
            program["title"],
        )

        if key in seen:
            continue

        seen.add(key)

        node = ET.SubElement(tv, "programme", {
            "start": xmltv_timestamp(program["start"]),
            "stop": xmltv_timestamp(program["stop"]),
            "channel": program["channel_id"],
        })

        add_text_element(node, "title", program["title"], {
            "lang": "en"
        })

        if program["description"]:
            add_text_element(node, "desc", program["description"], {
                "lang": "en"
            })

        if program["year"]:
            add_text_element(node, "date", program["year"])

        if program["image"]:
            add_text_element(node, "icon", "", {
                "src": program["image"]
            })

        if program["actors"]:
            credits = ET.SubElement(node, "credits")

            for actor in program["actors"].split(", "):
                add_text_element(credits, "actor", actor)

        if program["rating"]:
            rating = ET.SubElement(node, "rating", {
                "system": "ElCinema"
            })
            add_text_element(rating, "value", program["rating"])

    tree = ET.ElementTree(tv)

    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass

    output_path.parent.mkdir(parents=True, exist_ok=True)

    tree.write(
        str(output_path),
        encoding="utf-8",
        xml_declaration=True,
    )

    log(f"XMLTV محفوظ: {output_path}")
    log(f"عدد البرامج المكتوبة: {len(seen)}")


# ============================================================
# CSV OUTPUT
# ============================================================

def write_csv(programs, channel_names, output_path):
    """
    ملف CSV لمطابقة القنوات مع معرّفات XMLTV.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.writer(file)

        writer.writerow([
            "channel_id",
            "channel_name",
            "source_url",
            "program_count",
        ])

        counts = {}

        for program in programs:
            channel_id = program["channel_id"]
            counts[channel_id] = counts.get(channel_id, 0) + 1

        for channel_id, channel_name in sorted(channel_names.items()):
            urls = sorted({
                program["source_url"]
                for program in programs
                if program["channel_id"] == channel_id
            })

            writer.writerow([
                channel_id,
                channel_name,
                urls[0] if urls else "",
                counts.get(channel_id, 0),
            ])

    log(f"CSV محفوظ: {output_path}")


# ============================================================
# MAIN
# ============================================================

def main():
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    all_programs = []
    channel_names = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=HEADLESS)

        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="en-US",
            timezone_id="Africa/Cairo",
        )

        page = context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT)

        if CHANNEL_URLS:
            channel_urls = CHANNEL_URLS[:]
        else:
            channel_urls = discover_channel_urls(page, GUIDE_URL)

        # إذا لم تُكتشف روابط قنوات، نستخدم صفحة الدليل نفسها.
        if not channel_urls:
            log(
                "لم يتم اكتشاف روابط قنوات مستقلة. "
                "سأحاول قراءة صفحة الدليل مباشرة."
            )
            channel_urls = [GUIDE_URL]

        log(f"عدد صفحات القنوات المرشحة: {len(channel_urls)}")

        visited = set()

        for index, channel_url in enumerate(channel_urls, start=1):
            if channel_url in visited:
                continue

            visited.add(channel_url)

            log(
                f"[{index}/{len(channel_urls)}] "
                f"قراءة: {channel_url}"
            )

            try:
                page.goto(
                    channel_url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_TIMEOUT,
                )

                try:
                    page.wait_for_load_state(
                        "networkidle",
                        timeout=15000,
                    )
                except PlaywrightTimeoutError:
                    pass

                page.wait_for_timeout(1500)

                # نعيد قراءة HTML بعد تنفيذ JavaScript.
                html = page.content()
                soup = BeautifulSoup(html, "html.parser")

                channel_name, channel_id, programs = (
                    extract_programs_from_page(soup, channel_url)
                )

                if programs:
                    channel_names[channel_id] = channel_name
                    all_programs.extend(programs)

                    log(
                        f"تم استخراج {len(programs)} برنامج "
                        f"من {channel_name}"
                    )
                else:
                    log(f"لم تُستخرج برامج من: {channel_url}")

            except Exception as exc:
                log(f"خطأ في الصفحة {channel_url}: {exc}")
                traceback.print_exc()

        browser.close()

    # إزالة التكرار النهائي.
    unique_programs = []
    seen = set()

    for program in all_programs:
        key = (
            program["channel_id"],
            program["start"].isoformat(),
            program["title"],
        )

        if key in seen:
            continue

        seen.add(key)
        unique_programs.append(program)

    if not unique_programs:
        log("تحذير: لم يتم استخراج أي برامج.")
        log(
            "تحقق من روابط القنوات ومحددات HTML "
            "في الموقع قبل الاعتماد على الملفات."
        )

    write_xmltv(
        programs=unique_programs,
        channel_names=channel_names,
        output_path=XML_OUTPUT,
    )

    write_csv(
        programs=unique_programs,
        channel_names=channel_names,
        output_path=CSV_OUTPUT,
    )

    log("انتهى السكريبت.")
    log(f"XML: {XML_OUTPUT}")
    log(f"CSV: {CSV_OUTPUT}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("تم إيقاف السكريبت من المستخدم.")
        sys.exit(130)
    except Exception as exc:
        log(f"خطأ رئيسي: {exc}")
        traceback.print_exc()
        sys.exit(1)
