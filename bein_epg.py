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
# NETWORK DIAGNOSTICS
# ============================================================

def install_network_diagnostics(page):
    """
    Log relevant network responses.
    Call this BEFORE page.goto() so initial requests are captured.
    """

    keywords = (
        "graphql",
        "program",
        "schedule",
        "epg",
        "guide",
        "channel",
        "content",
        "api",
    )

    print(
        "\n========== NETWORK DIAGNOSTIC ENABLED ==========",
        flush=True,
    )

    def on_response(response):
        try:
            url = response.url
            lower_url = url.lower()

            if any(word in lower_url for word in keywords):
                request = response.request

                print(
                    "DATA RESPONSE | "
                    f"status={response.status} | "
                    f"method={request.method} | "
                    f"type={request.resource_type} | "
                    f"url={url}",
                    flush=True,
                )

        except Exception as exc:
            print(
                f"NETWORK DIAGNOSTIC WARNING: {exc}",
                flush=True,
            )

    page.on("response", on_response)

    def on_request_failed(request):
        try:
            lower_url = request.url.lower()

            if any(word in lower_url for word in keywords):
                print(
                    "FAILED REQUEST | "
                    f"type={request.resource_type} | "
                    f"error={request.failure} | "
                    f"url={request.url}",
                    flush=True,
                )

        except Exception:
            pass

    page.on("requestfailed", on_request_failed)


def print_loaded_javascript_files(page):
    """
    Print JavaScript files loaded by the current page.
    """

    print(
        "\n========== LOADED JAVASCRIPT FILES ==========",
        flush=True,
    )

    try:
        scripts = page.locator(
            "script[src]"
        ).evaluate_all(
            """elements => elements.map(
                element => element.src
            )"""
        )

        for script_url in scripts:
            print(script_url, flush=True)

        print(
            f"TOTAL JAVASCRIPT FILES: {len(scripts)}",
            flush=True,
        )

    except Exception as exc:
        print(
            f"Could not list JavaScript files: {exc}",
            flush=True,
        )

    print(
        "=============================================\n",
        flush=True,
    )


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
