import asyncio
import os
import sys

os.environ["DASHBOARD_PASSWORD"] = "test-parol"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import web  # noqa: E402

MLN = 1_000_000


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.connect(str(tmp_path / "t.db"))
    web._login_fails.clear()


def run(coro_fn):
    async def main():
        client = TestClient(TestServer(web.make_app()))
        await client.start_server()
        try:
            await coro_fn(client)
        finally:
            await client.close()
    asyncio.run(main())


async def login(client, pw="test-parol"):
    return await client.post("/login", data={"password": pw}, allow_redirects=False)


def test_requires_login():
    async def t(c):
        r = await c.get("/", allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "/login"
        r = await c.post("/hamkorlar/ochirish", data={"ids": "1"}, allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "/login"
    run(t)


def test_wrong_password_and_lockout():
    async def t(c):
        for _ in range(5):
            r = await login(c, "xato")
            assert "/login" in r.headers["Location"]
        r = await login(c)  # to'g'ri parol ham bloklangan
        assert "err=" in r.headers["Location"]
    run(t)


def test_pages_render_and_escape_html():
    cid = db.create_client("998901112233", "<script>x</script>", "Usta")
    db.add_sale(cid, 150 * MLN, "", None, "Rahbar")

    async def t(c):
        r = await login(c)
        assert r.headers["Location"] == "/"
        for path in ["/", "/?p=all", "/?from=2020-01-01&to=2030-01-01", "/hamkorlar", "/hamkorlar?q=998",
                     f"/hamkor/{cid}", "/savdolar?p=all", "/savdolar?p=all&status=cancelled",
                     "/tolovlar", "/sotuvchilar"]:
            r = await c.get(path)
            assert r.status == 200, path
            text = await r.text()
            assert "<script>x" not in text, path
        r = await c.get("/excel?p=all")
        assert r.status == 200 and (await r.read())[:2] == b"PK"
    run(t)


def test_bulk_delete_and_actions():
    a = db.create_client("998901112233", "Test 1", "Usta")
    b = db.create_client("998901112234", "Test 2", "Usta")
    keep = db.create_client("998901112235", "Haqiqiy", "Prorab")
    s1 = db.add_sale(keep, 150 * MLN, "", None, "Rahbar")
    s2 = db.add_sale(keep, 50 * MLN, "", None, "Rahbar")

    async def t(c):
        await login(c)
        r = await c.post("/hamkorlar/ochirish", data=[("ids", str(a)), ("ids", str(b))], allow_redirects=False)
        assert r.status == 302
        assert db.get_client(a) is None and db.get_client(b) is None and db.get_client(keep)

        await c.post(f"/savdo/{s1['id']}/bekor", data={"back": "/savdolar"}, allow_redirects=False)
        assert db.get_client(keep)["total_bonus"] == 0
        await c.post(f"/savdo/{s1['id']}/tiklash", data={"back": "/savdolar"}, allow_redirects=False)
        assert db.get_client(keep)["total_bonus"] == 500_000

        await c.post(f"/savdo/{s2['id']}/tahrir", data={"amount": "100000000", "back": "/"}, allow_redirects=False)
        assert db.get_client(keep)["total_bonus"] == 1 * MLN

        await c.post(f"/hamkor/{keep}/tolov", data={"amount": "400000", "note": "naqd"}, allow_redirects=False)
        assert db.client_balance(keep) == (1 * MLN, 400_000, 600_000)

        r = await c.post(f"/hamkor/{keep}/tahrir", data={"name": "Yangi ism", "phone": "998901112235", "category": "Dizayner"}, allow_redirects=False)
        assert "ok=" in r.headers["Location"]
        assert db.get_client(keep)["category"] == "Dizayner"

        await c.post("/sotuvchi/qoshish", data={"name": "Sardor"}, allow_redirects=False)
        s = db.q("SELECT * FROM sellers", fetch="one")
        old = s["code"]
        await c.post(f"/sotuvchi/{s['id']}/kod", allow_redirects=False)
        assert db.get_seller(s["id"])["code"] != old

        await c.post(f"/hamkor/{keep}/ochirish", allow_redirects=False)
        assert db.get_client(keep) is None
    run(t)


def test_cross_origin_post_blocked():
    cid = db.create_client("998901112233", "Test", "Usta")

    async def t(c):
        await login(c)
        r = await c.post(f"/hamkor/{cid}/ochirish", headers={"Origin": "https://evil.example"}, allow_redirects=False)
        assert r.status == 403
        assert db.get_client(cid)
    run(t)
