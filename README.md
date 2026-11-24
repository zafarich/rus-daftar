# Rus daftari

Bitta konteynerda ikki narsa ishlaydi:

- **Telegram bot.** Kuniga bir necha marta o'zi savol yuboradi (so'z, predlog, qo'shimcha, to'liq gap, tanlash), javobingizni tekshiradi va keyingisiga o'tadi. Ertalab 7 kunlik rejaning bugungi vazifasini yuboradi, kechqurun «Bajarildimi?» deb so'raydi.
- **Veb-daftar** (`https://DOMAIN`). Bo'limlar: qoida, flip kartochkalar, mashq (predlog / qo'shimcha / tanlash / tarjima / to'liq gap), tezkor «ramka» mashqi va 7 kunlik reja. Ruscha javobni ovoz chiqarib o'qiydi, Chrome'da javobni ovoz bilan aytish ham mumkin.

Xato qilingan savol 10 daqiqadan keyin, to'g'ri topilgani esa 1, 2, 4, 8… kundan keyin qaytadi (Leitner takrorlash usuli). Bot va veb bitta bazadan foydalanadi, shuning uchun natijalar umumiy.

## O'rnatish

```bash
# 1. Eski loyihani to'xtating (bitta token bilan ikki bot ishlay olmaydi)
# 2. Kodni serverga ko'chiring
cp .env.example .env
nano .env          # BOT_TOKEN va DOMAIN eski loyihadan; ACCESS_KEY: openssl rand -hex 24
docker compose up -d --build
```

Botga `/myid` yuboring, chiqqan raqamni `.env` dagi `OWNER_ID` ga yozing va qayta ishga tushiring:

```bash
docker compose up -d
```

Birinchi mavzuni yuklang:

```bash
docker compose exec daftar python manage.py import topics/01_predlozhny.json --activate
```

Botga `/start` yuboring va «Daftarni ochish» tugmasini bosing.

### Domen (nginx misoli)

Eski loyihadagi domen shu konteynerga yo'naltiriladi. Ilova `127.0.0.1:8000` da ishlaydi:

```nginx
server {
    server_name daftar.example.uz;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    # listen 443 ssl; ... (mavjud sertifikatingiz)
}
```

Eski loyiha webhook ishlatgan bo'lsa, yangi bot ishga tushganda uni avtomatik o'chiradi va polling'ga o'tadi.

### Docker'siz (Python 3.10+, venv + systemd)

```bash
cd /var/www/zafar
git clone https://github.com/zafarich/rus-daftar.git
cd rus-daftar
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env      # WEB_HOST=127.0.0.1 ni ham yozing
.venv/bin/python manage.py import topics/01_predlozhny.json --activate
```

`/etc/systemd/system/rus-daftar.service`:

```ini
[Unit]
Description=Rus daftari (bot + veb)
After=network-online.target

[Service]
User=SIZNING_USER
WorkingDirectory=/var/www/zafar/rus-daftar
ExecStart=/var/www/zafar/rus-daftar/.venv/bin/python -m app.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now rus-daftar
journalctl -u rus-daftar -f            # loglar
```

Yangilash: `git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart rus-daftar`.
`manage.py` buyruqlarini `docker compose exec daftar python` o'rniga `.venv/bin/python` bilan ishga tushiring.

## Yangi mavzu qo'shish

1. Claude bilan chatda mavzuni tayyorlaysiz. U sizga `topics/02_....json` fayl beradi.
2. Faylni serverdagi `topics/` papkasiga qo'yasiz.
3. Tekshirib, yuklaysiz:

```bash
docker compose exec daftar python manage.py check  topics/02_vinitelny.json
docker compose exec daftar python manage.py import topics/02_vinitelny.json --activate
```

Mavjud mavzuni tuzatib qayta import qilsangiz, o'zgarmagan savollar bo'yicha natijalaringiz saqlanib qoladi.

Boshqa buyruqlar:

```bash
docker compose exec daftar python manage.py list
docker compose exec daftar python manage.py activate predlozhny --restart   # 7 kunni qaytadan
```

## Bot buyruqlari

| Buyruq | Vazifasi |
|---|---|
| `/quiz`, `/quiz 15` | Savollarni hozir boshlash |
| `/hint`, `/skip`, `/stop` | Yordam, o'tkazish, to'xtatish |
| `/plan` | 7 kunlik reja, bugungi vazifa, belgilash |
| `/topics`, `/topic slug` | Mavzular, faol mavzuni almashtirish |
| `/restart` | Faol mavzuning 7 kunini qaytadan boshlash |
| `/stats` | Bugungi va haftalik natija |
| `/daftar` | Veb-daftar havolasi |

Vaqtlar `.env` da sozlanadi: `QUIZ_TIMES`, `PLAN_MORNING`, `PLAN_EVENING`.

## AI tekshiruv (ixtiyoriy)

`.env` ga `ANTHROPIC_API_KEY` yozsangiz, tarjima va to'liq gaplarda javob namunaga aynan mos kelmaganda Claude tekshiradi va o'zbekcha izoh beradi. Kalit bo'lmasa, faqat namunalar bilan solishtiriladi. Predlog va qo'shimcha savollari doim aniq tekshiriladi.

## Mavzu fayli formati

```json
{
  "slug": "predlozhny", "title": "…", "sort": 1,
  "summary": "## Sarlavha\nMatn, **qalin**\n- ro'yxat",
  "cards":     [{"front": "Toshkentda", "back": "в Ташкенте", "note": "…"}],
  "exercises": [
    {"type": "gap",       "prompt": "Я живу ___ Ташкенте.", "answers": ["в"]},
    {"type": "ending",    "prompt": "Я работаю в банк___.", "answers": ["е"]},
    {"type": "choice",    "prompt": "Он ___ концерте.", "options": ["в","на"], "answers": ["на"]},
    {"type": "translate", "prompt": "Men uydaman.", "answers": ["Я дома"]},
    {"type": "sentence",  "prompt": "Kecha to'yda edim.", "answers": ["Вчера я был на свадьбе"]}
  ],
  "drills": [{"title": "Я сейчас в …", "frame": "Я сейчас в ___.",
              "items": [{"uz": "do'kon", "base": "магазин", "answer": "магазине"}]}],
  "plan": [{"day": 1, "tasks": ["…", "…"]}]
}
```

`gap`, `ending` va `drill` savollarida `___` bo'lishi shart. Javob tekshiruvi katta-kichik harf, `ё/е` va tinish belgilariga qaramaydi. `ending` uchun «е», «банке» va «в банке» javoblarining hammasi qabul qilinadi.

## Zaxira nusxa

Butun baza bitta fayl: `data/daftar.db`. Uni vaqti-vaqti bilan nusxalab qo'ying.
