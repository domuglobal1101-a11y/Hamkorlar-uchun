"""Ma'lumotlar qatlami: SQLite sxema, bonus hisobi, to'lovlar, hisobotlar.

Bot ham, dashboard ham faqat shu modul orqali bazaga murojaat qiladi.
"""

import os
import sqlite3
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DB_PATH = os.getenv("DB_PATH", "loyalty.db")
TZ = ZoneInfo("Asia/Tashkent")

# (chegara, foiz) - kattadan kichikka. Foiz shu savdodan OLDINGI jami aylanmaga qarab.
TIERS = [
    (400_000_000, 4),
    (300_000_000, 3),
    (200_000_000, 2),
    (100_000_000, 1),
    (0, 0),
]
CATEGORIES = ["Usta", "Dizayner", "Prorab"]

_lock = threading.RLock()
_conn = None


def connect(path=None):
    """Bazaga ulanadi va sxemani yaratadi/yangilaydi. Testlar boshqa yo'l beradi."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = sqlite3.connect(path or DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _init()


def _init():
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            full_name TEXT,
            category TEXT,
            telegram_id INTEGER UNIQUE,
            total_turnover INTEGER NOT NULL DEFAULT 0,
            total_bonus INTEGER NOT NULL DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            percent INTEGER NOT NULL,
            bonus INTEGER NOT NULL,
            turnover_after INTEGER NOT NULL,
            customer TEXT,
            seller_id INTEGER,
            seller_name TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS owners (
            telegram_id INTEGER PRIMARY KEY,
            name TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sellers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            code TEXT UNIQUE NOT NULL,
            telegram_id INTEGER,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS payouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            note TEXT,
            created_by TEXT,
            created_at TEXT
        );
        """
    )
    # Eski bazalarga yangi ustunlarni qo'shish (ustun bo'lsa - xato, e'tiborsiz)
    for table, col in [
        ("sales", "seller_id INTEGER"),
        ("sales", "seller_name TEXT"),
        ("sales", "status TEXT NOT NULL DEFAULT 'active'"),
        ("sales", "cancelled_at TEXT"),
        ("sales", "cancelled_by TEXT"),
    ]:
        try:
            _conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass
    _conn.commit()


def q(sql, params=(), *, fetch=None):
    with _lock:
        cur = _conn.execute(sql, params)
        if fetch == "one":
            row = cur.fetchone()
        elif fetch == "all":
            row = cur.fetchall()
        else:
            row = None
        _conn.commit()
        return cur.lastrowid if fetch is None else row


# ---------- Vaqt va formatlash ----------

def now():
    return datetime.now(TZ).replace(tzinfo=None)


def now_str():
    return now().isoformat(timespec="seconds")


def clean(text):
    """Foydalanuvchi matnidan HTML belgilarini olib tashlaydi (Telegram HTML xabarlari buzilmasin)."""
    return " ".join((text or "").replace("<", "").replace(">", "").replace("&", "va").split())


def fmt(n):
    return f"{int(n or 0):,}".replace(",", " ")


def percent_for(turnover):
    for threshold, pct in TIERS:
        if turnover >= threshold:
            return pct
    return 0


def next_tier_info(turnover):
    for threshold, pct in sorted(TIERS):
        if turnover < threshold:
            return pct, threshold - turnover
    return None


# ---------- Hamkorlar ----------

def get_client(cid):
    return q("SELECT * FROM clients WHERE id=?", (cid,), fetch="one")


def get_client_by_phone(phone):
    return q("SELECT * FROM clients WHERE phone=?", (phone,), fetch="one")


def get_client_by_tg(tg_id):
    return q("SELECT * FROM clients WHERE telegram_id=?", (tg_id,), fetch="one")


def paid_total(cid):
    return q("SELECT COALESCE(SUM(amount),0) s FROM payouts WHERE client_id=?", (cid,), fetch="one")["s"]


def client_balance(cid):
    """(jami bonus, to'langan, qoldiq). Qoldiq manfiy bo'lsa - ortiqcha to'langan."""
    c = get_client(cid)
    paid = paid_total(cid)
    return c["total_bonus"], paid, c["total_bonus"] - paid


def clients_with_balance(search="", limit=None):
    sql = (
        "SELECT c.*, COALESCE((SELECT SUM(amount) FROM payouts p WHERE p.client_id=c.id),0) AS paid, "
        "(SELECT COUNT(*) FROM sales s WHERE s.client_id=c.id AND s.status='active') AS sales_count "
        "FROM clients c"
    )
    params = []
    if search:
        sql += " WHERE c.full_name LIKE ? OR c.phone LIKE ?"
        params = [f"%{search}%", f"%{search}%"]
    sql += " ORDER BY c.total_turnover DESC, c.id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return q(sql, params, fetch="all")


def create_client(phone, name, category, telegram_id=None):
    return q(
        "INSERT INTO clients(phone, full_name, category, telegram_id, created_at) VALUES(?,?,?,?,?)",
        (phone, name, category, telegram_id, now_str()),
    )


def update_client(cid, name, phone, category):
    q("UPDATE clients SET full_name=?, phone=?, category=? WHERE id=?", (name, phone, category, cid))


def delete_client(cid):
    """Hamkorni savdolari va to'lovlari bilan birga butunlay o'chiradi."""
    with _lock:
        _conn.execute("DELETE FROM sales WHERE client_id=?", (cid,))
        _conn.execute("DELETE FROM payouts WHERE client_id=?", (cid,))
        _conn.execute("DELETE FROM clients WHERE id=?", (cid,))
        _conn.commit()


# ---------- Savdolar ----------

def add_sale(client_id, amount, customer, seller_id, seller_name):
    """Savdoni yozadi va hamkor hisobini yangilaydi. Yangi savdo qatorini qaytaradi."""
    with _lock:
        c = get_client(client_id)
        before = c["total_turnover"]
        pct = percent_for(before)
        bonus = amount * pct // 100
        sid = q(
            "INSERT INTO sales(client_id, amount, percent, bonus, turnover_after, customer, "
            "seller_id, seller_name, created_at, status) VALUES(?,?,?,?,?,?,?,?,?, 'active')",
            (client_id, amount, pct, bonus, before + amount, customer, seller_id, seller_name, now_str()),
        )
        q(
            "UPDATE clients SET total_turnover=?, total_bonus=total_bonus+? WHERE id=?",
            (before + amount, bonus, client_id),
        )
        return get_sale(sid)


def get_sale(sid):
    return q(
        "SELECT s.*, c.full_name AS client_name, c.phone AS client_phone, c.telegram_id AS client_tg "
        "FROM sales s LEFT JOIN clients c ON c.id=s.client_id WHERE s.id=?",
        (sid,), fetch="one",
    )


def recalc_client(cid):
    """Hamkorning barcha faol savdolarini tartib bilan qayta hisoblaydi.

    Forward modelda har savdo foizi oldingi aylanmaga bog'liq, shuning uchun bitta
    savdo bekor qilinsa yoki summasi o'zgarsa, undan keyingilar ham o'zgarishi mumkin.
    Bekor qilingan savdolar aylanmaga kirmaydi, lekin tarix uchun saqlanadi.
    """
    with _lock:
        rows = _conn.execute(
            "SELECT id, amount FROM sales WHERE client_id=? AND status='active' ORDER BY id", (cid,)
        ).fetchall()
        turnover = 0
        total_bonus = 0
        for r in rows:
            pct = percent_for(turnover)
            bonus = r["amount"] * pct // 100
            turnover += r["amount"]
            total_bonus += bonus
            _conn.execute(
                "UPDATE sales SET percent=?, bonus=?, turnover_after=? WHERE id=?",
                (pct, bonus, turnover, r["id"]),
            )
        _conn.execute(
            "UPDATE clients SET total_turnover=?, total_bonus=? WHERE id=?", (turnover, total_bonus, cid)
        )
        _conn.commit()


def cancel_sale(sid, by):
    """Savdoni bekor qiladi. (eski_bonus, yangi_bonus) - hamkorning jami bonusi."""
    s = get_sale(sid)
    old = get_client(s["client_id"])["total_bonus"]
    q("UPDATE sales SET status='cancelled', cancelled_at=?, cancelled_by=? WHERE id=?", (now_str(), by, sid))
    recalc_client(s["client_id"])
    return old, get_client(s["client_id"])["total_bonus"]


def restore_sale(sid):
    s = get_sale(sid)
    q("UPDATE sales SET status='active', cancelled_at=NULL, cancelled_by=NULL WHERE id=?", (sid,))
    recalc_client(s["client_id"])


def update_sale_amount(sid, amount, customer=None):
    s = get_sale(sid)
    if customer is None:
        q("UPDATE sales SET amount=? WHERE id=?", (amount, sid))
    else:
        q("UPDATE sales SET amount=?, customer=? WHERE id=?", (amount, customer, sid))
    recalc_client(s["client_id"])


def delete_sale(sid):
    s = get_sale(sid)
    q("DELETE FROM sales WHERE id=?", (sid,))
    recalc_client(s["client_id"])


def list_sales(date_from=None, date_to=None, client_id=None, seller_id=None, status=None, limit=None):
    sql = (
        "SELECT s.*, c.full_name AS client_name, c.phone AS client_phone "
        "FROM sales s LEFT JOIN clients c ON c.id=s.client_id WHERE 1=1"
    )
    params = []
    if date_from:
        sql += " AND s.created_at >= ?"
        params.append(date_from.isoformat(timespec="seconds"))
    if date_to:
        sql += " AND s.created_at < ?"
        params.append(date_to.isoformat(timespec="seconds"))
    if client_id:
        sql += " AND s.client_id=?"
        params.append(client_id)
    if seller_id:
        sql += " AND s.seller_id=?"
        params.append(seller_id)
    if status:
        sql += " AND s.status=?"
        params.append(status)
    sql += " ORDER BY s.id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return q(sql, params, fetch="all")


# ---------- To'lovlar ----------

def add_payout(cid, amount, note, by):
    return q(
        "INSERT INTO payouts(client_id, amount, note, created_by, created_at) VALUES(?,?,?,?,?)",
        (cid, amount, note, by, now_str()),
    )


def delete_payout(pid):
    q("DELETE FROM payouts WHERE id=?", (pid,))


def list_payouts(client_id=None, date_from=None, date_to=None, limit=None):
    sql = (
        "SELECT p.*, c.full_name AS client_name, c.phone AS client_phone "
        "FROM payouts p LEFT JOIN clients c ON c.id=p.client_id WHERE 1=1"
    )
    params = []
    if client_id:
        sql += " AND p.client_id=?"
        params.append(client_id)
    if date_from:
        sql += " AND p.created_at >= ?"
        params.append(date_from.isoformat(timespec="seconds"))
    if date_to:
        sql += " AND p.created_at < ?"
        params.append(date_to.isoformat(timespec="seconds"))
    sql += " ORDER BY p.id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return q(sql, params, fetch="all")


# ---------- Sotuvchilar ----------

def get_seller(sid):
    return q("SELECT * FROM sellers WHERE id=?", (sid,), fetch="one")


def get_seller_by_tg(tg_id):
    return q("SELECT * FROM sellers WHERE telegram_id=?", (tg_id,), fetch="one")


def get_seller_by_code(code):
    return q("SELECT * FROM sellers WHERE code=?", (code,), fetch="one")


def gen_code():
    import secrets
    while True:
        code = str(secrets.randbelow(900000) + 100000)
        if not get_seller_by_code(code):
            return code


def create_seller(name):
    code = gen_code()
    q("INSERT INTO sellers(name, code, created_at) VALUES(?,?,?)", (name, code, now_str()))
    return code


def reset_seller_code(sid):
    """Yangi kod beradi va eski Telegram ulanishini uzadi."""
    code = gen_code()
    q("UPDATE sellers SET code=?, telegram_id=NULL WHERE id=?", (code, sid))
    return code


def delete_seller(sid):
    q("DELETE FROM sellers WHERE id=?", (sid,))


def sellers_stats(date_from=None, date_to=None):
    cond = "sa.seller_id=s.id AND sa.status='active'"
    params = []
    if date_from:
        cond += " AND sa.created_at >= ?"
        params.append(date_from.isoformat(timespec="seconds"))
    if date_to:
        cond += " AND sa.created_at < ?"
        params.append(date_to.isoformat(timespec="seconds"))
    return q(
        "SELECT s.id, s.name, s.code, s.telegram_id, COUNT(sa.id) cnt, "
        "COALESCE(SUM(sa.amount),0) turn, COALESCE(SUM(sa.bonus),0) bon "
        f"FROM sellers s LEFT JOIN sales sa ON {cond} GROUP BY s.id ORDER BY turn DESC",
        params, fetch="all",
    )


# ---------- Hisobotlar ----------

PERIODS = {
    "today": "Bugun",
    "week": "Shu hafta",
    "month": "Shu oy",
    "all": "Jami",
}


def period_range(key):
    """(boshlanish, tugash) - Toshkent vaqti bilan. 'all' uchun (None, None)."""
    today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    if key == "today":
        return today, today + timedelta(days=1)
    if key == "week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=7)
    if key == "month":
        start = today.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return start, nxt
    return None, None


def report(date_from=None, date_to=None):
    cond = "status='active'"
    params = []
    if date_from:
        cond += " AND created_at >= ?"
        params.append(date_from.isoformat(timespec="seconds"))
    if date_to:
        cond += " AND created_at < ?"
        params.append(date_to.isoformat(timespec="seconds"))
    s = q(
        f"SELECT COUNT(*) cnt, COALESCE(SUM(amount),0) turn, COALESCE(SUM(bonus),0) bon FROM sales WHERE {cond}",
        params, fetch="one",
    )
    top = q(
        "SELECT c.id, c.full_name, c.category, COUNT(sa.id) cnt, SUM(sa.amount) turn, SUM(sa.bonus) bon "
        f"FROM sales sa JOIN clients c ON c.id=sa.client_id WHERE {cond.replace('status', 'sa.status').replace('created_at', 'sa.created_at')} "
        "GROUP BY c.id ORDER BY turn DESC LIMIT 10",
        params, fetch="all",
    )
    pcond = "1=1"
    if date_from:
        pcond += " AND created_at >= ?"
    if date_to:
        pcond += " AND created_at < ?"
    paid = q(f"SELECT COALESCE(SUM(amount),0) s FROM payouts WHERE {pcond}", params, fetch="one")["s"]
    new_clients = q(f"SELECT COUNT(*) c FROM clients WHERE {pcond}", params, fetch="one")["c"]
    totals = q(
        "SELECT COUNT(*) c, COALESCE(SUM(total_turnover),0) t, COALESCE(SUM(total_bonus),0) b FROM clients",
        fetch="one",
    )
    all_paid = q("SELECT COALESCE(SUM(amount),0) s FROM payouts", fetch="one")["s"]
    return {
        "sales_count": s["cnt"],
        "turnover": s["turn"],
        "bonus": s["bon"],
        "paid": paid,
        "new_clients": new_clients,
        "top_clients": top,
        "sellers": sellers_stats(date_from, date_to),
        "clients_total": totals["c"],
        "turnover_total": totals["t"],
        "bonus_total": totals["b"],
        "paid_total": all_paid,
        "unpaid_total": totals["b"] - all_paid,
        "sellers_count": q("SELECT COUNT(*) c FROM sellers", fetch="one")["c"],
    }
