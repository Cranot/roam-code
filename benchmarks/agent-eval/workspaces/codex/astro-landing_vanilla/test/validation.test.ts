import { describe, expect, it } from "vitest";

import { hasValidationErrors, validateContactForm } from "../src/scripts/validation";

describe("validateContactForm", () => {
  it("returns errors for invalid values", () => {
    const errors = validateContactForm({
      name: "A",
      email: "invalid",
      message: "Too short"
    });

    expect(errors.name).toBeDefined();
    expect(errors.email).toBeDefined();
    expect(errors.message).toBeDefined();
    expect(hasValidationErrors(errors)).toBe(true);
  });

  it("returns no errors for valid values", () => {
    const errors = validateContactForm({
      name: "Taylor Morgan",
      email: "taylor@flowboard.io",
      message: "I need onboarding help for a 40-person product team."
    });

    expect(errors).toEqual({});
    expect(hasValidationErrors(errors)).toBe(false);
  });
});