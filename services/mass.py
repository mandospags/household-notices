"""FSSPX district weekly Mass times bulletin (Burghclere chapel), scraped
from the district's public page:
https://fsspx.uk/en/mass-times-calendars-32157

No API/RSS - this is a real HTML table, but a *dated* one: the header row
gives an actual date per column (e.g. "Sun 16th" ... "Sun 23rd"), covering
the current week plus the following Sunday, not a generic recurring weekly
schedule. That means feast-day exceptions (extra/moved Masses) are already
baked into whatever the page shows on a given day - no need to special-case
holy days here.

Gotcha confirmed live: a bare/default `requests` User-Agent gets HTTP 403;
a normal browser UA is required (see HEADERS below).

The page has several tables (other FSSPX UK districts); MASS_CHAPEL_NAME
picks the row by exact link text, so only one table/row ever matches.

Date parsing: only the first day-header cell's (day-of-month, month name)
is turned into a concrete date, picking whichever nearby year lands closest
to `now` - handles the table spanning a Dec/Jan boundary without needing a
second year source, since this page is always fetched fresh and always
represents "this week". Every other column is just +1 day from there, since
the 8 columns are always consecutive calendar days.

A cell's raw text may contain a bare "*" line, which refers to a footnote
printed once further down the page (currently "17:00 Benediction") - looked
up by text search and substituted in, rather than assuming a fixed
footnote each week.
"""

import os
import re
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

from .base import Notice

SOURCE = "mass"

MASS_TIMES_URL = "https://fsspx.uk/en/mass-times-calendars-32157"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def _closest_year(month: int, day: int, now: datetime) -> int:
    candidates = (now.year - 1, now.year, now.year + 1)
    return min(candidates, key=lambda year: abs((date(year, month, day) - now.date()).days))


def _header_dates(soup: BeautifulSoup, now: datetime) -> list[date]:
    month_th = soup.find("th", string=re.compile(r"^[A-Za-z]+$"))
    month = datetime.strptime(month_th.get_text(strip=True), "%b").month

    first_day_th = month_th.find_next_sibling("th")
    day_num = int(re.search(r"\d+", first_day_th.get_text()).group())
    year = _closest_year(month, day_num, now)
    first_date = date(year, month, day_num)

    return [first_date + timedelta(days=offset) for offset in range(8)]


def _footnote_text(soup: BeautifulSoup) -> str:
    footnote = soup.find(string=re.compile(r"Benediction"))
    return footnote.strip().lstrip("*").strip()


def _cell_text(cell, footnote: str) -> str:
    lines = [line.strip() for line in cell.get_text(separator="|").split("|") if line.strip()]
    return ", ".join(footnote if line == "*" else line for line in lines)


def fetch(now: datetime) -> list[Notice]:
    chapel_name = os.environ["MASS_CHAPEL_NAME"]

    resp = requests.get(MASS_TIMES_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    dates = _header_dates(soup, now)
    footnote = _footnote_text(soup)

    link = soup.find("a", string=lambda text: text and text.strip() == chapel_name)
    cells = link.find_parent("tr").find_all("td")[1:]

    today = now.date()
    notices = []
    for col_date, cell in zip(dates, cells):
        if col_date < today:
            continue
        notices.append(
            Notice(source=SOURCE, title=f"Mass: {_cell_text(cell, footnote)}", date=col_date)
        )
    return notices
