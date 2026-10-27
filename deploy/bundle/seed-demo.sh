#!/usr/bin/env bash
# Create the demo accounts (synthetic): two doctors and two patients of the first doctor, each patient
# with an active case and diagnosis F33.2. Run inside the bundle folder after ./start.sh.
# Safe to run again: existing accounts are skipped.
#
#   Doctor   demo-doctor@kompas-demo.ru   / demo-password-1
#   Doctor   other-doctor@kompas-demo.ru  / demo-password-2
#   Patient  anna@kompas-demo.ru          / anna-password-1   (Анна Смирнова)
#   Patient  maria@kompas-demo.ru         / maria-password-1  (Мария Кузнецова)
set -euo pipefail
cd "$(dirname "$0")"

docker compose exec -T api kompas create-doctor demo-doctor@kompas-demo.ru "Иванова Ирина Петровна" --password demo-password-1 || true
docker compose exec -T api kompas create-doctor other-doctor@kompas-demo.ru "Петров Олег Иванович" --password demo-password-2 || true

docker compose exec -T api python - <<'PY'
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"


def call(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body if body is not None else {}).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


status, login = call("POST", "/auth/login", {"email": "demo-doctor@kompas-demo.ru", "password": "demo-password-1"})
if status != 200:
    raise SystemExit(f"doctor login failed: {status} {login}")
doctor = login["access_token"]

PATIENTS = [
    ("Анна Смирнова", "1992-03-01", "female", "anna@kompas-demo.ru", "anna-password-1"),
    ("Мария Кузнецова", "1985-07-15", "female", "maria@kompas-demo.ru", "maria-password-1"),
]
for name, birth, sex, email, password in PATIENTS:
    status, _ = call("POST", "/auth/login", {"email": email, "password": password})
    if status == 200:
        print(f"{email}: уже есть")
        continue
    status, card = call("POST", "/patients", {"full_name": name, "birth_date": birth, "sex": sex}, doctor)
    if status != 201:
        raise SystemExit(f"{name}: card failed {status} {card}")
    pid = card["patient_id"]
    status, act = call("POST", "/auth/activate", {"invite_code": card["invite_code"], "email": email, "password": password})
    if status != 201:
        raise SystemExit(f"{email}: activation failed {status} {act}")
    status, case = call("POST", f"/patients/{pid}/cases", {}, doctor)
    if status == 201:
        call("POST", f"/cases/{case['case_id']}/diagnoses", {"icd_code": "F33.2"}, doctor)
    print(f"{email}: создан ({name}, F33.2)")
PY

echo
echo "Accounts:"
echo "  Doctor   demo-doctor@kompas-demo.ru  / demo-password-1"
echo "  Doctor   other-doctor@kompas-demo.ru / demo-password-2"
echo "  Patient  anna@kompas-demo.ru         / anna-password-1"
echo "  Patient  maria@kompas-demo.ru        / maria-password-1"
