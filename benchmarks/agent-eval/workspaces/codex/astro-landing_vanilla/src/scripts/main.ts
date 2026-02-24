import { initializeFAQ } from "./faq";
import { hasValidationErrors, validateContactForm, type ContactFormValues } from "./validation";

function initializeNavigation(root: Document = document): void {
  const menuButton = root.querySelector<HTMLButtonElement>("[data-menu-toggle]");
  const navList = root.querySelector<HTMLElement>("[data-nav-list]");
  const links = Array.from(root.querySelectorAll<HTMLAnchorElement>('a[href^="#"]'));

  const setMenuState = (open: boolean): void => {
    if (!menuButton || !navList) {
      return;
    }
    menuButton.setAttribute("aria-expanded", open ? "true" : "false");
    navList.classList.toggle("is-open", open);
  };

  if (menuButton && navList) {
    menuButton.addEventListener("click", () => {
      const expanded = menuButton.getAttribute("aria-expanded") === "true";
      setMenuState(!expanded);
    });

    links.forEach((link) => {
      link.addEventListener("click", () => {
        if (window.innerWidth <= 800) {
          setMenuState(false);
        }
      });
    });

    root.addEventListener("click", (event) => {
      if (!(event.target instanceof Node)) {
        return;
      }
      if (!navList.contains(event.target) && !menuButton.contains(event.target) && window.innerWidth <= 800) {
        setMenuState(false);
      }
    });
  }

  links.forEach((link) => {
    const href = link.getAttribute("href");
    if (!href || href === "#") {
      return;
    }

    const target = root.querySelector<HTMLElement>(href);
    if (!target) {
      return;
    }

    link.addEventListener("click", (event) => {
      event.preventDefault();
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

function initializeContactForm(root: Document = document): void {
  const form = root.querySelector<HTMLFormElement>("#contact-form");
  if (!form) {
    return;
  }

  const successMessage = form.querySelector<HTMLElement>("[data-form-success]");

  const setError = (fieldName: keyof ContactFormValues, message: string | undefined): void => {
    const errorElement = form.querySelector<HTMLElement>(`[data-error-for="${fieldName}"]`);
    const inputElement = form.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[name="${fieldName}"]`);
    if (!errorElement || !inputElement) {
      return;
    }

    errorElement.textContent = message ?? "";
    inputElement.setAttribute("aria-invalid", message ? "true" : "false");
  };

  const readValues = (): ContactFormValues => {
    const data = new FormData(form);
    return {
      name: String(data.get("name") ?? ""),
      email: String(data.get("email") ?? ""),
      message: String(data.get("message") ?? "")
    };
  };

  const renderErrors = (): boolean => {
    const errors = validateContactForm(readValues());
    setError("name", errors.name);
    setError("email", errors.email);
    setError("message", errors.message);
    return hasValidationErrors(errors);
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();

    const hasErrors = renderErrors();
    if (hasErrors) {
      successMessage?.setAttribute("hidden", "");
      return;
    }

    successMessage?.removeAttribute("hidden");
    if (successMessage) {
      successMessage.textContent = "Thanks! Your message has been sent. We will reply shortly.";
    }
    form.reset();
    setError("name", undefined);
    setError("email", undefined);
    setError("message", undefined);
  });

  const fields = Array.from(form.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input, textarea"));
  fields.forEach((field) => {
    field.addEventListener("input", () => {
      renderErrors();
      successMessage?.setAttribute("hidden", "");
    });
  });
}

export function initializePage(): void {
  initializeNavigation();
  initializeFAQ();
  initializeContactForm();
}
