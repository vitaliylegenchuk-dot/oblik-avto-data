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
PER_DAY = 6        # не більше новин на один день
PER_SRC_DAY = 3    # і не більше трьох з одного джерела
UA = "Mozilla/5.0 (compatible; oblik-avto-news/1.0; +https://github.com/vitaliylegenchuk-dot/oblik-avto-data)"

# auto=True: уся стрічка про авто; інакше беремо лише новини з «автомобільними» словами в заголовку.
# path: посилання мусить містити цей шматок (рубрика «Авто» в загальній стрічці).
# w: вага джерела (практичні українські новини вище за світові прем'єри).
SOURCES = [
    {"s": "ГСЦ МВС", "u": "https://hsc.gov.ua/feed/", "w": 3, "auto": True},
    {"s": "NV Авто", "u": "https://nv.ua/ukr/rss/154.xml", "w": 1, "path": "/auto/"},
    {"s": "AUTO.RIA", "u": "https://auto.ria.com/uk/news/rss/", "w": 2, "auto": True},
    {"s": "Укравтопром", "u": "https://ukrautoprom.com.ua/feed/", "w": 2, "auto": True},
    {"s": "Autogeek", "u": "https://autogeek.com.ua/feed/", "w": 1, "auto": True},
    {"s": "Автоцентр", "u": "https://www.autocentre.ua/feed", "w": 1, "auto": True},
    {"s": "ТСН", "u": "https://tsn.ua/rss/full.rss", "w": 1, "path": "/auto/"},
    {"s": "УНІАН", "u": "https://rss.unian.net/site/news_ukr.rss", "w": 1},
    {"s": "Укрінформ", "u": "https://www.ukrinform.ua/rss/block-lastnews", "w": 1},
    {"s": "Економічна правда", "u": "https://www.epravda.com.ua/rss/", "w": 1},
]

AUTO_RX = re.compile(
    r"водій|водії|водіям|водіїв|автомоб|\bавто\b|автівк|\bмашин|штраф\w*.{0,30}(?:водії|водіїв|водіям|швидк|паркув|пдр|авто|вихлоп|прямот)|\bпдр\b|пальн|бензин|"
    r"дизел|автогаз|\bазс\b|\bшин[иаму]?\b|техогляд|осцпв|автоцивілк|розмитн|номерн\w* знак|посвідчен\w* водія|"
    r"паркув|евакуатор|укравтодор|патрульн|електромоб|електрокар|зарядн\w* станц|автосервіс|\bсто\b", re.I)
NEG_RX = re.compile(
    r"загин|загибл|смерт|помер|вбив|постраждал|травмув|\bдтп\b|аварі|п.ян|нетверез|викра|угна|шахед|обстріл|ракет|"
    r"дрон|фронт|\bзсу\b|\bтцк\b|мобіліз|окупант|росі|\bрф\b|гороскоп|розіграш|реклам|партнерськ|\bпромо", re.I)
SKIP_RX = re.compile(  # службове з сайтів відомств: нікому з водіїв не цікаво
    r"нарад|зустріч|вітаємо|привітан|вакансі|тендер|закупівл|день народження|подяк|відзнак|нагород|меморандум", re.I)
RU_RX = re.compile(r"[ыэъё]", re.I)
RU_WORDS = re.compile(r"\b(?:как|что|это|или|если|будет|нужно|можно|только|теперь|сколько|почему|когда)\b", re.I)
UA_RX = re.compile(r"україн|українц|водіїв|водіям|для водіїв|сервісн\w* центр|\bмвс\b|кабмін|верховн\w* рад|з 1 ", re.I)
PREMIERE_RX = re.compile(r"концепт|прем.єр|представил|показал|рендер|шпигун|тест-драйв|огляд|фото|відео", re.I)

KINDS = [  # вид новини: застосунок малює мітку й колір
    ("fuel", r"пальн|бензин|дизел|автогаз|\bазс\b|\bа-9[25]"),
    ("law", r"штраф|закон|законопро|постанов|кабмін|\bкму\b|верховн\w* рад|\bпдр\b|правил\w* дорожн|кодекс"),
    ("docs", r"посвідчен|права водія|реєстрац|перереєстр|техпаспорт|номерн\w* знак|сервісн\w* центр|\bгсц\b|\bдія\b|"
             r"кабінет\w* водія|іспит|медогляд|медичн\w* довідк|належн\w* користувач|техогляд|осцпв|автоцивілк"),
    ("ev", r"електромоб|електрокар|зарядн|гібрид"),
    ("market", r"продаж|ринок|ринку|ввез|імпорт|розмитн|вживан|нових авто|укравтопром|ціни на авто|реєстрацій"),
    ("road", r"дорог|трас[аиі]|укравтодор|міст[иу]? |об.їзд|перекрит"),
]


def kind_of(t):
    for k, rx in KINDS:
        if re.search(rx, t, re.I):
            return k
    return "auto"


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
    r = requests.get(src["u"], headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
                     timeout=25)
    r.raise_for_status()
    return r.content


def russian(s):
    return bool(RU_RX.search(s) or (not re.search(r"[іїєґ]", s, re.I) and RU_WORDS.search(s)))


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
        if russian(t) or NEG_RX.search(t) or SKIP_RX.search(t):
            continue
        if not src.get("auto") and not src.get("path") and not AUTO_RX.search(t):
            continue
        d = e["date"]
        if d > now + dt.timedelta(hours=2) or (now - d).days >= NEW_DAYS:
            continue
        x = clean(e["desc"])
        if rt and x.lower().startswith(rt.lower()[:40]):
            x = x[len(rt):].lstrip(" .:,")
        if russian(x):
            x = ""
        x = cut(nodash(x))
        k = kind_of(t + " " + x)
        score = src["w"] + (2 if UA_RX.search(t + " " + x) else 0) + (2 if k in ("law", "docs", "fuel") else 1 if k in ("market", "ev", "road") else 0)
        score -= 2 if PREMIERE_RX.search(t) else 0
        out.append({"id": nid(link), "d": d.strftime("%Y-%m-%d"), "tm": d.strftime("%H:%M"), "t": t, "x": x,
                    "s": src["s"], "u": link, "k": k, "_sc": score})
    return out


def merge(old, cands, today):
    items = [i for i in old if i.get("d", "") >= (today - dt.timedelta(days=KEEP_DAYS)).isoformat()]
    have = {i["id"] for i in items}
    added = []
    for c in sorted(cands, key=lambda c: (c["_sc"], c["d"] + c["tm"]), reverse=True):
        if c["id"] in have:
            continue
        cd = dt.date.fromisoformat(c["d"])
        near = [i for i in items if abs((dt.date.fromisoformat(i["d"]) - cd).days) <= 3]
        w = words(c["t"])
        if any(len(w & words(i["t"])) / max(1, len(w | words(i["t"]))) >= 0.5 for i in near):
            continue  # ця сама подія з іншого джерела
        day = [i for i in items if i["d"] == c["d"]]
        if len(day) >= PER_DAY or sum(1 for i in day if i["s"] == c["s"]) >= PER_SRC_DAY:
            continue
        c = {k: v for k, v in c.items() if not k.startswith("_")}
        items.append(c)
        have.add(c["id"])
        added.append(c)
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
