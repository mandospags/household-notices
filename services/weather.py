"""Met Office severe weather warnings, from the public per-region RSS feed
(no API key/auth needed):
https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/<code>

Warnings are regional (16 fixed UK regions), not postcode-specific - the
region code for the target area is set via METOFFICE_REGION.

At the time this was written every region's feed was empty (no active UK
warnings), so the shape of a populated <item> couldn't be observed directly.
Only title/description/pubDate are guaranteed by RSS 2.0 itself, so that's
all this parses - no assumed severity/valid-from/valid-to fields. A warning's
validity window (if any) ends up as free text inside title/description,
surfaced via detail rather than parsed into structured dates. Each item is
dated by its pubDate (when issued/updated), so a warning that's still valid
several days out won't itself re-appear in "upcoming" once its pubDate ages
past that window - worth revisiting once we've actually seen a live one.
"""

import os
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import requests

from .base import Notice

SOURCE = "weather"

FEED_URL_TEMPLATE = "https://www.metoffice.gov.uk/public/data/PWSCache/WarningsRSS/Region/{region}"


def fetch() -> list[Notice]:
    region = os.environ["METOFFICE_REGION"]

    resp = requests.get(FEED_URL_TEMPLATE.format(region=region), timeout=15)
    resp.raise_for_status()
    root = ElementTree.fromstring(resp.content)

    notices = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        pub_date_text = item.findtext("pubDate")
        issued = parsedate_to_datetime(pub_date_text).date() if pub_date_text else None

        notices.append(
            Notice(
                source=SOURCE,
                title=title or "Weather warning",
                date=issued,
                detail=description or None,
            )
        )
    return notices
