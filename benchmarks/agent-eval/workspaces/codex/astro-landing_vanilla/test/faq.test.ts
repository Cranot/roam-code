import { describe, expect, it } from "vitest";

import { initializeFAQ } from "../src/scripts/faq";

describe("initializeFAQ", () => {
  it("opens one panel at a time when clicked", () => {
    document.body.innerHTML = `
      <section>
        <button data-faq-button aria-expanded="true" aria-controls="panel-1" id="btn-1">Question 1</button>
        <div data-faq-panel id="panel-1">Answer 1</div>
        <button data-faq-button aria-expanded="false" aria-controls="panel-2" id="btn-2">Question 2</button>
        <div data-faq-panel id="panel-2" hidden>Answer 2</div>
      </section>
    `;

    initializeFAQ(document);

    const first = document.getElementById("btn-1") as HTMLButtonElement;
    const second = document.getElementById("btn-2") as HTMLButtonElement;
    const firstPanel = document.getElementById("panel-1") as HTMLElement;
    const secondPanel = document.getElementById("panel-2") as HTMLElement;

    second.click();

    expect(first.getAttribute("aria-expanded")).toBe("false");
    expect(firstPanel.hidden).toBe(true);
    expect(second.getAttribute("aria-expanded")).toBe("true");
    expect(secondPanel.hidden).toBe(false);
  });

  it("supports arrow-key navigation between questions", () => {
    document.body.innerHTML = `
      <section>
        <button data-faq-button aria-expanded="false" aria-controls="panel-a" id="btn-a">Question A</button>
        <div data-faq-panel id="panel-a" hidden>Answer A</div>
        <button data-faq-button aria-expanded="false" aria-controls="panel-b" id="btn-b">Question B</button>
        <div data-faq-panel id="panel-b" hidden>Answer B</div>
      </section>
    `;

    initializeFAQ(document);

    const first = document.getElementById("btn-a") as HTMLButtonElement;
    const second = document.getElementById("btn-b") as HTMLButtonElement;

    first.focus();
    first.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));

    expect(document.activeElement).toBe(second);
  });
});