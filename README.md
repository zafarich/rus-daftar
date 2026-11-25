# Rus daftari

Bitta konteynerda ikki narsa ishlaydi:

- **Telegram bot.** Kuniga bir necha marta o'zi savol yuboradi (so'z, predlog, qo'shimcha, to'liq gap, tanlash), javobingizni tekshiradi va keyingisiga o'tadi. Ertalab 7 kunlik rejaning bugungi vazifasini yuboradi, kechqurun «Bajarildimi?» deb so'raydi.
- **Veb-daftar** (`https://DOMAIN`) — Telegram Mini App sifatida bot ichida ochiladi, brauzerda ham ishlaydi. Bo'limlar: qoida (jadval, misol va chizmalar bilan), flip kartochkalar, mashq (bo'sh joy / qo'shimcha / tanlash / xatoni topish / tarjima / to'liq gap, alohida «Xatolarim» ro'yxati), tezkor «ramka» mashqi va 7 kunlik reja. Ruscha javobni ovoz chiqarib o'qiydi, Chrome'da javobni ovoz bilan aytish ham mumkin.

Xato qilingan savol 10 daqiqadan keyin, to'g'ri topilgani esa 1, 2, 4, 8… kundan keyin qaytadi (Leitner takrorlash usuli).

**Ko'p foydalanuvchi.** Botdan istalgan Telegram foydalanuvchisi foydalana oladi. Mavzu va savollar umumiy, natijalar, 7 kunlik reja va faol mavzu esa har kimniki alohida. Bot va daftar bitta bazadan foydalanadi, shuning uchun bir odamning botdagi va daftardagi natijalari umumiy.

**Kirish.** Telegram ichida daftar foydalanuvchini Telegram imzosi (`initData`) orqali taniydi, parol kerak emas. Telegram'dan tashqarida ochish uchun bot har kimga shaxsiy «Brauzerda ochish» havolasini beradi (Android'dagi Telegram ichida ovoz funksiyalari ishlamasligi mumkin, brauzerda ishlaydi).

**Mini App sozlamasi.** @BotFather → botingiz → *Bot Settings → Configure Mini App* (va xohlasangiz *Menu Button*) ga `https://DOMAIN/` yozing.

## O'rnatish

```bash
# 1. Eski loyihani to'xtating (bitta token bilan ikki bot ishlay olmaydi)
# 2. Kodni serverga ko'chiring
cp .env.example .env
nano .env          # BOT_TOKEN va DOMAIN eski loyihadan; ACCESS_KEY: openssl rand -hex 24
docker compose up -d --build
```

Birinchi mavzuni yuklang (`--activate` — yangi foydalanuvchilarga shu mavzu beriladi):

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

1. Claude bilan chatda mavzuni tayyorlaysiz. U sizga `topics/0N_....json` fayl beradi.
2. Faylni repo'dagi `topics/` papkasiga qo'yib push qilasiz, serverda `git pull`.
3. Tekshirib, yuklaysiz:

```bash
.venv/bin/python manage.py check  topics/02_vinitelny.json
.venv/bin/python manage.py import topics/02_vinitelny.json
```

Mavzu darhol hammaning bosh sahifasida paydo bo'ladi; har kim uni o'zi tanlaydi (daftarda «Reja» → «Bugundan boshlash» yoki botda `/topic vinitelny`). Bot faqat foydalanuvchining faol mavzusidan yangi savol beradi, eski mavzulardan esa takrorlash vaqti kelganlarini aralashtirib turadi.

`--activate` qo'shilsa, mavzu **yangi** foydalanuvchilar uchun boshlang'ich bo'ladi. Odatda birinchi mavzu boshlang'ich bo'lib qolgani ma'qul.

Mavjud mavzuni tuzatib qayta import qilsangiz, o'zgarmagan savollar bo'yicha hammaning natijasi saqlanib qoladi.

Boshqa buyruqlar:

```bash
docker compose exec daftar python manage.py list                  # * — standart mavzu
docker compose exec daftar python manage.py activate predlozhny   # standart mavzuni almashtirish
docker compose exec daftar python manage.py users                 # foydalanuvchilar soni
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
| `/daftar` | Daftarni Telegram ichida yoki brauzerda ochish |
| `/off`, `/on` | Avtomatik savol va eslatmalarni o'chirish / yoqish |
| `/myid` | Telegram ID |

Botni bloklagan foydalanuvchiga avtomatik xabar yuborish o'zi to'xtaydi, u yana yozsa qayta yoqiladi.

Vaqtlar `.env` da sozlanadi: `QUIZ_TIMES`, `PLAN_MORNING`, `PLAN_EVENING`.

## AI tekshiruv (ixtiyoriy)

`.env` ga `ANTHROPIC_API_KEY` yozsangiz, tarjima va to'liq gaplarda javob namunaga aynan mos kelmaganda Claude tekshiradi va o'zbekcha izoh beradi. Kalit bo'lmasa, faqat namunalar bilan solishtiriladi. Predlog va qo'shimcha savollari doim aniq tekshiriladi.

## Mavzu fayli formati

```json
{
  "slug": "predlozhny", "title": "…", "sort": 1,
  "summary": ["## Sarlavha", "Matn, **qalin**", "> Я дома. — Men uydaman."],
  "cards":     [{"front": "Toshkentda", "back": "в Ташкенте", "note": "…"}],
  "exercises": [
    {"type": "gap",       "prompt": "Я живу ___ Ташкенте.", "answers": ["в"]},
    {"type": "ending",    "prompt": "Я работаю в банк___.", "answers": ["е"]},
    {"type": "choice",    "prompt": "Он ___ концерте.", "options": ["в","на"], "answers": ["на"]},
    {"type": "fix",       "prompt": "Мама в работе.", "answers": ["Мама на работе"], "note": "…"},
    {"type": "translate", "prompt": "Men uydaman.", "answers": ["Я дома"]},
    {"type": "sentence",  "prompt": "Kecha to'yda edim.", "answers": ["Вчера я был на свадьбе"]}
  ],
  "drills": [{"title": "Я сейчас в …", "frame": "Я сейчас в ___.",
              "items": [{"uz": "do'kon", "base": "магазин", "answer": "магазине"}]}],
  "plan": [{"day": 1, "tasks": ["…", "…"]}]
}
```

`gap`, `ending` va `drill` savollarida `___` bo'lishi shart. Javob tekshiruvi katta-kichik harf, `ё/е` va tinish belgilariga qaramaydi. `ending` uchun «е», «банке» va «в банке» javoblarining hammasi qabul qilinadi.

`fix` — «Xatoni topish»: `prompt` ataylab xato yozilgan gap, `answers` uning to'g'ri shakli. To'g'ri gap ham, faqat tuzatilgan so'z ham («на», «на работе») qabul qilinadi. `note` ga xato sababini yozing, u noto'g'ri javobdan keyin ko'rsatiladi.

### Qoida matni (`summary`)

`summary` — satrlar ro'yxati (yoki `\n` bilan ajratilgan bitta matn). Har bir satr boshidagi belgi uning turini bildiradi:

| Satr boshi | Nima chiqadi |
|---|---|
| `## Sarlavha` | bo'lim sarlavhasi |
| oddiy matn | xatboshi; `**qalin**` ajratib ko'rsatiladi (o'zgargan qo'shimchani shunday belgilang) |
| `- matn` | ro'yxat |
| `! matn` | tuzoq yoki muhim eslatma (qizil chiziqli blok) |
| `> ruscha gap — o'zbekcha tarjima` | misol; bosilsa ruschasini ovoz chiqarib o'qiydi. Ajratkich: ` — ` |
| `\| a \| b \| c \|` | jadval satri, birinchi satr sarlavha bo'ladi |
| `@ belgi \| savol \| ruscha \| o'zbekcha` | chizma katagi; ketma-ket kelgan kataklar bitta qatorga teriladi |
| `= bo'lak :: izoh \| bo'lak :: izoh` | gap qolipi: har bir bo'lak alohida katakda, ostida izohi |

Chizma belgilari (quti — joy, nuqta — siz, odam shakli — boshqa kishi, strelka — harakat):

| Belgi | Ma'nosi | Misol |
|---|---|---|
| `in` | ichida | в школе |
| `to` | ichiga | в школу |
| `from` | ichidan | из школы |
| `on` | ustida, ochiq joyda | на работе |
| `onto` | ustiga | на работу |
| `off` | ustidan | с работы |
| `at` | yonida | у окна |
| `away` | odamdan | от брата |
| `none` | yo'q | нет воды |
| `toward` | odamning oldiga | к брату |
| `beside` | odamning huzurida | у брата |
| `give` | kimgadir beriladi | дать брату |
| `along` | bo'ylab | по улице |
| `with` | birga, bilan | с братом |
| `above` | tepasida | над столом |
| `under` | ostida | под столом |
| `front` | oldida | перед домом |
| `behind` | orqasida | за домом |
| `between` | orasida | между домом и парком |
| `one` | bitta | один стол |
| `few` | ikki, uch, to'rt | два стола |
| `many` | ko'p | пять столов |

Bo'sh satr blokni yopadi (masalan, ikki jadval orasida). Tayyor namuna: `topics/06_koplik.json`.

## Zaxira nusxa

Butun baza bitta fayl: `data/daftar.db`. Uni vaqti-vaqti bilan nusxalab qo'ying.
