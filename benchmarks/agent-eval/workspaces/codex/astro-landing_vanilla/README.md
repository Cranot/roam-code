# FlowBoard Landing Page (Astro)

A complete SaaS landing page for the fictional project management platform **FlowBoard**.

## Tech Stack

- Astro (static site generation)
- TypeScript
- Handwritten CSS (no UI frameworks)
- Vitest + jsdom for tests

## Implemented Sections

- Hero with headline, subheadline, email CTA, and illustration
- Features grid with 6 icon cards
- 3-step "How It Works"
- Pricing with 3 tiers and feature comparison table
- Testimonials (3 customers)
- FAQ with accessible accordion behavior
- Contact form with client-side validation
- Sticky header navigation with smooth scroll + mobile hamburger menu
- Footer with links, social icons, and copyright

## Accessibility and UX

- Semantic landmarks (`header`, `main`, `section`, `footer`)
- ARIA labels/controls for navigation and FAQ accordion
- Keyboard support for FAQ (`ArrowUp`, `ArrowDown`, `Home`, `End`)
- Focus-visible states and skip-to-content link

## Getting Started

```bash
npm install
npm run dev
```

App runs at `http://localhost:4321` by default.

## Build and Preview

```bash
npm run build
npm run preview
```

## Run Tests

```bash
npm test
```

## Project Structure

```text
src/
  layouts/
    BaseLayout.astro
  pages/
    index.astro
  scripts/
    main.ts
    faq.ts
    validation.ts
  styles/
    global.css
public/
  images/
    hero-illustration.svg
    avatar-1.svg
    avatar-2.svg
    avatar-3.svg
test/
  faq.test.ts
  validation.test.ts
```