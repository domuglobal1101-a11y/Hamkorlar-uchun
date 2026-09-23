import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db  # noqa: E402

MLN = 1_000_000


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    db.connect(str(tmp_path / "t.db"))


def make_client(phone="998901112233", name="Ali"):
    return db.create_client(phone, name, "Usta")


def test_forward_bonus_model():
    cid = make_client()
    s1 = db.add_sale(cid, 120 * MLN, "", None, "Rahbar")  # 0 dan boshlanadi -> 0%
    s2 = db.add_sale(cid, 50 * MLN, "", None, "Rahbar")   # 120 mln -> 1%
    s3 = db.add_sale(cid, 100 * MLN, "", None, "Rahbar")  # 170 mln -> 1%
    s4 = db.add_sale(cid, 10 * MLN, "", None, "Rahbar")   # 270 mln -> 2%
    assert [s1["percent"], s2["percent"], s3["percent"], s4["percent"]] == [0, 1, 1, 2]
    c = db.get_client(cid)
    assert c["total_turnover"] == 280 * MLN
    assert c["total_bonus"] == 500_000 + 1 * MLN + 200_000


def test_cancel_recalculates_following_sales():
    cid = make_client()
    first = db.add_sale(cid, 150 * MLN, "", None, "Rahbar")
    db.add_sale(cid, 100 * MLN, "", None, "Rahbar")  # 1% = 1 mln
    db.add_sale(cid, 100 * MLN, "", None, "Rahbar")  # 250 -> 2% = 2 mln
    assert db.get_client(cid)["total_bonus"] == 3 * MLN

    old, new = db.cancel_sale(first["id"], "test")
    assert old == 3 * MLN
    # Endi: 100 (0%), 100 (100 mln dan keyin 1%) -> 1 mln
    assert new == 1 * MLN
    c = db.get_client(cid)
    assert c["total_turnover"] == 200 * MLN
    cancelled = db.get_sale(first["id"])
    assert cancelled["status"] == "cancelled"

    db.restore_sale(first["id"])
    assert db.get_client(cid)["total_bonus"] == 3 * MLN
    assert db.get_client(cid)["total_turnover"] == 350 * MLN


def test_edit_and_delete_sale():
    cid = make_client()
    a = db.add_sale(cid, 100 * MLN, "", None, "Rahbar")
    b = db.add_sale(cid, 50 * MLN, "", None, "Rahbar")
    assert db.get_sale(b["id"])["bonus"] == 500_000
    db.update_sale_amount(a["id"], 90 * MLN)  # endi b 90 mln dan keyin -> 0%
    assert db.get_sale(b["id"])["bonus"] == 0
    db.delete_sale(a["id"])
    c = db.get_client(cid)
    assert c["total_turnover"] == 50 * MLN and c["total_bonus"] == 0


def test_payouts_and_balance():
    cid = make_client()
    db.add_sale(cid, 100 * MLN, "", None, "Rahbar")
    db.add_sale(cid, 100 * MLN, "", None, "Rahbar")  # 1 mln bonus
    assert db.client_balance(cid) == (1 * MLN, 0, 1 * MLN)
    pid = db.add_payout(cid, 400_000, "naqd", "test")
    assert db.client_balance(cid) == (1 * MLN, 400_000, 600_000)
    db.delete_payout(pid)
    assert db.client_balance(cid)[2] == 1 * MLN


def test_delete_client_removes_everything():
    cid = make_client()
    other = make_client("998907778899", "Vali")
    db.add_sale(cid, 200 * MLN, "", None, "Rahbar")
    db.add_payout(cid, 1, "", "test")
    db.add_sale(other, 5 * MLN, "", None, "Rahbar")
    db.delete_client(cid)
    assert db.get_client(cid) is None
    assert db.q("SELECT COUNT(*) c FROM sales WHERE client_id=?", (cid,), fetch="one")["c"] == 0
    assert db.q("SELECT COUNT(*) c FROM payouts WHERE client_id=?", (cid,), fetch="one")["c"] == 0
    assert db.get_client(other)["total_turnover"] == 5 * MLN


def test_report_excludes_cancelled_and_counts_sellers():
    cid = make_client()
    db.create_seller("Sotuvchi 1")
    seller = db.q("SELECT * FROM sellers", fetch="one")
    db.add_sale(cid, 10 * MLN, "", seller["id"], seller["name"])
    s = db.add_sale(cid, 20 * MLN, "", seller["id"], seller["name"])
    db.cancel_sale(s["id"], "test")
    rep = db.report(*db.period_range("today"))
    assert rep["sales_count"] == 1 and rep["turnover"] == 10 * MLN
    assert rep["sellers"][0]["cnt"] == 1
    assert rep["new_clients"] == 1
    assert db.report(*db.period_range("all"))["turnover_total"] == 10 * MLN


def test_old_schema_migrates(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE sales (id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL, amount INTEGER NOT NULL,"
        " percent INTEGER NOT NULL, bonus INTEGER NOT NULL, turnover_after INTEGER NOT NULL, customer TEXT, created_at TEXT);"
        "INSERT INTO sales(client_id, amount, percent, bonus, turnover_after) VALUES (1, 5, 0, 0, 5);"
    )
    con.commit()
    con.close()
    db.connect(path)
    row = db.q("SELECT * FROM sales", fetch="one")
    assert row["status"] == "active" and row["seller_id"] is None


def test_excel_builds():
    import excel
    cid = make_client()
    db.add_sale(cid, 150 * MLN, "Xaridor", None, "Rahbar")
    data = excel.build()
    assert data[:2] == b"PK" and len(data) > 3000


def test_clean():
    assert db.clean("  <b>Ali</b> & Vali  ") == "bAli/b va Vali"
