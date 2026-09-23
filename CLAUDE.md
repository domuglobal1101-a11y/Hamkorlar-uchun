# DOMU SODIQLIK TIZIMI - Telegram bot (loyiha konteksti / handoff)

Usta / dizayner / prorablar (B2B hamkorlar) uchun sodiqlik (loyalty) tizimi - Telegram bot. Hamkor olib kelgan mijozlar showroomdan mahsulot sotib olsa, hamkorga bonus yoziladi.

## Bonus mantigi (FORWARD model)
Hamkorning jami aylanmasi osib boradi; bosqichga chiqqandan KEYINGI har bir savdoga foiz qollanadi: 100mln -> 1%, 200mln -> 2%, 300mln -> 3%, 400mln -> 4% (doimiy). Foiz = shu savdodan OLDINGI jami aylanmaga qarab. TIERS royxatida (bot.py) sozlanadi.

## Rollar
- Rahbar (owner): /admin + ADMIN_PASSWORD bilan kiradi. Sotuvchilarni qoshadi/ochiradi, hisobotlarni koradi. owners jadvali yoki SUPER_ADMINS env.
- Sotuvchi (seller): /kirish + shaxsiy 6 xonali kod. Savdo kiritadi; har savdo shu sotuvchiga boglanadi (sales.seller_id).
- Mijoz (hamkor): /start + telefon raqami bilan royxatdan otadi; oz statistikasini koradi.

## Texnologiya
- Python 3.13, aiogram 3.13.1 (long polling).
- SQLite (loyalty.db), Railway Volume ichida: /data/loyalty.db.
- Fayllar: bot.py (butun bot), requirements.txt, Procfile.
- Google Sheets sync: har savdo/sotuvchi/registratsiyada bot GSHEET_URL ga JSON POST qiladi. Apps Script web app Hamkorlar, Sotuvchilar, Statistika varaqlarini yozadi.

## DB sxemasi (db_init)
- clients(id, phone UNIQUE, full_name, category, telegram_id UNIQUE, total_turnover, total_bonus, created_at)
- sales(id, client_id, amount, percent, bonus, turnover_after, customer, seller_id, seller_name, created_at)
- owners(telegram_id PK, name, created_at)
- sellers(id, name, code UNIQUE, telegram_id, created_at)

## Deploy
- GitHub: https://github.com/domuglobal1101-a11y/Hamkorlar-uchun
- Railway: project vivacious-grace, service worker (GitHub auto-deploy), Hobby reja (~5 USD/oy). Volume worker-volume mount /data.
- Bot: @domu_sales_kpi_bot. Komandalar: /start, /admin, /kirish.

## Muhit ozgaruvchilari (Railway -> worker -> Variables)
- BOT_TOKEN: BotFather tokeni (MAXFIY)
- ADMIN_PASSWORD: rahbar paroli (MAXFIY)
- DB_PATH: /data/loyalty.db
- GSHEET_URL: Apps Script web app URL (MAXFIY)
- SUPER_ADMINS: (ixtiyoriy) rahbar Telegram ID, vergul bilan

DIQQAT: MAXFIY qiymatlarni (token, parol, GSHEET_URL) hech qachon kodga yoki GitHub ga yozmang. Ular faqat Railway Variables da turadi.

## Mahalliy ishga tushirish
1. git clone https://github.com/domuglobal1101-a11y/Hamkorlar-uchun
2. pip install -r requirements.txt
3. BOT_TOKEN, ADMIN_PASSWORD, DB_PATH, GSHEET_URL ni bering
4. python bot.py
Kod main ga push qilinsa, Railway avtomatik qayta deploy qiladi.

## Kelajakdagi yaxshilash goyalari (TODO)
1. Bonus tolash boshqaruvi (tolangan/tolanmagan holati).
2. Excel/PDF hisobot eksporti (oylik/haftalik).
3. Sanaga qarab filtrlangan hisobotlar.
4. Savdoni tahrirlash yoki bekor qilish.
5. Bonus bosqich va foizlarni botdan sozlash.
6. Sotuvchiga oz savdolari royxati.
7. Mijozga toliq xaridlar tarixi.
8. Kop tillilik (ozbek/rus).
9. PostgreSQL migratsiyasi.
10. Unit-testlar.
11. Loglar va ogohlantirishlarni yaxshilash.

## Claude Code bilan ishlash
1. Claude Code ni ornating.
2. Reponi klon qiling.
3. Papka ichida claude buyrugini ishga tushiring - u shu CLAUDE.md ni oqiydi.
4. Kerakli yaxshilashni ayting; u kodni yozadi, siz git push qilasiz, Railway deploy qiladi.
