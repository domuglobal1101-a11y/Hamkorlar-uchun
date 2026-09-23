"""SODIQLIK TIZIMI - Telegram loyalty bot (hamkor + sotuvchi + rahbar) va boshqaruv dashboardi."""

import asyncio
import hmac
import logging
import os
import re
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import db
import excel
import sheets
from db import CATEGORIES, fmt, percent_for

N = chr(10)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Sukut bo'yicha parol YO'Q: o'rnatilmagan bo'lsa /admin o'chiq turadi.
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SUPER_ADMINS = {
    int(x) for x in os.getenv("SUPER_ADMINS", "").replace(" ", "").split(",") if x
}
PUBLIC_DOMAIN = os.getenv("DASHBOARD_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN", "")

MAX_FAILS = 5
BLOCK_SECONDS = 15 * 60
MAX_AMOUNT = 100_000_000_000

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("loyalty-bot")


# ---------- Rollar ----------

def is_owner(tg_id):
    return tg_id in SUPER_ADMINS or db.q(
        "SELECT 1 FROM owners WHERE telegram_id=?", (tg_id,), fetch="one"
    ) is not None


def _can_sell(uid):
    return is_owner(uid) or db.get_seller_by_tg(uid) is not None


def _actor(uid):
    """(seller_id, ism) - savdoni kim kiritganini yozish uchun."""
    s = db.get_seller_by_tg(uid)
    if s:
        return s["id"], s["name"]
    return None, "Rahbar"


# ---------- Parol/kod terib ko'rishdan himoya ----------

_fails = {}  # uid -> (urinishlar, bloklangan_gacha)


def _blocked_for(uid):
    cnt, until = _fails.get(uid, (0, 0))
    left = until - time.time()
    return int(left) if left > 0 else 0


def _register_fail(uid):
    cnt, until = _fails.get(uid, (0, 0))
    if until and until < time.time():
        cnt = 0
    cnt += 1
    if cnt >= MAX_FAILS:
        _fails[uid] = (0, time.time() + BLOCK_SECONDS)
        logger.warning("Kirish bloklandi: uid=%s", uid)
        return True
    _fails[uid] = (cnt, 0)
    return False


def _reset_fails(uid):
    _fails.pop(uid, None)


async def _delete_quietly(message):
    try:
        await message.delete()
    except Exception:
        pass


# ---------- Yordamchilar ----------

def normalize_phone(text):
    digits = re.sub("[^0-9]", "", text or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 9:
        digits = "998" + digits
    return digits


def parse_amount(text):
    t = (text or "").lower().strip()
    mult = 1
    if "mlrd" in t:
        mult = 1_000_000_000
    elif "mln" in t:
        mult = 1_000_000
    num = re.sub("[^0-9.]", "", t.replace(",", "."))
    if not num:
        return None
    try:
        return int(round(float(num) * mult))
    except ValueError:
        return None


def short_date(s):
    return (s or "")[:16].replace("T", " ")


# ---------- Klaviaturalar ----------

def kb(rows):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows], resize_keyboard=True
    )


def client_menu():
    return kb([["📊 Statistikam"], ["📜 Barcha savdolarim", "🏆 Bonus bosqichlari"]])


def owner_menu():
    return kb([
        ["➕ Savdo qoshish"],
        ["💸 Bonus to'lash", "↩️ Savdoni bekor qilish"],
        ["👥 Mijozlar", "🔍 Mijozni tekshirish"],
        ["📊 Hisobot", "📥 Excel"],
        ["🧾 Sotuvchilar", "➕ Sotuvchi qoshish", "🗑 Sotuvchi ochirish"],
        ["🖥 Dashboard", "🚪 Chiqish"],
    ])


def seller_menu():
    return kb([["➕ Savdo qoshish"], ["🔍 Mijozni tekshirish"], ["📈 Mening natijam"], ["🚪 Chiqish"]])


def menu_for(uid):
    if is_owner(uid):
        return owner_menu()
    if db.get_seller_by_tg(uid):
        return seller_menu()
    if db.get_client_by_tg(uid):
        return client_menu()
    return ReplyKeyboardRemove()


def contact_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )


def category_kb(with_cancel=False):
    rows = [[c] for c in CATEGORIES]
    if with_cancel:
        rows.append(["❌ Bekor qilish"])
    return kb(rows)


def cancel_kb():
    return kb([["❌ Bekor qilish"]])


def confirm_kb():
    return kb([["✅ Tasdiqlash", "❌ Bekor qilish"]])


def period_kb(prefix):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=db.PERIODS[k], callback_data=f"{prefix}:{k}") for k in ("today", "week")],
        [InlineKeyboardButton(text=db.PERIODS[k], callback_data=f"{prefix}:{k}") for k in ("month", "all")],
    ])


# ---------- Holatlar ----------

class Reg(StatesGroup):
    name = State()
    category = State()


class OwnerLogin(StatesGroup):
    password = State()


class SellerLogin(StatesGroup):
    code = State()


class AddSale(StatesGroup):
    phone = State()
    new_name = State()
    new_category = State()
    amount = State()
    customer = State()
    confirm = State()


class Lookup(StatesGroup):
    phone = State()


class AddSeller(StatesGroup):
    name = State()


class DelSeller(StatesGroup):
    code = State()


class Payout(StatesGroup):
    phone = State()
    amount = State()
    note = State()
    confirm = State()


class CancelSale(StatesGroup):
    sale_id = State()
    confirm = State()


router = Router()

# Tartib muhim: avval buyruqlar va "Bekor qilish", keyin menyu tugmalari,
# keyin holat (FSM) qadamlari. Shunda buyruq/menyu har qanday holatda ishlaydi.


# ---------- Buyruqlar ----------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    if is_owner(uid):
        await message.answer("👋 Salom, Rahbar! Boshqaruv paneli.", reply_markup=owner_menu())
        return
    seller = db.get_seller_by_tg(uid)
    if seller:
        await message.answer(f"👋 Salom, {seller['name']}! (sotuvchi)", reply_markup=seller_menu())
        return
    client = db.get_client_by_tg(uid)
    if client:
        await message.answer(
            f"👋 Salom, {client['full_name']}!{N}SODIQLIK TIZIMIga xush kelibsiz.", reply_markup=client_menu()
        )
        return
    await message.answer(
        f"👋 Assalomu alaykum!{N}{N}"
        f"Bu - usta, dizayner va prorablar uchun <b>SODIQLIK TIZIMI</b>.{N}"
        f"Siz olib kelgan mijozlar showroomdan mahsulot xarid qilsa, sizga bonus yoziladi.{N}{N}"
        f"Royxatdan otish uchun pastdagi tugma orqali raqamingizni yuboring:",
        reply_markup=contact_kb(),
    )


@router.message(Command("admin"))
async def owner_login_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    if is_owner(uid):
        await state.clear()
        await message.answer("Siz allaqachon rahbarsiz.", reply_markup=owner_menu())
        return
    if not ADMIN_PASSWORD:
        await message.answer("Rahbar kirishi hozircha o'chirilgan.")
        return
    left = _blocked_for(uid)
    if left:
        await message.answer(f"⛔ Juda ko'p xato urinish. {left // 60 + 1} daqiqadan keyin qayta urinib ko'ring.")
        return
    await state.set_state(OwnerLogin.password)
    await message.answer("🔑 Rahbar parolini kiriting:", reply_markup=cancel_kb())


@router.message(Command("kirish"))
async def seller_login_start(message: Message, state: FSMContext):
    left = _blocked_for(message.from_user.id)
    if left:
        await message.answer(f"⛔ Juda ko'p xato urinish. {left // 60 + 1} daqiqadan keyin qayta urinib ko'ring.")
        return
    await state.set_state(SellerLogin.code)
    await message.answer("🔑 Sotuvchi kodingizni kiriting:", reply_markup=cancel_kb())


@router.message(F.text == "❌ Bekor qilish")
async def cancel_any(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=menu_for(message.from_user.id))


# ---------- Hamkor (mijoz) ----------

@router.message(F.text == "📊 Statistikam")
async def my_stats(message: Message, state: FSMContext):
    await state.clear()
    client = db.get_client_by_tg(message.from_user.id)
    if not client:
        await message.answer("Avval /start orqali royxatdan oting.")
        return
    turnover = client["total_turnover"]
    total, paid, unpaid = db.client_balance(client["id"])
    text = (
        f"📊 <b>{client['full_name']}</b> ({client['category']}){N}"
        f"Jami aylanma: <b>{fmt(turnover)}</b> som{N}"
        f"Joriy bosqich: <b>{percent_for(turnover)}%</b>{N}{N}"
        f"Jami bonus: <b>{fmt(total)}</b> som{N}"
        f"To'langan: <b>{fmt(paid)}</b> som{N}"
        f"To'lanishi kerak: <b>{fmt(max(unpaid, 0))}</b> som{N}"
    )
    nxt = db.next_tier_info(turnover)
    if nxt:
        text += f"{N}🎯 {nxt[0]}% gacha yana <b>{fmt(nxt[1])}</b> som kerak."
    else:
        text += f"{N}🏆 Eng yuqori bosqichdasiz - har savdoga 4%!"
    sales = db.list_sales(client_id=client["id"], status="active", limit=5)
    if sales:
        text += f"{N}{N}<b>Oxirgi savdolar:</b>{N}"
        for s in sales:
            text += f"- {s['created_at'][:10]}: {fmt(s['amount'])} som -> {s['percent']}% = <b>{fmt(s['bonus'])}</b>{N}"
    else:
        text += f"{N}{N}Hali savdolar yoq."
    await message.answer(text, reply_markup=client_menu())


@router.message(F.text == "📜 Barcha savdolarim")
async def my_history(message: Message, state: FSMContext):
    await state.clear()
    client = db.get_client_by_tg(message.from_user.id)
    if not client:
        await message.answer("Avval /start orqali royxatdan oting.")
        return
    sales = db.list_sales(client_id=client["id"], status="active", limit=50)
    payouts = db.list_payouts(client_id=client["id"], limit=20)
    if not sales and not payouts:
        await message.answer("Hali savdolar yoq.", reply_markup=client_menu())
        return
    text = f"📜 <b>Savdolar tarixi</b> ({len(sales)} ta){N}{N}"
    for s in sales:
        who = f" - {s['customer']}" if s["customer"] else ""
        text += f"{s['created_at'][:10]}: {fmt(s['amount'])} som, {s['percent']}% = <b>{fmt(s['bonus'])}</b>{who}{N}"
    if payouts:
        text += f"{N}💸 <b>Bonus to'lovlari</b>{N}"
        for p in payouts:
            text += f"{p['created_at'][:10]}: <b>{fmt(p['amount'])}</b> som{N}"
    await message.answer(text[:4000], reply_markup=client_menu())


@router.message(F.text == "🏆 Bonus bosqichlari")
async def tiers_info(message: Message, state: FSMContext):
    await state.clear()
    lines = "".join(
        f"- {t // 1_000_000} mln dan keyin -> <b>{p}%</b>{N}" for t, p in sorted(db.TIERS) if p
    )
    top = max(p for _, p in db.TIERS)
    await message.answer(
        f"🏆 <b>BONUS BOSQICHLARI</b>{N}Mijozlaringizning jami xaridiga qarab:{N}{N}{lines}{N}"
        f"Har bosqichga bir marta chiqsangiz kifoya - keyingi har bir savdoga shu foiz. "
        f"Eng yuqori bosqichda doimiy {top}%.",
        reply_markup=menu_for(message.from_user.id),
    )


# ---------- Chiqish ----------

@router.message(F.text == "🚪 Chiqish")
async def logout(message: Message, state: FSMContext):
    uid = message.from_user.id
    await state.clear()
    if uid in SUPER_ADMINS:
        await message.answer("Siz super-adminsiz.", reply_markup=owner_menu())
        return
    db.q("DELETE FROM owners WHERE telegram_id=?", (uid,))
    db.q("UPDATE sellers SET telegram_id=NULL WHERE telegram_id=?", (uid,))
    await message.answer("Chiqdingiz.", reply_markup=menu_for(uid))


# ---------- Rahbar: sotuvchilar ----------

@router.message(F.text == "➕ Sotuvchi qoshish")
async def add_seller_start(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.set_state(AddSeller.name)
    await message.answer("Yangi sotuvchi ism-familiyasini yozing:", reply_markup=cancel_kb())


@router.message(F.text == "🗑 Sotuvchi ochirish")
async def del_seller_start(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.set_state(DelSeller.code)
    await message.answer("Ochiriladigan sotuvchi kodini yozing:", reply_markup=cancel_kb())


@router.message(F.text == "🧾 Sotuvchilar")
async def sellers_list(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.clear()
    rows = db.sellers_stats()
    if not rows:
        await message.answer("Hali sotuvchilar yoq. '➕ Sotuvchi qoshish' orqali qoshing.", reply_markup=owner_menu())
        return
    text = f"🧾 <b>SOTUVCHILAR</b>{N}{N}"
    for r in rows:
        link = "ulangan" if r["telegram_id"] else "ulanmagan"
        text += (
            f"👤 <b>{r['name']}</b> (kod: {r['code']}, {link}){N}"
            f"   Savdolar: {r['cnt']} | Aylanma: {fmt(r['turn'])} | Bonus: {fmt(r['bon'])}{N}{N}"
        )
    await message.answer(text[:4000], reply_markup=owner_menu())


# ---------- Rahbar: hamkorlar, hisobot, Excel, dashboard ----------

@router.message(F.text == "👥 Mijozlar")
async def clients_list(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.clear()
    rows = db.clients_with_balance(limit=40)
    if not rows:
        await message.answer("Hali mijozlar yoq.", reply_markup=owner_menu())
        return
    text = f"👥 <b>HAMKORLAR (aylanma boyicha)</b>{N}{N}"
    for i, c in enumerate(rows, 1):
        unpaid = c["total_bonus"] - c["paid"]
        text += (
            f"{i}. {c['full_name']} - {fmt(c['total_turnover'])} som ({percent_for(c['total_turnover'])}%)"
            f" | bonus: {fmt(c['total_bonus'])}, qoldiq: {fmt(unpaid)}{N}"
        )
    await message.answer(text[:4000], reply_markup=owner_menu())


@router.message(F.text == "📊 Hisobot")
async def report_start(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.clear()
    await message.answer("📊 Qaysi davr uchun hisobot?", reply_markup=period_kb("rep"))


def report_text(key):
    rep = db.report(*db.period_range(key))
    text = (
        f"📊 <b>HISOBOT: {db.PERIODS[key].upper()}</b>{N}{N}"
        f"Savdolar soni: <b>{rep['sales_count']}</b>{N}"
        f"Aylanma: <b>{fmt(rep['turnover'])}</b> som{N}"
        f"Yozilgan bonus: <b>{fmt(rep['bonus'])}</b> som{N}"
        f"To'langan bonus: <b>{fmt(rep['paid'])}</b> som{N}"
        f"Yangi hamkorlar: <b>{rep['new_clients']}</b>{N}"
    )
    sellers = [s for s in rep["sellers"] if s["cnt"]]
    if sellers:
        text += f"{N}🧾 <b>Sotuvchilar reytingi</b>{N}"
        for i, s in enumerate(sellers, 1):
            text += f"{i}. {s['name']} - {s['cnt']} ta, {fmt(s['turn'])} som{N}"
    if rep["top_clients"]:
        text += f"{N}👥 <b>Top hamkorlar</b>{N}"
        for i, c in enumerate(rep["top_clients"], 1):
            text += f"{i}. {c['full_name']} - {fmt(c['turn'])} som ({c['cnt']} ta){N}"
    text += (
        f"{N}<b>Umumiy holat</b>{N}"
        f"Hamkorlar: {rep['clients_total']} | Sotuvchilar: {rep['sellers_count']}{N}"
        f"Jami aylanma: {fmt(rep['turnover_total'])} som{N}"
        f"Jami bonus: {fmt(rep['bonus_total'])} | to'langan: {fmt(rep['paid_total'])}{N}"
        f"<b>To'lanmagan bonus: {fmt(rep['unpaid_total'])} som</b>"
    )
    return text


@router.callback_query(F.data.startswith("rep:"))
async def report_cb(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer()
        return
    key = call.data.split(":", 1)[1]
    if key not in db.PERIODS:
        await call.answer()
        return
    await call.message.answer(report_text(key)[:4000])
    await call.answer()


@router.message(F.text == "📥 Excel")
async def excel_start(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.clear()
    await message.answer("📥 Qaysi davr savdolari Excelga tushsin?", reply_markup=period_kb("xls"))


@router.callback_query(F.data.startswith("xls:"))
async def excel_cb(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer()
        return
    key = call.data.split(":", 1)[1]
    if key not in db.PERIODS:
        await call.answer()
        return
    await call.answer("Tayyorlanmoqda...")
    data = await asyncio.to_thread(excel.build, *db.period_range(key))
    name = f"domu-hisobot-{key}-{db.now().strftime('%Y-%m-%d')}.xlsx"
    await call.message.answer_document(
        BufferedInputFile(data, filename=name), caption=f"📥 {db.PERIODS[key]} - Excel hisobot"
    )


@router.message(F.text == "🖥 Dashboard")
async def dashboard_link(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.clear()
    if PUBLIC_DOMAIN:
        url = PUBLIC_DOMAIN if PUBLIC_DOMAIN.startswith("http") else f"https://{PUBLIC_DOMAIN}"
        await message.answer(f"🖥 Boshqaruv dashboardi:{N}{url}{N}{N}Kirish: rahbar paroli bilan.", reply_markup=owner_menu())
    else:
        await message.answer("Dashboard manzili hali sozlanmagan (Railway -> Settings -> Generate Domain).", reply_markup=owner_menu())


# ---------- Savdo kiritish (rahbar + sotuvchi) ----------

@router.message(F.text == "➕ Savdo qoshish")
async def sale_start(message: Message, state: FSMContext):
    if not _can_sell(message.from_user.id):
        return
    await state.clear()
    await state.set_state(AddSale.phone)
    await message.answer(
        f"➕ <b>Yangi savdo</b>{N}{N}Hamkor telefon raqamini yuboring.{N}Masalan: 901234567", reply_markup=cancel_kb()
    )


@router.message(F.text == "🔍 Mijozni tekshirish")
async def lookup_start(message: Message, state: FSMContext):
    if not _can_sell(message.from_user.id):
        return
    await state.set_state(Lookup.phone)
    await message.answer("Hamkor telefon raqamini yuboring:", reply_markup=cancel_kb())


@router.message(F.text == "📈 Mening natijam")
async def my_result(message: Message, state: FSMContext):
    seller = db.get_seller_by_tg(message.from_user.id)
    if not seller:
        return
    await state.clear()
    month_from, month_to = db.period_range("month")
    total = next((r for r in db.sellers_stats() if r["id"] == seller["id"]), None)
    month = next((r for r in db.sellers_stats(month_from, month_to) if r["id"] == seller["id"]), None)
    text = (
        f"📈 <b>{seller['name']}</b> - natijangiz{N}{N}"
        f"<b>Shu oy:</b> {month['cnt']} ta savdo, {fmt(month['turn'])} som{N}"
        f"<b>Jami:</b> {total['cnt']} ta savdo, {fmt(total['turn'])} som{N}"
        f"Yozilgan bonus (jami): {fmt(total['bon'])} som{N}"
    )
    last = db.list_sales(seller_id=seller["id"], limit=10)
    if last:
        text += f"{N}<b>Oxirgi savdolaringiz:</b>{N}"
        for s in last:
            mark = " ❌ bekor" if s["status"] != "active" else ""
            text += f"#{s['id']} {s['created_at'][:10]} {s['client_name']}: {fmt(s['amount'])} som{mark}{N}"
    await message.answer(text, reply_markup=seller_menu())


# ---------- Rahbar: bonus to'lash ----------

@router.message(F.text == "💸 Bonus to'lash")
async def payout_start(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.clear()
    await state.set_state(Payout.phone)
    await message.answer("💸 Bonus to'lanadigan hamkor telefon raqamini yuboring:", reply_markup=cancel_kb())


# ---------- Rahbar: savdoni bekor qilish ----------

@router.message(F.text == "↩️ Savdoni bekor qilish")
async def cancel_sale_start(message: Message, state: FSMContext):
    if not is_owner(message.from_user.id):
        return
    await state.clear()
    sales = db.list_sales(status="active", limit=15)
    if not sales:
        await message.answer("Faol savdolar yoq.", reply_markup=owner_menu())
        return
    text = f"↩️ <b>Oxirgi savdolar</b>{N}{N}"
    for s in sales:
        text += f"<b>#{s['id']}</b> {short_date(s['created_at'])} | {s['client_name']} | {fmt(s['amount'])} som | {s['seller_name'] or '-'}{N}"
    text += f"{N}Bekor qilinadigan savdo raqamini (#dan keyingi son) yozing:"
    await state.set_state(CancelSale.sale_id)
    await message.answer(text[:4000], reply_markup=cancel_kb())


# ---------- Holat qadamlari: ro'yxatdan o'tish ----------

@router.message(F.contact)
async def got_contact(message: Message, state: FSMContext):
    uid = message.from_user.id
    if message.contact.user_id and message.contact.user_id != uid:
        await message.answer("Iltimos, ozingizning raqamingizni yuboring.")
        return
    if db.get_client_by_tg(uid):
        await message.answer("Siz allaqachon royxatdan otgansiz.", reply_markup=client_menu())
        return
    phone = normalize_phone(message.contact.phone_number)
    await state.update_data(phone=phone)
    await state.set_state(Reg.name)
    await message.answer("Rahmat! Endi <b>ism-familiyangizni</b> yozing:", reply_markup=ReplyKeyboardRemove())


@router.message(Reg.name, F.text)
async def reg_name(message: Message, state: FSMContext):
    name = db.clean(message.text)[:60]
    if len(name) < 2:
        await message.answer("Ism-familiyangizni to'liq yozing:")
        return
    await state.update_data(name=name)
    await state.set_state(Reg.category)
    await message.answer("Yonalishingizni tanlang:", reply_markup=category_kb())


@router.message(Reg.category, F.text)
async def reg_category(message: Message, state: FSMContext):
    cat = message.text.strip()
    if cat not in CATEGORIES:
        await message.answer("Iltimos, tugmalardan birini tanlang.", reply_markup=category_kb())
        return
    data = await state.get_data()
    existing = db.get_client_by_phone(data["phone"])
    if existing:
        # Sotuvchi oldinroq qo'shgan hamkor - Telegramni ulaymiz, tarix saqlanadi
        db.q(
            "UPDATE clients SET full_name=?, category=?, telegram_id=? WHERE phone=?",
            (data["name"], cat, message.from_user.id, data["phone"]),
        )
    else:
        db.create_client(data["phone"], data["name"], cat, message.from_user.id)
    await state.clear()
    await message.answer(
        f"✅ Royxatdan otdingiz!{N}{N}Ism: <b>{data['name']}</b>{N}Yonalish: <b>{cat}</b>{N}{N}"
        f"Endi statistikangizni kuzatib borishingiz mumkin.",
        reply_markup=client_menu(),
    )
    await sheets.sync()


# ---------- Holat qadamlari: kirish ----------

@router.message(OwnerLogin.password, F.text)
async def owner_login_check(message: Message, state: FSMContext):
    uid = message.from_user.id
    await _delete_quietly(message)  # parol chatda qolmasin
    if _blocked_for(uid):
        await state.clear()
        await message.answer("⛔ Juda ko'p xato urinish. Keyinroq urinib ko'ring.", reply_markup=menu_for(uid))
        return
    if ADMIN_PASSWORD and hmac.compare_digest(message.text.strip().encode(), ADMIN_PASSWORD.encode()):
        _reset_fails(uid)
        db.q(
            "INSERT OR IGNORE INTO owners(telegram_id, name, created_at) VALUES(?,?,?)",
            (uid, message.from_user.full_name, db.now_str()),
        )
        await state.clear()
        logger.info("Yangi rahbar kirdi: uid=%s %s", uid, message.from_user.full_name)
        await message.answer("✅ Rahbar sifatida kirdingiz!", reply_markup=owner_menu())
        return
    if _register_fail(uid):
        await state.clear()
        await message.answer(f"⛔ {MAX_FAILS} marta xato. 15 daqiqaga bloklandingiz.", reply_markup=menu_for(uid))
    else:
        await message.answer("❌ Parol notogri. Qayta urinib koring yoki bekor qiling.")


@router.message(SellerLogin.code, F.text)
async def seller_login_check(message: Message, state: FSMContext):
    uid = message.from_user.id
    await _delete_quietly(message)
    if _blocked_for(uid):
        await state.clear()
        await message.answer("⛔ Juda ko'p xato urinish. Keyinroq urinib ko'ring.", reply_markup=menu_for(uid))
        return
    seller = db.get_seller_by_code(message.text.strip())
    if not seller:
        if _register_fail(uid):
            await state.clear()
            await message.answer(f"⛔ {MAX_FAILS} marta xato. 15 daqiqaga bloklandingiz.", reply_markup=menu_for(uid))
        else:
            await message.answer("❌ Bunday kod yoq. Qayta kiriting yoki bekor qiling.")
        return
    _reset_fails(uid)
    db.q("UPDATE sellers SET telegram_id=NULL WHERE telegram_id=?", (uid,))
    db.q("UPDATE sellers SET telegram_id=? WHERE id=?", (uid, seller["id"]))
    await state.clear()
    await message.answer(f"✅ Xush kelibsiz, {seller['name']}! Endi savdo kiritishingiz mumkin.", reply_markup=seller_menu())


# ---------- Holat qadamlari: sotuvchi qo'shish/o'chirish ----------

@router.message(AddSeller.name, F.text)
async def add_seller_done(message: Message, state: FSMContext):
    name = db.clean(message.text)[:60]
    if not name:
        await message.answer("Ism yozing:")
        return
    code = db.create_seller(name)
    await state.clear()
    await message.answer(
        f"✅ Sotuvchi qoshildi!{N}{N}Ism: <b>{name}</b>{N}Kod: <code>{code}</code>{N}{N}"
        f"Sotuvchiga ayting: botga kirib <b>/kirish</b> yozsin va shu kodni kiritsin.",
        reply_markup=owner_menu(),
    )
    await sheets.sync()


@router.message(DelSeller.code, F.text)
async def del_seller_done(message: Message, state: FSMContext):
    seller = db.get_seller_by_code(message.text.strip())
    await state.clear()
    if not seller:
        await message.answer("Bunday kod topilmadi.", reply_markup=owner_menu())
        return
    db.delete_seller(seller["id"])
    await message.answer(f"🗑 Sotuvchi ochirildi: {seller['name']} (savdolari saqlanib qoladi).", reply_markup=owner_menu())
    await sheets.sync()


# ---------- Holat qadamlari: savdo ----------

@router.message(AddSale.phone, F.text)
async def sale_phone(message: Message, state: FSMContext):
    phone = normalize_phone(message.text)
    if len(phone) < 9:
        await message.answer("Raqam notogri. Masalan: 901234567. Qayta yuboring.")
        return
    await state.update_data(phone=phone)
    client = db.get_client_by_phone(phone)
    if client:
        await state.update_data(client_id=client["id"])
        await state.set_state(AddSale.amount)
        await message.answer(
            f"Hamkor: <b>{client['full_name']}</b> ({client['category']}){N}"
            f"Joriy aylanma: {fmt(client['total_turnover'])} som ({percent_for(client['total_turnover'])}%){N}{N}"
            f"Savdo summasini kiriting (somda):",
            reply_markup=cancel_kb(),
        )
    else:
        await state.set_state(AddSale.new_name)
        await message.answer(
            f"Bu raqam royxatda yoq. Yangi hamkor qoshamiz.{N}Hamkorning <b>ism-familiyasini</b> yozing:",
            reply_markup=cancel_kb(),
        )


@router.message(AddSale.new_name, F.text)
async def sale_new_name(message: Message, state: FSMContext):
    await state.update_data(new_name=db.clean(message.text)[:60] or "Nomsiz")
    await state.set_state(AddSale.new_category)
    await message.answer("Yonalishini tanlang:", reply_markup=category_kb(with_cancel=True))


@router.message(AddSale.new_category, F.text)
async def sale_new_category(message: Message, state: FSMContext):
    cat = message.text.strip()
    if cat not in CATEGORIES:
        await message.answer("Tugmalardan birini tanlang.", reply_markup=category_kb(with_cancel=True))
        return
    # Hamkor faqat savdo tasdiqlanganda yaratiladi - bekor qilinsa bo'sh yozuv qolmaydi
    await state.update_data(new_category=cat)
    await state.set_state(AddSale.amount)
    await message.answer("Savdo summasini kiriting (somda):", reply_markup=cancel_kb())


@router.message(AddSale.amount, F.text)
async def sale_amount(message: Message, state: FSMContext):
    amount = parse_amount(message.text)
    if not amount or amount <= 0 or amount > MAX_AMOUNT:
        await message.answer("Summa notogri. Masalan: 50000000 yoki 50 mln")
        return
    await state.update_data(amount=amount)
    await state.set_state(AddSale.customer)
    await message.answer("Xaridor ismini yozing yoki otkazish uchun - belgisini yuboring:", reply_markup=cancel_kb())


@router.message(AddSale.customer, F.text)
async def sale_customer(message: Message, state: FSMContext):
    customer = "" if message.text.strip() == "-" else db.clean(message.text)[:80]
    data = await state.get_data()
    if data.get("client_id"):
        c = db.get_client(data["client_id"])
        name, cat, before = c["full_name"], c["category"], c["total_turnover"]
    else:
        name, cat, before = data["new_name"], data["new_category"], 0
    amount = data["amount"]
    pct = percent_for(before)
    bonus = amount * pct // 100
    await state.update_data(customer=customer)
    await state.set_state(AddSale.confirm)
    msg = (
        f"🧾 <b>Tekshiring:</b>{N}"
        f"Hamkor: <b>{name}</b> ({cat}){' - YANGI' if not data.get('client_id') else ''}{N}"
        f"Savdo summasi: <b>{fmt(amount)}</b> som{N}"
        f"Foiz: <b>{pct}%</b>{N}"
        f"Bonus: <b>{fmt(bonus)}</b> som{N}"
        f"Yangi aylanma: <b>{fmt(before + amount)}</b> som (keyingi bosqich: {percent_for(before + amount)}%){N}"
    )
    if customer:
        msg += f"Xaridor: {customer}{N}"
    msg += f"{N}Togrimi?"
    await message.answer(msg, reply_markup=confirm_kb())


@router.message(AddSale.confirm, F.text == "✅ Tasdiqlash")
async def sale_confirm(message: Message, state: FSMContext):
    uid = message.from_user.id
    data = await state.get_data()
    await state.clear()
    cid = data.get("client_id")
    if not cid:
        existing = db.get_client_by_phone(data["phone"])  # shu orada ro'yxatdan o'tgan bo'lishi mumkin
        cid = existing["id"] if existing else db.create_client(data["phone"], data["new_name"], data["new_category"])
    sid, sname = _actor(uid)
    sale = db.add_sale(cid, data["amount"], data.get("customer", ""), sid, sname)
    await message.answer(
        f"✅ Saqlandi! Savdo #{sale['id']} (kiritdi: {sname}){N}Bonus: <b>{fmt(sale['bonus'])}</b> som ({sale['percent']}%)",
        reply_markup=menu_for(uid),
    )
    if sale["client_tg"]:
        try:
            await message.bot.send_message(
                sale["client_tg"],
                f"🛍 Yangi savdo!{N}{N}Summa: <b>{fmt(sale['amount'])}</b> som{N}"
                f"Bonus ({sale['percent']}%): <b>{fmt(sale['bonus'])}</b> som{N}"
                f"Jami aylanma: <b>{fmt(sale['turnover_after'])}</b> som",
            )
        except Exception as e:
            logger.warning("Hamkorga xabar: %s", e)
    await sheets.sync()


@router.message(Lookup.phone, F.text)
async def lookup_phone(message: Message, state: FSMContext):
    uid = message.from_user.id
    client = db.get_client_by_phone(normalize_phone(message.text))
    await state.clear()
    if not client:
        await message.answer("Bunday hamkor topilmadi.", reply_markup=menu_for(uid))
        return
    tt = client["total_turnover"]
    total, paid, unpaid = db.client_balance(client["id"])
    await message.answer(
        f"👤 <b>{client['full_name']}</b> ({client['category']}){N}Raqam: {client['phone']}{N}"
        f"Jami aylanma: <b>{fmt(tt)}</b> som ({percent_for(tt)}%){N}"
        f"Jami bonus: <b>{fmt(total)}</b> som{N}To'langan: {fmt(paid)} | qoldiq: <b>{fmt(unpaid)}</b> som",
        reply_markup=menu_for(uid),
    )


# ---------- Holat qadamlari: bonus to'lash ----------

@router.message(Payout.phone, F.text)
async def payout_phone(message: Message, state: FSMContext):
    client = db.get_client_by_phone(normalize_phone(message.text))
    if not client:
        await message.answer("Bunday hamkor topilmadi. Raqamni qayta yuboring yoki bekor qiling.")
        return
    total, paid, unpaid = db.client_balance(client["id"])
    if unpaid <= 0:
        await state.clear()
        await message.answer(
            f"<b>{client['full_name']}</b> uchun to'lanmagan bonus yoq.{N}Jami bonus: {fmt(total)} | to'langan: {fmt(paid)}",
            reply_markup=owner_menu(),
        )
        return
    await state.update_data(client_id=client["id"], unpaid=unpaid)
    await state.set_state(Payout.amount)
    await message.answer(
        f"👤 <b>{client['full_name']}</b>{N}Jami bonus: {fmt(total)} som{N}To'langan: {fmt(paid)} som{N}"
        f"<b>Qoldiq: {fmt(unpaid)} som</b>{N}{N}Qancha to'layapsiz? Summani yozing yoki tugmani bosing:",
        reply_markup=kb([[f"Hammasi ({fmt(unpaid)})"], ["❌ Bekor qilish"]]),
    )


@router.message(Payout.amount, F.text)
async def payout_amount(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = data["unpaid"] if message.text.startswith("Hammasi") else parse_amount(message.text)
    if not amount or amount <= 0:
        await message.answer("Summa notogri. Masalan: 1500000")
        return
    if amount > data["unpaid"]:
        await message.answer(f"Summa qoldiqdan ({fmt(data['unpaid'])}) katta bo'lmasligi kerak.")
        return
    await state.update_data(amount=amount)
    await state.set_state(Payout.note)
    await message.answer("Izoh yozing (masalan: naqd, karta) yoki - belgisini yuboring:", reply_markup=cancel_kb())


@router.message(Payout.note, F.text)
async def payout_note(message: Message, state: FSMContext):
    note = "" if message.text.strip() == "-" else db.clean(message.text)[:100]
    await state.update_data(note=note)
    data = await state.get_data()
    c = db.get_client(data["client_id"])
    await state.set_state(Payout.confirm)
    await message.answer(
        f"💸 <b>Tekshiring:</b>{N}Hamkor: <b>{c['full_name']}</b>{N}To'lov: <b>{fmt(data['amount'])}</b> som{N}"
        f"Qoldiq keyin: {fmt(data['unpaid'] - data['amount'])} som{N}" + (f"Izoh: {note}{N}" if note else "") + f"{N}Togrimi?",
        reply_markup=confirm_kb(),
    )


@router.message(Payout.confirm, F.text == "✅ Tasdiqlash")
async def payout_confirm(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    c = db.get_client(data["client_id"])
    db.add_payout(c["id"], data["amount"], data.get("note", ""), message.from_user.full_name)
    _, _, unpaid = db.client_balance(c["id"])
    await message.answer(
        f"✅ To'lov yozildi: {c['full_name']} - <b>{fmt(data['amount'])}</b> som{N}Qoldiq: {fmt(unpaid)} som",
        reply_markup=owner_menu(),
    )
    if c["telegram_id"]:
        try:
            await message.bot.send_message(
                c["telegram_id"],
                f"💸 Sizga bonus to'landi: <b>{fmt(data['amount'])}</b> som{N}To'lanmagan qoldiq: {fmt(max(unpaid, 0))} som",
            )
        except Exception as e:
            logger.warning("Hamkorga xabar: %s", e)
    await sheets.sync()


# ---------- Holat qadamlari: savdoni bekor qilish ----------

@router.message(CancelSale.sale_id, F.text)
async def cancel_sale_pick(message: Message, state: FSMContext):
    digits = re.sub("[^0-9]", "", message.text)
    sale = db.get_sale(int(digits)) if digits else None
    if not sale or sale["status"] != "active":
        await message.answer("Bunday faol savdo topilmadi. Raqamni qayta yozing yoki bekor qiling.")
        return
    await state.update_data(sale_id=sale["id"])
    await state.set_state(CancelSale.confirm)
    await message.answer(
        f"↩️ <b>Savdo #{sale['id']}</b>{N}Sana: {short_date(sale['created_at'])}{N}Hamkor: {sale['client_name']}{N}"
        f"Summa: <b>{fmt(sale['amount'])}</b> som{N}Bonus: {fmt(sale['bonus'])} som ({sale['percent']}%){N}"
        f"Kiritgan: {sale['seller_name'] or '-'}{N}{N}"
        f"Bekor qilinsa, hamkorning aylanmasi va keyingi savdolar bonusi qayta hisoblanadi. Tasdiqlaysizmi?",
        reply_markup=confirm_kb(),
    )


@router.message(CancelSale.confirm, F.text == "✅ Tasdiqlash")
async def cancel_sale_confirm(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    sale = db.get_sale(data["sale_id"])
    old_bonus, new_bonus = db.cancel_sale(sale["id"], message.from_user.full_name)
    _, paid, unpaid = db.client_balance(sale["client_id"])
    text = (
        f"✅ Savdo #{sale['id']} bekor qilindi.{N}{sale['client_name']}: jami bonus {fmt(old_bonus)} -> <b>{fmt(new_bonus)}</b> som"
    )
    if unpaid < 0:
        text += f"{N}⚠️ Hamkorga {fmt(-unpaid)} som ortiqcha to'langan."
    await message.answer(text, reply_markup=owner_menu())
    if sale["client_tg"]:
        try:
            await message.bot.send_message(
                sale["client_tg"],
                f"↩️ {short_date(sale['created_at'])} dagi {fmt(sale['amount'])} som savdo bekor qilindi.{N}"
                f"Jami bonus: <b>{fmt(new_bonus)}</b> som",
            )
        except Exception as e:
            logger.warning("Hamkorga xabar: %s", e)
    await sheets.sync()


# ---------- Qolgan hamma narsa ----------

@router.message()
async def fallback(message: Message, state: FSMContext):
    uid = message.from_user.id
    if await state.get_state():
        await message.answer("Iltimos, matn yoki tugma orqali javob bering (yoki ❌ Bekor qilish).")
        return
    if is_owner(uid) or db.get_seller_by_tg(uid) or db.get_client_by_tg(uid):
        await message.answer("Menyudan tanlang:", reply_markup=menu_for(uid))
    else:
        await message.answer("Boshlash uchun /start ni bosing. Sotuvchilar uchun: /kirish")


async def main():
    import web

    db.connect()
    port = int(os.getenv("PORT", "0") or 0)
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if BOT_TOKEN else None

    runner = None
    if port:
        try:
            runner = await web.start(port, bot)
            logger.info("Dashboard %s-portda ishga tushdi", port)
        except Exception:
            logger.exception("Dashboard ishga tushmadi - bot baribir ishlaydi")
    else:
        logger.warning("PORT berilmagan - dashboard ishga tushmadi")

    if not bot:
        if not runner:
            raise SystemExit("BOT_TOKEN berilmagan!")
        logger.error("BOT_TOKEN berilmagan - faqat dashboard ishlayapti")
        await asyncio.Event().wait()
        return

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("Bot ishga tushdi...")
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        if runner:
            await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
