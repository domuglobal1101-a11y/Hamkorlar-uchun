"""SODIQLIK TIZIMI - Telegram loyalty bot (usta/dizayner/prorab uchun bonus).
Bonus FORWARD: 100mln 1%, 200mln 2%, 300mln 3%, 400mln 4% (doimiy)."""

import asyncio
import logging
import os
import re
import sqlite3
import threading
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

N = chr(10)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234")
SUPER_ADMINS = {
    int(x) for x in os.getenv("SUPER_ADMINS", "").replace(" ", "").split(",") if x
}
DB_PATH = os.getenv("DB_PATH", "loyalty.db")

TIERS = [
    (400_000_000, 4),
    (300_000_000, 3),
    (200_000_000, 2),
    (100_000_000, 1),
    (0, 0),
]

CATEGORIES = ["Usta", "Dizayner", "Prorab"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("loyalty-bot")

_db_lock = threading.Lock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row


def db_init():
    with _db_lock:
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
                admin_id INTEGER,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS admins (
                telegram_id INTEGER PRIMARY KEY,
                name TEXT,
                created_at TEXT
            );
            """
        )
        _conn.commit()


def q(sql, params=(), *, fetch=None):
    with _db_lock:
        cur = _conn.execute(sql, params)
        if fetch == "one":
            row = cur.fetchone()
        elif fetch == "all":
            row = cur.fetchall()
        else:
            row = None
        _conn.commit()
        return cur.lastrowid if fetch is None else row


def get_client_by_phone(phone):
    return q("SELECT * FROM clients WHERE phone=?", (phone,), fetch="one")


def get_client_by_tg(tg_id):
    return q("SELECT * FROM clients WHERE telegram_id=?", (tg_id,), fetch="one")


def is_admin(tg_id):
    return tg_id in SUPER_ADMINS or q(
        "SELECT 1 FROM admins WHERE telegram_id=?", (tg_id,), fetch="one"
    ) is not None


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
        value = float(num) * mult if mult > 1 else float(num)
        return int(round(value))
    except ValueError:
        return None


def fmt(n):
    return f"{int(n):,}".replace(",", " ")


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


def client_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistikam")],
            [KeyboardButton(text="🏆 Bonus bosqichlari")],
        ],
        resize_keyboard=True,
    )


def admin_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Savdo qoshish")],
            [KeyboardButton(text="🔍 Mijozni tekshirish")],
            [KeyboardButton(text="👥 Mijozlar"), KeyboardButton(text="📊 Hisobot")],
            [KeyboardButton(text="🚪 Admin chiqish")],
        ],
        resize_keyboard=True,
    )


def contact_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def category_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=c)] for c in CATEGORIES],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Bekor qilish")]],
        resize_keyboard=True,
    )


class Reg(StatesGroup):
    name = State()
    category = State()


class AdminLogin(StatesGroup):
    password = State()


class AddSale(StatesGroup):
    phone = State()
    new_name = State()
    new_category = State()
    amount = State()
    customer = State()
    confirm = State()


class Lookup(StatesGroup):
    phone = State()


router = Router()
bot: Bot = None


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    tg_id = message.from_user.id
    if is_admin(tg_id):
        await message.answer("👋 Salom, admin! Savdo kiritishingiz mumkin.", reply_markup=admin_menu())
        return
    client = get_client_by_tg(tg_id)
    if client:
        await message.answer(
            f"👋 Salom, {client['full_name']}!{N}SODIQLIK TIZIMIga xush kelibsiz.",
            reply_markup=client_menu(),
        )
        return
    await message.answer(
        f"👋 Assalomu alaykum!{N}{N}"
        f"Bu - usta, dizayner va prorablar uchun <b>SODIQLIK TIZIMI</b>.{N}"
        f"Siz olib kelgan mijozlar showroomdan mahsulot xarid qilsa, sizga bonus yoziladi.{N}{N}"
        f"Royxatdan otish uchun pastdagi tugma orqali raqamingizni yuboring:",
        reply_markup=contact_kb(),
    )


@router.message(F.contact)
async def got_contact(message: Message, state: FSMContext):
    if message.contact.user_id and message.contact.user_id != message.from_user.id:
        await message.answer("Iltimos, ozingizning raqamingizni yuboring.")
        return
    phone = normalize_phone(message.contact.phone_number)
    await state.update_data(phone=phone)
    await state.set_state(Reg.name)
    await message.answer("Rahmat! Endi <b>ism-familiyangizni</b> yozing:", reply_markup=ReplyKeyboardRemove())


@router.message(Reg.name)
async def reg_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(Reg.category)
    await message.answer("Yonalishingizni tanlang:", reply_markup=category_kb())


@router.message(Reg.category)
async def reg_category(message: Message, state: FSMContext):
    cat = message.text.strip()
    if cat not in CATEGORIES:
        await message.answer("Iltimos, tugmalardan birini tanlang.")
        return
    data = await state.get_data()
    phone = data["phone"]
    name = data["name"]
    tg_id = message.from_user.id
    now = datetime.now().isoformat(timespec="seconds")
    existing = get_client_by_phone(phone)
    if existing:
        q("UPDATE clients SET full_name=?, category=?, telegram_id=? WHERE phone=?", (name, cat, tg_id, phone))
    else:
        q("INSERT INTO clients(phone, full_name, category, telegram_id, created_at) VALUES(?,?,?,?,?)", (phone, name, cat, tg_id, now))
    await state.clear()
    await message.answer(
        f"✅ Royxatdan otdingiz!{N}{N}"
        f"Ism: <b>{name}</b>{N}Yonalish: <b>{cat}</b>{N}Raqam: <b>{phone}</b>{N}{N}"
        f"Endi statistikangizni kuzatib borishingiz mumkin.",
        reply_markup=client_menu(),
    )


@router.message(F.text == "📊 Statistikam")
async def my_stats(message: Message):
    client = get_client_by_tg(message.from_user.id)
    if not client:
        await message.answer("Avval /start orqali royxatdan oting.")
        return
    turnover = client["total_turnover"]
    pct = percent_for(turnover)
    sales = q("SELECT * FROM sales WHERE client_id=? ORDER BY id DESC LIMIT 5", (client["id"],), fetch="all")
    text = (
        f"📊 <b>{client['full_name']}</b> ({client['category']}){N}"
        f"Jami aylanma: <b>{fmt(turnover)}</b> som{N}"
        f"Joriy bosqich: <b>{pct}%</b>{N}"
        f"Jami bonus: <b>{fmt(client['total_bonus'])}</b> som{N}"
    )
    nxt = next_tier_info(turnover)
    if nxt:
        nxt_pct, need = nxt
        text += f"{N}🎯 {nxt_pct}% gacha yana <b>{fmt(need)}</b> som kerak."
    else:
        text += f"{N}🏆 Eng yuqori bosqichdasiz - har savdoga 4%!"
    if sales:
        text += f"{N}{N}<b>Oxirgi savdolar:</b>{N}"
        for s in sales:
            d = s["created_at"][:10]
            text += f"- {d}: {fmt(s['amount'])} som -> {s['percent']}% = <b>{fmt(s['bonus'])}</b>{N}"
    else:
        text += f"{N}{N}Hali savdolar yoq."
    await message.answer(text, reply_markup=client_menu())


@router.message(F.text == "🏆 Bonus bosqichlari")
async def tiers_info(message: Message):
    text = (
        f"🏆 <b>BONUS BOSQICHLARI</b>{N}"
        f"Siz olib kelgan mijozlarning jami xaridiga qarab:{N}{N}"
        f"- 100 mln dan keyin -> <b>1%</b>{N}"
        f"- 200 mln dan keyin -> <b>2%</b>{N}"
        f"- 300 mln dan keyin -> <b>3%</b>{N}"
        f"- 400 mln dan keyin -> <b>4%</b>{N}{N}"
        f"Har bosqichga bir marta chiqsangiz kifoya - keyingi har bir savdoga shu foizda bonus yoziladi. 400 mln dan keyin doimiy 4%."
    )
    await message.answer(text, reply_markup=client_menu())


@router.message(Command("admin"))
async def admin_login_start(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        await message.answer("Siz allaqachon adminsiz.", reply_markup=admin_menu())
        return
    await state.set_state(AdminLogin.password)
    await message.answer("🔑 Admin parolini kiriting:", reply_markup=cancel_kb())


@router.message(AdminLogin.password)
async def admin_login_check(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=ReplyKeyboardRemove())
        return
    if message.text.strip() == ADMIN_PASSWORD:
        q("INSERT OR IGNORE INTO admins(telegram_id, name, created_at) VALUES(?,?,?)", (message.from_user.id, message.from_user.full_name, datetime.now().isoformat(timespec="seconds")))
        await state.clear()
        await message.answer("✅ Admin sifatida kirdingiz!", reply_markup=admin_menu())
    else:
        await message.answer("❌ Parol notogri. Qayta urinib koring yoki bekor qiling.")


@router.message(F.text == "🚪 Admin chiqish")
async def admin_logout(message: Message, state: FSMContext):
    if message.from_user.id in SUPER_ADMINS:
        await message.answer("Siz super-adminsiz.", reply_markup=admin_menu())
        return
    q("DELETE FROM admins WHERE telegram_id=?", (message.from_user.id,))
    await state.clear()
    await message.answer("Admin rejimidan chiqdingiz.", reply_markup=ReplyKeyboardRemove())


@router.message(F.text == "➕ Savdo qoshish")
async def sale_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(AddSale.phone)
    await message.answer(f"➕ <b>Yangi savdo</b>{N}{N}Mijoz telefon raqamini yuboring.{N}Masalan: 901234567", reply_markup=cancel_kb())


@router.message(AddSale.phone)
async def sale_phone(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu())
        return
    phone = normalize_phone(message.text)
    if len(phone) < 9:
        await message.answer("Raqam notogri. Qayta yuboring.")
        return
    await state.update_data(phone=phone)
    client = get_client_by_phone(phone)
    if client:
        await state.update_data(client_id=client["id"])
        await state.set_state(AddSale.amount)
        await message.answer(
            f"Mijoz: <b>{client['full_name']}</b> ({client['category']}){N}"
            f"Joriy aylanma: {fmt(client['total_turnover'])} som ({percent_for(client['total_turnover'])}%){N}{N}"
            f"Savdo summasini kiriting (somda):",
            reply_markup=cancel_kb(),
        )
    else:
        await state.set_state(AddSale.new_name)
        await message.answer(f"Bu raqam royxatda yoq. Yangi mijoz qoshamiz.{N}Mijozning <b>ism-familiyasini</b> yozing:", reply_markup=cancel_kb())


@router.message(AddSale.new_name)
async def sale_new_name(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu())
        return
    await state.update_data(new_name=message.text.strip())
    await state.set_state(AddSale.new_category)
    await message.answer("Yonalishini tanlang:", reply_markup=category_kb())


@router.message(AddSale.new_category)
async def sale_new_category(message: Message, state: FSMContext):
    cat = message.text.strip()
    if cat not in CATEGORIES:
        await message.answer("Tugmalardan birini tanlang.")
        return
    data = await state.get_data()
    now = datetime.now().isoformat(timespec="seconds")
    client_id = q("INSERT INTO clients(phone, full_name, category, created_at) VALUES(?,?,?,?)", (data["phone"], data["new_name"], cat, now))
    await state.update_data(client_id=client_id)
    await state.set_state(AddSale.amount)
    await message.answer(f"✅ Yangi mijoz qoshildi: <b>{data['new_name']}</b>{N}{N}Savdo summasini kiriting (somda):", reply_markup=cancel_kb())


@router.message(AddSale.amount)
async def sale_amount(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu())
        return
    amount = parse_amount(message.text)
    if not amount or amount <= 0:
        await message.answer("Summa notogri. Masalan: 50000000")
        return
    await state.update_data(amount=amount)
    await state.set_state(AddSale.customer)
    await message.answer("Xaridor ismini yozing yoki otkazish uchun - belgisini yuboring:", reply_markup=cancel_kb())


@router.message(AddSale.customer)
async def sale_customer(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu())
        return
    customer = "" if message.text.strip() == "-" else message.text.strip()
    data = await state.get_data()
    client = q("SELECT * FROM clients WHERE id=?", (data["client_id"],), fetch="one")
    before = client["total_turnover"]
    pct = percent_for(before)
    amount = data["amount"]
    bonus = amount * pct // 100
    after = before + amount
    await state.update_data(customer=customer, percent=pct, bonus=bonus, after=after)
    await state.set_state(AddSale.confirm)
    confirm_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✅ Tasdiqlash"), KeyboardButton(text="❌ Bekor qilish")]], resize_keyboard=True)
    msg = (
        f"🧾 <b>Tekshiring:</b>{N}"
        f"Mijoz: <b>{client['full_name']}</b> ({client['category']}){N}"
        f"Savdo summasi: <b>{fmt(amount)}</b> som{N}"
        f"Qollanadigan foiz: <b>{pct}%</b>{N}"
        f"Bonus: <b>{fmt(bonus)}</b> som{N}"
        f"Yangi jami aylanma: <b>{fmt(after)}</b> som (keyingi bosqich: {percent_for(after)}%){N}"
    )
    if customer:
        msg += f"Xaridor: {customer}{N}"
    msg += f"{N}Togrimi?"
    await message.answer(msg, reply_markup=confirm_kb)


@router.message(AddSale.confirm)
async def sale_confirm(message: Message, state: FSMContext):
    if message.text != "✅ Tasdiqlash":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu())
        return
    data = await state.get_data()
    now = datetime.now().isoformat(timespec="seconds")
    client = q("SELECT * FROM clients WHERE id=?", (data["client_id"],), fetch="one")
    q("INSERT INTO sales(client_id, amount, percent, bonus, turnover_after, customer, admin_id, created_at) VALUES(?,?,?,?,?,?,?,?)", (data["client_id"], data["amount"], data["percent"], data["bonus"], data["after"], data.get("customer", ""), message.from_user.id, now))
    q("UPDATE clients SET total_turnover=?, total_bonus=total_bonus+? WHERE id=?", (data["after"], data["bonus"], data["client_id"]))
    await state.clear()
    await message.answer(f"✅ Saqlandi!{N}Bonus: <b>{fmt(data['bonus'])}</b> som ({data['percent']}%)", reply_markup=admin_menu())
    if client["telegram_id"]:
        try:
            await bot.send_message(client["telegram_id"], f"🛍 Yangi savdo qayd etildi!{N}{N}Summa: <b>{fmt(data['amount'])}</b> som{N}Bonus ({data['percent']}%): <b>{fmt(data['bonus'])}</b> som{N}Jami aylanma: <b>{fmt(data['after'])}</b> som")
        except Exception as e:
            logger.warning("Mijozga xabar yuborilmadi: %s", e)


@router.message(F.text == "🔍 Mijozni tekshirish")
async def lookup_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.set_state(Lookup.phone)
    await message.answer("Mijoz telefon raqamini yuboring:", reply_markup=cancel_kb())


@router.message(Lookup.phone)
async def lookup_phone(message: Message, state: FSMContext):
    if message.text == "❌ Bekor qilish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=admin_menu())
        return
    phone = normalize_phone(message.text)
    client = get_client_by_phone(phone)
    await state.clear()
    if not client:
        await message.answer("Bunday mijoz topilmadi.", reply_markup=admin_menu())
        return
    turnover = client["total_turnover"]
    await message.answer(
        f"👤 <b>{client['full_name']}</b> ({client['category']}){N}"
        f"Raqam: {client['phone']}{N}"
        f"Jami aylanma: <b>{fmt(turnover)}</b> som ({percent_for(turnover)}%){N}"
        f"Jami bonus: <b>{fmt(client['total_bonus'])}</b> som",
        reply_markup=admin_menu(),
    )


@router.message(F.text == "👥 Mijozlar")
async def clients_list(message: Message):
    if not is_admin(message.from_user.id):
        return
    rows = q("SELECT * FROM clients ORDER BY total_turnover DESC LIMIT 30", fetch="all")
    if not rows:
        await message.answer("Hali mijozlar yoq.", reply_markup=admin_menu())
        return
    text = f"👥 <b>Mijozlar (aylanma boyicha):</b>{N}"
    i = 0
    for c in rows:
        i += 1
        text += f"{i}. {c['full_name']} - {fmt(c['total_turnover'])} som ({percent_for(c['total_turnover'])}%){N}"
    await message.answer(text, reply_markup=admin_menu())


@router.message(F.text == "📊 Hisobot")
async def report(message: Message):
    if not is_admin(message.from_user.id):
        return
    row = q("SELECT COUNT(*) c, COALESCE(SUM(total_turnover),0) t, COALESCE(SUM(total_bonus),0) b FROM clients", fetch="one")
    sales_cnt = q("SELECT COUNT(*) c FROM sales", fetch="one")["c"]
    await message.answer(
        f"📊 <b>UMUMIY HISOBOT</b>{N}"
        f"Mijozlar: <b>{row['c']}</b>{N}"
        f"Savdolar soni: <b>{sales_cnt}</b>{N}"
        f"Jami aylanma: <b>{fmt(row['t'])}</b> som{N}"
        f"Jami berilgan bonus: <b>{fmt(row['b'])}</b> som",
        reply_markup=admin_menu(),
    )


@router.message()
async def fallback(message: Message):
    if is_admin(message.from_user.id):
        await message.answer("Menyudan tanlang:", reply_markup=admin_menu())
    elif get_client_by_tg(message.from_user.id):
        await message.answer("Menyudan tanlang:", reply_markup=client_menu())
    else:
        await message.answer("Boshlash uchun /start ni bosing.")


async def main():
    global bot
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN berilmagan!")
    db_init()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("Bot ishga tushdi...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
