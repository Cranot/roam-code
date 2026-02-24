# FlowBoard Landing Page

A complete SaaS landing page for FlowBoard, a fictional project management tool. Built with Astro, hand-written CSS (no Tailwind/UI libraries), and vanilla JavaScript.

## Features

- **Hero section** with headline, email signup CTA, and Kanban board illustration
- **Features section** with 6 feature cards in a responsive grid with inline SVG icons
- **How It Works** section with 3 numbered steps and connecting line
- **Pricing section** with 3 tiers (Free / Pro $12/mo / Enterprise $49/mo) and feature comparison
- **Testimonials** with 3 customer quotes, names, and roles
- **FAQ section** with 6 questions using native `<details>`/`<summary>` accordion
- **Contact form** with name, email, message fields and client-side validation
- **Sticky navigation** with smooth scroll to sections and mobile hamburger menu
- **Footer** with link groups, social media icons, and copyright

## Technical Highlights

- Astro framework with static site generation
- Mobile-first responsive design with CSS custom properties
- No CSS frameworks — all styles written from scratch
- Semantic HTML with ARIA labels, roles, and keyboard navigation
- Skip-to-content link for accessibility
- Inline SVG icons (no external icon dependencies)
- Client-side form validation with live error feedback
- Escape key closes mobile menu

## Prerequisites

- [Node.js](https://nodejs.org/) 18 or later
- npm 9 or later

## Setup

```bash
# Install dependencies
npm install

# Start development server (http://localhost:4321)
npm run dev

# Build for production
npm run build

# Preview the production build locally
npm run preview

# Run tests (requires a build first)
npm run build && npm test
```

## Project Structure

```
src/
  layouts/
    BaseLayout.astro       # HTML shell, meta tags, font loading
  components/
    Navigation.astro       # Sticky header, mobile hamburger menu
    Hero.astro             # Hero with email signup and board graphic
    Features.astro         # 6-feature grid with SVG icons
    HowItWorks.astro       # 3-step process section
    Pricing.astro          # 3-tier pricing cards
    Testimonials.astro     # 3 customer testimonials
    FAQ.astro              # 6-question accordion
    Contact.astro          # Contact form with validation
    Footer.astro           # Links, social icons, copyright
  styles/
    global.css             # CSS reset, custom properties, utilities
  pages/
    index.astro            # Page composition
tests/
  build.test.ts            # 49 tests covering build output, accessibility, content, responsiveness
public/
  favicon.svg              # FlowBoard brand icon
```

## Commands

| Command           | Action                                       |
| :---------------- | :------------------------------------------- |
| `npm install`     | Install dependencies                         |
| `npm run dev`     | Start dev server at `localhost:4321`         |
| `npm run build`   | Build production site to `./dist/`           |
| `npm run preview` | Preview production build locally             |
| `npm test`        | Run tests with Vitest (build first)          |

## Testing

Tests are in `tests/build.test.ts` and run against the built output in `dist/`. They verify:

- Build output files (HTML, CSS, scripts)
- All 7 sections are present with correct IDs
- Accessibility attributes (ARIA labels, roles, landmarks, focus styles)
- Content completeness (features, pricing tiers, testimonials, FAQ questions)
- Responsive design (media queries in components)
- Interactive behavior (form validation, mobile menu, accordion)
- CSS custom properties and reset styles
- Component file existence

```bash
npm run build && npm test
```

## Browser Support

Targets modern evergreen browsers (Chrome, Firefox, Safari, Edge). Uses:

- CSS custom properties
- CSS Grid and Flexbox
- `clamp()` for fluid typography
- `<details>`/`<summary>` for accordion
- `scroll-behavior: smooth`
- `backdrop-filter` for header blur
