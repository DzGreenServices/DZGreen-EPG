#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate an XMLTV guide and a CSV tvg-id mapping from beIN's TV guide.

Default: fetch Sports + Entertainment for today and the following 3 days,
then write docs/BeIN-EPG.xml and docs/BeIN-Channel-Mapping.csv by default.

Install dependencies:
    pip install requests beautifulsoup4
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse, unquote

import requests
from bs4 import BeautifulSoup

PAGE_URL = "https://www.bein.com/en/tv-guide/?c=dz&"
AJAX_URL = "https://www.bein.com/en/epg-ajax-template/"
XML_FILENAME = "BeIN-EPG.xml"
CSV_FILENAME = "BeIN-Channel-Mapping.csv"
CATEGORIES = ("sports", "entertainment")

# The page's data-start-ms/data-end-ms fields are Unix timestamps in milliseconds.
# Keep the XMLTV timestamps in UTC; IPTV players convert them to the device timezone.
XML_TZ = timezone.utc

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": PAGE_URL,
}


@dataclass
class Channel:
    tvg_id: str
    name: str
    logo: str = ""
    channel_url: str = ""
    group_title: str = ""
    programme_keys: set[tuple[str, str, str]] = field(default_factory=set)


@dataclass(frozen=True)
class Programme:
    channel_id: str
    start: datetime
    stop: datetime
    title: str
    category: str = ""


def _slug_from_row(row) -> tuple[str, str, str]:
    """Return (slug, channel page URL, logo URL), using href first and logo as fallback."""
    anchor = row.select_one(".channel-col a[href]") or row.select_one("a[href]")
    channel_url = anchor.get("href", "").strip() if anchor else ""
    image = row.select_one(".channel-col img[src]") or row.select_one("img[src]")
    logo = image.get("src", "").strip() if image else ""

    slug = ""
    if channel_url:
        slug = unquote(urlparse(channel_url).path.rstrip("/").split("/")[-1])
    if not slug and logo:
        filename = Path(urlparse(logo).path).name
        alkass = re.search(r"alkass[_-]?(\d+)", filename, re.I)
        if alkass:
            slug = f"Alkass{alkass.group(1)}"
        elif re.search(r"4k", filename, re.I):
            slug = "beINSPORTS4K"
        else:
            # Last-resort identifier derived from the logo filename.
            slug = re.sub(r"\.(png|jpe?g|webp|svg)$", "", filename, flags=re.I)
            slug = re.sub(r"(?:_DIGITAL_Mono|_DIGITAL|_Mono|logos?[-_]).*$", "", slug, flags=re.I)
    return slug, channel_url, logo


def channel_name(slug: str, logo_url: str = "") -> str:
    """Convert beIN page slugs/logo names into readable channel names."""
    raw = slug.strip()
    low = raw.lower()

    if "alkass" in low:
        match = re.search(r"alkass[_-]?(\d+)", raw, re.I)
        if not match:
            match = re.search(r"alkass[_-]?(\d+)", urlparse(logo_url).path, re.I)
        return f"Alkass {match.group(1)}" if match else "Alkass"
    if re.fullmatch(r"4k", low) or "4k" in low or re.search(r"4k", logo_url, re.I):
        return "beIN SPORTS 4K"
    if re.fullmatch(r"beinsports?", low):
        return "beIN SPORTS"
    if re.fullmatch(r"beinsportsnews", low):
        return "beIN SPORTS News"

    match = re.fullmatch(r"beinsportsxtra(\d+)", raw, re.I)
    if match:
        return f"beIN SPORTS XTRA {match.group(1)}"
    match = re.fullmatch(r"beinsportsmax(\d+)", raw, re.I)
    if match:
        return f"beIN SPORTS MAX {match.group(1)}"
    match = re.fullmatch(r"beinsports?(\d+)(en|fr)?", raw, re.I)
    if match:
        suffix = f" {match.group(2).upper()}" if match.group(2) else ""
        return f"beIN SPORTS {match.group(1)}{suffix}"

    # Friendly names for common entertainment channel slugs.
    aliases = {
        "beinmovies1": "beIN MOVIES 1",
        "beinmovies2": "beIN MOVIES 2",
        "beinmovies3": "beIN MOVIES 3",
        "beinmovies4": "beIN MOVIES 4",
        "beinseries": "beIN SERIES",
        "beindrama": "beIN DRAMA",
        "beinfamily": "beIN FAMILY",
        "beincomedy": "beIN COMEDY",
        "beinlife": "beIN LIFE",
        "beingourmet": "beIN GOURMET",
    }
    if low in aliases:
        return aliases[low]

    # Generic readable fallback for channels whose slug was not listed above.
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw)
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    name = re.sub(r"(?<=\D)(\d+)", r" \1", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"^be\s*in\b", "beIN", name, flags=re.I)
    # Keep well-known brand casing tidy without destroying the rest of the name.
    name = re.sub(r"\bSports\b", "SPORTS", name, flags=re.I)
    name = re.sub(r"\bMovies\b", "MOVIES", name, flags=re.I)
    name = re.sub(r"\bSeries\b", "SERIES", name, flags=re.I)
    return name or raw or "Unknown Channel"


def channel_tvg_id(slug: str, name: str) -> str:
    """Create a stable tvg-id. These exact IDs are also written as XMLTV channel IDs."""
    raw = slug.strip()
    low = raw.lower()

    if "alkass" in low:
        m = re.search(r"alkass[_-]?(\d+)", raw, re.I)
        if not m:
            m = re.search(r"alkass[_-]?(\d+)", name, re.I)
        return f"Alkass{m.group(1)}.qa@MENA" if m else "Alkass.qa@MENA"
    if re.fullmatch(r"beinsportsnews", raw, re.I):
        # Preserve the ID already used in the user's M3U example.
        return "beINSportsNews.qa@SD"
    if re.fullmatch(r"beinsports?", raw, re.I):
        return "beINSports.qa@MENA"
    if re.search(r"4k", raw, re.I) or (not raw and "4k" in name.lower()):
        return "beINSports4K.qa@MENA"

    m = re.fullmatch(r"beinsportsxtra(\d+)", raw, re.I)
    if m:
        return f"beINSportsXTRA{m.group(1)}.qa@MENA"
    m = re.fullmatch(r"beinsportsmax(\d+)", raw, re.I)
    if m:
        return f"beINSportsMAX{m.group(1)}.qa@MENA"
    m = re.fullmatch(r"beinsports?(\d+)(en|fr)?", raw, re.I)
    if m:
        suffix = (m.group(2) or "").upper()
        return f"beINSports{m.group(1)}{suffix}.qa@MENA"

    # For other channels, turn the source slug into a stable XML-safe ID.
    clean = re.sub(r"[^A-Za-z0-9._-]+", "", raw)
    if not clean:
        clean = re.sub(r"[^A-Za-z0-9._-]+", "", name.replace(" ", "")) or "Channel"
    return f"{clean}.qa@MENA"


def get_datetime_from_ms(value: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=XML_TZ)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def parse_channel_rows(html: str, page_category: str) -> tuple[dict[str, Channel], list[Programme]]:
    """Parse channel-row/prog-block markup from the page or its AJAX response."""
    soup = BeautifulSoup(html, "html.parser")
    channels: dict[str, Channel] = {}
    programmes: list[Programme] = []

    for row in soup.select("div.channel-row"):
        slug, channel_url, logo = _slug_from_row(row)
        if not slug and not logo and not channel_url:
            continue
        name = channel_name(slug, logo)
        tvg_id = channel_tvg_id(slug, name)
        channel = channels.get(tvg_id)
        if channel is None:
            channel = Channel(
                tvg_id=tvg_id,
                name=name,
                logo=logo,
                channel_url=channel_url,
                group_title=page_category.title(),
            )
            channels[tvg_id] = channel
        else:
            # Prefer non-empty values when the same channel is returned for another day.
            if not channel.logo and logo:
                channel.logo = logo
            if not channel.channel_url and channel_url:
                channel.channel_url = channel_url
            if not channel.group_title:
                channel.group_title = page_category.title()

        for block in row.select("div.prog-block"):
            title = (block.get("data-full-title") or "").strip()
            if not title:
                title_node = block.select_one(".prog-title-text") or block.select_one(".prog-title")
                title = title_node.get_text(" ", strip=True) if title_node else ""
            if not title:
                continue

            start = get_datetime_from_ms(block.get("data-start-ms", ""))
            stop = get_datetime_from_ms(block.get("data-end-ms", ""))
            if not start or not stop or stop <= start:
                # Timestamp attributes are expected in the source; skip malformed entries
                # rather than silently generating incorrect programme times.
                continue

            prog_category = (block.get("data-full-category") or "").strip()
            if not prog_category:
                category_node = block.select_one(".prog-category")
                prog_category = category_node.get_text(" ", strip=True) if category_node else ""

            program = Programme(
                channel_id=tvg_id,
                start=start,
                stop=stop,
                title=title,
                category=prog_category,
            )
            key = (start.strftime("%Y%m%d%H%M%S %z"), stop.strftime("%Y%m%d%H%M%S %z"), title)
            if key not in channel.programme_keys:
                channel.programme_keys.add(key)
                programmes.append(program)

    return channels, programmes


def extract_html_from_response(response: requests.Response) -> str:
    """Handle either a plain HTML fragment or a JSON response containing HTML."""
    text = response.text or ""
    if "channel-row" in text or "prog-block" in text:
        return text
    try:
        data = response.json()
    except ValueError:
        return text

    candidates: list[str] = []
    def walk(value):
        if isinstance(value, str):
            if "channel-row" in value or "prog-block" in value:
                candidates.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(data)
    return max(candidates, key=len) if candidates else text


def fetch_page_html(session: requests.Session, category: str, day: date, timeout: int) -> str:
    params = {
        "action": "epg_fetch",
        "offset": "0",
        "category": category,
        "serviceidentity": "bein.net",
        "mins": "00",
        "cdate": day.isoformat(),
        "language": "EN",
        "postid": "25356",
        "loadindex": "0",
    }
    response = session.get(AJAX_URL, params=params, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    html = extract_html_from_response(response)
    if "channel-row" not in html:
        short = re.sub(r"\s+", " ", html[:240]).strip()
        raise RuntimeError(
            f"لم أجد channel-row في رد beIN للقسم {category} بتاريخ {day}. "
            f"قد يكون الموقع غيّر طريقة الطلب. بداية الرد: {short!r}"
        )
    return html


def xmltv_timestamp(value: datetime) -> str:
    # XMLTV accepts an explicit UTC offset; timestamps retain the exact source instant.
    return value.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S +0000")


def write_xml(path: Path, channels: dict[str, Channel], programmes: Iterable[Programme]) -> int:
    root = ET.Element(
        "tv",
        {
            "source-info-name": "beIN TV Guide",
            "source-info-url": PAGE_URL,
            "generator-info-name": "DZGreen beIN EPG",
        },
    )

    for channel in sorted(channels.values(), key=lambda item: item.name.casefold()):
        node = ET.SubElement(root, "channel", {"id": channel.tvg_id})
        ET.SubElement(node, "display-name", {"lang": "en"}).text = channel.name
        if channel.logo:
            ET.SubElement(node, "icon", {"src": channel.logo})

    items = sorted(programmes, key=lambda p: (p.start, p.channel_id, p.title.casefold()))
    for programme in items:
        attrs = {
            "start": xmltv_timestamp(programme.start),
            "stop": xmltv_timestamp(programme.stop),
            "channel": programme.channel_id,
        }
        node = ET.SubElement(root, "programme", attrs)
        ET.SubElement(node, "title", {"lang": "en"}).text = programme.title
        if programme.category:
            ET.SubElement(node, "category", {"lang": "en"}).text = programme.category
        # No <desc> is added because the inspected source does not provide descriptions.

    ET.indent(root, space="  ")
    tree = ET.ElementTree(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return len(items)


def write_csv(path: Path, channels: dict[str, Channel], programme_counts: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "tvg_id",
                "tvg_name",
                "tvg_logo",
                "group_title",
                "channel_url",
                "programmes_count",
                "status",
            ],
        )
        writer.writeheader()
        for channel in sorted(channels.values(), key=lambda item: item.name.casefold()):
            count = programme_counts.get(channel.tvg_id, 0)
            writer.writerow(
                {
                    "tvg_id": channel.tvg_id,
                    "tvg_name": channel.name,
                    "tvg_logo": channel.logo,
                    "group_title": channel.group_title,
                    "channel_url": channel.channel_url,
                    "programmes_count": count,
                    "status": "OK" if count else "NO_PROGRAMMES_IN_SOURCE",
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch beIN TV guide and create an XMLTV file plus a tvg-id CSV mapping."
    )
    parser.add_argument("--days", type=int, default=4, help="عدد الأيام (الافتراضي 4).")
    parser.add_argument("--start-date", default="", help="تاريخ البداية بصيغة YYYY-MM-DD؛ الافتراضي اليوم.")
    parser.add_argument("--output-dir", default="docs", help="مجلد حفظ XML وCSV؛ الافتراضي docs.")
    parser.add_argument("--timeout", type=int, default=30, help="مهلة طلب الموقع بالثواني.")
    args = parser.parse_args()

    if args.days < 1 or args.days > 14:
        parser.error("--days يجب أن يكون بين 1 و14.")
    if args.timeout < 5:
        parser.error("--timeout يجب أن يكون 5 ثوانٍ أو أكثر.")
    try:
        start_day = date.fromisoformat(args.start_date) if args.start_date else datetime.now(ZoneInfo("Africa/Algiers")).date()
    except ValueError:
        parser.error("--start-date يجب أن يكون بصيغة YYYY-MM-DD.")

    output_dir = Path(args.output_dir).expanduser().resolve()
    all_channels: dict[str, Channel] = {}
    all_programmes: dict[tuple[str, str, str, str], Programme] = {}

    session = requests.Session()
    session.headers.update(HEADERS)

    total_requests = args.days * len(CATEGORIES)
    request_index = 0
    for day_offset in range(args.days):
        day = start_day + timedelta(days=day_offset)
        for category in CATEGORIES:
            request_index += 1
            print(f"[{request_index}/{total_requests}] تحميل {category} — {day.isoformat()} ...")
            try:
                html = fetch_page_html(session, category, day, args.timeout)
            except (requests.RequestException, RuntimeError) as exc:
                print(f"خطأ: {exc}", file=sys.stderr)
                return 2

            channels, programmes = parse_channel_rows(html, category)
            if not channels:
                print(f"تنبيه: لم تُكتشف قنوات في {category} بتاريخ {day}.", file=sys.stderr)

            for tvg_id, channel in channels.items():
                if tvg_id not in all_channels:
                    all_channels[tvg_id] = channel
                else:
                    old = all_channels[tvg_id]
                    if not old.logo and channel.logo:
                        old.logo = channel.logo
                    if not old.channel_url and channel.channel_url:
                        old.channel_url = channel.channel_url
                    if old.group_title != channel.group_title and channel.group_title:
                        groups = set(filter(None, old.group_title.split(" / ")))
                        groups.add(channel.group_title)
                        old.group_title = " / ".join(sorted(groups))

            for programme in programmes:
                key = (
                    programme.channel_id,
                    xmltv_timestamp(programme.start),
                    xmltv_timestamp(programme.stop),
                    programme.title,
                )
                all_programmes[key] = programme
            # Avoid hammering the server; small pause between requests.
            time.sleep(0.35)

    if not all_channels:
        print("لم يتم اكتشاف أي قناة؛ لم تُنشأ ملفات فارغة.", file=sys.stderr)
        return 3

    programme_list = list(all_programmes.values())
    counts: dict[str, int] = {}
    for programme in programme_list:
        counts[programme.channel_id] = counts.get(programme.channel_id, 0) + 1

    xml_path = output_dir / XML_FILENAME
    csv_path = output_dir / CSV_FILENAME
    try:
        programme_count = write_xml(xml_path, all_channels, programme_list)
        write_csv(csv_path, all_channels, counts)
    except OSError as exc:
        print(f"تعذر حفظ الملفات: {exc}", file=sys.stderr)
        return 4

    print("\nاكتمل التصدير:")
    print(f"القنوات: {len(all_channels)}")
    print(f"البرامج: {programme_count}")
    print(f"XMLTV: {xml_path}")
    print(f"CSV:   {csv_path}")
    print("تذكير: يجب أن يطابق tvg-id في قائمة M3U قيمة id في عنصر channel داخل XML.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
