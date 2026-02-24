import { describe, it, expect } from 'vitest';

// Email validation function (same as used in components)
function validateEmail(email) {
  const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  return re.test(email);
}

// Name validation
function validateName(name) {
  return name && name.trim().length >= 2;
}

// Message validation
function validateMessage(message) {
  return message && message.trim().length >= 10;
}

describe('Email Validation', () => {
  it('should validate correct email addresses', () => {
    expect(validateEmail('user@example.com')).toBe(true);
    expect(validateEmail('test.user@domain.co.uk')).toBe(true);
    expect(validateEmail('name+tag@company.org')).toBe(true);
  });

  it('should reject invalid email addresses', () => {
    expect(validateEmail('')).toBe(false);
    expect(validateEmail('invalid')).toBe(false);
    expect(validateEmail('@example.com')).toBe(false);
    expect(validateEmail('user@')).toBe(false);
    expect(validateEmail('user@domain')).toBe(false);
    expect(validateEmail('user name@example.com')).toBe(false);
  });
});

describe('Name Validation', () => {
  it('should validate correct names', () => {
    expect(validateName('John Doe')).toBe(true);
    expect(validateName('AB')).toBe(true);
    expect(validateName('Mary Jane Watson')).toBe(true);
  });

  it('should reject invalid names', () => {
    expect(validateName('')).toBe(false);
    expect(validateName('A')).toBe(false);
    expect(validateName('  ')).toBe(false);
    expect(validateName(null)).toBe(false);
  });
});

describe('Message Validation', () => {
  it('should validate correct messages', () => {
    expect(validateMessage('This is a valid message')).toBe(true);
    expect(validateMessage('Ten chars!')).toBe(true);
    expect(validateMessage('A longer message with more content')).toBe(true);
  });

  it('should reject invalid messages', () => {
    expect(validateMessage('')).toBe(false);
    expect(validateMessage('Short')).toBe(false);
    expect(validateMessage('123456789')).toBe(false);
    expect(validateMessage('          ')).toBe(false);
    expect(validateMessage(null)).toBe(false);
  });
});

describe('Form Validation Integration', () => {
  it('should validate complete contact form data', () => {
    const formData = {
      name: 'John Doe',
      email: 'john@example.com',
      message: 'This is a test message for the contact form'
    };

    expect(validateName(formData.name)).toBe(true);
    expect(validateEmail(formData.email)).toBe(true);
    expect(validateMessage(formData.message)).toBe(true);
  });

  it('should reject incomplete form data', () => {
    const formData = {
      name: 'J',
      email: 'invalid-email',
      message: 'Short'
    };

    expect(validateName(formData.name)).toBe(false);
    expect(validateEmail(formData.email)).toBe(false);
    expect(validateMessage(formData.message)).toBe(false);
  });
});
