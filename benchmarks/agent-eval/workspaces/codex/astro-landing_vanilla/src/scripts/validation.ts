export interface ContactFormValues {
  name: string;
  email: string;
  message: string;
}

export type ContactFormErrors = Partial<Record<keyof ContactFormValues, string>>;

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateContactForm(values: ContactFormValues): ContactFormErrors {
  const errors: ContactFormErrors = {};
  const trimmedName = values.name.trim();
  const trimmedEmail = values.email.trim();
  const trimmedMessage = values.message.trim();

  if (trimmedName.length < 2) {
    errors.name = "Please enter your full name.";
  }

  if (!EMAIL_PATTERN.test(trimmedEmail)) {
    errors.email = "Please provide a valid email address.";
  }

  if (trimmedMessage.length < 10) {
    errors.message = "Message must be at least 10 characters.";
  }

  return errors;
}

export function hasValidationErrors(errors: ContactFormErrors): boolean {
  return Object.keys(errors).length > 0;
}
