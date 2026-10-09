#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Generate beIN XMLTV guide and channel mapping CSV."""

from __future__ import annotations

import argparse
import csv
import re
import sys
import xml.etree.ElementTree as ET

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse, unquote

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


PAGE_URL = "https://www.bein.com/en/tv-guide/?c=dz&"

XML_FILENAME = "BeIN-EPG.xml"
CSV_FILENAME = "BeIN-Channel-Mapping.csv"

CATEGORIES = ("sports", "entertainment")
XML_TZ = timezone.utc


@dataclass
class Channel:
    tvg_id: str
    name: str
    logo: str = ""
    channel_url: str = ""
    group_title: str = ""
    programme_keys: set[tuple[str, str, str]] = field(
        default_factory=set
    )


@dataclass(frozen=True)
class Programme:
    channel_id: str
    start: datetime
    stop: datetime
    title: str
    category: str = ""


def _slug_from_row(row) -> tuple[str, str, str]:
    """Return channel slug, page URL and logo URL."""

    anchor = (
        row.select_one(".channel-col a[href]")
        or row.select_one("a[href]")
    )

    channel_url = (
        anchor.get("href", "").strip()
        if anchor else ""
    )

    image = (
        row.select_one(".channel-col img[src]")
        or row.select_one("img[src]")
    )

    logo = (
        image.get("src", "").strip()
        if image else ""
    )

    slug = ""

    if channel_url:
        slug = unquote(
            urlparse(channel_url).path.rstrip("/").split("/")[-1]
        )

    if not slug and logo:
        filename = Path(urlparse(logo).path).name

        alkass = re.search(
            r"alkass[_-]?(\d+)", filename, re.I
        )

        if alkass:
            slug = f"Alkass{alkass.group(1)}"

        elif re.search(r"4k", filename, re.I):
            slug = "beINSPORTS4K"

        else:
            slug = re.sub(
                r"\.(png|jpe?g|webp|svg)$",
                "",
                filename,
                flags=re.I,
            )

            slug = re.sub(
                r"(?:_DIGITAL_Mono|_DIGITAL|_Mono|logos?[-_]).*$",
                "",
                slug,
                flags=re.I,
            )

    return slug, channel_url, logo


def channel_name(slug: str, logo_url: str = "") -> str:
    """Convert channel slugs into readable names."""

    raw = slug.strip()
    low = raw.lower()

    if "alkass" in low:
        match = re.search(
            r"alkass[_-]?(\d+)", raw, re.I
        )

        if not match:
            match = re.search(
                r"alkass[_-]?(\d+)",
                urlparse(logo_url).path,
                re.I,
            )

        return (
            f"Alkass {match.group(1)}"
            if match else "Alkass"
        )

    if (
        re.fullmatch(r"4k", low)
        or "4k" in low
        or re.search(r"4k", logo_url, re.I)
    ):
        return "beIN SPORTS 4K"

    if re.fullmatch(r"beinsports?", low):
        return "beIN SPORTS"

    if re.fullmatch(r"beinsportsnews", low):
        return "beIN SPORTS News"

    match = re.fullmatch(
        r"beinsportsxtra(\d+)", raw, re.I
    )

    if match:
        return f"beIN SPORTS XTRA {match.group(1)}"

    match = re.fullmatch(
        r"beinsportsmax(\d+)", raw, re.I
    )

    if match:
        return f"beIN SPORTS MAX {match.group(1)}"

    match = re.fullmatch(
        r"beinsports?(\d+)(en|fr)?", raw, re.I
    )

    if match:
        suffix = (
            f" {match.group(2).upper()}"
            if match.group(2) else ""
        )

        return f"beIN SPORTS {match.group(1)}{suffix}"

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

    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw)
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    name = re.sub(r"(?<=\D)(\d+)", r" \1", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"^be\s*in\b", "beIN", name, flags=re.I)
    name = re.sub(r"\bSports\b", "SPORTS", name, flags=re.I)
    name = re.sub(r"\bMovies\b", "MOVIES", name, flags=re.I)
    name = re.sub(r"\bSeries\b", "SERIES", name, flags=re.I)

    return name or raw or "Unknown Channel"


def channel_tvg_id(slug: str, name: str) -> str:
    """Generate stable channel IDs."""

    raw = slug.strip()
    low = raw.lower()

    if "alkass" in low:
        match = re.search(
            r"alkass[_-]?(\d+)", raw, re.I
        )

        if not match:
            match = re.search(
                r"alkass[_-]?(\d+)", name, re.I
            )

        return (
            f"Alkass{match.group(1)}.qa@MENA"
            if match else "Alkass.qa@MENA"
        )

    if re.fullmatch(r"beinsportsnews", raw, re.I):
        return "beINSportsNews.qa@SD"

    if re.fullmatch(r"beinsports?", raw, re.I):
        return "beINSports.qa@MENA"

    if re.search(r"4k", raw, re.I) or (
        not raw and "4k" in name.lower()
    ):
        return "beINSports4K.qa@MENA"

    match = re.fullmatch(
        r"beinsportsxtra(\d+)", raw, re.I
    )

    if match:
        return f"beINSportsXTRA{match.group(1)}.qa@MENA"

    match = re.fullmatch(
        r"beinsportsmax(\d+)", raw, re.I
    )

    if match:
        return f"beINSportsMAX{match.group(1)}.qa@MENA"

    match = re.fullmatch(
        r"beinsports?(\d+)(en|fr)?", raw, re.I
    )

    if match:
        suffix = (match.group(2) or "").upper()

        return (
            f"beINSports{match.group(1)}{suffix}.qa@MENA"
        )

    clean = re.sub(r"[^A-Za-z0-9._-]+", "", raw)

    if not clean:
        clean = (
            re.sub(r"[^A-Za-z0-9._-]+", "", name.replace(" ", ""))
            or "Channel"
        )

    return f"{clean}.qa@MENA"


def get_datetime_from_ms(value: str) -> datetime | None:
    """Convert Unix milliseconds to UTC datetime."""

    try:
        return datetime.fromtimestamp(
            int(value) / 1000.0,
            tz=XML_TZ,
        )

    except (TypeError, ValueError, OverflowError, OSError):
        return None


def parse_channel_rows(
    html: str,
    page_category: str,
) -> tuple[dict[str, Channel], list[Programme]]:
    """Parse channels and programmes from page HTML."""

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
            if not channel.logo and logo:
                channel.logo = logo

            if not channel.channel_url and channel_url:
                channel.channel_url = channel_url

        for block in row.select("div.prog-block"):

            title = (
                block.get("data-full-title") or ""
            ).strip()

            if not title:
                title_node = (
                    block.select_one(".prog-title-text")
                    or block.select_one(".prog-title")
                )

                title = (
                    title_node.get_text(" ", strip=True)
                    if title_node else ""
                )

            if not title:
                continue

            start = get_datetime_from_ms(
                block.get("data-start-ms", "")
            )

            stop = get_datetime_from_ms(
                block.get("data-end-ms", "")
            )

            if not start or not stop or stop <= start:
                continue

            prog_category = (
                block.get("data-full-category") or ""
            ).strip()

            if not prog_category:
                category_node = block.select_one(
                    ".prog-category"
                )

                prog_category = (
                    category_node.get_text(" ", strip=True)
                    if category_node else ""
                )

            program = Programme(
                channel_id=tvg_id,
                start=start,
                stop=stop,
                title=title,
                category=prog_category,
            )

            key = (
                start.strftime("%Y%m%d%H%M%S %z"),
                stop.strftime("%Y%m%d%H%M%S %z"),
                title,
            )

            if key not in channel.programme_keys:
                channel.programme_keys.add(key)
                programmes.append(program)

    return channels, programmes


def xmltv_timestamp(value: datetime) -> str:
    """Format timestamps in XMLTV UTC format."""

    return value.astimezone(
        timezone.utc
    ).strftime("%Y%m%d%H%M%S +0000")


def write_xml(
    path: Path,
    channels: dict[str, Channel],
    programmes: Iterable[Programme],
) -> int:

    root = ET.Element(
        "tv",
        {
            "source-info-name": "beIN TV Guide",
            "source-info-url": PAGE_URL,
            "generator-info-name": "DZGreen beIN EPG",
        },
    )

    for channel in sorted(
        channels.values(),
        key=lambda item: item.name.casefold(),
    ):

        node = ET.SubElement(
            root,
            "channel",
            {"id": channel.tvg_id},
        )

        ET.SubElement(
            node,
            "display-name",
            {"lang": "en"},
        ).text = channel.name

        if channel.logo:
            ET.SubElement(
                node,
                "icon",
                {"src": channel.logo},
            )

    items = sorted(
        programmes,
        key=lambda item: (
            item.start,
            item.channel_id,
            item.title.casefold(),
        ),
    )

    for programme in items:

        attrs = {
            "start": xmltv_timestamp(programme.start),
            "stop": xmltv_timestamp(programme.stop),
            "channel": programme.channel_id,
        }

        node = ET.SubElement(root, "programme", attrs)

        ET.SubElement(
            node,
            "title",
            {"lang": "en"},
        ).text = programme.title

        if programme.category:
            ET.SubElement(
                node,
                "category",
                {"lang": "en"},
            ).text = programme.category

    ET.indent(root, space="  ")

    path.parent.mkdir(parents=True, exist_ok=True)

    tree = ET.ElementTree(root)

    tree.write(
        path,
        encoding="utf-8",
        xml_declaration=True,
    )

    return len(items)


def write_csv(
    path: Path,
    channels: dict[str, Channel],
    programme_counts: dict[str, int],
) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
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

        for channel in sorted(
            channels.values(),
            key=lambda item: item.name.casefold(),
        ):

            count = programme_counts.get(
                channel.tvg_id, 0
            )

            writer.writerow(
                {
                    "tvg_id": channel.tvg_id,
                    "tvg_name": channel.name,
                    "tvg_logo": channel.logo,
                    "group_title": channel.group_title,
                    "channel_url": channel.channel_url,
                    "programmes_count": count,
                    "status": (
                        "OK"
                        if count
                        else "NO_PROGRAMMES_IN_SOURCE"
                    ),
                }
            )


# =========================================================
# PLAYWRIGHT: WAIT FOR PAGE CONTENT
# =========================================================

def wait_for_content_change(
    page,
    previous_html,
    active_selector,
    attribute,
    value,
    timeout_ms,
):
    """Wait until the selected tab/date is active and content changes."""

    page.wait_for_function(
        """arg => {
            const root = document.querySelector('#channelRows');
            const active = document.querySelector(arg.activeSelector);

            return root
                && active
                && active.getAttribute(arg.attribute) === arg.value
                && root.innerHTML !== arg.previousHtml
                && root.querySelector('.prog-block');
        }""",
        {
            "previousHtml": previous_html,
            "activeSelector": active_selector,
            "attribute": attribute,
            "value": value,
        },
        timeout=timeout_ms,
    )


def activate_category(page, category, timeout_ms):
    """Activate Sports or Entertainment."""

    tab = page.locator(
        f'.category-tab[data-category="{category}"]'
    ).first

    if tab.count() == 0:
        raise RuntimeError(
            f"Category tab not found: {category}"
        )

    classes = (
        tab.get_attribute("class") or ""
    ).split()

    if "active" in classes:
        return

    previous_html = page.locator(
        "#channelRows"
    ).inner_html()

    tab.click()

    wait_for_content_change(
        page,
        previous_html,
        ".category-tab.active",
        "data-category",
        category,
        timeout_ms,
    )


def activate_date(page, date_value, timeout_ms):
    """Activate the requested date."""

    cell = page.locator(
        f'.day-cell[data-date="{date_value}"]'
    ).first

    if cell.count() == 0:
        raise RuntimeError(
            f"Date not found: {date_value}"
        )

    classes = (
        cell.get_attribute("class") or ""
    ).split()

    if "active" in classes:
        return

    previous_html = page.locator(
        "#channelRows"
    ).inner_html()

    cell.click()

    wait_for_content_change(
        page,
        previous_html,
        ".day-cell.active",
        "data-date",
        date_value,
        timeout_ms,
    )


# =========================================================
# MAIN
# =========================================================

def main() -> int:

    parser = argparse.ArgumentParser(
        description=(
            "Generate beIN XMLTV and channel mapping CSV."
        )
    )

    parser.add_argument(
        "--days",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--start-date",
        default="",
    )

    parser.add_argument(
        "--output-dir",
        default="docs",
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
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

    requested_start = ""

    if args.start_date:
        try:
            requested_start = date.fromisoformat(
                args.start_date
            ).isoformat()

        except ValueError:
            parser.error(
                "--start-date must use YYYY-MM-DD."
            )

    timeout_ms = args.timeout * 1000

    output_dir = Path(
        args.output_dir
    ).expanduser().resolve()

    all_channels: dict[str, Channel] = {}
    all_programmes: dict[
        tuple[str, str, str, str], Programme
    ] = {}

    warnings = []

    try:
        with sync_playwright() as playwright:

            browser = playwright.chromium.launch(
                headless=True
            )

            try:
                context = browser.new_context(
                    viewport={
                        "width": 1600,
                        "height": 1200,
                    },
                    locale="en-US",
                    timezone_id="Africa/Algiers",
                )

                page = context.new_page()

                print("Opening the beIN TV Guide...")

                page.goto(
                    PAGE_URL,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )

                page.wait_for_selector(
                    ".category-tab",
                    timeout=timeout_ms,
                )

                page.wait_for_selector(
                    ".day-cell",
                    timeout=timeout_ms,
                )

                page.wait_for_selector(
                    "#channelRows .channel-row",
                    timeout=timeout_ms,
                )

                available_dates = page.locator(
                    ".day-cell"
                ).evaluate_all(
                    """cells => [...new Set(
                        cells
                            .map(c => c.getAttribute('data-date'))
                            .filter(Boolean)
                    )]"""
                )

                if not available_dates:
                    raise RuntimeError(
                        "No dates found on the page."
                    )

                if requested_start:
                    if requested_start not in available_dates:
                        raise RuntimeError(
                            f"Date {requested_start} is not visible. "
                            f"Available dates: {available_dates}"
                        )

                    start_index = available_dates.index(
                        requested_start
                    )

                    available_dates = available_dates[
                        start_index:
                    ]

                dates = available_dates[:args.days]

                if len(dates) < args.days:
                    print(
                        f"Warning: only {len(dates)} dates "
                        "are available on the page."
                    )

                total = len(dates) * len(CATEGORIES)
                request_number = 0

                for category in CATEGORIES:

                    print(
                        f"\n=== {category.upper()} ==="
                    )

                    activate_category(
                        page,
                        category,
                        timeout_ms,
                    )

                    for date_value in dates:

                        request_number += 1

                        print(
                            f"[{request_number}/{total}] "
                            f"{category} - {date_value}"
                        )

                        activate_date(
                            page,
                            date_value,
                            timeout_ms,
                        )

                        html = page.content()

                        channels, programmes = parse_channel_rows(
                            html,
                            category,
                        )

                        print(
                            f"  Channels: {len(channels)}"
                            f" | Programmes: {len(programmes)}"
                        )

                        if not channels:
                            warnings.append(
                                f"No channels: {category}, "
                                f"{date_value}"
                            )

                        for channel_id, channel in channels.items():

                            if channel_id not in all_channels:
                                all_channels[channel_id] = channel

                            else:
                                existing = all_channels[channel_id]

                                if not existing.logo and channel.logo:
                                    existing.logo = channel.logo

                                if (
                                    not existing.channel_url
                                    and channel.channel_url
                                ):
                                    existing.channel_url = (
                                        channel.channel_url
                                    )

                                groups = set(
                                    filter(
                                        None,
                                        existing.group_title.split(" / "),
                                    )
                                )

                                if channel.group_title:
                                    groups.add(channel.group_title)

                                existing.group_title = " / ".join(
                                    sorted(groups)
                                )

                        for programme in programmes:

                            key = (
                                programme.channel_id,
                                xmltv_timestamp(programme.start),
                                xmltv_timestamp(programme.stop),
                                programme.title,
                            )

                            all_programmes[key] = programme

                context.close()

            finally:
                browser.close()

    except Exception as exc:

        print(
            f"Error reading the beIN TV Guide: {exc}",
            file=sys.stderr,
        )

        print(
            "No new XML or CSV files were written.",
            file=sys.stderr,
        )

        return 2

    if not all_channels or not all_programmes:

        print(
            "No usable channels or programmes were collected.",
            file=sys.stderr,
        )

        return 3

    programme_list = list(
        all_programmes.values()
    )

    counts: dict[str, int] = {}

    for programme in programme_list:
        counts[programme.channel_id] = (
            counts.get(programme.channel_id, 0) + 1
        )

    try:
        xml_path = output_dir / XML_FILENAME
        csv_path = output_dir / CSV_FILENAME

        write_xml(
            xml_path,
            all_channels,
            programme_list,
        )

        write_csv(
            csv_path,
            all_channels,
            counts,
        )

    except OSError as exc:

        print(
            f"Error saving output files: {exc}",
            file=sys.stderr,
        )

        return 4

    print("\nExport completed.")
    print("Sections: Sports and Entertainment")
    print(f"Channels: {len(all_channels)}")
    print(f"Programmes: {len(programme_list)}")
    print(f"XMLTV: {xml_path}")
    print(f"CSV: {csv_path}")

    for warning in warnings:
        print(f"Warning: {warning}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
