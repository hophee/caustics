import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/client";
import { activateProblems, afterSignIn, errorLines, normalizeCode, registerProblems } from "./auth";

const HOME = { doctor: "/d", patient: "/p", pending: "/welcome", admin: "/admin" } as const;

describe("public auth helpers", () => {
  it("returns to the guarded page only within the signed-in role's area", () => {
    expect(afterSignIn({ from: "/p/forms" }, "patient", HOME)).toBe("/p/forms");
    expect(afterSignIn({ from: "/d/patients" }, "patient", HOME)).toBe("/p");
    expect(afterSignIn(null, "doctor", HOME)).toBe("/d");
    expect(afterSignIn({ from: "/d/patients" }, "pending", HOME)).toBe("/welcome");
  });

  it("shows the API message and field details once", () => {
    const error = new ApiError(422, "invalid_input", "Неверные данные", [
      { code: "x", message: "Пароль слишком короткий", pointer: "password" },
      { code: "y", message: "Неверные данные", pointer: null },
    ]);
    expect(errorLines(error)).toEqual(["Неверные данные", "Пароль слишком короткий"]);
    expect(errorLines(new TypeError("fetch"))).toEqual(["Нет связи с сервером"]);
  });

  it("checks the activation form", () => {
    const ok = { code: "ealr fxea", email: "a@b.ru", password: "password12", repeat: "password12" };
    expect(activateProblems(ok)).toEqual([]);
    expect(activateProblems({ ...ok, repeat: "password13" })).toEqual(["Пароли не совпадают"]);
    expect(activateProblems({ ...ok, password: "short", repeat: "short" })).toEqual(["Пароль не короче 10 символов"]);
    expect(activateProblems({ ...ok, code: " ", email: "" })).toHaveLength(2);
    expect(normalizeCode(" ealr fxea ")).toBe("EALRFXEA");
  });

  it("checks the registration form", () => {
    const ok = { name: "Ирина Новикова", email: "a@b.ru", password: "password12", repeat: "password12" };
    expect(registerProblems(ok)).toEqual([]);
    expect(registerProblems({ ...ok, name: " " })).toEqual(["Введите имя"]);
    expect(registerProblems({ ...ok, repeat: "x" })).toEqual(["Пароли не совпадают"]);
  });
});
