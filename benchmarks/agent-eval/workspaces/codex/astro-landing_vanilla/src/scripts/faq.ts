function setAccordionState(button: HTMLButtonElement, panel: HTMLElement, expanded: boolean): void {
  button.setAttribute("aria-expanded", expanded ? "true" : "false");
  panel.hidden = !expanded;
}

export function initializeFAQ(root: ParentNode = document): void {
  const buttons = Array.from(root.querySelectorAll<HTMLButtonElement>("[data-faq-button]"));
  if (buttons.length === 0) {
    return;
  }

  const panels = buttons.map((button) => {
    const panelId = button.getAttribute("aria-controls");
    return panelId ? root.querySelector<HTMLElement>(`#${panelId}`) : null;
  });

  const collapseAll = (): void => {
    buttons.forEach((button, index) => {
      const panel = panels[index];
      if (!panel) {
        return;
      }
      setAccordionState(button, panel, false);
    });
  };

  buttons.forEach((button, index) => {
    const panel = panels[index];
    if (!panel) {
      return;
    }

    setAccordionState(button, panel, button.getAttribute("aria-expanded") === "true");

    button.addEventListener("click", () => {
      const currentlyExpanded = button.getAttribute("aria-expanded") === "true";
      collapseAll();
      if (!currentlyExpanded) {
        setAccordionState(button, panel, true);
      }
    });

    button.addEventListener("keydown", (event) => {
      let nextIndex = index;
      switch (event.key) {
        case "ArrowDown":
          nextIndex = (index + 1) % buttons.length;
          break;
        case "ArrowUp":
          nextIndex = (index - 1 + buttons.length) % buttons.length;
          break;
        case "Home":
          nextIndex = 0;
          break;
        case "End":
          nextIndex = buttons.length - 1;
          break;
        default:
          return;
      }
      event.preventDefault();
      buttons[nextIndex]?.focus();
    });
  });
}
