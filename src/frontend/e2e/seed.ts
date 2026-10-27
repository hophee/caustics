/**
 * Synthetic demo data, created through the public API only (no direct DB access): four patients of
 * the doctor, two weeks of diary, questionnaires, a critical answer (→ alert), a clinician rating,
 * a confirmed prescription with dose marks and a referral. All names and data are invented.
 *
 * usage: node e2e/seed.ts [api-url]   (default $KOMPAS_API or http://127.0.0.1:8000; doctor from `kompas create-doctor`)
 * Skips if the doctor already has patients. Prints the accounts as JSON (also used by Playwright).
 */

export const API = process.env.KOMPAS_API ?? "http://127.0.0.1:8000";
export const DOCTOR = { email: process.env.KOMPAS_DOCTOR_EMAIL ?? "doctor@example.ru", password: process.env.KOMPAS_DOCTOR_PASSWORD ?? "kompas-demo-2026" };
export const PATIENT_PASSWORD = "kompas-demo-2026";

declare const process: { argv: string[]; env: Record<string, string | undefined>; exitCode?: number };

type Json = Record<string, unknown>;

let base = `${API}/api/v1`;

async function call<T = Json>(token: string | null, method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(base + path, {
    method,
    headers: { "content-type": "application/json", ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`${method} ${path} → ${response.status}: ${text}`);
  return (text ? JSON.parse(text) : null) as T;
}

const id = () => crypto.randomUUID();

/** Local calendar day in Moscow, `offset` days from today. */
function day(offset: number): string {
  const now = new Date(Date.now() + 3 * 3600_000 + offset * 86_400_000);
  return now.toISOString().slice(0, 10);
}

async function login(email: string, password: string): Promise<string> {
  return (await call<{ access_token: string }>(null, "POST", "/auth/login", { email, password })).access_token;
}

interface Spec {
  full_name: string;
  birth_date: string;
  sex: "female" | "male";
  icd: string;
  email: string | null;
  profile: Json;
  /** Diary: days back from today → [mood, anxiety, energy, sleep, complaints, text?]; missing = no entry. */
  diary: Record<number, [number, number, number, string, string[], string?]>;
}

const SPECS: Spec[] = [
  {
    full_name: "Смирнова Анна",
    birth_date: "1992-03-01",
    sex: "female",
    icd: "F33.2",
    email: "anna@example.ru",
    profile: { pregnant: "no", lactating: "yes", substance_use: "no", suicide_risk: "low", diagnoses: [], allergies: [], intolerant_excipients: null, past_intolerance: ["пароксетин"], past_inefficacy: [], weight_kg: 58 },
    diary: Object.fromEntries(
      [13, 12, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0].map((d) => [
        d,
        d === 1
          ? [3, 7, 3, "poor", ["low_mood", "fatigue"], "Устала от всего. Иногда думаю, что всем было бы легче без меня."]
          : [4 + (d % 3), 5 - (d % 2), 4, d % 4 === 0 ? "poor" : "normal", d % 3 === 0 ? ["low_mood"] : []],
      ]),
    ),
  },
  {
    full_name: "Белов Игорь",
    birth_date: "1985-07-14",
    sex: "male",
    icd: "F41.1",
    email: "igor@example.ru",
    profile: { pregnant: "no", lactating: "no", substance_use: "no", suicide_risk: "low", diagnoses: [], allergies: [], intolerant_excipients: [], past_intolerance: [], past_inefficacy: [], weight_kg: 82 },
    diary: Object.fromEntries([13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0].map((d) => [d, [6, 4, 6, "normal", d % 5 === 0 ? ["generalized_anxiety"] : []]])),
  },
  {
    full_name: "Лебедева Дарья",
    birth_date: "2001-11-02",
    sex: "female",
    icd: "F32.1",
    email: "darya@example.ru",
    profile: { pregnant: "unknown", lactating: "no", substance_use: "unknown", suicide_risk: "unknown", diagnoses: [], allergies: null, intolerant_excipients: null, past_intolerance: [], past_inefficacy: [] },
    diary: Object.fromEntries([13, 11, 9, 6, 4].map((d) => [d, [3, 6, 2, "poor", ["anhedonia", "fatigue"]]])),
  },
  {
    full_name: "Ковалёв Пётр",
    birth_date: "1978-05-20",
    sex: "male",
    icd: "F40.1",
    email: null,
    profile: { pregnant: "no", lactating: "no", substance_use: "no", suicide_risk: "low", diagnoses: [], allergies: [], intolerant_excipients: [], past_intolerance: [], past_inefficacy: [] },
    diary: {},
  },
];

export interface Seeded {
  doctor: typeof DOCTOR;
  patients: { name: string; patient_id: string; email: string | null; password: string | null; invite_code: string | null }[];
}

export async function seed(api: string = API): Promise<Seeded> {
  base = `${api}/api/v1`;
  const doctor = await login(DOCTOR.email, DOCTOR.password);
  const existing = await call<{ total: number; items: { patient_id: string; full_name: string }[] }>(doctor, "GET", "/patients?limit=50");
  if (existing.total > 0) {
    return {
      doctor: DOCTOR,
      patients: existing.items.map((p) => {
        const spec = SPECS.find((s) => s.full_name === p.full_name);
        return { name: p.full_name, patient_id: p.patient_id, email: spec?.email ?? null, password: spec?.email ? PATIENT_PASSWORD : null, invite_code: null };
      }),
    };
  }

  const out: Seeded = { doctor: DOCTOR, patients: [] };
  for (const spec of SPECS) {
    const invite = await call<{ patient_id: string; invite_code: string }>(doctor, "POST", "/patients", {
      last_name: spec.full_name.split(" ")[0],
      first_name: spec.full_name.split(" ")[1],
      birth_date: spec.birth_date,
      sex: spec.sex,
      timezone: "Europe/Moscow",
    });
    const pid = invite.patient_id;
    const kase = await call<{ case_id: string }>(doctor, "POST", `/patients/${pid}/cases`);
    await call(doctor, "POST", `/cases/${kase.case_id}/diagnoses`, { icd_code: spec.icd });
    await call(doctor, "PUT", `/patients/${pid}/profile`, { birth_date: spec.birth_date, sex: spec.sex, timezone: "Europe/Moscow", ...spec.profile });
    await call(doctor, "POST", `/cases/${kase.case_id}/visits`, {
      kind: "first",
      held_at: `${day(-14)}T08:30:00Z`,
      content: { complaints: "Сниженное настроение, нарушения сна", anamnesis: "Синтетические данные для демонстрации", plan: "Дневник, анкеты, подбор терапии" },
    });
    const scale = spec.icd.startsWith("F41") || spec.icd.startsWith("F40") ? "GAD-7" : "PHQ-9";
    const assignment = await call<{ assignment_id: string }>(doctor, "POST", `/cases/${kase.case_id}/assignments`, {
      scale_code: scale,
      mode: "repeat",
      every_days: 14,
      starts_on: day(-14),
      due_days: 3,
    });

    if (!spec.email) {
      out.patients.push({ name: spec.full_name, patient_id: pid, email: null, password: null, invite_code: invite.invite_code });
      continue;
    }
    const patient = (
      await call<{ access_token: string }>(null, "POST", "/auth/activate", {
        invite_code: invite.invite_code,
        email: spec.email,
        password: PATIENT_PASSWORD,
      })
    ).access_token;

    for (const [back, [mood, anxiety, energy, sleep, complaints, text]] of Object.entries(spec.diary).sort((a, b) => Number(b[0]) - Number(a[0]))) {
      await call(patient, "POST", "/me/diary", {
        submission_id: id(),
        day: day(-Number(back)),
        mood,
        anxiety,
        energy,
        sleep,
        complaints,
        side_effects: [],
        self_harm_thoughts: false,
        text: text ?? null,
      });
    }

    if (spec.full_name === "Смирнова Анна") {
      // PHQ-9 with item 9 = 1 → urgent alert; the doctor accepts it but has not recorded a decision.
      await call(patient, "POST", "/me/responses", {
        submission_id: id(),
        scale_code: "PHQ-9",
        assignment_id: assignment.assignment_id,
        answers: { "1": 2, "2": 2, "3": 3, "4": 2, "5": 1, "6": 2, "7": 1, "8": 1, "9": 1 },
      });
      await call(doctor, "POST", `/cases/${kase.case_id}/ratings`, {
        submission_id: id(),
        scale_code: "HDRS-17",
        answers: Object.fromEntries(Array.from({ length: 17 }, (_, i) => [String(i + 1), i < 4 ? 2 : 1])),
      });
      const alerts = await call<{ items: { alert_id: string; patient_id: string }[] }>(doctor, "GET", "/alerts?limit=50");
      for (const a of alerts.items.filter((x) => x.patient_id === pid).slice(0, 1)) {
        await call(doctor, "POST", `/alerts/${a.alert_id}/delivered`);
        await call(doctor, "POST", `/alerts/${a.alert_id}/accept`);
      }
      // Amitriptyline tablets, 25 mg at 21:00; lactation warning justified by the doctor.
      const drugs = await call<{ items: { label_id: string; form: string }[] }>(doctor, "GET", `/drugs?q=${encodeURIComponent("амитриптилин")}&limit=10`);
      const tablets = drugs.items.find((d) => d.form.startsWith("таблетки")) ?? drugs.items[0];
      if (tablets) {
        const current = await call<{ base_token: string }>(doctor, "GET", `/patients/${pid}/therapy/current`);
        const draft = {
          new_items: [{ key: "a", label_id: tablets.label_id, origin: { kind: "search" }, regimen: { intakes: [{ at: "21:00", amount: 25, unit: "мг" }], start: day(-2), duration_days: 28 } }],
          stopped: [],
        };
        const check = await call<{ warnings: { id: string; severity: string }[] }>(doctor, "POST", `/patients/${pid}/basket/check`, draft);
        const justifications = Object.fromEntries(check.warnings.map((w) => [w.id, "Грудное вскармливание прекращено, решение согласовано с пациенткой"]));
        await call(doctor, "POST", `/patients/${pid}/basket/confirm`, { draft, base_token: current.base_token, justifications });
        const doses = await call<{ course_id: string; scheduled_at: string; can_mark: boolean }[]>(patient, "GET", "/me/doses");
        const markable = doses.filter((d) => d.can_mark);
        for (const [i, dose] of markable.slice(0, -1).entries()) {
          await call(patient, "POST", "/me/doses/marks", {
            submission_id: id(),
            course_id: dose.course_id,
            scheduled_at: dose.scheduled_at,
            ...(i === 0 ? { status: "skipped", reason: "forgot" } : { status: "taken", taken_at: dose.scheduled_at }),
          });
        }
      }
    }
    if (spec.full_name === "Белов Игорь") {
      await call(patient, "POST", "/me/responses", {
        submission_id: id(),
        scale_code: "GAD-7",
        assignment_id: assignment.assignment_id,
        answers: { "1": 1, "2": 1, "3": 1, "4": 0, "5": 1, "6": 1, "7": 0 },
      });
    }
    if (spec.full_name === "Лебедева Дарья") {
      await call(doctor, "POST", `/patients/${pid}/referrals`, {
        submission_id: id(),
        kind: "consultation",
        specialist: "Психотерапевт",
        urgency: "planned",
        clinical_reason: "Когнитивно-поведенческая терапия при депрессивном эпизоде",
        valid_until: day(30),
      });
    }
    out.patients.push({ name: spec.full_name, patient_id: pid, email: spec.email, password: PATIENT_PASSWORD, invite_code: null });
  }
  return out;
}

// Run as a script (not when imported by Playwright).
if (process.argv[1]?.endsWith("seed.ts")) {
  seed(process.argv[2] ?? API).then(
    (result) => console.log(JSON.stringify(result, null, 2)),
    (error: unknown) => {
      console.error(error);
      process.exitCode = 1;
    },
  );
}
