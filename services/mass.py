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

The page has several tables (other FSSPX UK districts, e.g. "SOUTHEAST",
"SOUTH"), each with its own header row and footnote - MASS_CHAPEL_NAME picks
the row by exact link text, and header/footnote lookups are then scoped to
that same `<table>` (via find_parent), not the whole document. Every
district's table happens to cover the same calendar week, so a document-wide
header search would give the same dates by coincidence - but a document-wide
footnote search silently pulled a *different* district's footnote text in
testing (confirmed live against a saved copy of the page), which is why both
are scoped to Burghclere's own table.

Date parsing: only the first day-header cell's (day-of-month, month name)
is turned into a concrete date, picking whichever nearby year lands closest
to `now` - handles the table spanning a Dec/Jan boundary without needing a
second year source, since this page is always fetched fresh and always
represents "this week". Every other column is just +1 day from there, since
the 8 columns are always consecutive calendar days - each computed date's
weekday is cross-checked against the header cell's day abbreviation ("Sun",
"Mon", ...) and raises on mismatch, since a misaligned column would
otherwise produce a wrong-but-plausible-looking digest.

A cell's raw text may contain a bare "*" line, referring to a footnote
printed once further down Burghclere's table (currently "17:00
Benediction"). If no footnote is found there, the "*" is dropped rather than
failing the whole fetch - the footnote is a nice-to-have, not something
worth losing the Mass times over.
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


def _header_ths(table) -> list:
    for row in table.find_all("tr"):
        ths = row.find_all("th", recursive=False)
        if len(ths) == 9:
            return ths
    raise ValueError("mass times header row (month + 8 day cells) not found")


def _header_dates(table, now: datetime) -> list[date]:
    month_th, *day_ths = _header_ths(table)
    month = datetime.strptime(month_th.get_text(strip=True), "%b").month

    first_day_num = int(re.search(r"\d+", day_ths[0].get_text()).group())
    year = _closest_year(month, first_day_num, now)
    first_date = date(year, month, first_day_num)

    dates = [first_date + timedelta(days=offset) for offset in range(8)]
    for expected_date, day_th in zip(dates, day_ths):
        abbrev = day_th.get_text(strip=True)[:3]
        if expected_date.strftime("%a") != abbrev:
            raise ValueError(
                f"mass times header mismatch: expected {expected_date:%a} for "
                f"{expected_date}, header cell says {abbrev!r}"
            )
    return dates


def _footnote_text(table) -> str | None:
    footnote = table.find(string=re.compile(r"Benediction"))
    return footnote.strip().lstrip("*").strip() if footnote else None


def _cell_text(cell, footnote: str | None) -> str:
    lines = [line.strip() for line in cell.get_text(separator="|").split("|") if line.strip()]
    return ", ".join(
        (footnote if line == "*" and footnote else line) for line in lines if line != "*" or footnote
    )


def fetch(now: datetime) -> list[Notice]:
    chapel_name = os.environ["MASS_CHAPEL_NAME"]

    resp = requests.get(MASS_TIMES_URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    link = soup.find("a", string=lambda text: text and text.strip() == chapel_name)
    table = link.find_parent("table")
    cells = link.find_parent("tr").find_all("td")[1:]

    dates = _header_dates(table, now)
    footnote = _footnote_text(table)

    today = now.date()
    notices = []
    for col_date, cell in zip(dates, cells):
        if col_date < today:
            continue
        notices.append(
            Notice(source=SOURCE, title=f"Mass: {_cell_text(cell, footnote)}", date=col_date)
        )
    return notices
