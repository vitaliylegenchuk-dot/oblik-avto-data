"""Пуш-сповіщення про небезпеки на дорогах через Firebase (FCM).

Читає weather.json і для кожного міста надсилає ОДНЕ повідомлення про нові небезпеки (рівень 2+),
які почнуться протягом найближчих 12 годин. Тиха година 22:00–07:00 (Київ): нічого не надсилаємо,
усе накопичене піде першим ранковим запуском. Вже надіслані ключі — у push_sent.json.
Ключ сервісного акаунта Firebase береться з секрету FCM_SA (вміст JSON-файлу).
"""
import datetime
import json
import os
import sys
from zoneinfo import ZoneInfo

SENT = "push_sent.json"


def main():
    sa = os.environ.get("FCM_SA", "").strip()
    if not sa:
        print("FCM_SA не задано — пуші пропущено")
        return
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    info = json.loads(sa)
    cred = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    s = AuthorizedSession(cred)
    url = f"https://fcm.googleapis.com/v1/projects/{info['project_id']}/messages:send"

    today = datetime.datetime.now(ZoneInfo("Europe/Kyiv")).strftime("%Y-%m-%d")
    sent = []
    if os.path.exists(SENT):
        try:
            sent = json.load(open(SENT, encoding="utf-8"))
        except Exception:
            sent = []
    sent = [k for k in sent if k[-10:] >= today]
    seen = set(sent)

    d = json.load(open("weather.json", encoding="utf-8"))
    n_ok = 0
    errs = []
    now = datetime.datetime.now(ZoneInfo("Europe/Kyiv")).replace(tzinfo=None)
    quiet = now.hour >= 22 or now.hour < 7
    horizon = now + datetime.timedelta(hours=12)
    for p in ([] if quiet else d["places"]):
        fresh = []
        for z in p.get("haz", []):
            frm = z.get("from") or ""
            if z.get("lvl", 0) < 2 or len(frm) < 16 or frm[:10] < today:
                continue
            try:
                start = datetime.datetime.fromisoformat(frm[:16])
            except ValueError:
                continue
            if start > horizon:
                continue  # ще далеко — надішлемо пізніше, коли стане актуально
            key = f"{p['id']}:{z['k']}:{frm[:10]}"
            if key not in seen:
                fresh.append((z, key))
        if not fresh:
            continue
        fresh.sort(key=lambda zk: (-zk[0].get("lvl", 0), zk[0].get("from", "")))
        top = fresh[0][0]
        first = min((zk[0] for zk in fresh), key=lambda z: z.get("from", ""))
        names = [zk[0].get("t", "") for zk in fresh]
        title = ", ".join([names[0]] + [n[:1].lower() + n[1:] for n in names[1:]])
        keys = [zk[1] for zk in fresh]
        msg = {"message": {
            "topic": "wx_" + p["id"],
            "data": {"type": "wx", "place": p["id"], "n": p.get("n", ""),
                     "k": "+".join(zk[0]["k"] for zk in fresh), "keys": ",".join(keys),
                     "fr": first.get("from", ""), "t": title, "x": top.get("x", ""),
                     "when": first.get("when", "")},
            "android": {"priority": "high", "ttl": "21600s"},
        }}
        r = s.post(url, json=msg, timeout=20)
        if r.status_code == 200:
            for k in keys:
                seen.add(k); sent.append(k)
            n_ok += 1
        else:
            print("помилка", p["id"], r.status_code, r.text[:300], file=sys.stderr)
            errs.append(f"{p['id']} {r.status_code} {r.text[:400]}")
    if quiet:
        errs.append("тиха година — пуші відкладено до ранку")
    # Тестове повідомлення: push_test.json = {"place": "cherkasy"}
    if os.path.exists("push_test.json"):
        try:
            tp = json.load(open("push_test.json", encoding="utf-8")).get("place", "cherkasy")
            pn = next((p.get("n", "") for p in d["places"] if p["id"] == tp), tp)
            now = datetime.datetime.now(ZoneInfo("Europe/Kyiv"))
            r = s.post(url, json={"message": {"topic": "wx_" + tp, "android": {"priority": "high", "ttl": "3600s"},
                "data": {"type": "wx", "place": tp, "n": pn, "k": "test" + now.strftime("%H%M%S"),
                         "fr": now.strftime("%Y-%m-%dT%H:%M"), "t": "Тестове сповіщення",
                         "x": "Пуші працюють: попередження про погоду приходитимуть одразу.", "when": ""}}}, timeout=20)
            errs.append(f"test {tp} {r.status_code} {r.text[:200]}")
        except Exception as e:
            errs.append(f"test error {e}")
        os.remove("push_test.json")
    json.dump(sorted(set(sent)), open(SENT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("надіслано пушів:", n_ok)
    open("push_log.txt", "w", encoding="utf-8").write(
        datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC") + f" · надіслано {n_ok}, помилок {len(errs)}\n" + "\n".join(errs[:3]) + "\n")


if __name__ == "__main__":
    main()



