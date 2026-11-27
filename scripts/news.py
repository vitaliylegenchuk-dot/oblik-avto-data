"""Автоновини для застосунку «Облік авто»: бот збирає новини для водіїв з RSS-стрічок і пише news.json.

Застосунок показує їх у розділі «Поради» (блок «Сьогодні» і вікно «Новини») і світить кольорову крапку,
коли з'являються нові. З кожної новини беремо лише заголовок, короткий опис зі стрічки (до ~220 знаків)
і посилання на першоджерело. Добірку, яку додали вручну (поле "ed": 1), бот не змінює, доки вона не застаріє.

Запуск:
  python scripts/news.py                  зібрати стрічки й записати news.json
  python scripts/news.py --dry            лише показати, що знайшлося, без запису
  python scripts/news.py --file a.xml Х   перевірити розбір локального файлу стрічки (Х: назва джерела)
"""
import datetime as dt
import email.utils
import hashlib
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
OUT = Path(__file__).resolve().parent.parent / "news.json"
KEEP_DAYS = 45     # скільки днів тримати новини у файлі
MAX_ITEMS = 150
NEW_DAYS = 4       # нові новини беремо лише за останні дні: стрічки інколи віддають давнє
PER_DAY = 5        # не більше новин на один день
PER_SRC_DAY = 2    # з одного джерела
GLOBAL_PER_DAY = 1 # і лише одна «світова» новина на день: насамперед те, що стосується водіїв в Україні
PER_KIND_DAY = {"fuel": 1}  # про ціни на пальне пишуть щодня всі, а середні ціни застосунок і так показує
HDR = {"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
       "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
       "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.5"}

# auto=True: уся стрічка про авто; інакше беремо лише новини з «автомобільними» словами в заголовку.
# path: посилання мусить містити цей шматок (рубрика «Авто» в загальній стрічці).
# ua=True: усе зі стрічки стосується водіїв в Україні. w: вага джерела.
# Сайти ГСЦ МВС, «Автоцентру» й «Економічної правди» не віддають стрічку серверам GitHub (403),
# тож роз'яснення сервісних центрів беремо з новин медіа, які їх переказують.
SOURCES = [
    {"s": "NV Авто", "u": "https://nv.ua/ukr/rss/154.xml", "w": 1, "path": "/auto/"},
    {"s": "Укравтопром", "u": "https://ukrautoprom.com.ua/feed/", "w": 1, "auto": True},
    {"s": "Autogeek", "u": "https://autogeek.com.ua/feed/", "w": 1, "auto": True},
    {"s": "УНІАН", "u": "https://rss.unian.net/site/news_ukr.rss", "w": 1},
    {"s": "ТСН", "u": "https://tsn.ua/rss/full.rss", "w": 1},
    {"s": "Укрінформ", "u": "https://www.ukrinform.ua/rss/block-lastnews", "w": 1},
    {"s": "24 Канал", "u": "https://24tv.ua/rss/all.xml", "w": 1},
    {"s": "РБК-Україна", "u": "https://www.rbc.ua/static/rss/all.ukr.rss.xml", "w": 1},
    {"s": "Суспільне", "u": "https://suspilne.media/rss/all.rss", "w": 1},
    {"s": "Finance.ua", "u": "https://news.finance.ua/ua/rss", "w": 1},
]
SRC = {x["s"]: x for x in SOURCES}

# для загальних стрічок: у заголовку мусить бути щось саме про водіїв і авто
STRONG_RX = re.compile(
    r"водій|водії|водіям|водіїв|автомобіл|автівк|\bпдр\b|\bпальн(?:е|ого|ому|им)\b|бензин|дизел|автогаз|\bазс\b|"
    r"штраф\w*.{0,30}(?:водії|водіїв|водіям|швидк|паркув|пдр|авто|вихлоп|прямот)|техогляд|осцпв|автоцивілк|розмитн|"
    r"номерн\w* знак|посвідчен\w* водія|паркуван|евакуатор|укравтодор|електромоб|електрокар|зарядн\w* станц|автосервіс|"
    r"дорожн\w* знак|шиномонтаж|зимов\w* (?:шин|гум)|\bшини\b|"
    r"(?:продаж|ринок|ринку|ввез|ввіз|імпорт|ціни на|подорожч|подешевш)\w*.{0,25}\b(?:авто|машин)|"
    r"\b(?:авто|машин)\w*.{0,25}(?:ринок|ринку|продаж|ввез|розмитн)", re.I)
NEG_RX = re.compile(
    r"загин|загибл|смерт|помер|вбив|постраждал|травмув|\bдтп\b|аварі|п.ян|нетверез|викра|угна|шахед|обстріл|ракет|"
    r"дрон|фронт|\bзсу\b|\bтцк\b|мобіліз|окупант|росі|\bрф\b|гороскоп|розіграш|реклам|партнерськ|\bпромо|"
    r"врізав|зіткн|перекину|вибух|підпал|згорі|пожеж|затрима|підозр|вирок|хабар|корупц|контрабанд", re.I)
SKIP_RX = re.compile(  # службове з сайтів відомств: нікому з водіїв не цікаво
    r"нарад|зустріч|вітаємо|привітан|вакансі|тендер|закупівл|день народження|подяк|відзнак|нагород|меморандум", re.I)
RU_RX = re.compile(r"[ыэъё]", re.I)
MONTHS = r"(?:січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня)"
DAILY_RX = re.compile(r"\b\d{1,2} " + MONTHS, re.I)  # «Ціни на АЗС 26 листопада»: щоденні зведення цін
RU_WORDS = re.compile(r"\b(?:как|что|это|или|если|будет|нужно|можно|только|теперь|сколько|почему|когда)\b", re.I)
UA_RX = re.compile(r"україн|українц|водіїв|водіям|для водіїв|сервісн\w* центр|\bмвс\b|кабмін|верховн\w* рад|\bз 1 ", re.I)
PREMIERE_RX = re.compile(r"концепт|прем.єр|представил|показал|рендер|шпигун|тест-драйв|огляд|фото|відео", re.I)

KINDS = [  # вид новини: застосунок малює мітку й колір
    ("fuel", r"\bпальн(?:е|ого|ому|им|і)\b|бензин|дизел|автогаз|\bазс\b|\bа-9[25]"),
    ("law", r"штраф|закон|законопро|постанов|кабмін|\bкму\b|верховн\w* рад|\bпдр\b|правил\w* дорожн|кодекс"),
    ("docs", r"посвідчен|права водія|реєстрац|перереєстр|техпаспорт|номерн\w* знак|сервісн\w* центр|\bгсц\b|\bдія\b|"
             r"кабінет\w* водія|іспит|медогляд|медичн\w* довідк|належн\w* користувач|техогляд|осцпв|автоцивілк"),
    ("ev", r"електромоб|електрокар|зарядн|гібрид"),
    ("market", r"продаж|ринок|ринку|ввез|імпорт|розмитн|вживан|нових авто|укравтопром|ціни на авто|реєстрацій"),
    ("road", r"\bдорог(?:а|и|і|ах|ами)\b|дорожн|трас[аиі]\b|укравтодор|об.їзд|перекрит"),
]


def kind_of(t, x=""):
    """Вид за заголовком; якщо там нічого, за описом (лише пальне, електро й ринок)."""
    for k, rx in KINDS:
        if re.search(rx, t, re.I):
            return k
    for k, rx in KINDS:
        if k in ("fuel", "ev", "market") and re.search(rx, x, re.I):
            return k
    return "auto"


def russian(s):
    return bool(RU_RX.search(s) or (not re.search(r"[іїєґ]", s, re.I) and RU_WORDS.search(s)))


def nodash(s, title=False):
    """Без довгих тире: у заголовку перше « — » стає двокрапкою, решта комами."""
    if title:
        s = re.sub(r"\s+[—–]\s+", ": ", s, count=1)
    s = re.sub(r"\s+[—–]\s+", ", ", s)
    return s.replace("—", "-")


def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"The post .*? appeared first on .*$", "", s, flags=re.S)
    s = re.sub(r"\s*(?:\[…\]|\[\.\.\.\]|Читати далі.*|Читайте також.*|Детальніше.*)$", "", s, flags=re.S)
    return re.sub(r"\s+", " ", s).strip()


# У деяких стрічках підзаголовок і перший абзац злиплися без крапки: «…двигуна На контактах…»
STARTERS = (r"На|У|В|З|За|Із|Це|Як|Що|Для|Під|Після|Через|Також|Однак|Але|При|Від|До|Про|Серед|Іноді|Часто|Зазвичай|Тому|Тож|"
            r"Саме|Нині|Зараз|Раніше|Відтепер|Отже|Крім|Попри|Щоб|Якщо|Коли|Його|Її|Їх|Ці|Цей|Ця|Такі|Такий|Така|Утім|"
            r"Водночас|Натомість|Окрім|Згідно|Відповідно|Йдеться|Експерти|Фахівці|Водії|Власники")
JOIN_RX = re.compile(r"([а-яіїєґ'’]{3,}) (" + STARTERS + r")\b")


CAP_RX = re.compile(r"([а-яіїєґ'’]{3,}) ([А-ЯІЇЄҐ][а-яіїєґ'’]{2,})")


def unjoin(s):
    """Крапка перед службовим словом з великої літери і перед словом, яке вже траплялося в тексті раніше
    (опис повторює підзаголовок: «…після провітрювання Запах тютюну…»)."""
    s = JOIN_RX.sub(r"\1. \2", s)

    def f(m):
        w = m.group(2).lower()
        seen = re.search(r"(?<![а-яіїєґ'’])" + re.escape(w) + r"(?![а-яіїєґ'’])", s[:m.start()].lower())
        return m.group(1) + (". " if seen else " ") + m.group(2)
    return CAP_RX.sub(f, s)


def cut(s, n=220):
    if len(s) <= n:
        return s
    ends = [m.end() for m in re.finditer(r"[.!?…](?=\s|$)", s[:n + 1])]
    if ends and ends[-1] >= 60:
        return s[:ends[-1]].strip()
    return s[:n].rsplit(" ", 1)[0].rstrip(",;:—–- ") + "…"


def pdate(s):
    if not s:
        return None
    d = None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        try:
            d = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(KYIV)


def norm_link(u):
    u = re.sub(r"[?#].*$", "", u.strip())
    u = re.sub(r"^https?://(www\.)?", "", u)
    return u.rstrip("/").lower()


def nid(link):
    return "n" + hashlib.sha1(norm_link(link).encode()).hexdigest()[:10]


def words(t):
    return {w for w in re.findall(r"[a-zа-яіїєґ0-9']+", t.lower()) if len(w) > 3}


def parse(raw):
    """RSS 2.0 або Atom → [{title, link, desc, date}]."""
    A = "{http://www.w3.org/2005/Atom}"
    CE = "{http://purl.org/rss/1.0/modules/content/}encoded"
    DC = "{http://purl.org/dc/elements/1.1/}date"
    root = ET.fromstring(raw.lstrip(b"\xef\xbb\xbf \r\n\t"))
    items = root.findall(".//item") or root.findall(f".//{A}entry")
    out = []
    for it in items:
        def tx(*names):
            for n in names:
                e = it.find(n)
                if e is not None and (e.text or "").strip():
                    return e.text.strip()
            return ""
        link = tx("link", "guid")
        if not link.startswith("http"):
            link = ""
            for e in it.findall(f"{A}link"):
                if e.get("rel", "alternate") == "alternate" and e.get("href"):
                    link = e.get("href")
                    break
        out.append({"title": tx("title", f"{A}title"), "link": link,
                    "desc": tx("description", f"{A}summary", CE, f"{A}content"),
                    "date": pdate(tx("pubDate", DC, f"{A}published", f"{A}updated"))})
    return out


def fetch(src):
    import requests
    r = requests.get(src["u"], headers=HDR, timeout=25)
    r.raise_for_status()
    return r.content


def relevant(src, t):
    if russian(t) or NEG_RX.search(t) or SKIP_RX.search(t):
        return False
    return bool(src.get("auto") or src.get("path") or STRONG_RX.search(t))


def score_of(src, t, x, k):
    ua = bool(src.get("ua") or UA_RX.search(t + " " + x))
    sc = src["w"] + (2 if ua else 0) + (2 if k in ("law", "docs", "fuel") else 1 if k in ("market", "ev", "road") else 0)
    return sc - (2 if PREMIERE_RX.search(t) else 0), ua


def candidates(src, entries, now):
    """Відібрати зі стрічки свіжі новини для водіїв і привести до формату news.json."""
    out = []
    for e in entries:
        rt = clean(e["title"])
        t = nodash(rt, title=True)
        link = e["link"]
        if not t or not link.startswith("http") or e["date"] is None:
            continue
        if src.get("path") and src["path"] not in link:
            continue
        if not relevant(src, t):
            continue
        d = e["date"]
        if d > now + dt.timedelta(hours=2) or (now - d).days >= NEW_DAYS:
            continue
        x = clean(e["desc"])
        if rt and x.lower().startswith(rt.lower()[:40]):
            x = x[len(rt):].lstrip(" .:,")
        if russian(x):
            x = ""
        x = cut(unjoin(nodash(x)))
        k = kind_of(t, x)
        if k == "fuel" and DAILY_RX.search(t):
            continue  # щоденне зведення цін на АЗС: застосунок має власні ціни
        sc, ua = score_of(src, t, x, k)
        if not ua and PREMIERE_RX.search(t):
            continue  # світові прем'єри й фотоогляди: не для цього розділу
        out.append({"id": nid(link), "d": d.strftime("%Y-%m-%d"), "tm": d.strftime("%H:%M"), "t": t, "x": x,
                    "s": src["s"], "u": link, "k": k, "_sc": sc, "_ua": ua})
    return out


def merge(old, cands, today):
    """Добірку ("ed") лишаємо як є; решту відбираємо заново за тими самими правилами, що й нові."""
    lim = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    items = [dict(i, _ua=True) for i in old if i.get("ed") and i.get("d", "") >= lim]
    pool = []
    for i in old:
        if i.get("ed") or i.get("d", "") < lim:
            continue
        src = SRC.get(i["s"], {"s": i["s"], "w": 1})
        if not relevant(src, i["t"]):
            continue
        k = kind_of(i["t"], i.get("x", ""))
        if k == "fuel" and DAILY_RX.search(i["t"]):
            continue
        sc, ua = score_of(src, i["t"], i.get("x", ""), k)
        pool.append(dict(i, k=k, x=unjoin(i.get("x", "")), _sc=sc + 1, _ua=ua))  # +1: уже показана новина не поступається рівній новій
    seen = {i["id"] for i in items} | {i["id"] for i in pool}
    pool += [c for c in cands if c["id"] not in seen]
    old_ids = {i["id"] for i in old}
    added = []
    for c in sorted(pool, key=lambda c: (c["_sc"], c["d"] + c["tm"]), reverse=True):
        if any(i["id"] == c["id"] for i in items):
            continue
        cd = dt.date.fromisoformat(c["d"])
        w = words(c["t"])
        if any(abs((dt.date.fromisoformat(i["d"]) - cd).days) <= 3 and len(w & words(i["t"])) / max(1, len(w | words(i["t"]))) >= 0.5
               for i in items):
            continue  # ця сама подія з іншого джерела
        day = [i for i in items if i["d"] == c["d"]]
        if len(day) >= PER_DAY or sum(1 for i in day if i["s"] == c["s"]) >= PER_SRC_DAY:
            continue
        if not c["_ua"] and sum(1 for i in day if not i.get("_ua")) >= GLOBAL_PER_DAY:
            continue
        if c["k"] in PER_KIND_DAY and sum(1 for i in day if i.get("k") == c["k"]) >= PER_KIND_DAY[c["k"]]:
            continue
        items.append(c)
        if c["id"] not in old_ids:
            added.append(c)
    items = [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]
    items.sort(key=lambda i: (i["d"], i.get("tm", "")), reverse=True)
    return items[:MAX_ITEMS], added


def main():
    now = dt.datetime.now(KYIV)
    if "--file" in sys.argv:
        p = sys.argv[sys.argv.index("--file") + 1]
        name = sys.argv[sys.argv.index("--file") + 2] if len(sys.argv) > sys.argv.index("--file") + 2 else "Тест"
        src = next((s for s in SOURCES if s["s"] == name), {"s": name, "u": "", "w": 1, "auto": True})
        ents = parse(Path(p).read_bytes())
        print(f"{len(ents)} записів у стрічці")
        for c in candidates(src, ents, now):
            print(json.dumps(c, ensure_ascii=False))
        return
    try:
        data = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        data = {"items": []}
    old = data.get("items", [])
    cands, ok = [], []
    for src in SOURCES:
        try:
            ents = parse(fetch(src))
            cs = candidates(src, ents, now)
            cands += cs
            ok.append(src["s"])
            print(f"{src['s']:20} OK   записів {len(ents):3}, свіжих для водіїв {len(cs):2}", flush=True)
        except Exception as e:
            print(f"{src['s']:20} ПОМИЛКА {type(e).__name__}: {str(e)[:120]}", flush=True)
    items, added = merge(old, cands, now.date())
    gone = {i["id"] for i in old} - {i["id"] for i in items}
    if gone:
        print(f"Прибрано за новими правилами: {len(gone)}")
    for a in added:
        print(f"+ {a['d']} {a['tm']} [{a['k']}] {a['s']}: {a['t']}")
    print(f"Нових: {len(added)}, усього в news.json: {len(items)}")
    if "--dry" in sys.argv:
        return
    if items == old and OUT.exists():
        print("Без змін")
        return
    out = {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "note": "Автоновини для водіїв: заголовок, короткий опис і посилання на першоджерело. Оновлює scripts/news.py.",
           "items": items}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
