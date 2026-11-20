"""Погода й небезпеки на дорогах для застосунку «Облік авто».
Джерело: Open-Meteo (безкоштовно, без ключа). Запускається GitHub Actions кожні 3 години."""
import json, urllib.request, urllib.parse, datetime, sys
from zoneinfo import ZoneInfo

PLACES = [
 ("kyiv","Київ","Київська",50.45,30.52),("vinnytsia","Вінниця","Вінницька",49.23,28.47),
 ("lutsk","Луцьк","Волинська",50.75,25.33),("dnipro","Дніпро","Дніпропетровська",48.46,35.05),
 ("kryvyi-rih","Кривий Ріг","Дніпропетровська",47.91,33.39),("kramatorsk","Краматорськ","Донецька",48.72,37.56),
 ("zhytomyr","Житомир","Житомирська",50.25,28.66),("uzhhorod","Ужгород","Закарпатська",48.62,22.29),
 ("zaporizhzhia","Запоріжжя","Запорізька",47.84,35.14),("ivano-frankivsk","Івано-Франківськ","Івано-Франківська",48.92,24.71),
 ("kropyvnytskyi","Кропивницький","Кіровоградська",48.51,32.26),("lviv","Львів","Львівська",49.84,24.03),
 ("mykolaiv","Миколаїв","Миколаївська",46.98,32.00),("odesa","Одеса","Одеська",46.48,30.72),
 ("poltava","Полтава","Полтавська",49.59,34.55),("rivne","Рівне","Рівненська",50.62,26.25),
 ("sumy","Суми","Сумська",50.91,34.80),("ternopil","Тернопіль","Тернопільська",49.55,25.59),
 ("kharkiv","Харків","Харківська",49.99,36.23),("kherson","Херсон","Херсонська",46.64,32.61),
 ("khmelnytskyi","Хмельницький","Хмельницька",49.42,26.99),("cherkasy","Черкаси","Черкаська",49.44,32.06),
 ("chernivtsi","Чернівці","Чернівецька",48.29,25.94),("chernihiv","Чернігів","Чернігівська",51.49,31.29),
 ("bila-tserkva","Біла Церква","Київська",49.80,30.11),("uman","Умань","Черкаська",48.75,30.22),
 ("mukachevo","Мукачево","Закарпатська",48.44,22.72),
]
HOURLY = "temperature_2m,precipitation,rain,snowfall,weather_code,wind_gusts_10m,visibility"
DAILY = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,snowfall_sum,wind_gusts_10m_max"
CURRENT = "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,wind_gusts_10m,precipitation"

def fetch():
    q = {"latitude": ",".join(str(p[3]) for p in PLACES), "longitude": ",".join(str(p[4]) for p in PLACES),
         "current": CURRENT, "hourly": HOURLY, "daily": DAILY, "timezone": "Europe/Kyiv",
         "forecast_days": 3, "past_hours": 6, "wind_speed_unit": "ms"}
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=60) as r:
        d = json.load(r)
    return d if isinstance(d, list) else [d]

def part(h):
    return "вночі" if h < 6 else "вранці" if h < 11 else "вдень" if h < 17 else "увечері"

def when(ts, now):
    t = datetime.datetime.fromisoformat(ts)
    day = "сьогодні" if t.date() == now.date() else "завтра" if t.date() == now.date() + datetime.timedelta(days=1) else t.strftime("%d.%m")
    return f"{day} {part(t.hour)}, з {t.strftime('%H:%M')}" if t > now else "зараз"

FREEZE = {56, 57, 66, 67}
RAINY = set(range(51, 68)) | {80, 81, 82}
SNOWY = {71, 73, 75, 77, 85, 86}

def hazards(H, now):
    """H — список годин від 6 год тому до +24 год. Повертає небезпеки на найближчу добу."""
    out = {}
    def add(k, lvl, t, x, ts):
        if k not in out or lvl > out[k]["lvl"]:
            out[k] = {"k": k, "lvl": lvl, "t": t, "x": x, "from": ts, "when": when(ts, now)}
    fut = [h for h in H if h["ts"] >= now.replace(minute=0, second=0, microsecond=0)][:24]
    for i, h in enumerate(H):
        if h not in fut: continue
        t, p, c = h["t"], h["p"] or 0, h["c"]
        if c in FREEZE:
            add("ice", 3, "Крижаний дощ, ожеледь", "Дорога вкривається льодом. Якщо можна — не їдьте. Інакше: шипи або гарна зимова гума, дистанція втричі більша, гальмуйте плавно.", h["iso"])
        elif t is not None and -4 <= t <= 1:
            prev = H[max(0, i - 6):i]
            wet = p > 0.1 and (c in RAINY or c in FREEZE)
            refreeze = t <= 0 and any((x["p"] or 0) > 0.2 and (x["t"] or 0) > 0 for x in prev)
            if wet or refreeze:
                add("ice", 3 if t <= 0 else 2, "Ожеледиця", "Мокра дорога замерзає. Збільште дистанцію, не гальмуйте різко, обережно на мостах і в тіні.", h["iso"])
        if c in {45, 48} or (h["v"] is not None and h["v"] < 300):
            add("fog", 2, "Туман", "Видимість менше 300 м. Увімкніть ближнє світло й протитуманні фари, знизьте швидкість.", h["iso"])
        if c in {95, 96, 99}:
            add("storm", 2, "Гроза", "Можливі злива й град. Не ставте авто під деревами.", h["iso"])
        if p >= 7 and c not in SNOWY:
            add("rain", 2, "Злива", "Ризик аквапланування: знизьте швидкість, не заїжджайте в глибокі калюжі.", h["iso"])
        g = h["g"] or 0
        if g >= 15:
            add("wind", 3 if g >= 20 else 2, "Сильний вітер", f"Пориви до {round(g)} м/с. Тримайте кермо міцніше, не паркуйтеся під деревами й рекламними щитами.", h["iso"])
    snow = sum((h["s"] or 0) for h in fut)
    if snow >= 1:
        first = next(h for h in fut if (h["s"] or 0) > 0)
        add("snow", 3 if snow >= 5 else 2, "Снігопад", f"Очікується близько {round(snow)} см снігу. Сніг і сльота на дорозі: зимова гума, дистанція, щітка в багажнику.", first["iso"])
    temps = [h["t"] for h in fut if h["t"] is not None]
    if temps and min(temps) <= -15:
        h = min(fut, key=lambda x: x["t"] if x["t"] is not None else 99)
        add("frost", 2, "Сильний мороз", f"До {round(min(temps))}°. Акумулятор може не завести авто: тримайте його зарядженим, перевірте незамерзайку.", h["iso"])
    if temps and max(temps) >= 33:
        h = max(fut, key=lambda x: x["t"] if x["t"] is not None else -99)
        add("heat", 1, "Спека", f"До {round(max(temps))}°. Слідкуйте за температурою двигуна, не лишайте в авто дітей і тварин.", h["iso"])
    return sorted(out.values(), key=lambda x: (-x["lvl"], x["from"]))

def r1(v): return None if v is None else round(v, 1)

def main():
    data = fetch()
    now = datetime.datetime.now(ZoneInfo("Europe/Kyiv")).replace(tzinfo=None)
    places = []
    for p, d in zip(PLACES, data):
        hr = d["hourly"]; tz_now = datetime.datetime.fromisoformat(d["current"]["time"])
        H = [{"iso": hr["time"][i], "ts": datetime.datetime.fromisoformat(hr["time"][i]), "t": hr["temperature_2m"][i],
              "p": hr["precipitation"][i], "s": hr["snowfall"][i], "c": hr["weather_code"][i],
              "g": hr["wind_gusts_10m"][i], "v": hr["visibility"][i]} for i in range(len(hr["time"]))]
        cur = d["current"]; dl = d["daily"]
        days = [{"d": dl["time"][i], "c": dl["weather_code"][i], "tmin": r1(dl["temperature_2m_min"][i]), "tmax": r1(dl["temperature_2m_max"][i]),
                 "p": r1(dl["precipitation_sum"][i]), "s": r1(dl["snowfall_sum"][i]), "g": r1(dl["wind_gusts_10m_max"][i])} for i in range(len(dl["time"]))]
        start = tz_now.replace(minute=0)
        hours = [{"h": h["iso"][11:16], "d": h["iso"][:10], "t": r1(h["t"]), "c": h["c"], "p": r1(h["p"]), "g": r1(h["g"])}
                 for h in H if h["ts"] >= start][:24:2]
        places.append({"id": p[0], "n": p[1], "ob": p[2], "la": p[3], "lo": p[4],
            "now": {"t": r1(cur["temperature_2m"]), "f": r1(cur["apparent_temperature"]), "c": cur["weather_code"],
                    "w": r1(cur["wind_speed_10m"]), "g": r1(cur["wind_gusts_10m"]), "p": r1(cur["precipitation"]), "at": cur["time"]},
            "days": days, "hours": hours, "haz": hazards(H, tz_now)})
    out = {"updated": now.strftime("%Y-%m-%dT%H:%M"), "source": "Open-Meteo (open-meteo.com), прогнозні моделі ECMWF, DWD та інших метеослужб",
           "source_url": "https://open-meteo.com/", "places": places}
    json.dump(out, open("weather.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(out["updated"], sum(len(p["haz"]) for p in places), "небезпек")

if __name__ == "__main__":
    main()
