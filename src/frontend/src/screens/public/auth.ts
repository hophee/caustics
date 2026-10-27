/** Sign-in helpers for the public pages: where to go after signing in and how to show API errors. */
import { ApiError } from "../../api/client";
import type { Role } from "../../api/types";

/** `location.state.from` set by the role guard, if it belongs to the signed-in role. */
export function afterSignIn(state: unknown, role: Role, home: Record<Role, string>): string {
  const from = (state as { from?: unknown } | null)?.from;
  if (typeof from === "string" && from.startsWith(home[role])) return from;
  return home[role];
}

/** The error message plus field details («Пароль: не короче 10 символов»), without duplicates. */
export function errorLines(error: unknown): string[] {
  if (error instanceof ApiError) {
    const lines = [error.message, ...error.details.map((d) => d.message)];
    return [...new Set(lines.filter(Boolean))];
  }
  if (error instanceof TypeError) return ["Нет связи с сервером"];
  return ["Что-то пошло не так"];
}

export const MIN_PASSWORD = 10;

export interface ActivateForm {
  code: string;
  email: string;
  password: string;
  repeat: string;
}

/** Client-side checks before `POST /auth/activate`; the server checks again. */
export function activateProblems(form: ActivateForm): string[] {
  const out: string[] = [];
  if (!normalizeCode(form.code)) out.push("Введите код приглашения");
  if (!form.email.trim()) out.push("Введите электронную почту");
  if (form.password.length < MIN_PASSWORD) out.push(`Пароль не короче ${MIN_PASSWORD} символов`);
  else if (form.password !== form.repeat) out.push("Пароли не совпадают");
  return out;
}

export interface RegisterForm {
  name: string;
  email: string;
  password: string;
  repeat: string;
}

/** Client-side checks before `POST /auth/register`; the server checks again. */
export function registerProblems(form: RegisterForm): string[] {
  const out: string[] = [];
  if (!form.name.trim()) out.push("Введите имя");
  if (!form.email.trim()) out.push("Введите электронную почту");
  if (form.password.length < MIN_PASSWORD) out.push(`Пароль не короче ${MIN_PASSWORD} символов`);
  else if (form.password !== form.repeat) out.push("Пароли не совпадают");
  return out;
}

/** Invite codes are shown in capitals with no spaces. */
export function normalizeCode(code: string): string {
  return code.replace(/\s+/g, "").toUpperCase();
}
