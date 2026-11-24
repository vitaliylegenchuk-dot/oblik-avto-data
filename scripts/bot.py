"""Диспетчер: замість ненадійного розкладу GitHub сам вчасно запускає завдання.

Працює ~50 хвилин, щохвилини перевіряє:
  • кнопку «Завантажити зараз» у застосунку (документи now_* у Firestore) → запускає ria-now.yml;
  • щогодини о :41 → довідник AUTO.RIA (ria.yml);
  • погоду: о :17 кожні 3 години і о 04:01, 05:01 UTC → weather.yml.
Наприкінці запускає сам себе знову (наступний запуск чекає в черзі, доки цей не завершиться).
Запуск, який уже відбувся (за розкладом GitHub чи вручну), повторно не дублює.
"""
import datetime as dt
import json
import os
import sys
import time

import requests

REPO = os.environ.get("GITHUB_REPOSITORY", "vitaliylegenchuk-dot/oblik-avto-data")
GH = {"Authorization": f"Bearer {os.environ.get('GH_TOKEN', '')}", "Accept": "application/vnd.github+json"}
API = f"https://api.github.com/repos/{REPO}/actions/workflows"
LIFE = 50 * 60


def utc():
    return dt.datetime.now(dt.timezone.utc)


def runs(wf, n=5):
    try:
        r = requests.get(f"{API}/{wf}/runs", headers=GH, params={"per_page": n}, timeout=30)
        return r.json().get("workflow_runs", []) if r.ok else []
    except Exception:
        return []


def busy(wf):
    return any(x["status"] in ("queued", "in_progress", "waiting", "pending", "requested") for x in runs(wf))


def last_start(wf):
    rs = runs(wf, 3)
    return dt.datetime.fromisoformat(rs[0]["created_at"].replace("Z", "+00:00")) if rs else None


def dispatch(wf, why):
    r = requests.post(f"{API}/{wf}/dispatches", headers=GH, json={"ref": "main"}, timeout=30)
    print(f"{utc():%H:%M} → {wf} ({why}): {r.status_code}", flush=True)


def due(now, slots):
    """Останній момент за розкладом (не пізніше за now). slots: [(години або None=щогодини, хвилина)]."""
    best = None
    for d in (0, 1):
        day = (now - dt.timedelta(days=d)).replace(second=0, microsecond=0)
        for hours, minute in slots:
            for h in (range(24) if hours is None else hours):
                t = day.replace(hour=h, minute=minute)
                if t <= now and (best is None or t > best):
                    best = t
    return best


RIA_SLOTS = [(None, 41)]
WX_SLOTS = [(range(0, 24, 3), 17), ([4, 5], 1)]


def check(wf, slots, now):
    t = due(now, slots)
    ls = last_start(wf)
    if t and (ls is None or ls < t) and now - t < dt.timedelta(minutes=50) and not busy(wf):
        dispatch(wf, f"за розкладом {t:%H:%M} UTC")


def fs():
    sa = os.environ.get("FCM_SA", "").strip()
    if not sa:
        return None, None
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    info = json.loads(sa)
    cred = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/datastore"])
    return AuthorizedSession(cred), f"https://firestore.googleapis.com/v1/projects/{info['project_id']}/databases/(default)/documents/riaReq"


def urgent(s, url):
    if not s:
        return False
    try:
        # лише документи now_* і не більше одного: Firestore рахує читання за кожен повернений документ
        # (мінімум 1 на запит), тож це ~5,8 тис. читань на добу з безкоштовних 50 тис.
        base = url.rsplit("/documents/", 1)[0] + "/documents"
        q = {"structuredQuery": {"from": [{"collectionId": "riaReq"}], "select": {"fields": [{"fieldPath": "__name__"}]},
             "where": {"compositeFilter": {"op": "AND", "filters": [
                 {"fieldFilter": {"field": {"fieldPath": "__name__"}, "op": "GREATER_THAN_OR_EQUAL", "value": {"referenceValue": url.split("/v1/", 1)[1] + "/now_"}}},
                 {"fieldFilter": {"field": {"fieldPath": "__name__"}, "op": "LESS_THAN", "value": {"referenceValue": url.split("/v1/", 1)[1] + "/now`"}}}]}},
             "limit": 1}}
        r = s.post(base + ":runQuery", json=q, timeout=30)
        return any(x.get("document") for x in r.json())
    except Exception as e:
        print("riaReq:", e, file=sys.stderr)
        return False


def main():
    start = time.time()
    s, url = fs()
    last_now = last_chk = 0
    while time.time() - start < LIFE:
        now = utc()
        # кнопку перевіряємо кожні 15 с (це лише читання Firestore), розклад — раз на хвилину
        if urgent(s, url) and time.time() - last_now > 120 and not busy("ria-now.yml"):
            dispatch("ria-now.yml", "кнопка «Завантажити зараз»")
            last_now = time.time()
        if time.time() - last_chk >= 60:
            check("ria.yml", RIA_SLOTS, now)
            check("weather.yml", WX_SLOTS, now)
            last_chk = time.time()
        time.sleep(15)
    dispatch("bot.yml", "наступна зміна")


if __name__ == "__main__":
    main()
