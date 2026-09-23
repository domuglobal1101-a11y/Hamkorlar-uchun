"""Bot oqimlarini Telegram'ga ulanmasdan tekshirish: so'rovlar soxta sessiya orqali ushlanadi."""

import asyncio
import os
import sys
from datetime import datetime

os.environ["ADMIN_PASSWORD"] = "boss-parol"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402
from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.methods import DeleteMessage, SendDocument, SendMessage  # noqa: E402
from aiogram.types import Chat, Contact, Message, Update, User  # noqa: E402

import bot as botmod  # noqa: E402
import db  # noqa: E402

OWNER, SELLER, PARTNER = 1001, 2002, 3003


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, SendDocument)):
            return Message(message_id=len(self.calls), date=datetime.now(), chat=Chat(id=method.chat_id, type="private"),
                           text=getattr(method, "text", None))
        if isinstance(method, DeleteMessage):
            return True
        return True

    async def stream_content(self, *a, **k):
        yield b""

    async def close(self):
        pass


class Harness:
    def __init__(self):
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session)
        self.dp = Dispatcher(storage=MemoryStorage())
        self.dp.include_router(botmod.router)
        self.n = 0

    async def send(self, uid, text=None, contact=None):
        self.n += 1
        user = User(id=uid, is_bot=False, first_name=f"U{uid}")
        msg = Message(message_id=self.n, date=datetime.now(), chat=Chat(id=uid, type="private"),
                      from_user=user, text=text, contact=contact)
        before = len(self.session.calls)
        await self.dp.feed_update(self.bot, Update(update_id=self.n, message=msg))
        return self.session.calls[before:]

    async def say(self, uid, text=None, contact=None):
        calls = await self.send(uid, text, contact)
        return "\n".join(c.text for c in calls if isinstance(c, SendMessage))


@pytest.fixture(autouse=True)
def fresh(tmp_path):
    db.connect(str(tmp_path / "t.db"))
    botmod._fails.clear()
    botmod.sheets.GSHEET_URL = ""
    # Router faqat bitta Dispatcher'ga ulanadi - har testda uzamiz
    botmod.router._parent_router = None


def test_full_flow():
    async def t():
        h = Harness()
        # Hamkor ro'yxatdan o'tadi
        assert "SODIQLIK" in await h.say(PARTNER, "/start")
        out = await h.say(PARTNER, contact=Contact(phone_number="+998901234567", first_name="A", user_id=PARTNER))
        assert "ism-familiya" in out
        await h.say(PARTNER, "Ali <Usta>")
        assert "Royxatdan otdingiz" in await h.say(PARTNER, "Usta")
        assert db.get_client_by_tg(PARTNER)["full_name"] == "Ali Usta"

        # Rahbar: xato parol, keyin to'g'ri; parol xabari o'chiriladi
        await h.say(OWNER, "/admin")
        assert "notogri" in await h.say(OWNER, "xato")
        calls = await h.send(OWNER, "boss-parol")
        assert any(isinstance(c, DeleteMessage) for c in calls)
        assert botmod.is_owner(OWNER)

        # Sotuvchi qo'shish va kirish
        await h.say(OWNER, "➕ Sotuvchi qoshish")
        await h.say(OWNER, "Sardor")
        code = db.q("SELECT code FROM sellers", fetch="one")["code"]
        await h.say(SELLER, "/kirish")
        assert "Xush kelibsiz" in await h.say(SELLER, code)

        # Sotuvchi savdo kiritadi (150 mln -> 0%, keyin 100 mln -> 1%)
        for amount in ["150 mln", "100000000"]:
            await h.say(SELLER, "➕ Savdo qoshish")
            await h.say(SELLER, "901234567")
            await h.say(SELLER, amount)
            await h.say(SELLER, "-")
            assert "Saqlandi" in await h.say(SELLER, "✅ Tasdiqlash")
        c = db.get_client_by_tg(PARTNER)
        assert c["total_turnover"] == 250_000_000 and c["total_bonus"] == 1_000_000

        # Hamkor statistikasi
        out = await h.say(PARTNER, "📊 Statistikam")
        assert "To'lanishi kerak: <b>1 000 000</b>" in out

        # Rahbar bonus to'laydi
        await h.say(OWNER, "💸 Bonus to'lash")
        await h.say(OWNER, "901234567")
        await h.say(OWNER, "Hammasi (1 000 000)")
        await h.say(OWNER, "naqd")
        assert "To'lov yozildi" in await h.say(OWNER, "✅ Tasdiqlash")
        assert db.client_balance(c["id"]) == (1_000_000, 1_000_000, 0)

        # Rahbar birinchi savdoni bekor qiladi -> bonus 0, ortiqcha to'lov haqida ogohlantirish
        first = db.q("SELECT id FROM sales ORDER BY id LIMIT 1", fetch="one")["id"]
        await h.say(OWNER, "↩️ Savdoni bekor qilish")
        await h.say(OWNER, f"#{first}")
        out = await h.say(OWNER, "✅ Tasdiqlash")
        assert "bekor qilindi" in out and "ortiqcha" in out

        # Hisobot va Excel
        await h.say(OWNER, "📊 Hisobot")
        calls = await h.send(OWNER, "📥 Excel")
        assert calls

        # Menyu tugmasi FSM ichida ham ishlaydi; Bekor qilish holatni tozalaydi
        await h.say(SELLER, "➕ Savdo qoshish")
        assert "Bekor qilindi" in await h.say(SELLER, "❌ Bekor qilish")
        await h.bot.session.close()
    asyncio.run(t())


def test_seller_code_bruteforce_blocked():
    async def t():
        h = Harness()
        db.create_seller("X")
        for _ in range(5):
            await h.say(SELLER, "/kirish")
            await h.say(SELLER, "000000")
        code = db.q("SELECT code FROM sellers", fetch="one")["code"]
        out = await h.say(SELLER, "/kirish")
        assert "Juda ko'p" in out
        assert db.get_seller_by_tg(SELLER) is None
        # Bloklangan paytda to'g'ri kod ham qabul qilinmaydi
        await h.say(SELLER, code)
        assert db.get_seller_by_tg(SELLER) is None
    asyncio.run(t())


def test_admin_disabled_without_password(monkeypatch):
    async def t():
        monkeypatch.setattr(botmod, "ADMIN_PASSWORD", "")
        h = Harness()
        assert "o'chirilgan" in await h.say(OWNER, "/admin")
    asyncio.run(t())
