import csv
import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://elcinema.com"
GUIDE_URL = "https://elcinema.com/en/tvguide"

M3U_CHANNELS_FILE = "epg_channels.json"
OUTPUT_FILE = "DZGreen-EPG.xml"
MAPPING_REPORT = "epg_mapping_report.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/154.0.0.0 Safari/537.36"
    ),
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
    "December": 12,
}

QUALITY_WORDS = {
    "4k", "8k", "uhd", "fhd", "hd", "sd", "hdr",
    "hevc", "h265", "h264", "x265", "x264",
    "vip", "plus", "premium", "backup", "test"
}

CATEGORY_PREFIXES = {
    "sport", "sports", "kids", "kid", "movies", "movie",
    "news", "music", "documentary", "docs",
    "ar", "en", "fr", "de", "es", "it", "tr",
    "ca", "sa", "uk", "us"
}

WORD_ALIASES = {
    "masr": "egypt",
    "masr1": "egypt1",
    "masr2": "egypt2",
    "alharam": "alharam",
    "alkahera": "alkahera",
    "kahera": "kahera",
    "alaraby": "al araby",
    "alraby": "al araby",
    "alkass": "al kass",
    "alkas": "al kass",
    "bein": "bein",
    "beinsports": "bein sports",
    "beinxtra": "bein xtra",
}

COUNTRY_WORDS = {
    "morocco", "maroc", "marocain", "moroccan",
    "egypt", "egyptian",
    "saudi", "saudia", "qatar", "qatari",
    "uae", "emirates", "jordan", "tunisia",
    "algeria", "algerian", "iraq", "iraqi"
}


def clean_text(text):
    if not text:
        return ""
    return " ".join(text.split())


def strip_diacritics(text):
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalize_name(text):
    text = strip_diacritics(clean_text(text)).lower()

    # Keep the part after a category/language prefix when clearly present.
    if "|" in text:
        parts = [clean_text(p) for p in text.split("|") if clean_text(p)]
        if parts:
            text = parts[-1]

    # Remove common "Category: " prefixes only at the beginning.
    text = re.sub(
        r"^(sport|sports|kids?|movies?|news|music|documentary|docs)\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE
    )
    text = re.sub(
        r"^(ar|en|fr|de|es|it|tr)\s*:\s*",
        "",
        text,
        flags=re.IGNORECASE
    )

    # Arabic letter normalization.
    text = (
        text.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ة", "ه")
        .replace("ـ", "")
    )

    text = text.replace("&", " and ")
    text = text.replace("+", " plus ")
    text = re.sub(r"[’'`]", "", text)
    text = re.sub(r"[^0-9a-zA-Z\u0600-\u06FF]+", " ", text)

    tokens = []
    for token in text.split():
        if token in QUALITY_WORDS:
            continue
        if token in COUNTRY_WORDS and len(text.split()) > 2:
            continue

        replacement = WORD_ALIASES.get(token)
        if replacement:
            tokens.extend(replacement.split())
        else:
            tokens.append(token)

    # Remove duplicate adjacent tokens.
    cleaned = []
    for token in tokens:
        if not cleaned or cleaned[-1] != token:
            cleaned.append(token)

    return " ".join(cleaned).strip()


def numeric_tokens(text):
    return re.findall(r"\d+", normalize_name(text))


def token_set(text):
    return set(normalize_name(text).split())


def similarity(a, b):
    na = normalize_name(a)
    nb = normalize_name(b)

    if not na or not nb:
        return 0.0

    if na == nb:
        return 1.0

    # Never allow a fuzzy match to cross different channel numbers.
    nums_a = numeric_tokens(a)
    nums_b = numeric_tokens(b)

    if nums_a and nums_b and nums_a != nums_b:
        return 0.0

    seq = SequenceMatcher(None, na, nb).ratio()

    ta = token_set(a)
    tb = token_set(b)

    if ta and tb:
        overlap = len(ta & tb) / max(len(ta), len(tb))
    else:
        overlap = 0.0

    return max(seq, overlap * 0.96)


def create_session():
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def get_page(session, url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            response = session.get(url, timeout=30)

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


def load_user_channels():
    if not os.path.exists(M3U_CHANNELS_FILE):
        raise RuntimeError(
            f"الملف {M3U_CHANNELS_FILE} غير موجود."
        )

    with open(
        M3U_CHANNELS_FILE,
        "r",
        encoding="utf-8"
    ) as file:
        data = json.load(file)

    channels = data.get("channels", [])

    if not channels:
        raise RuntimeError(
            "لم يتم العثور على قنوات في epg_channels.json."
        )

    # Group repeated tvg-id values under one XML channel.
    grouped = {}

    for item in channels:
        tvg_id = clean_text(item.get("tvg-id", ""))
        name = clean_text(item.get("name", ""))

        if not tvg_id or not name:
            continue

        if tvg_id not in grouped:
            grouped[tvg_id] = {
                "tvg_id": tvg_id,
                "name": name,
                "names": []
            }

        if name not in grouped[tvg_id]["names"]:
            grouped[tvg_id]["names"].append(name)

    result = list(grouped.values())

    print()
    print("قنوات M3U ذات tvg-id:", len(result))

    return result


def discover_elcinema_channels(session):
    print()
    print("===================================")
    print("اكتشاف قنوات ElCinema")
    print("===================================")

    response = get_page(
        session,
        GUIDE_URL
    )

    if not response:
        raise RuntimeError(
            "تعذر الوصول إلى دليل ElCinema."
        )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    channels = {}

    for link in soup.find_all("a", href=True):
        href = link["href"].strip()

        match = re.search(
            r"/tvguide/(\d+)/?$",
            href
        )

        if not match:
            continue

        channel_id = match.group(1)

        name = clean_text(
            link.get_text(" ", strip=True)
        )

        if not name:
            continue

        channels[channel_id] = {
            "id": channel_id,
            "name": name,
            "url": urljoin(BASE_URL, href)
        }

    print("قنوات ElCinema المكتشفة:", len(channels))

    return list(channels.values())


def build_matches(user_channels, elcinema_channels):
    print()
    print("===================================")
    print("مطابقة القنوات")
    print("===================================")

    matches = []
    unmatched = []

    for user_channel in user_channels:
        best = None
        best_score = 0.0
        second_score = 0.0

        for source in elcinema_channels:
            score = 0.0

            # Check every display name associated with the tvg-id.
            for user_name in user_channel["names"]:
                candidate_score = similarity(
                    user_name,
                    source["name"]
                )

                if candidate_score > score:
                    score = candidate_score

            if score > best_score:
                second_score = best_score
                best_score = score
                best = source
            elif score > second_score:
                second_score = score

        # Exact/very strong match.
        accepted = (
            best is not None
            and best_score >= 0.90
            and (best_score - second_score >= 0.03 or best_score >= 0.97)
        )

        if accepted:
            matches.append({
                "tvg_id": user_channel["tvg_id"],
                "user_name": user_channel["name"],
                "all_user_names": " | ".join(user_channel["names"]),
                "elcinema_id": best["id"],
                "elcinema_name": best["name"],
                "score": round(best_score, 4),
                "url": best["url"]
            })
        else:
            unmatched.append({
                "tvg_id": user_channel["tvg_id"],
                "user_name": user_channel["name"],
                "best_candidate": best["name"] if best else "",
                "score": round(best_score, 4)
            })

    print("مطابقات مؤكدة:", len(matches))
    print("غير مطابق:", len(unmatched))

    if unmatched:
        print()
        print("أمثلة غير مطابقة:")
        for item in unmatched[:30]:
            print(
                item["tvg_id"],
                "|",
                item["user_name"],
                "| أفضل:",
                item["best_candidate"],
                "|",
                item["score"]
            )

    return matches, unmatched


def save_mapping_report(matches, unmatched):
    with open(
        MAPPING_REPORT,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:
        writer = csv.writer(file)

        writer.writerow([
            "status",
            "tvg-id",
            "user_name",
            "all_user_names",
            "elcinema_id",
            "elcinema_name",
            "score",
            "url"
        ])

        for item in matches:
            writer.writerow([
                "MATCHED",
                item["tvg_id"],
                item["user_name"],
                item["all_user_names"],
                item["elcinema_id"],
                item["elcinema_name"],
                item["score"],
                item["url"]
            ])

        for item in unmatched:
            writer.writerow([
                "UNMATCHED",
                item["tvg_id"],
                item["user_name"],
                "",
                "",
                item["best_candidate"],
                item["score"],
                ""
            ])

    print("تقرير المطابقة:", MAPPING_REPORT)


def parse_date(date_text):
    date_text = clean_text(date_text)

    match = re.search(
        r"(\d{1,2})\s+([^\s]+)",
        date_text
    )

    if not match:
        return None

    day = int(match.group(1))
    month_name = match.group(2)

    month = MONTHS.get(month_name)

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
        result = result.replace(year=year + 1)

    return result


def parse_time(time_text):
    time_text = clean_text(time_text)

    match = re.search(
        r"(\d{1,2}):(\d{2})\s*(AM|PM)",
        time_text,
        re.IGNORECASE
    )

    if not match:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2))
    meridiem = match.group(3).upper()

    if meridiem == "AM":
        if hour == 12:
            hour = 0
    else:
        if hour != 12:
            hour += 12

    return hour, minute


def parse_duration(duration_text):
    duration_text = clean_text(duration_text)

    match = re.search(
        r"(\d+)",
        duration_text
    )

    if not match:
        return None

    return int(match.group(1))


def extract_programs(soup, channel_id):
    programs_output = []

    dates = soup.select("div.dates")

    for date_node in dates:
        current_date = parse_date(
            date_node.get_text(" ", strip=True)
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
                    "ul.unstyled.no-margin li:first-child"
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
                start += timedelta(days=1)

            stop = start + timedelta(minutes=duration)

            previous_start = start

            programs_output.append({
                "channel_id": channel_id,
                "title": title,
                "start": start,
                "stop": stop
            })

    return programs_output


def extract_logo(soup):
    for image in soup.find_all("img"):
        src = image.get("src")

        if not src:
            continue

        if "/tvguide/" in src:
            return urljoin(BASE_URL, src)

    return ""


def process_elcinema_channel(session, channel):
    response = get_page(
        session,
        channel["url"]
    )

    if not response:
        return {
            "logo": "",
            "programs": []
        }

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    logo = extract_logo(soup)

    programs = extract_programs(
        soup,
        channel["id"]
    )

    return {
        "logo": logo,
        "programs": programs
    }


def create_xml(matches, source_results):
    tv = ET.Element("tv")

    total_programs = 0
    channels_with_programs = 0

    for match in matches:
        result = source_results.get(match["elcinema_id"], {})

        programs = result.get("programs", [])
        logo = result.get("logo", "")

        if not programs:
            continue

        channels_with_programs += 1
        total_programs += len(programs)

        channel_element = ET.SubElement(
            tv,
            "channel",
            id=match["tvg_id"]
        )

        display_name = ET.SubElement(
            channel_element,
            "display-name"
        )

        display_name.text = match["user_name"]

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
                channel=match["tvg_id"]
            )

            title = ET.SubElement(
                programme,
                "title",
                lang="ar"
            )

            title.text = item["title"]

    if total_programs == 0:
        raise RuntimeError(
            "لم يتم استخراج أي برنامج. "
            "لن يتم استبدال ملف XML الحالي."
        )

    ET.indent(tv, space="  ")

    temporary_file = OUTPUT_FILE + ".tmp"

    tree = ET.ElementTree(tv)
    tree.write(
        temporary_file,
        encoding="utf-8",
        xml_declaration=True
    )

    os.replace(
        temporary_file,
        OUTPUT_FILE
    )

    print()
    print("===================================")
    print("النتيجة النهائية")
    print("===================================")
    print("المطابقات:", len(matches))
    print("قنوات لها برامج:", channels_with_programs)
    print("إجمالي البرامج:", total_programs)
    print("الملف:", OUTPUT_FILE)
    print("===================================")


def main():
    print()
    print("===================================")
    print("DZGreen EPG")
    print("M3U tvg-id + ElCinema")
    print("===================================")

    session = create_session()

    user_channels = load_user_channels()

    elcinema_channels = discover_elcinema_channels(
        session
    )

    matches, unmatched = build_matches(
        user_channels,
        elcinema_channels
    )

    save_mapping_report(
        matches,
        unmatched
    )

    if not matches:
        raise RuntimeError(
            "لم يتم العثور على أي مطابقة آمنة."
        )

    source_results = {}

    unique_sources = {}
    for match in matches:
        unique_sources[match["elcinema_id"]] = {
            "id": match["elcinema_id"],
            "name": match["elcinema_name"],
            "url": match["url"]
        }

    print()
    print("تحميل قنوات ElCinema المطابقة:", len(unique_sources))

    for index, channel in enumerate(
        unique_sources.values(),
        start=1
    ):
        print(
            "[",
            index,
            "/",
            len(unique_sources),
            "]",
            channel["name"],
            "|",
            channel["id"]
        )

        try:
            source_results[channel["id"]] = process_elcinema_channel(
                session,
                channel
            )
        except Exception as error:
            print("خطأ:", error)
            source_results[channel["id"]] = {
                "logo": "",
                "programs": []
            }

        time.sleep(0.5)

    create_xml(
        matches,
        source_results
    )


if __name__ == "__main__":
    main()
