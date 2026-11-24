"""Довідник авто з AUTO.RIA (developers.ria.com): марки, моделі, покоління, модифікації.

Безкоштовний ключ: 1000 запитів на місяць і 30 на годину. Тому довідник збирається поступово:
кожен запуск (щогодини) робить не більше RUN_MAX запитів, за місяць — не більше MONTH_MAX.
Спочатку популярні в Україні марки, потім решта.

Правила зберігання:
- нічого не видаляємо: якщо запис зник з AUTO.RIA, він лишається, просто не оновлюється поле "seen";
- нове додаємо (поле "first" — коли вперше побачили);
- щотижня перевіряємо марки й моделі популярних марок, щоб підхопити новинки.

Пріоритет: авто, які є в користувачів (колекція riaReq у Firestore — лише назва моделі й вид транспорту),
завантажуються першими й поза місячним бюджетом наповнення.

Ключ — у секреті RIA_KEY. Внутрішня база — ria/db.json, стан черги — ria/state.json.
Для застосунку: ria/marks.json (список марок) і ria/m/<id марки>.json (моделі → покоління → модифікації).
Дані: AUTO.RIA (https://auto.ria.com) — посилання на джерело обов'язкове.
"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://developers.ria.com"
DIR = "ria"
DB = f"{DIR}/db.json"
STATE = f"{DIR}/state.json"
RUN_MAX = 24            # запитів за один запуск
HOUR_MAX = 27           # ліміт AUTO.RIA — 30 за будь-які 60 хвилин; рахуємо самі, з запасом
PRICE_DAYS = 7          # середню ціну авто користувача оновлюємо раз на тиждень
# пальне застосунку → типи пального AUTO.RIA (1 бензин, 2 дизель, 3 газ, 4 газ/бензин, 6 електро, 9 пропан-бутан)
FUEL_RIA = {"Дизель": [2], "А-95": [1], "А-92": [1], "А-100": [1], "Газ": [3, 4, 9], "Газ і бензин": [4, 9], "ev": [6]}
MONTH_MAX = 960         # з 1000 на місяць лишаємо запас
CRAWL_MAX = 720         # з них на початкове наповнення; решта — щотижневе оновлення
# Категорії AUTO.RIA: 1 легкові, 2 мото, 6 вантажні, 7 автобуси, 8 автодома, 4 спецтехніка, 5 причепи
CATS = [1, 6, 7, 2, 8, 5, 4]
# Популярні в Україні марки (назви як на AUTO.RIA) — їх збираємо першими й оновлюємо щотижня
TOP = ["Volkswagen", "Toyota", "Skoda", "Renault", "Ford", "BMW", "Audi", "Mercedes-Benz", "Hyundai",
       "Kia", "Nissan", "Chevrolet", "Opel", "Mitsubishi", "Honda", "Mazda", "Peugeot", "Citroen",
       "Daewoo", "ВАЗ / Lada", "ЗАЗ", "Tesla", "Lexus", "Subaru", "Volvo", "Fiat", "Dacia", "Suzuki",
       "Land Rover", "Jeep", "Infiniti", "Chery", "Geely", "BYD", "Seat", "Porsche", "MG", "Iveco",
       "MAN", "DAF", "Scania"]


def now_iso():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def save(path, obj, pretty=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1 if pretty else None, separators=None if pretty else (",", ":"))
    os.replace(tmp, path)


VT_CAT = {"car": [1], "ev": [1], "van": [1, 6, 7], "truck": [6, 1], "tractor": [6], "bus": [7, 6], "moto": [2]}


def norm(x):
    x = (x or "").lower().replace("ё", "е")
    return "".join(ch if ch.isalnum() else " " for ch in x).split()


def fs_requests():
    """Авто, які є в користувачів: колекція riaReq у Firestore (лише назва моделі й вид транспорту)."""
    sa = os.environ.get("FCM_SA", "").strip()
    if not sa:
        return []
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        info = json.loads(sa)
        cred = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/datastore"])
        s = AuthorizedSession(cred)
        url = f"https://firestore.googleapis.com/v1/projects/{info['project_id']}/databases/(default)/documents/riaReq"
        out, tok = [], ""
        for _ in range(20):
            r = s.get(url, params={"pageSize": 300, **({"pageToken": tok} if tok else {})}, timeout=30)
            if r.status_code != 200:
                print("riaReq:", r.status_code, r.text[:200], file=sys.stderr)
                break
            d = r.json()
            for doc in d.get("documents", []):
                f = doc.get("fields", {})
                n = f.get("n", {}).get("stringValue", "")
                if n:
                    out.append({"id": doc["name"].rsplit("/", 1)[-1], "n": n[:60], "vt": f.get("vt", {}).get("stringValue", "car"),
                                "y": f.get("y", {}).get("stringValue", ""), "f": f.get("f", {}).get("stringValue", "")})
            tok = d.get("nextPageToken", "")
            if not tok:
                break
        return out
    except Exception as e:
        print("riaReq помилка:", e, file=sys.stderr)
        return []


def resolve(db, name, vt):
    """«Ford Fusion» → (марка, модель) за довідником. Повертає (mark, model, cats)."""
    w = norm(name)
    cats = VT_CAT.get(vt, [1])
    best = None
    for m in db["marks"].values():
        if m.get("cat") not in cats:
            continue
        for alt in m["n"].split(" / "):
            a = norm(alt)
            if a and w[:len(a)] == a and (best is None or len(a) > best[1] or (len(a) == best[1] and cats.index(m["cat"]) < cats.index(best[0]["cat"]))):
                best = (m, len(a))
    if not best:
        return None, None, cats
    mark, k = best
    rest = w[k:]
    mdl = None
    for mid, md in db["models"].items():
        if md.get("mk") != mark["id"] or md.get("cat") != mark["cat"]:
            continue
        a = norm(md["n"])
        if a and rest[:len(a)] == a and (mdl is None or len(a) > mdl[1]):
            mdl = (mid, len(a))
    return mark, (mdl[0] if mdl else None), cats


class Limit(Exception):
    pass


def get(path, key):
    url = f"{API}{path}{'&' if '?' in path else '?'}api_key={key}"
    req = urllib.request.Request(url, headers={"User-Agent": "oblik-avto-catalog/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise Limit("429")
        if e.code in (400, 404):
            return None  # немає даних — завдання вважаємо виконаним
        raise


def upsert(table, key, data, today):
    """Додати або оновити запис, нічого не видаляючи."""
    key = str(key)
    cur = table.get(key)
    if cur is None:
        cur = dict(data, first=today, seen=today)
        table[key] = cur
        return True
    cur.update({k: v for k, v in data.items() if v not in (None, "")})
    cur["seen"] = today
    return False


def main():
    key = os.environ.get("RIA_KEY", "").strip()
    if not key:
        print("RIA_KEY не задано — довідник пропущено")
        return
    today = now_iso()
    month = today[:7]
    db = load(DB, {"marks": {}, "models": {}, "gens": {}, "mods": {}})
    st = load(STATE, {})
    if st.get("month") != month:
        st["month"], st["used"] = month, 0
    st.setdefault("queue", [])
    st.setdefault("done", [])        # виконані завдання початкового наповнення (щоб не повторювати)
    st.setdefault("weekly", "")
    q, done = st["queue"], set(st["done"])

    def push(task, front=False):
        t = list(task)
        if t in q:
            return
        if front:
            q.insert(0, t)
        else:
            q.append(t)

    # початкова черга
    if not q and not done:
        for c in CATS:
            push(["marks", c])
        st["weekly"] = "%d-%02d" % datetime.date.today().isocalendar()[:2]  # перший тиждень — і так усе свіже

    # щотижня (понеділок): марки всіх категорій + моделі популярних марок — на початок черги
    wk = datetime.date.today().isocalendar()
    wk = f"{wk[0]}-{wk[1]:02d}"
    weekly_due = st["weekly"] != wk
    if weekly_due:
        refresh = [["marks", c, "w"] for c in CATS[:3]]
        top_ids = {m["n"]: m["id"] for m in db["marks"].values() if m.get("cat") == 1}
        refresh += [["models", 1, int(top_ids[n]), "w"] for n in TOP if n in top_ids]
        for t in reversed(refresh):
            push(t, front=True)
        st["weekly"] = wk

    # авто користувачів — першими в черзі
    st.setdefault("req", {})
    reqs = fs_requests()
    for rq in reqs:
        r = st["req"].setdefault(rq["id"], {"n": rq["n"], "vt": rq["vt"], "ok": False})
        r["n"], r["vt"], r["y"], r["f"] = rq["n"], rq["vt"], rq.get("y", ""), rq.get("f", "")
    pri = []
    for rid, r in st["req"].items():
        if r.get("ok"):
            pd = st.setdefault("price", {}).get(rid, "")
            if r.get("y") and r.get("model") and (not pd or (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(pd)).days >= PRICE_DAYS):
                pri.append(["price", rid, "r"])
            continue
        mark, mid, _ = resolve(db, r["n"], r["vt"])
        if not mark:
            r["why"] = "марки немає в довіднику"
            continue
        mk_models = [x for x in db["models"].values() if x.get("mk") == mark["id"] and x.get("cat") == mark["cat"]]
        if not mk_models:
            pri.append(["models", mark["cat"], mark["id"], "r"]); r["why"] = "чекаємо моделі марки"
            continue
        if not mid:
            r["why"] = "моделі не знайдено"
            continue
        r["model"], r["mark"], r["cat"] = int(mid), mark["id"], mark["cat"]
        pd = st.setdefault("price", {}).get(rid, "")
        if r.get("y") and (not pd or (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(pd)).days >= PRICE_DAYS):
            pri.append(["price", rid, "r"])
        if json.dumps(["gens", int(mid)]) not in done:
            pri.append(["gens", int(mid), "r"]); r["why"] = "чекаємо покоління"
            continue
        gens = [gid for gid, g in db["gens"].items() if str(g.get("md")) == str(mid)]
        need = [["mods", int(g), "r"] for g in gens if json.dumps(["mods", int(g)]) not in done]
        if need:
            pri += need; r["why"] = "чекаємо модифікації"
            continue
        r["ok"], r["why"] = True, ""
    for t in reversed(pri):
        base = t[:-1]
        if base in q:
            q.remove(base)
        push(t, front=True)

    n_run, added, errs = 0, 0, []
    nowts = time.time()
    st["hour"] = [x for x in st.get("hour", []) if nowts - x < 3600]
    run_max = max(0, min(RUN_MAX, HOUR_MAX - len(st["hour"])))
    prices = load(f"{DIR}/prices.json", {"src": "AUTO.RIA", "url": "https://auto.ria.com", "cars": {}})
    while q and n_run < run_max and st["used"] < MONTH_MAX:
        t = q[0]
        cur = t
        weekly = t[-1] == "w"
        prio = t[-1] == "r"
        if prio:
            t = t[:-1]
        if not weekly and t[0] != "price" and json.dumps(t) in done:
            q.remove(cur)  # вже зроблено (наприклад, раніше як пріоритетне)
            if not q and st.get("later"):
                q.extend(st.pop("later"))
            continue
        if not weekly and not prio and st["used"] >= CRAWL_MAX:
            break  # бюджет наповнення на місяць вичерпано — лишаємо запас на щотижневе оновлення
        kind = t[0]
        try:
            if kind == "marks":
                cat = t[1]
                res = get(f"/auto/categories/{cat}/marks", key) or []
                for m in res:
                    if upsert(db["marks"], f"{cat}:{m['value']}", {"id": m["value"], "n": m["name"], "cat": cat}, today):
                        added += 1
                    tk = ["models", cat, m["value"]]
                    if json.dumps(tk) not in done:
                        # популярні легкові — вперед, решта — після них
                        if cat == 1 and m["name"] in TOP:
                            push(tk, front=False)
                        else:
                            st.setdefault("later", [])
                            if tk not in st["later"]:
                                st["later"].append(tk)
            elif kind == "models":
                cat, mark = t[1], t[2]
                res = get(f"/auto/categories/{cat}/marks/{mark}/models", key) or []
                for m in res:
                    if upsert(db["models"], m["value"], {"n": m["name"], "mk": mark, "cat": cat}, today):
                        added += 1
                    tk = ["gens", m["value"]]
                    if json.dumps(tk) not in done and ["gens", m["value"], "r"] not in q:
                        push(tk)
            elif kind == "gens":
                model = t[1]
                res = get(f"/generations/by/models/{model}/generations", key) or []
                for blk in res if isinstance(res, list) else []:
                    for g in blk.get("generations", []):
                        gid = g.get("generationId")
                        if gid is None:
                            continue
                        if upsert(db["gens"], gid, {"n": g.get("name", ""), "md": model,
                                                    "y1": g.get("yearFrom"), "y2": g.get("yearTo"),
                                                    "eng": g.get("eng", "")}, today):
                            added += 1
                        tk = ["mods", gid]
                        if json.dumps(tk) not in done:
                            if prio:
                                push(["mods", gid, "r"], front=True)  # покоління авто користувача — одразу й модифікації
                            else:
                                push(tk)
            elif kind == "price":
                rid = t[1]
                r = st["req"].get(rid, {})
                if r.get("model") and r.get("y"):
                    y = int(r["y"])
                    res, span = None, 0
                    for span in (0, 1):  # спершу точний рік; якщо оголошень мало — ±1 рік
                        qs = f"/auto/average_price?main_category={r.get('cat', 1)}&marka_id={r['mark']}&model_id={r['model']}&yers={y - span}&yers={y + span}"
                        fu = FUEL_RIA.get("ev" if r.get("vt") == "ev" else r.get("f", ""), [])
                        qs += "".join(f"&fuel_id={x}" for x in fu)
                        res = get(qs, key)
                        st["used"] += 1; n_run += 1; st["hour"].append(time.time())
                        if res and res.get("total", 0) >= 3:
                            break
                        if n_run >= run_max:
                            break
                    st["price"][rid] = today
                    pc_ = prices["cars"].setdefault(rid, {"n": r["n"], "y": y, "hist": []})
                    if res and res.get("total"):
                        pc = res.get("percentiles", {}) or {}
                        val = lambda k: round(pc.get(k) or pc.get(str(float(k))) or 0)
                        pc_.update({"n": r["n"], "y": y, "upd": today, "total": res.get("total"),
                                    "mean": round(res.get("arithmeticMean") or 0), "iq": round(res.get("interQuartileMean") or 0),
                                    "p25": val("25.0"), "p50": val("50.0"), "p75": val("75.0"), "years": [y - span, y + span], "none": False})
                        if not pc_["hist"] or pc_["hist"][-1][0] != today:
                            pc_["hist"].append([today, pc_["p50"] or pc_["iq"]])  # історія — щоб показати, як дешевшає авто
                    else:
                        pc_["upd"], pc_["none"] = today, True
                    n_run -= 1; st["used"] -= 1; st["hour"].pop()  # нижче лічильники додадуться ще раз
            elif kind == "mods":
                gen = t[1]
                res = get(f"/modifications/by/generation/{gen}/modifications", key) or []
                for m in res if isinstance(res, list) else []:
                    if upsert(db["mods"], m["value"], {"n": m["name"], "g": gen}, today):
                        added += 1
        except Limit:
            errs.append("429: ліміт AUTO.RIA — продовжимо пізніше")
            break
        except Exception as e:  # мережа тощо — спробуємо наступного разу
            errs.append(f"{t}: {e}")
            if cur in q:
                q.remove(cur)
            q.append(cur)
            n_run += 1
            st["used"] += 1
            st["hour"].append(time.time())
            continue
        if cur in q:
            q.remove(cur)
        if not weekly and kind != "price":
            done.add(json.dumps(t))
            if t in q:
                q.remove(t)
        n_run += 1
        st["used"] += 1
        st["hour"].append(time.time())
        if not q and st.get("later"):
            q.extend(st.pop("later"))
        time.sleep(1.2)

    st["done"] = sorted(done)
    st["left"] = len(q) + len(st.get("later", []))
    save(DB, db)
    save(STATE, st, pretty=True)
    save(f"{DIR}/prices.json", dict(prices, updated=today), pretty=True)
    export(db, today)
    msg = (f"{datetime.datetime.utcnow():%Y-%m-%d %H:%M} UTC · запитів {n_run}, за місяць {st['used']}, "
           f"нових записів {added}, у черзі {st['left']}; марок {len(db['marks'])}, моделей {len(db['models'])}, "
           f"поколінь {len(db['gens'])}, модифікацій {len(db['mods'])}; авто користувачів {len(st['req'])}, "
           f"готово {sum(1 for r in st['req'].values() if r.get('ok'))}")
    print(msg)
    open(f"{DIR}/log.txt", "w", encoding="utf-8").write(msg + "\n" + "\n".join(errs[:5]) + "\n")


def export(db, today):
    """Файли для застосунку: список марок і по файлу на марку."""
    marks = [{"id": int(v["id"]), "n": v["n"], "cat": v.get("cat", 1)} for v in db["marks"].values()]
    marks.sort(key=lambda m: (m["cat"], m["n"].lower()))
    by_model = {}
    for gid, g in db["gens"].items():
        by_model.setdefault(str(g["md"]), []).append((gid, g))
    by_gen = {}
    for mid, m in db["mods"].items():
        by_gen.setdefault(str(m["g"]), []).append({"id": int(mid), "n": m["n"]})
    by_mark = {}
    for mid, m in db["models"].items():
        gens = []
        for gid, g in sorted(by_model.get(mid, []), key=lambda x: (x[1].get("y1") or 0)):
            gens.append({"id": int(gid), "n": g["n"], "y1": g.get("y1"), "y2": g.get("y2"),
                         "mods": sorted(by_gen.get(gid, []), key=lambda x: x["n"])})
        by_mark.setdefault(str(m["mk"]), []).append({"id": int(mid), "n": m["n"], "cat": m.get("cat", 1), "gens": gens})
    for mk, models in by_mark.items():
        models.sort(key=lambda x: x["n"].lower())
        save(f"{DIR}/m/{mk}.json", {"src": "AUTO.RIA", "url": "https://auto.ria.com", "updated": today,
                                    "mark": next((v["n"] for v in db["marks"].values() if str(v["id"]) == mk), ""),
                                    "models": models})
    for m in marks:
        m["has"] = str(m["id"]) in by_mark
    save(f"{DIR}/marks.json", {"src": "AUTO.RIA", "url": "https://auto.ria.com", "updated": today, "marks": marks})


if __name__ == "__main__":
    main()
