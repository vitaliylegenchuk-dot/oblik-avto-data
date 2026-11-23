"""Пуш-сповіщення про небезпеки на дорогах через Firebase (FCM).

Читає weather.json, і для кожної нової небезпеки (рівень 2+) надсилає повідомлення
у тему wx_<місто>. Вже надіслані ключі зберігаються в push_sent.json, щоб не повторюватись.
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
    for p in d["places"]:
        for z in p.get("haz", []):
            frm = (z.get("from") or "")[:10]
            if z.get("lvl", 0) < 2 or len(frm) < 10 or frm < today:
                continue
            key = f"{p['id']}:{z['k']}:{frm}"
            if key in seen:
                continue
            msg = {"message": {
                "topic": "wx_" + p["id"],
                "data": {"type": "wx", "place": p["id"], "n": p.get("n", ""), "k": z["k"],
                         "from": z.get("from", ""), "t": z.get("t", ""), "x": z.get("x", ""),
                         "when": z.get("when", "")},
                "android": {"priority": "high", "ttl": "21600s"},
            }}
            r = s.post(url, json=msg, timeout=20)
            if r.status_code == 200:
                seen.add(key); sent.append(key); n_ok += 1
            else:
                print("помилка", key, r.status_code, r.text[:300], file=sys.stderr)
    json.dump(sorted(set(sent)), open(SENT, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("надіслано пушів:", n_ok)


if __name__ == "__main__":
    main()

