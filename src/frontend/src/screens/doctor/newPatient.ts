/** «Новый пациент» form: validation and the `POST /patients` body. */

export interface NewPatientForm {
  lastName: string;
  firstName: string;
  middleName: string;
  birthDate: string;
  sex: "female" | "male" | "";
  timezone: string;
}

export type FormErrors = Partial<Record<keyof NewPatientForm, string>>;

export function validateNewPatient(form: NewPatientForm, today: Date = new Date()): FormErrors {
  const errors: FormErrors = {};
  for (const key of ["lastName", "firstName", "middleName"] as const) {
    if (form[key].trim().length > 60) errors[key] = "Не более 60 символов";
  }
  if (!form.lastName.trim()) errors.lastName = "Укажите фамилию";
  if (!form.firstName.trim()) errors.firstName = "Укажите имя";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.birthDate)) {
    errors.birthDate = "Укажите дату рождения";
  } else {
    const birth = new Date(`${form.birthDate}T00:00:00`);
    if (Number.isNaN(birth.getTime()) || birth > today) errors.birthDate = "Дата рождения в будущем";
    else if (birth.getFullYear() < 1900) errors.birthDate = "Проверьте год рождения";
  }
  if (!form.sex) errors.sex = "Укажите пол";
  if (!form.timezone) errors.timezone = "Укажите город";
  return errors;
}

export function newPatientBody(form: NewPatientForm) {
  return {
    last_name: form.lastName.trim(),
    first_name: form.firstName.trim(),
    middle_name: form.middleName.trim(),
    birth_date: form.birthDate,
    sex: form.sex,
    timezone: form.timezone,
  };
}

