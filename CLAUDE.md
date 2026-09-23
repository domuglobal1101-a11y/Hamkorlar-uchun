# DOMU SODIQLIK TIZIMI - Telegram bot (loyiha konteksti / handoff)

Usta / dizayner / prorablar (B2B hamkorlar) uchun sodiqlik (loyalty) tizimi - Telegram bot. Hamkor olib kelgan mijozlar showroomdan mahsulot sotib olsa, hamkorga bonus yoziladi.

## Bonus mantigi (FORWARD model)
Hamkorning jami aylanmasi osib boradi; bosqichga chiqqandan KEYINGI har bir savdoga foiz qollanadi: 100mln -> 1%, 200mln -> 2%, 300mln -> 3%, 400mln -> 4% (doimiy). Foiz = shu savdodan OLDINGI jami aylanmaga qarab. TIERS royxatida (bot.py) sozlanadi.

## Rollar
- Rahbar (owner): /admin + ADMIN_PASSWORD bilan kiradi (parol o'rnatilmasa /admin o'chiq). Sotuvchilarni qoshadi/ochiradi, bonus to'laydi, savdoni bekor qiladi, hisobot va Excel oladi. owners jadvali yoki SUPER_ADMINS env.
- /admin va /kirish: 5 marta xato -> 15 daqiqa blok; kiritilgan parol/kod chatdan o'chiriladi.
- Sotuvchi (seller): /kirish + shaxsiy 6 xonali kod. Savdo kiritadi; har savdo shu sotuvchiga boglanadi (sales.seller_id).
- Mijoz (hamkor): /start + telefon raqami bilan royxatdan otadi; oz statistikasini koradi.

## Texnologiya
- Python 3.13, aiogram 3.13.1 (long polling).
- SQLite (loyalty.db), Railway Volume ichida: /data/loyalty.db.
- Fayllar: bot.py (Telegram handlerlar + main), db.py (sxema, bonus hisobi, hisobotlar), web.py (dashboard), excel.py (xlsx eksport), sheets.py (Google Sheets sync), tests/ (pytest).
- Dashboard: aiohttp, bot bilan BITTA jarayonda, PORT env berilganda ishga tushadi. Kirish: DASHBOARD_PASSWORD (bo'lmasa ADMIN_PASSWORD). Hamkor/savdo/to'lov/sotuvchini tahrirlash va o'chirish, sana bo'yicha hisobot, Excel.
- Vaqt: Asia/Tashkent (db.now). 2026-09-23 dan oldingi yozuvlar UTC da saqlangan (5 soat farq).
- Google Sheets sync: har savdo/sotuvchi/registratsiyada bot GSHEET_URL ga JSON POST qiladi. Apps Script web app Hamkorlar, Sotuvchilar, Statistika varaqlarini yozadi.

## DB sxemasi (db_init)
- clients(id, phone UNIQUE, full_name, category, telegram_id UNIQUE, total_turnover, total_bonus, created_at)
- sales(id, client_id, amount, percent, bonus, turnover_after, customer, seller_id, seller_name, created_at, status['active'|'cancelled'], cancelled_at, cancelled_by)
- payouts(id, client_id, amount, note, created_by, created_at) - to'langan bonuslar. Qoldiq = clients.total_bonus - SUM(payouts).
- owners(telegram_id PK, name, created_at)
- sellers(id, name, code UNIQUE, telegram_id, created_at)

## Deploy
- GitHub: https://github.com/domuglobal1101-a11y/Hamkorlar-uchun
- Railway: project vivacious-grace, service worker (GitHub auto-deploy), Hobby reja (~5 USD/oy). Volume worker-volume mount /data.
- Bot: @domu_sales_kpi_bot. Komandalar: /start, /admin, /kirish.

## Bonusni qayta hisoblash
Savdo bekor qilinsa/tahrirlansa/o'chirilsa db.recalc_client() hamkorning barcha FAOL savdolarini id tartibida qaytadan hisoblaydi (forward modelda keyingi savdolar foizi ham o'zgaradi). Bekor qilingan savdo aylanmaga kirmaydi, tarixda qoladi.

## Muhit ozgaruvchilari (Railway -> worker -> Variables)
- BOT_TOKEN: BotFather tokeni (MAXFIY)
- ADMIN_PASSWORD: rahbar paroli (MAXFIY)
- DB_PATH: /data/loyalty.db
- GSHEET_URL: Apps Script web app URL (MAXFIY)
- SUPER_ADMINS: (ixtiyoriy) rahbar Telegram ID, vergul bilan
- DASHBOARD_PASSWORD: (ixtiyoriy, MAXFIY) dashboard paroli; bo'lmasa ADMIN_PASSWORD ishlatiladi
- SESSION_SECRET: (ixtiyoriy) dashboard cookie imzosi uchun
- PORT: Railway o'zi beradi; dashboard shu portda ochiladi (Settings -> Networking -> Generate Domain)

DIQQAT: MAXFIY qiymatlarni (token, parol, GSHEET_URL) hech qachon kodga yoki GitHub ga yozmang. Ular faqat Railway Variables da turadi.

## Mahalliy ishga tushirish
1. git clone https://github.com/domuglobal1101-a11y/Hamkorlar-uchun
2. pip install -r requirements.txt
3. BOT_TOKEN, ADMIN_PASSWORD, DB_PATH, GSHEET_URL ni bering
4. python bot.py  (dashboard uchun PORT=8765 ham bering; BOT_TOKEN bo'lmasa faqat dashboard ishlaydi)
5. Testlar: pip install pytest && python -m pytest tests
Kod main ga push qilinsa, Railway avtomatik qayta deploy qiladi.

## Kelajakdagi yaxshilash goyalari (TODO)
Bajarilgan (2026-09-23): bonus to'lash, Excel eksport, sana bo'yicha hisobot, savdoni bekor qilish/tahrirlash, sotuvchiga o'z savdolari, hamkorga to'liq tarix, unit-testlar, dashboard, xavfsizlik.
1. Bonus bosqich va foizlarni botdan/dashboarddan sozlash.
2. Kop tillilik (ozbek/rus).
3. PostgreSQL migratsiyasi.
4. Loglar va ogohlantirishlarni yaxshilash.
5. Bazaning avtomatik zaxira nusxasi (backup).

## Claude Code bilan ishlash
1. Claude Code ni ornating.
2. Reponi klon qiling.
3. Papka ichida claude buyrugini ishga tushiring - u shu CLAUDE.md ni oqiydi.
4. Kerakli yaxshilashni ayting; u kodni yozadi, siz git push qilasiz, Railway deploy qiladi.
