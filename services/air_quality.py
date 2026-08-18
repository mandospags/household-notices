"""DEFRA Daily Air Quality Index forecast, from the public forecast RSS feed
(no API key/auth needed): https://uk-air.defra.gov.uk/assets/rss/forecast.xml

The feed has one <item> per monitoring station, each with a Mon-Fri set of
index values for "this week" and a <pubDate> for when it was built. Labels
are weekday names, not calendar dates, so they're anchored to the Monday of
the pubDate's week. If the feed is stale (e.g. not rebuilt over a weekend),
the resulting dates may fall outside today/tomorrow/day-after - the digest's
existing window filter drops those silently, same as any other source.
"""

import os
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import requests

from .base import Notice

SOURCE = "air_quality"

FEED_URL = "https://uk-air.defra.gov.uk/assets/rss/forecast.xml"

WEEKDAY_OFFSETS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}

DAQI_BANDS = [
    (3, "Low"),
    (6, "Moderate"),
    (9, "High"),
    (10, "Very High"),
]


def _band(index: int) -> str:
    for max_index, name in DAQI_BANDS:
        if index <= max_index:
            return name
    return "Very High"


def fetch(now: datetime) -> list[Notice]:
    station = os.environ["DAQI_STATION"]

    resp = requests.get(FEED_URL, timeout=15)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.text)

    item = next(
        (
            i
            for i in root.iter("item")
            if (i.findtext("title") or "").strip().upper() == station.upper()
        ),
        None,
    )
    if item is None:
        raise ValueError(f"DAQI station {station!r} not found in forecast feed")

    pub_date = parsedate_to_datetime(item.findtext("pubDate")).date()
    monday = pub_date - timedelta(days=pub_date.weekday())

    description = item.findtext("description") or ""
    notices = []
    for day_abbr, index_str in re.findall(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun): (\d+)", description):
        forecast_date = monday + timedelta(days=WEEKDAY_OFFSETS[day_abbr])
        index = int(index_str)
        notices.append(
            Notice(
                source=SOURCE,
                title=f"Air quality: {_band(index)} ({index})",
                date=forecast_date,
            )
        )
    return notices
