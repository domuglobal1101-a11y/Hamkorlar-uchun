"""Boshqaruv dashboardi (aiohttp). Bot bilan bitta jarayonda, bitta bazada ishlaydi.

Kirish: DASHBOARD_PASSWORD (bo'lmasa ADMIN_PASSWORD). Sessiya imzolangan cookie'da.
"""

import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timedelta
from html import escape
from urllib.parse import quote, urlencode

from aiohttp import web

import db
import excel
import sheets
from db import CATEGORIES, fmt, percent_for

logger = logging.getLogger("loyalty-bot")

PASSWORD = os.getenv("DASHBOARD_PASSWORD") or os.getenv("ADMIN_PASSWORD", "")
SECRET = (
    os.getenv("SESSION_SECRET")
    or hashlib.sha256(("domu-dash:" + PASSWORD + os.getenv("BOT_TOKEN", "")).encode()).hexdigest()
).encode()
COOKIE = "domu_s"
SESSION_TTL = 7 * 24 * 3600
MAX_FAILS = 5
BLOCK_SECONDS = 15 * 60

_login_fails = {}  # ip -> (urinishlar, bloklangan_gacha)


# ---------- Sessiya ----------

def _sign(value):
    return hmac.new(SECRET, value.encode(), hashlib.sha256).hexdigest()


def _make_session():
    exp = str(int(time.time()) + SESSION_TTL)
    return f"{exp}.{_sign(exp)}"


def _valid_session(token):
    if not token or "." not in token:
        return False
    exp, sig = token.split(".", 1)
    return hmac.compare_digest(sig, _sign(exp)) and exp.isdigit() and int(exp) > time.time()


def _client_ip(request):
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote or "?")


@web.middleware
async def auth_mw(request, handler):
    if request.path in ("/login", "/health"):
        return await handler(request)
    if not _valid_session(request.cookies.get(COOKIE)):
        raise web.HTTPFound("/login")
    if request.method == "POST":
        # SameSite=Strict cookie + Origin tekshiruvi = CSRF himoyasi
        origin = request.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] != request.host:
            raise web.HTTPForbidden(text="Origin mos emas")
    return await handler(request)


# ---------- HTML ----------

CSS = """
:root{--bg:#f4f6f9;--card:#fff;--text:#14202e;--muted:#66758a;--line:#e3e8ef;--accent:#1f5fbf;
--accent-soft:#e8f0fc;--danger:#c0352b;--danger-soft:#fdecea;--ok:#1d7a46;--ok-soft:#e6f5ec;--warn:#9a6700;--warn-soft:#fff4d6}
@media (prefers-color-scheme:dark){:root{--bg:#0d1520;--card:#15202e;--text:#e6edf5;--muted:#8fa0b5;--line:#243244;
--accent:#6aa5ff;--accent-soft:#1b2b44;--danger:#ff7b72;--danger-soft:#3a1d1d;--ok:#56d38a;--ok-soft:#143325;--warn:#e3b341;--warn-soft:#3a2f12}}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
.top{max-width:1200px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.brand{font-weight:700;font-size:16px;color:var(--text)}
nav{display:flex;gap:4px;flex-wrap:wrap;flex:1}
nav a{padding:6px 10px;border-radius:6px;color:var(--muted)}
@media(max-width:640px){nav{order:3;flex:0 0 100%;flex-wrap:nowrap;overflow-x:auto}.top{justify-content:space-between;gap:8px}}
nav a{white-space:nowrap}
nav a.on,nav a:hover{background:var(--accent-soft);color:var(--accent);text-decoration:none}
main{max-width:1200px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:22px;margin-bottom:14px}h2{font-size:16px;margin:0 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:16px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.kpi .l{color:var(--muted);font-size:12px}.kpi .s{color:var(--muted);font-size:12px;margin-top:2px}.kpi .v.bad{color:var(--danger)}.kpi .v{font-size:18px;font-weight:700;margin-top:2px;font-variant-numeric:tabular-nums}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:800px){.two{grid-template-columns:1fr}}
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle;white-space:nowrap}
th{color:var(--muted);font-weight:600;font-size:12px}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr.off td{opacity:.5;text-decoration:line-through}tr.off td.act{opacity:1;text-decoration:none}
input,select,button{font:inherit;color:var(--text);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:7px 10px}
input:focus,select:focus{outline:2px solid var(--accent-soft);border-color:var(--accent)}
button,.btn{cursor:pointer;background:var(--accent);border-color:var(--accent);color:#fff;display:inline-block;border-radius:6px;padding:7px 12px}
.btn:hover{text-decoration:none;filter:brightness(1.05)}
button.sec,.btn.sec{background:var(--card);color:var(--text);border-color:var(--line)}
button.dang{background:var(--danger);border-color:var(--danger)}
button.sm{padding:3px 8px;font-size:12px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.row+.row{margin-top:8px}
form.inline{display:inline}
.flash{padding:10px 14px;border-radius:8px;margin-bottom:14px;background:var(--ok-soft);color:var(--ok)}
.flash.err{background:var(--danger-soft);color:var(--danger)}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;background:var(--accent-soft);color:var(--accent)}
.tag.warn{background:var(--warn-soft);color:var(--warn)}.tag.bad{background:var(--danger-soft);color:var(--danger)}
.muted{color:var(--muted)}
.login{max-width:340px;margin:12vh auto}.login input{width:100%;margin:10px 0}.login button{width:100%}
label{display:block;font-size:12px;color:var(--muted);margin-bottom:3px}
.fld{display:flex;flex-direction:column}
"""

NAV = [
    ("/", "Umumiy"),
    ("/hamkorlar", "Hamkorlar"),
    ("/savdolar", "Savdolar"),
    ("/tolovlar", "To'lovlar"),
    ("/sotuvchilar", "Sotuvchilar"),
]


def e(v):
    return escape(str(v if v is not None else ""))


def page(request, title, body, nav=True):
    flash = ""
    if request.query.get("ok"):
        flash = f'<div class="flash">{e(request.query["ok"])}</div>'
    elif request.query.get("err"):
        flash = f'<div class="flash err">{e(request.query["err"])}</div>'
    header = ""
    if nav:
        links = "".join(
            f'<a href="{h}" class="{"on" if (request.path == h or (h != "/" and request.path.startswith(h[:-1]))) else ""}">{t}</a>'
            for h, t in NAV
        )
        header = (
            f'<header><div class="top"><span class="brand">DOMU · Sodiqlik</span><nav>{links}</nav>'
            f'<form method="post" action="/logout" class="inline"><button class="sec sm">Chiqish</button></form></div></header>'
        )
    html = (
        '<!doctype html><html lang="uz"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{e(title)} · DOMU</title><style>{CSS}</style></head><body>{header}<main>{flash}{body}</main></body></html>"
    )
    return web.Response(text=html, content_type="text/html")


def back(url, ok=None, err=None):
    if not url or not url.startswith("/") or url.startswith("//"):
        url = "/"
    base, _, qs = url.partition("?")
    params = [p for p in qs.split("&") if p and not p.startswith(("ok=", "err="))]
    if ok:
        params.append("ok=" + quote(ok))
    if err:
        params.append("err=" + quote(err))
    raise web.HTTPFound(base + ("?" + "&".join(params) if params else ""))


def here(request):
    return request.path_qs


def confirm_btn(action, label, question, cls="dang sm", ret=None, hidden=None):
    extra = "".join(f'<input type="hidden" name="{k}" value="{e(v)}">' for k, v in (hidden or {}).items())
    ret_input = f'<input type="hidden" name="back" value="{e(ret)}">' if ret else ""
    return (
        f'<form method="post" action="{action}" class="inline" onsubmit="return confirm({e(repr(question))})">'
        f'{ret_input}{extra}<button class="{cls}">{label}</button></form>'
    )


def parse_amount(text):
    t = (text or "").replace(" ", "").replace(",", ".")
    try:
        v = int(round(float(t)))
    except ValueError:
        return None
    return v if 0 < v <= 100_000_000_000 else None


def get_period(request):
    """So'rovdagi davr: ?p=today|week|month|all yoki ?from=YYYY-MM-DD&to=YYYY-MM-DD."""
    f, t = request.query.get("from", ""), request.query.get("to", "")
    if f or t:
        try:
            df = datetime.strptime(f, "%Y-%m-%d") if f else None
            dt = datetime.strptime(t, "%Y-%m-%d") + timedelta(days=1) if t else None
            return "custom", df, dt, f, t
        except ValueError:
            pass
    key = request.query.get("p", "month")
    if key not in db.PERIODS:
        key = "month"
    df, dt = db.period_range(key)
    return key, df, dt, "", ""


def period_bar(request, key, f, t, extra=None):
    extra = extra or {}
    chips = "".join(
        f'<a class="btn {"" if key == k else "sec"}" href="?{urlencode({**extra, "p": k})}">{v}</a>'
        for k, v in db.PERIODS.items()
    )
    hidden = "".join(f'<input type="hidden" name="{k}" value="{e(v)}">' for k, v in extra.items())
    return (
        f'<div class="row" style="margin-bottom:14px">{chips}'
        f'<form class="row" method="get">{hidden}<input type="date" name="from" value="{e(f)}">'
        f'<span class="muted">—</span><input type="date" name="to" value="{e(t)}">'
        f'<button class="sec">Ko\'rsatish</button></form></div>'
    )


def period_label(key, f, t):
    if key == "custom":
        return f"{f or '...'} — {t or '...'}"
    return db.PERIODS[key]


# ---------- Kirish ----------

async def login_get(request):
    if not PASSWORD:
        return page(request, "Kirish", '<div class="card login"><h1>Dashboard sozlanmagan</h1>'
                    '<p class="muted">Railway Variables\'da DASHBOARD_PASSWORD yoki ADMIN_PASSWORD o\'rnating.</p></div>', nav=False)
    return page(
        request, "Kirish",
        '<form class="card login" method="post" action="/login"><h1>DOMU · Sodiqlik</h1>'
        '<p class="muted">Boshqaruv paneli</p>'
        '<input type="password" name="password" placeholder="Parol" autofocus required>'
        '<button>Kirish</button></form>',
        nav=False,
    )


async def login_post(request):
    ip = _client_ip(request)
    cnt, until = _login_fails.get(ip, (0, 0))
    if until > time.time():
        back("/login", err="Juda ko'p xato urinish. 15 daqiqadan keyin qayta urinib ko'ring.")
    form = await request.post()
    pw = form.get("password", "")
    if PASSWORD and hmac.compare_digest(pw.encode(), PASSWORD.encode()):
        _login_fails.pop(ip, None)
        logger.info("Dashboard'ga kirildi: ip=%s", ip)
        resp = web.HTTPFound("/")
        resp.set_cookie(
            COOKIE, _make_session(), max_age=SESSION_TTL, httponly=True, samesite="Strict",
            secure=request.headers.get("X-Forwarded-Proto", request.scheme) == "https",
        )
        raise resp
    cnt += 1
    _login_fails[ip] = (0, time.time() + BLOCK_SECONDS) if cnt >= MAX_FAILS else (cnt, 0)
    logger.warning("Dashboard: xato parol ip=%s (%s)", ip, cnt)
    back("/login", err="Parol noto'g'ri")


async def logout(request):
    resp = web.HTTPFound("/login")
    resp.del_cookie(COOKIE)
    raise resp


async def health(request):
    return web.Response(text="ok")


# ---------- Umumiy ----------

async def overview(request):
    key, df, dt, f, t = get_period(request)
    rep = db.report(df, dt)
    tiles = [
        ("Savdolar", fmt(rep["sales_count"])),
        ("Aylanma", fmt(rep["turnover"]) + " so'm"),
        ("Yozilgan bonus", fmt(rep["bonus"]) + " so'm"),
        ("To'langan bonus", fmt(rep["paid"]) + " so'm"),
        ("Yangi hamkorlar", fmt(rep["new_clients"])),
    ]
    totals = [
        ("Hamkorlar", fmt(rep["clients_total"])),
        ("Jami aylanma", fmt(rep["turnover_total"]) + " so'm"),
        ("Jami bonus", fmt(rep["bonus_total"]) + " so'm"),
        ("To'langan", fmt(rep["paid_total"]) + " so'm"),
        ("To'lanmagan qoldiq", fmt(rep["unpaid_total"]) + " so'm"),
    ]
    tile = lambda items: '<div class="grid">' + "".join(
        f'<div class="kpi"><div class="l">{l}</div><div class="v">{v}</div></div>' for l, v in items
    ) + "</div>"
    srows = "".join(
        f"<tr><td>{i}</td><td>{e(s['name'])}</td><td class='n'>{s['cnt']}</td><td class='n'>{fmt(s['turn'])}</td></tr>"
        for i, s in enumerate(rep["sellers"], 1)
    ) or "<tr><td colspan=4 class='muted'>Ma'lumot yo'q</td></tr>"
    crows = "".join(
        f"<tr><td>{i}</td><td><a href='/hamkor/{c['id']}'>{e(c['full_name'])}</a></td><td class='n'>{c['cnt']}</td><td class='n'>{fmt(c['turn'])}</td></tr>"
        for i, c in enumerate(rep["top_clients"], 1)
    ) or "<tr><td colspan=4 class='muted'>Ma'lumot yo'q</td></tr>"
    xls_q = urlencode({"from": f, "to": t} if key == "custom" else {"p": key})
    body = (
        f'<div class="row" style="justify-content:space-between"><h1>Umumiy · {e(period_label(key, f, t))}</h1>'
        f'<a class="btn sec" href="/excel?{xls_q}">Excel yuklab olish</a></div>'
        + period_bar(request, key, f, t)
        + tile(tiles)
        + '<div class="two">'
        f'<div class="card"><h2>Sotuvchilar reytingi</h2><div class="tw"><table><tr><th>#</th><th>Sotuvchi</th><th class="n">Savdolar</th><th class="n">Aylanma</th></tr>{srows}</table></div></div>'
        f'<div class="card"><h2>Top hamkorlar</h2><div class="tw"><table><tr><th>#</th><th>Hamkor</th><th class="n">Savdolar</th><th class="n">Aylanma</th></tr>{crows}</table></div></div>'
        "</div><h2 style='margin-top:6px'>Umumiy holat (butun davr)</h2>"
        + tile(totals)
    )
    return page(request, "Umumiy", body)


# ---------- Hamkorlar ----------

def _unpaid_cell(v):
    if v < 0:
        return f"<span class='tag bad' title=\"ortiqcha to'langan\">{fmt(v)}</span>"
    return f"<b>{fmt(v)}</b>" if v else "0"


async def clients(request):
    search = request.query.get("q", "").strip()
    rows = db.clients_with_balance(search)
    trs = []
    for c in rows:
        unpaid = c["total_bonus"] - c["paid"]
        tg = '<span class="tag">bot</span>' if c["telegram_id"] else '<span class="tag warn">botsiz</span>'
        trs.append(
            f"<tr><td><input type='checkbox' name='ids' value='{c['id']}' form='bulk'></td>"
            f"<td><a href='/hamkor/{c['id']}'>{e(c['full_name'])}</a></td><td>{e(c['phone'])}</td>"
            f"<td>{e(c['category'])}</td><td>{tg}</td><td class='n'>{c['sales_count']}</td>"
            f"<td class='n'>{fmt(c['total_turnover'])}</td><td class='n'>{percent_for(c['total_turnover'])}%</td>"
            f"<td class='n'>{fmt(c['total_bonus'])}</td><td class='n'>{fmt(c['paid'])}</td>"
            f"<td class='n'>{_unpaid_cell(unpaid)}</td>"
            f"<td>{e((c['created_at'] or '')[:10])}</td></tr>"
        )
    table = "".join(trs) or "<tr><td colspan=12 class='muted'>Hamkor topilmadi</td></tr>"
    body = (
        f"<h1>Hamkorlar <span class='muted'>({len(rows)})</span></h1>"
        f'<div class="card"><div class="row" style="justify-content:space-between">'
        f'<form class="row" method="get"><input name="q" value="{e(search)}" placeholder="Ism yoki telefon">'
        f'<button class="sec">Qidirish</button></form>'
        f'<form id="bulk" method="post" action="/hamkorlar/ochirish" class="row" '
        f'onsubmit="var n=document.querySelectorAll(\'input[name=ids]:checked\').length;'
        f'if(!n){{alert(\'Avval hamkorlarni belgilang\');return false}}'
        f'return confirm(n+\' ta hamkor savdo va to\\\'lovlari bilan butunlay o\\\'chiriladi. Davom etasizmi?\')">'
        f'<input type="hidden" name="back" value="{e(here(request))}">'
        f'<button class="dang">Belgilanganlarni o\'chirish</button></form></div></div>'
        f'<div class="card tw"><table><tr><th><input type="checkbox" onclick="document.querySelectorAll(\'input[name=ids]\').forEach(x=>x.checked=this.checked)"></th>'
        f"<th>Ism</th><th>Telefon</th><th>Yo'nalish</th><th>Bot</th><th class='n'>Savdo</th><th class='n'>Aylanma</th>"
        f"<th class='n'>%</th><th class='n'>Bonus</th><th class='n'>To'langan</th><th class='n'>Qoldiq</th><th>Qo'shilgan</th></tr>{table}</table></div>"
    )
    return page(request, "Hamkorlar", body)


async def clients_bulk_delete(request):
    form = await request.post()
    ids = [int(x) for x in form.getall("ids", []) if str(x).isdigit()]
    for cid in ids:
        c = db.get_client(cid)
        if c:
            logger.warning("Dashboard: hamkor o'chirildi id=%s %s %s", cid, c["full_name"], c["phone"])
            db.delete_client(cid)
    sheets.sync_later()
    back("/hamkorlar", ok=f"{len(ids)} ta hamkor o'chirildi")


async def client_detail(request):
    cid = int(request.match_info["cid"])
    c = db.get_client(cid)
    if not c:
        back("/hamkorlar", err="Hamkor topilmadi")
    total, paid, unpaid = db.client_balance(cid)
    me = here(request)
    cats = "".join(f"<option {'selected' if c['category'] == k else ''}>{k}</option>" for k in CATEGORIES)
    sales = db.list_sales(client_id=cid)
    srows = "".join(_sale_row(s, me, show_client=False) for s in sales) or "<tr><td colspan=9 class='muted'>Savdolar yo'q</td></tr>"
    payouts = db.list_payouts(client_id=cid)
    prows = "".join(
        f"<tr><td>{e((p['created_at'] or '')[:16].replace('T', ' '))}</td><td class='n'>{fmt(p['amount'])}</td>"
        f"<td>{e(p['note'])}</td><td>{e(p['created_by'])}</td><td class='act'>"
        + confirm_btn(f"/tolov/{p['id']}/ochirish", "O'chirish", "To'lov yozuvi o'chirilsinmi?", ret=me)
        + "</td></tr>"
        for p in payouts
    ) or "<tr><td colspan=5 class='muted'>To'lovlar yo'q</td></tr>"
    nxt = db.next_tier_info(c["total_turnover"])
    tier_sub = f"{nxt[0]}% gacha yana {fmt(nxt[1])} so'm" if nxt else "eng yuqori bosqich"
    unpaid_tile = (
        f'<div class="v bad">{fmt(unpaid)}</div><div class="s">ortiqcha to\'langan</div>' if unpaid < 0
        else f'<div class="v">{fmt(unpaid)}</div>'
    )
    body = (
        f'<p><a href="/hamkorlar">← Hamkorlar</a></p><h1>{e(c["full_name"])}</h1>'
        '<div class="grid">'
        f'<div class="kpi"><div class="l">Jami aylanma</div><div class="v">{fmt(c["total_turnover"])}</div></div>'
        f'<div class="kpi"><div class="l">Bosqich</div><div class="v">{percent_for(c["total_turnover"])}%</div><div class="s">{tier_sub}</div></div>'
        f'<div class="kpi"><div class="l">Jami bonus</div><div class="v">{fmt(total)}</div></div>'
        f'<div class="kpi"><div class="l">To\'langan</div><div class="v">{fmt(paid)}</div></div>'
        f'<div class="kpi"><div class="l">Qoldiq</div>{unpaid_tile}</div></div>'
        '<div class="two">'
        f'<form class="card" method="post" action="/hamkor/{cid}/tahrir"><h2>Ma\'lumotlar</h2>'
        f'<div class="row"><div class="fld"><label>Ism</label><input name="name" value="{e(c["full_name"])}" required></div>'
        f'<div class="fld"><label>Telefon</label><input name="phone" value="{e(c["phone"])}" required></div>'
        f'<div class="fld"><label>Yo\'nalish</label><select name="category">{cats}</select></div></div>'
        f'<div class="row"><button>Saqlash</button><span class="muted">Telegram: {"ulangan" if c["telegram_id"] else "ulanmagan"} · qo\'shilgan {e((c["created_at"] or "")[:10])}</span></div></form>'
        f'<form class="card" method="post" action="/hamkor/{cid}/tolov"><h2>Bonus to\'lash</h2>'
        f'<div class="row"><div class="fld"><label>Summa (qoldiq {fmt(unpaid)})</label><input name="amount" inputmode="numeric" value="{max(unpaid, 0) or ""}" required></div>'
        f'<div class="fld"><label>Izoh</label><input name="note" placeholder="naqd, karta..."></div></div>'
        f'<div class="row"><button>To\'lovni yozish</button></div></form></div>'
        f'<div class="card"><h2>Savdolar</h2><div class="tw"><table><tr><th>#</th><th>Sana</th><th class="n">Summa</th><th class="n">%</th>'
        f'<th class="n">Bonus</th><th>Xaridor</th><th>Sotuvchi</th><th>Holat</th><th></th></tr>{srows}</table></div></div>'
        f'<div class="card"><h2>To\'lovlar</h2><div class="tw"><table><tr><th>Sana</th><th class="n">Summa</th><th>Izoh</th><th>Kim</th><th></th></tr>{prows}</table></div></div>'
        '<div class="card"><h2>Xavfli zona</h2><p class="muted" style="margin-bottom:8px">Hamkor barcha savdo va to\'lovlari bilan butunlay o\'chiriladi. Test uchun yaratilgan yozuvlarni tozalashda ishlating.</p>'
        + confirm_btn(f"/hamkor/{cid}/ochirish", "Hamkorni o'chirish", f"{c['full_name']} butunlay o'chirilsinmi?", cls="dang")
        + "</div>"
    )
    return page(request, c["full_name"], body)


async def client_edit(request):
    cid = int(request.match_info["cid"])
    form = await request.post()
    name = db.clean(form.get("name", ""))[:60]
    phone = "".join(ch for ch in form.get("phone", "") if ch.isdigit())
    cat = form.get("category", "")
    if not name or len(phone) < 9 or cat not in CATEGORIES:
        back(f"/hamkor/{cid}", err="Ma'lumotlar noto'g'ri")
    other = db.get_client_by_phone(phone)
    if other and other["id"] != cid:
        back(f"/hamkor/{cid}", err="Bu raqam boshqa hamkorga tegishli")
    db.update_client(cid, name, phone, cat)
    sheets.sync_later()
    back(f"/hamkor/{cid}", ok="Saqlandi")


async def client_delete(request):
    cid = int(request.match_info["cid"])
    c = db.get_client(cid)
    if c:
        logger.warning("Dashboard: hamkor o'chirildi id=%s %s %s", cid, c["full_name"], c["phone"])
        db.delete_client(cid)
        sheets.sync_later()
    back("/hamkorlar", ok=f"{c['full_name'] if c else 'Hamkor'} o'chirildi")


async def client_payout(request):
    cid = int(request.match_info["cid"])
    form = await request.post()
    amount = parse_amount(form.get("amount", ""))
    if not amount or not db.get_client(cid):
        back(f"/hamkor/{cid}", err="Summa noto'g'ri")
    _, _, left = db.client_balance(cid)
    if amount > left:
        back(f"/hamkor/{cid}", err=f"Summa qoldiqdan ({fmt(left)} so'm) katta bo'lmasligi kerak")
    db.add_payout(cid, amount, db.clean(form.get("note", ""))[:100], "Dashboard")
    c = db.get_client(cid)
    _, _, unpaid = db.client_balance(cid)
    bot = request.app["bot"]
    if bot and c["telegram_id"]:
        try:
            await bot.send_message(
                c["telegram_id"],
                f"💸 Sizga bonus to'landi: <b>{fmt(amount)}</b> som\nTo'lanmagan qoldiq: {fmt(max(unpaid, 0))} som",
            )
        except Exception as ex:
            logger.warning("Hamkorga xabar: %s", ex)
    sheets.sync_later()
    back(f"/hamkor/{cid}", ok=f"{fmt(amount)} so'm to'lov yozildi")


# ---------- Savdolar ----------

def _sale_row(s, ret, show_client=True):
    active = s["status"] == "active"
    status = '<span class="tag">faol</span>' if active else '<span class="tag bad">bekor</span>'
    client = f"<td><a href='/hamkor/{s['client_id']}'>{e(s['client_name'])}</a></td>" if show_client else ""
    toggle = (
        confirm_btn(f"/savdo/{s['id']}/bekor", "Bekor", f"Savdo #{s['id']} bekor qilinsinmi? Bonuslar qayta hisoblanadi.", cls="sec sm", ret=ret)
        if active else
        confirm_btn(f"/savdo/{s['id']}/tiklash", "Tiklash", f"Savdo #{s['id']} qayta faollashtirilsinmi?", cls="sec sm", ret=ret)
    )
    edit = (
        f"<form method='post' action='/savdo/{s['id']}/tahrir' class='inline' "
        f"onsubmit=\"var v=prompt('Yangi summa (so\\'m):','{s['amount']}');if(!v)return false;this.amount.value=v;return true\">"
        f"<input type='hidden' name='amount'><input type='hidden' name='back' value='{e(ret)}'>"
        f"<button class='sec sm'>Summa</button></form>"
    )
    delete = confirm_btn(f"/savdo/{s['id']}/ochirish", "O'chirish", f"Savdo #{s['id']} butunlay o'chirilsinmi?", ret=ret)
    return (
        f"<tr class='{'' if active else 'off'}'><td>{s['id']}</td><td>{e((s['created_at'] or '')[:16].replace('T', ' '))}</td>{client}"
        f"<td class='n'>{fmt(s['amount'])}</td><td class='n'>{s['percent']}%</td><td class='n'>{fmt(s['bonus'])}</td>"
        f"<td>{e(s['customer'])}</td><td>{e(s['seller_name'])}</td><td class='act'>{status}</td>"
        f"<td class='act'><div class='row' style='flex-wrap:nowrap'>{edit}{toggle}{delete}</div></td></tr>"
    )


async def sales(request):
    key, df, dt, f, t = get_period(request)
    status = request.query.get("status", "")
    rows = db.list_sales(df, dt, status=status or None, limit=1000)
    me = here(request)
    trs = "".join(_sale_row(s, me) for s in rows) or "<tr><td colspan=10 class='muted'>Savdolar yo'q</td></tr>"
    total = sum(s["amount"] for s in rows if s["status"] == "active")
    st_opts = "".join(
        f"<option value='{v}' {'selected' if status == v else ''}>{l}</option>"
        for v, l in [("", "Hammasi"), ("active", "Faol"), ("cancelled", "Bekor qilingan")]
    )
    extra = {"status": status} if status else {}
    body = (
        f"<h1>Savdolar · {e(period_label(key, f, t))}</h1>"
        + period_bar(request, key, f, t, extra)
        + f'<form class="row" method="get" style="margin-bottom:14px">'
        + "".join(f'<input type="hidden" name="{k}" value="{e(v)}">' for k, v in (
            {"from": f, "to": t} if key == "custom" else {"p": key}).items())
        + f'<select name="status" onchange="this.form.submit()">{st_opts}</select>'
        f'<span class="muted">{len(rows)} ta · faol aylanma {fmt(total)} so\'m</span></form>'
        f'<div class="card tw"><table><tr><th>#</th><th>Sana</th><th>Hamkor</th><th class="n">Summa</th><th class="n">%</th>'
        f'<th class="n">Bonus</th><th>Xaridor</th><th>Sotuvchi</th><th>Holat</th><th></th></tr>{trs}</table></div>'
        "<p class='muted'>Savdo bekor qilinsa, tahrirlansa yoki o'chirilsa, hamkorning aylanmasi va keyingi savdolar bonusi avtomatik qayta hisoblanadi.</p>"
    )
    return page(request, "Savdolar", body)


async def _sale_action(request, fn, msg):
    sid = int(request.match_info["sid"])
    form = await request.post()
    ret = form.get("back", "/savdolar")
    if not db.get_sale(sid):
        back(ret, err="Savdo topilmadi")
    fn(sid, form)
    sheets.sync_later()
    back(ret, ok=msg.format(sid=sid))


async def sale_cancel(request):
    return await _sale_action(request, lambda sid, f: db.cancel_sale(sid, "Dashboard"), "Savdo #{sid} bekor qilindi")


async def sale_restore(request):
    return await _sale_action(request, lambda sid, f: db.restore_sale(sid), "Savdo #{sid} tiklandi")


async def sale_delete(request):
    def fn(sid, f):
        logger.warning("Dashboard: savdo o'chirildi #%s", sid)
        db.delete_sale(sid)
    return await _sale_action(request, fn, "Savdo #{sid} o'chirildi")


async def sale_edit(request):
    sid = int(request.match_info["sid"])
    form = await request.post()
    ret = form.get("back", "/savdolar")
    amount = parse_amount(form.get("amount", ""))
    if not amount or not db.get_sale(sid):
        back(ret, err="Summa noto'g'ri")
    db.update_sale_amount(sid, amount)
    sheets.sync_later()
    back(ret, ok=f"Savdo #{sid} summasi {fmt(amount)} so'mga o'zgartirildi")


# ---------- To'lovlar ----------

async def payouts(request):
    key, df, dt, f, t = get_period(request)
    rows = db.list_payouts(date_from=df, date_to=dt)
    me = here(request)
    trs = "".join(
        f"<tr><td>{p['id']}</td><td>{e((p['created_at'] or '')[:16].replace('T', ' '))}</td>"
        f"<td><a href='/hamkor/{p['client_id']}'>{e(p['client_name'])}</a></td><td class='n'>{fmt(p['amount'])}</td>"
        f"<td>{e(p['note'])}</td><td>{e(p['created_by'])}</td><td>"
        + confirm_btn(f"/tolov/{p['id']}/ochirish", "O'chirish", "To'lov yozuvi o'chirilsinmi?", ret=me)
        + "</td></tr>"
        for p in rows
    ) or "<tr><td colspan=7 class='muted'>To'lovlar yo'q</td></tr>"
    unpaid = [c for c in db.clients_with_balance() if c["total_bonus"] - c["paid"] > 0]
    urows = "".join(
        f"<tr><td><a href='/hamkor/{c['id']}'>{e(c['full_name'])}</a></td><td>{e(c['phone'])}</td>"
        f"<td class='n'>{fmt(c['total_bonus'])}</td><td class='n'>{fmt(c['paid'])}</td><td class='n'><b>{fmt(c['total_bonus'] - c['paid'])}</b></td></tr>"
        for c in sorted(unpaid, key=lambda c: c["paid"] - c["total_bonus"])
    ) or "<tr><td colspan=5 class='muted'>Hammasi to'langan</td></tr>"
    body = (
        f"<h1>To'lovlar</h1>"
        f'<div class="card"><h2>To\'lanishi kerak ({len(unpaid)})</h2><div class="tw"><table><tr><th>Hamkor</th><th>Telefon</th>'
        f'<th class="n">Jami bonus</th><th class="n">To\'langan</th><th class="n">Qoldiq</th></tr>{urows}</table></div></div>'
        f"<h2>To'lovlar tarixi · {e(period_label(key, f, t))} · {fmt(sum(p['amount'] for p in rows))} so'm</h2>"
        + period_bar(request, key, f, t)
        + f'<div class="card tw"><table><tr><th>#</th><th>Sana</th><th>Hamkor</th><th class="n">Summa</th><th>Izoh</th><th>Kim</th><th></th></tr>{trs}</table></div>'
    )
    return page(request, "To'lovlar", body)


async def payout_delete(request):
    pid = int(request.match_info["pid"])
    form = await request.post()
    logger.warning("Dashboard: to'lov o'chirildi #%s", pid)
    db.delete_payout(pid)
    sheets.sync_later()
    back(form.get("back", "/tolovlar"), ok="To'lov o'chirildi")


# ---------- Sotuvchilar ----------

async def sellers(request):
    key, df, dt, f, t = get_period(request)
    rows = db.sellers_stats(df, dt)
    linked = '<span class="tag">ulangan</span>'
    unlinked = '<span class="tag warn">ulanmagan</span>'
    trs = "".join(
        f"<tr><td>{e(s['name'])}</td><td><code>{e(s['code'])}</code></td>"
        f"<td>{linked if s['telegram_id'] else unlinked}</td>"
        f"<td class='n'>{s['cnt']}</td><td class='n'>{fmt(s['turn'])}</td><td class='n'>{fmt(s['bon'])}</td><td><div class='row' style='flex-wrap:nowrap'>"
        + confirm_btn(f"/sotuvchi/{s['id']}/kod", "Yangi kod", f"{s['name']} uchun yangi kod berilsinmi? Eski kod ishlamay qoladi va bot'dan chiqariladi.", cls="sec sm")
        + confirm_btn(f"/sotuvchi/{s['id']}/ochirish", "O'chirish", f"{s['name']} o'chirilsinmi? Savdolari saqlanib qoladi.")
        + "</div></td></tr>"
        for s in rows
    ) or "<tr><td colspan=7 class='muted'>Sotuvchilar yo'q</td></tr>"
    body = (
        f"<h1>Sotuvchilar · {e(period_label(key, f, t))}</h1>"
        + period_bar(request, key, f, t)
        + f'<div class="card tw"><table><tr><th>Ism</th><th>Kod</th><th>Bot</th><th class="n">Savdolar</th>'
        f'<th class="n">Aylanma</th><th class="n">Bonus</th><th></th></tr>{trs}</table></div>'
        '<form class="card" method="post" action="/sotuvchi/qoshish"><h2>Yangi sotuvchi</h2>'
        '<div class="row"><input name="name" placeholder="Ism-familiya" required><button>Qo\'shish</button></div>'
        '<p class="muted" style="margin-top:8px">Sotuvchi botga /kirish yozib, berilgan 6 xonali kodni kiritadi.</p></form>'
    )
    return page(request, "Sotuvchilar", body)


async def seller_add(request):
    form = await request.post()
    name = db.clean(form.get("name", ""))[:60]
    if not name:
        back("/sotuvchilar", err="Ism kiriting")
    code = db.create_seller(name)
    sheets.sync_later()
    back("/sotuvchilar", ok=f"{name} qo'shildi. Kodi: {code}")


async def seller_delete(request):
    sid = int(request.match_info["sid"])
    s = db.get_seller(sid)
    db.delete_seller(sid)
    sheets.sync_later()
    back("/sotuvchilar", ok=f"{s['name'] if s else 'Sotuvchi'} o'chirildi")


async def seller_reset(request):
    sid = int(request.match_info["sid"])
    s = db.get_seller(sid)
    if not s:
        back("/sotuvchilar", err="Sotuvchi topilmadi")
    code = db.reset_seller_code(sid)
    back("/sotuvchilar", ok=f"{s['name']} uchun yangi kod: {code}")


# ---------- Excel ----------

async def excel_download(request):
    import asyncio
    key, df, dt, f, t = get_period(request)
    data = await asyncio.to_thread(excel.build, df, dt)
    name = f"domu-hisobot-{key}-{db.now().strftime('%Y-%m-%d')}.xlsx"
    return web.Response(
        body=data,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ---------- Ilova ----------

def make_app(bot=None):
    app = web.Application(middlewares=[auth_mw])
    app["bot"] = bot
    app.add_routes([
        web.get("/health", health),
        web.get("/login", login_get),
        web.post("/login", login_post),
        web.post("/logout", logout),
        web.get("/", overview),
        web.get("/hamkorlar", clients),
        web.post("/hamkorlar/ochirish", clients_bulk_delete),
        web.get(r"/hamkor/{cid:\d+}", client_detail),
        web.post(r"/hamkor/{cid:\d+}/tahrir", client_edit),
        web.post(r"/hamkor/{cid:\d+}/ochirish", client_delete),
        web.post(r"/hamkor/{cid:\d+}/tolov", client_payout),
        web.get("/savdolar", sales),
        web.post(r"/savdo/{sid:\d+}/bekor", sale_cancel),
        web.post(r"/savdo/{sid:\d+}/tiklash", sale_restore),
        web.post(r"/savdo/{sid:\d+}/ochirish", sale_delete),
        web.post(r"/savdo/{sid:\d+}/tahrir", sale_edit),
        web.get("/tolovlar", payouts),
        web.post(r"/tolov/{pid:\d+}/ochirish", payout_delete),
        web.get("/sotuvchilar", sellers),
        web.post("/sotuvchi/qoshish", seller_add),
        web.post(r"/sotuvchi/{sid:\d+}/ochirish", seller_delete),
        web.post(r"/sotuvchi/{sid:\d+}/kod", seller_reset),
        web.get("/excel", excel_download),
    ])
    return app


async def start(port, bot=None):
    runner = web.AppRunner(make_app(bot), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    return runner
