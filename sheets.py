"""Google Sheets sinxronlash: har o'zgarishdan keyin GSHEET_URL ga to'liq JSON yuboriladi."""

import asyncio
import json
import logging
import os
import urllib.request

import db

GSHEET_URL = os.getenv("GSHEET_URL", "")
logger = logging.getLogger("loyalty-bot")


def _sync_blocking():
    if not GSHEET_URL:
        return
    plist = [
        {
            "name": p["full_name"], "phone": p["phone"], "category": p["category"],
            "turnover": p["total_turnover"], "percent": db.percent_for(p["total_turnover"]),
            "bonus": p["total_bonus"], "paid": p["paid"], "unpaid": p["total_bonus"] - p["paid"],
        }
        for p in db.clients_with_balance()
    ]
    slist = [
        {"name": r["name"], "count": r["cnt"], "turnover": r["turn"], "bonus": r["bon"]}
        for r in db.sellers_stats()
    ]
    rep = db.report()
    stats = {
        "clients": rep["clients_total"], "sales": rep["sales_count"],
        "turnover": rep["turnover_total"], "bonus": rep["bonus_total"],
        "paid": rep["paid_total"], "unpaid": rep["unpaid_total"],
    }
    body = json.dumps({"partners": plist, "sellers": slist, "stats": stats}).encode("utf-8")
    req = urllib.request.Request(GSHEET_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        logger.warning("Sheet sync: %s", e)


async def sync():
    try:
        await asyncio.to_thread(_sync_blocking)
    except Exception as e:
        logger.warning("sync_sheet: %s", e)


def sync_later():
    """Dashboard'dan chaqirish uchun: javobni kutmasdan fon vazifa sifatida."""
    asyncio.get_running_loop().create_task(sync())
