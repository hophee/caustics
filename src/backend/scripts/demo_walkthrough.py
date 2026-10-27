"""End-to-end walkthrough against a running API + worker; records every request/response.

Prerequisites (see README "Run the server"): an empty migrated database, `kompas serve` and
`kompas worker` running, and two doctors created with `kompas create-doctor`:
    demo-doctor@kompas-demo.ru / demo-password-1 ("Иванова Ирина Петровна")
    other-doctor@kompas-demo.ru / demo-password-2 ("Петров Олег Иванович")

    uv run python scripts/demo_walkthrough.py --base http://127.0.0.1:8000 --out demo.json

All data is synthetic (fictional patient «Анна Смирнова»).
"""

import argparse
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

DOCTOR = ("demo-doctor@kompas-demo.ru", "demo-password-1")
OTHER = ("other-doctor@kompas-demo.ru", "demo-password-2")


class Recorder:
    """HTTP client that records each call as a transcript step."""

    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base.rstrip("/") + "/api/v1", timeout=60, trust_env=False)
        self.steps: list[dict[str, Any]] = []
        self.section = ""

    def call(
        self, who: str, title: str, method: str, path: str, token: str | None = None, body: Any = None, **params: Any
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = self.http.request(method, path, json=body, headers=headers, params=params or None)
        data = r.json() if r.content else None
        self.steps.append(
            {
                "section": self.section,
                "who": who,
                "title": title,
                "method": method,
                "path": path + (("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else ""),
                "request": body,
                "status": r.status_code,
                "response": data,
            }
        )
        print(f"[{r.status_code}] {who:8} {method} {path} — {title}")
        return data

    def note(self, text: str) -> None:
        self.steps.append({"section": self.section, "note": text})
        print(f"   · {text}")


def wait_for(predicate: Any, what: str, timeout: float = 900) -> Any:
    start = time.time()
    while time.time() - start < timeout:
        value = predicate()
        if value:
            return value
        time.sleep(2)
    raise SystemExit(f"timed out waiting for {what}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    rec = Recorder(args.base)

    # ------------------------------------------------------------------ accounts
    rec.section = "1. Учётные записи и приглашение пациента"
    doc = rec.call("врач", "Вход врача", "POST", "/auth/login", body={"email": DOCTOR[0], "password": DOCTOR[1]})[
        "access_token"
    ]
    other = rec.call(
        "врач 2", "Вход другого врача", "POST", "/auth/login", body={"email": OTHER[0], "password": OTHER[1]}
    )["access_token"]
    invite = rec.call(
        "врач",
        "Врач заводит карточку пациента и получает одноразовый код",
        "POST",
        "/patients",
        doc,
        {"last_name": "Смирнова", "first_name": "Анна", "birth_date": "1992-03-01", "sex": "female", "timezone": "Europe/Moscow"},
    )
    patient_id = invite["patient_id"]
    pat = rec.call(
        "пациент",
        "Пациент активирует учётную запись по коду",
        "POST",
        "/auth/activate",
        body={
            "invite_code": invite["invite_code"],
            "email": f"anna-{uuid.uuid4().hex[:6]}@kompas-demo.ru",
            "password": "anna-password-1",
        },
    )["access_token"]
    rec.call(
        "врач",
        "Повторное использование кода отклоняется",
        "POST",
        "/auth/activate",
        body={"invite_code": invite["invite_code"], "email": "x@kompas-demo.ru", "password": "anna-password-1"},
    )

    # ------------------------------------------------------------------ case and profile
    rec.section = "2. Случай, диагноз, профиль безопасности"
    case_id = rec.call("врач", "Открыть случай", "POST", f"/patients/{patient_id}/cases", doc)["case_id"]
    rec.call("врач", "Основной диагноз F33.2", "POST", f"/cases/{case_id}/diagnoses", doc, {"icd_code": "F33.2"})
    rec.call("врач", "Второй активный случай запрещён (A02)", "POST", f"/patients/{patient_id}/cases", doc)
    rec.call(
        "врач",
        "Профиль: кормит грудью, непереносимость пароксетина; аллергии «нет» ≠ «не спрашивали»",
        "PUT",
        f"/patients/{patient_id}/profile",
        doc,
        {
            "birth_date": "1992-03-01",
            "sex": "female",
            "timezone": "Europe/Moscow",
            "pregnant": "no",
            "lactating": "yes",
            "substance_use": "no",
            "suicide_risk": "low",
            "diagnoses": [],
            "allergies": [],
            "intolerant_excipients": None,
            "past_intolerance": ["пароксетин"],
            "past_inefficacy": [],
        },
    )

    # ------------------------------------------------------------------ access control
    rec.section = "3. Разграничение доступа"
    rec.call("врач 2", "Чужой пациент выглядит несуществующим (404)", "GET", f"/patients/{patient_id}/profile", other)
    rec.call("пациент", "Пациент не может вызвать действие врача (403)", "GET", f"/patients/{patient_id}/profile", pat)
    rec.call("—", "Без токена (401)", "GET", "/auth/me")

    # ------------------------------------------------------------------ live notifications
    events: list[str] = []
    ticket = rec.call("врач", "Билет для живого потока уведомлений (SSE)", "POST", "/me/notifications/ticket", doc)

    def listen() -> None:
        with rec.http.stream("GET", "/me/notifications/stream", params={"ticket": ticket["ticket"]}, timeout=None) as r:
            for line in r.iter_lines():
                if line.startswith("event:") or line.startswith("data:"):
                    events.append(line)

    threading.Thread(target=listen, daemon=True).start()
    time.sleep(1)

    # ------------------------------------------------------------------ scales
    rec.section = "4. Шкалы: пациент видит только «ответы сохранены», врач — числа; критический ответ → сигнал"
    answers = {"1": 2, "2": 2, "3": 3, "4": 2, "5": 1, "6": 2, "7": 1, "8": 1, "9": 1}
    rec.call(
        "пациент",
        "PHQ-9 с пунктом 9 = 1 (мысли о смерти)",
        "POST",
        "/me/responses",
        pat,
        {"submission_id": str(uuid.uuid4()), "scale_code": "PHQ-9", "answers": answers},
    )
    rec.call("пациент", "Пациент: история анкет — название и дата, без результатов", "GET", "/me/responses", pat)
    rec.call("врач", "Врач: полный результат с баллами и ответами", "GET", f"/patients/{patient_id}/results", doc)
    wait_for(lambda: any("alert" in e for e in events), "SSE alert event", 120)
    rec.note(f"Живой поток врача получил событие: {next(e for e in events if e.startswith('event:'))}")
    alerts = rec.call("врач", "Открытые сигналы врача", "GET", "/alerts", doc)
    alert_id = alerts["items"][0]["alert_id"]
    rec.call("врач", "Браузер подтверждает доставку", "POST", f"/alerts/{alert_id}/delivered", doc)
    rec.call("врач", "Врач открыл сигнал", "POST", f"/alerts/{alert_id}/accept", doc)
    rec.call(
        "врач",
        "Врач фиксирует, что сделано",
        "POST",
        f"/alerts/{alert_id}/handle",
        doc,
        {"note": "Позвонила пациентке, суицидальный риск низкий, приём перенесён на завтра"},
    )
    rating = rec.call(
        "врач",
        "HDRS-17 (шкала врача) — результат только у врача",
        "POST",
        f"/cases/{case_id}/ratings",
        doc,
        {"submission_id": str(uuid.uuid4()), "scale_code": "HDRS-17", "answers": {str(i): 1 for i in range(1, 18)}},
    )
    rec.note(f"HDRS-17 = {rating['total']:g}: виден только врачу, в истории пациента не появляется")
    rec.call("пациент", "История анкет пациента не изменилась", "GET", "/me/responses", pat)

    # ------------------------------------------------------------------ diary
    rec.section = "5. Дневник: правила сразу, локальная модель в фоне"
    rec.call(
        "пациент",
        "Запись с тревожным текстом (галочка о самоповреждении не стоит)",
        "POST",
        "/me/diary",
        pat,
        {
            "submission_id": str(uuid.uuid4()),
            "day": time.strftime("%Y-%m-%d"),
            "mood": 3,
            "anxiety": 7,
            "energy": 3,
            "sleep": "poor",
            "complaints": [],
            "side_effects": [],
            "self_harm_thoughts": False,
            "text": "Устала от всего. Иногда думаю, что всем было бы легче без меня.",
        },
    )
    rec.call("врач", "Врач сразу видит запись (модель ещё работает)", "GET", f"/patients/{patient_id}/diary", doc)

    def triaged() -> Any:
        r = rec.http.get(f"/patients/{patient_id}/diary", headers={"Authorization": f"Bearer {doc}"}).json()
        return r if r and r[0]["triage"] and not r[0]["triage"]["model_pending"] else None

    wait_for(triaged, "model verdict")
    rec.call("врач", "Вердикт локальной модели добавлен к оценке", "GET", f"/patients/{patient_id}/diary", doc)
    rec.call("пациент", "Пациент видит свою запись — без оценки и заметок", "GET", "/me/diary", pat)
    rec.call("врач", "Полнота данных рядом с «нет сигналов» (A12)", "GET", f"/patients/{patient_id}/freshness", doc)

    # ------------------------------------------------------------------ therapy
    rec.section = "6. Корзина назначений: проверки, второе подтверждение, вид пациента"
    rec.call("врач", "Подбор по КР (все строки диагноза)", "GET", f"/patients/{patient_id}/suggestions", doc)
    found = rec.call("врач", "Поиск: амитриптилин", "GET", "/drugs", doc, q="амитриптилин", limit=20)
    label_id = next(
        (i["label_id"] for i in found["items"] if "таблет" in i["form"].lower()), found["items"][0]["label_id"]
    )
    current = rec.call(
        "врач", "Корзина открывается с текущей терапией", "GET", f"/patients/{patient_id}/therapy/current", doc
    )
    draft = {
        "new_items": [
            {
                "key": "a",
                "label_id": label_id,
                "origin": {"kind": "search"},
                "regimen": {
                    "intakes": [{"at": "21:00", "amount": 300, "unit": "мг"}],
                    "start": time.strftime("%Y-%m-%d"),
                    "duration_days": 28,
                },
            }
        ],
        "stopped": [],
    }
    rec.call(
        "врач", "Проверка: 300 мг/сут при кормлении грудью", "POST", f"/patients/{patient_id}/basket/check", doc, draft
    )
    refused = rec.call(
        "врач",
        "Подтверждение без обоснования отклонено",
        "POST",
        f"/patients/{patient_id}/basket/confirm",
        doc,
        {"draft": draft, "base_token": current["base_token"], "justifications": {}},
    )
    needed = [p["pointer"] for p in refused["error"]["details"] if p["code"] == "justification_required"]
    rec.call(
        "врач",
        "Второе подтверждение с письменным обоснованием",
        "POST",
        f"/patients/{patient_id}/basket/confirm",
        doc,
        {
            "draft": draft,
            "base_token": current["base_token"],
            "justifications": {
                w: "Решение консилиума: грудное вскармливание прекращено, доза титруется" for w in needed
            },
        },
    )
    rec.call(
        "врач",
        "Повтор со старым base_token — 409 (терапия уже изменилась)",
        "POST",
        f"/patients/{patient_id}/basket/confirm",
        doc,
        {"draft": draft, "base_token": current["base_token"], "justifications": {}},
    )
    rec.call("пациент", "Пациент видит назначение: препарат, доза, время", "GET", "/me/prescription", pat)
    wait_for(
        lambda: any(
            n["kind"] == "prescription"
            for n in rec.http.get("/me/notifications", headers={"Authorization": f"Bearer {pat}"}).json()
        ),
        "prescription notification",
        120,
    )
    rec.call("пациент", "Уведомление пациента о назначении", "GET", "/me/notifications", pat)

    args.out.write_text(json.dumps({"steps": rec.steps, "sse_events": events}, ensure_ascii=False, indent=1))
    print(f"\n{len(rec.steps)} steps → {args.out}")


if __name__ == "__main__":
    main()
