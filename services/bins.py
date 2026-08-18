"""Test Valley Borough Council bin collection days, via the iTouchVision
platform their my.testvalley.gov.uk widget calls under the hood.

The API "encrypts" every request/response body with a fixed AES key/IV that
ships in the council's own public JS bundle - it's obfuscation, not a secret,
so it's kept here as a constant rather than in .env. CLIENT_ID/COUNCIL_ID/
ACCESS_KEY are likewise fixed tenant IDs for the whole council, not
per-household credentials.
"""

import json
import os
from datetime import datetime

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from .base import Notice

SOURCE = "bins"

API_URL = "https://iweb.itouchvision.com/portal/itouchvision/"
AES_KEY = bytes.fromhex(
    "F57E76482EE3DC3336495DEDEEF3962671B054FE353E815145E29C5689F72FEC"
)
AES_IV = bytes.fromhex("2CBF4FC35C69B82362D393A4F0B9971A")

CLIENT_ID = 94
COUNCIL_ID = 390
ACCESS_KEY = "13353F039C42D41875EE90553154801A8C058BF4"

DATE_FORMAT = "%d-%m-%Y"  # API returns DD-MM-YYYY, not month-first


def _encrypt(payload: dict) -> str:
    data = json.dumps(payload).encode("utf-8")
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    return cipher.encrypt(pad(data, AES.block_size)).hex()


def _decrypt(hex_body: str) -> dict:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    data = unpad(cipher.decrypt(bytes.fromhex(hex_body)), AES.block_size)
    return json.loads(data)


def _call(path: str, payload: dict) -> dict:
    headers = {
        "Accept": "application/json",
        "Content-type": "application/json; charset=UTF-8",
        "P_PARAMETER": _encrypt(payload),
        "User-Agent": "Mozilla/5.0",
    }
    resp = requests.get(f"{API_URL}{path}", headers=headers, timeout=15)
    resp.raise_for_status()
    return _decrypt(resp.text)


def fetch(now: datetime) -> list[Notice]:
    uprn = os.environ["TVBC_UPRN"]
    data = _call(
        "kmbd/collectionDay",
        {
            "P_UPRN": int(uprn),
            "P_CLIENT_ID": CLIENT_ID,
            "P_ACCESS_KEY": ACCESS_KEY,
            "P_COUNCIL_ID": COUNCIL_ID,
            "P_TIME_ZONE": "GMT",
            "LANG_CODE": "EN",
        },
    )

    notices = []
    for item in data.get("collectionDay", []):
        # followingDay is the collection after next - always outside the
        # today/tomorrow/day-after window this digest cares about, so it's
        # deliberately dropped rather than surfaced.
        collection_date = datetime.strptime(item["collectionDay"], DATE_FORMAT).date()
        notices.append(
            Notice(
                source=SOURCE,
                title=f"{item['binType']} collection",
                date=collection_date,
            )
        )
    return notices
