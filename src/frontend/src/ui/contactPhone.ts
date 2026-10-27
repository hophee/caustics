export interface EmergencyContact {
  last_name: string;
  first_name: string;
  middle_name: string;
  role: string;
  phone: string;
}

export function normalizeContactPhone(value: string): string {
  const digits = value.replace(/[^0-9]/g, "");
  if (!digits) return "";
  if (digits.startsWith("8")) return digits;
  return digits.startsWith("7") ? `+${digits}` : `+7${digits}`;
}

export function formatContactPhone(phone: string): string {
  return phone.replace(/^(\+7|8)(\d{3})(\d{3})(\d{2})(\d{2})$/, "$1 ($2) $3-$4-$5");
}
