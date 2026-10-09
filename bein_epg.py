# -*- coding: utf-8 -*-
"""DZGreen beIN EPG scraper. Requires: pip install playwright; playwright install chromium."""
import csv
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, unquote

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://www.bein.com/en/tv-guide/?c=dz&"
OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_XML = OUTPUT_DIR / "BeIN-EPG.xml"
OUTPUT_CSV = OUTPUT_DIR / "BeIN-Channels.csv"
CATEGORIES = [("sports", "Sports"), ("entertainment", "Entertainment")]
BEIN_TZ = timezone(timedelta(hours=3))


def clean_text(value):
    return " ".join(str(value or "").split()).strip()


def xml_escape(value):
    return (str(value or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;"))


def channel_id_from_href(href):
    try:
        path = unquote(urlparse(href or "").path).strip("/")
        return path.split("/")[-1].strip() if path else ""
    except Exception:
        return ""


def channel_name_from_logo(logo_url, alt_text=""):
    alt_text = clean_text(alt_text)
    if alt_text and alt_text.lower() not in {"channel logo", "logo"}:
        return alt_text
    if not logo_url:
        return ""
    filename = unquote(urlparse(logo_url).path.split("/")[-1])
    filename = re.sub(r"\.(png|jpe?g|webp|svg)$", "", filename, flags=re.I)
    filename = re.sub(r"^2023_", "", filename, flags=re.I)
    filename = re.sub(r"(_digital_mono|_mono|_digital)$", "", filename, flags=re.I)
    filename = clean_text(filename.replace("_", " "))
    low = filename.lower()
    if "4k" in low:
        return "beIN 4K"
    if "bara3em" in low or "baraem" in low:
        return "Baraem"
    if "bein sports" in low:
        filename = re.sub(r"\bbein sports\s*", "beIN SPORTS ", filename, flags=re.I)
    return filename


def get_channel_identity(row):
    info = row.evaluate("""row => {
      const img = row.querySelector('.channel-col img, img');
      const link = row.querySelector('.channel-col a[href], a[href]');
      const name = row.querySelector('.channel-name, .channel-title, .channel-col .name');
      return {
        href: link ? (link.getAttribute('href') || '') : '',
        logo: img ? (img.getAttribute('src') || img.getAttribute('data-src') || '') : '',
        alt: img ? (img.getAttribute('alt') || '') : '',
        name: name ? (name.innerText || name.getAttribute('title') || '') : ''
      };
    }""")
    href = clean_text(info.get("href"))
    logo = clean_text(info.get("logo"))
    channel_id = channel_id_from_href(href)
    name = clean_text(info.get("name")) or channel_name_from_logo(logo, info.get("alt", ""))
    if not channel_id:
        channel_id = name
    slug = channel_id.lower()
    if slug in {"baraemtv", "baraem"}:
        name = "Baraem"
    elif slug in {"beinsports4k", "bein4k", "4k"}:
        name = "beIN 4K"
    return {
        "channel_id": channel_id,
        "channel_name": name or channel_id,
        "logo_url": logo,
        "bein_url": href,
    }


def ms_to_datetime(ms):
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).astimezone(BEIN_TZ)


def xmltv_datetime(dt):
    return dt.strftime("%Y%m%d%H%M%S %z")


def create_xml(channels, programs):
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<tv generator-info-name="DZGreen beIN EPG" source-info-name="beIN">',
    ]
    for channel_id in sorted(channels, key=str.casefold):
        ch = channels[channel_id]
        lines.append(f'  <channel id="{xml_escape(channel_id)}">')
        lines.append(f'    <display-name>{xml_escape(ch["channel_name"])}</display-name>')
        if ch.get("logo_url"):
            lines.append(f'    <icon src="{xml_escape(ch["logo_url"])}"/>')
        lines.append("  </channel>")
    for p in sorted(programs, key=lambda x: (x["channel_id"].casefold(), x["start_ms"])):
        start = xmltv_datetime(ms_to_datetime(p["start_ms"]))
        stop = xmltv_datetime(ms_to_datetime(p["end_ms"]))
        lines.append(f'  <programme start="{start}" stop="{stop}" channel="{xml_escape(p["channel_id"])}">')
        lines.append(f'    <title lang="en">{xml_escape(p["title"])}</title>')
        if p.get("category"):
            lines.append(f'    <category lang="en">{xml_escape(p["category"])}</category>')
        lines.append("  </programme>")
    lines.append("</tv>")
    OUTPUT_XML.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Created {OUTPUT_XML}: {len(channels)} channels, {len(programs)} programmes")


def create_csv(channels):
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["channel_name", "tvg_id", "logo_url", "bein_url", "category"])
        for channel_id in sorted(channels, key=str.casefold):
            ch = channels[channel_id]
            writer.writerow([ch["channel_name"], channel_id, ch.get("logo_url", ""),
                             ch.get("bein_url", ""), ch.get("category", "")])
    print(f"Created {OUTPUT_CSV}")


def read_dates(page):
    page.wait_for_selector(".day-cell", timeout=60000)
    return page.locator(".day-cell").evaluate_all(
        "cells => [...new Set(cells.map(c => c.getAttribute('data-date')).filter(Boolean))]"
    )


def wait_for_rows(page):
    # Avoid networkidle: the guide may keep background requests open.
    page.wait_for_selector(".channel-row", timeout=60000)
    page.wait_for_timeout(800)


def read_programs(row, channel_id):
    return row.evaluate("""(row, id) => Array.from(row.querySelectorAll('.prog-block')).map(b => ({
      channel_id: id,
      title: b.getAttribute('data-full-title') || '',
      category: b.getAttribute('data-full-category') || '',
      start_ms: b.getAttribute('data-start-ms') || '',
      end_ms: b.getAttribute('data-end-ms') || ''
    }))""", channel_id)


def main():
    started = time.time()
    channels, programs, program_keys, warnings = {}, [], set(), []
    print("=" * 60)
    print("DZGreen - beIN EPG | English guide | Algeria")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000},
                                locale="en-US", timezone_id="Asia/Qatar")
        print("Opening:", BASE_URL)
        page.goto(BASE_URL, wait_until="domcontentloaded", timeout=120000)
        wait_for_rows(page)
        initial_dates = read_dates(page)
        print("Dates found:", len(initial_dates), initial_dates)

        for code, category_name in CATEGORIES:
            tab = page.locator(f'.category-tab[data-category="{code}"]').first
            if tab.count() == 0:
                print("WARNING: category tab missing:", code)
                warnings.append("category:" + code)
                continue
            tab.click()
            page.wait_for_timeout(1000)
            try:
                dates = read_dates(page) or initial_dates
            except PlaywrightTimeoutError:
                dates = initial_dates

            for date_value in dates:
                cell = page.locator(f'.day-cell[data-date="{date_value}"]').first
                if cell.count() == 0:
                    warnings.append(f"{code}:{date_value} date button missing")
                    continue
                cell.click()
                try:
                    wait_for_rows(page)
                except PlaywrightTimeoutError:
                    print("WARNING: rows did not load:", code, date_value)
                    warnings.append(f"{code}:{date_value} rows missing")
                    continue

                rows = page.locator(".channel-row")
                count = rows.count()
                before = len(programs)
                print(f"{category_name} | {date_value} | rows={count}")

                for ri in range(count):
                    row = rows.nth(ri)
                    try:
                        ident = get_channel_identity(row)
                    except Exception as exc:
                        print(f"WARNING: cannot read row {ri + 1}: {exc}")
                        continue
                    cid = clean_text(ident["channel_id"])
                    if not cid:
                        print(f"WARNING: row {ri + 1} has no channel ID/name/logo")
                        continue

                    if cid not in channels:
                        channels[cid] = {
                            "channel_name": ident["channel_name"],
                            "logo_url": ident["logo_url"],
                            "bein_url": ident["bein_url"],
                            "category": category_name,
                        }
                    else:
                        old = channels[cid]
                        for field in ("logo_url", "bein_url"):
                            if not old.get(field) and ident.get(field):
                                old[field] = ident[field]
                        if not old.get("channel_name") and ident.get("channel_name"):
                            old["channel_name"] = ident["channel_name"]

                    try:
                        row_programs = read_programs(row, cid)
                    except Exception as exc:
                        print(f"WARNING: cannot read programmes for {cid}: {exc}")
                        continue

                    for item in row_programs:
                        title = clean_text(item.get("title"))
                        category = clean_text(item.get("category"))
                        try:
                            start_ms, end_ms = int(item["start_ms"]), int(item["end_ms"])
                        except (ValueError, TypeError, KeyError):
                            continue
                        if not title or end_ms <= start_ms:
                            continue
                        # Only exact duplicates are removed; same title at a different time remains.
                        key = (cid, start_ms, end_ms, title)
                        if key in program_keys:
                            continue
                        program_keys.add(key)
                        programs.append({"channel_id": cid, "title": title, "category": category,
                                         "start_ms": start_ms, "end_ms": end_ms})
                print(f"  Programmes added: {len(programs) - before}; total channels: {len(channels)}")
        browser.close()

    if not channels:
        raise RuntimeError("No channels were extracted; XML/CSV were not generated.")

    create_xml(channels, programs)
    create_csv(channels)
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("Channels:", len(channels))
    print("Programmes:", len(programs))
    print("Dates:", len(initial_dates))
    print("Warnings:", len(warnings))
    for warning in warnings:
        print(" -", warning)
    print(f"Finished in {time.time() - started:.1f} seconds")


if __name__ == "__main__":
    main()
