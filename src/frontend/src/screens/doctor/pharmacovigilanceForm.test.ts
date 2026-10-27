import { expect, test } from "vitest";
import { formGroups, printableForm } from "./pharmacovigilanceForm";

test("Printable report includes all form sections and treats patient text as text", () => {
  const html = printableForm({ description: '<script>alert("x")</script>\nСледующая строка', drug0_name: "Трамал" });
  expect(html).not.toContain('<script>');
  expect(html).toContain("&lt;script&gt;");
  expect(html).toContain("Следующая строка");
  expect(html).toContain("Другой препарат за последние 3 месяца — 5");
  expect(html).toContain("npr@roszdravnadzor.gov.ru");
  const keys = formGroups.flatMap((g) => g.fields.map((f) => f.key));
  expect(new Set(keys).size).toBe(keys.length);
  expect(keys.length).toBeLessThanOrEqual(100);
});
