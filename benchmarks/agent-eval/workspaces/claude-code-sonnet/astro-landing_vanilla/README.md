# FlowBoard - SaaS Landing Page

A complete, production-ready SaaS landing page for FlowBoard, a fictional project management tool. Built with Astro and vanilla CSS, featuring responsive design, accessibility, and comprehensive form validation.

## Features

### Sections

- **Hero Section**: Eye-catching headline with email signup CTA and animated illustration
- **Features Section**: 6 features displayed in a responsive grid with custom SVG icons
- **How It Works**: 3-step process with numbered steps and connecting lines
- **Pricing Section**: 3 pricing tiers (Free, Pro $12/mo, Enterprise $49/mo) with feature comparison
- **Testimonials**: 3 customer testimonials with avatars and roles
- **FAQ Section**: 6 questions with accordion expand/collapse functionality
- **Contact Form**: Name, email, and message fields with client-side validation
- **Navigation**: Sticky header with smooth scroll and mobile hamburger menu
- **Footer**: Link categories, social icons, and copyright

### Technical Highlights

- Static site generation with Astro
- Responsive, mobile-first design
- Custom CSS framework (no Tailwind or UI libraries)
- Semantic HTML with ARIA labels
- Keyboard navigation support
- Client-side form validation
- Smooth scrolling and animations
- Accessible accordion components
- SVG icons and illustrations

## Setup Instructions

### Prerequisites

- Node.js 18.0 or higher
- npm, yarn, or pnpm

### Installation

1. Clone the repository or download the files

2. Install dependencies:

```bash
npm install
```

### Development

Start the development server:

```bash
npm run dev
```

The site will be available at `http://localhost:4321`

### Build

Build the site for production:

```bash
npm run build
```

The static files will be generated in the `dist/` directory.

### Preview

Preview the production build:

```bash
npm run preview
```

### Testing

Run the test suite:

```bash
npm test
```

## Project Structure

```
/
├── public/
│   └── favicon.svg          # Site favicon
├── src/
│   ├── components/
│   │   ├── Header.astro     # Sticky navigation with mobile menu
│   │   ├── Hero.astro       # Hero section with email signup
│   │   ├── Features.astro   # Features grid with icons
│   │   ├── HowItWorks.astro # 3-step process section
│   │   ├── Pricing.astro    # Pricing tiers comparison
│   │   ├── Testimonials.astro # Customer testimonials
│   │   ├── FAQ.astro        # Accordion FAQ section
│   │   ├── Contact.astro    # Contact form with validation
│   │   └── Footer.astro     # Footer with links and social icons
│   ├── layouts/
│   │   └── Layout.astro     # Base layout with meta tags
│   ├── pages/
│   │   └── index.astro      # Main landing page
│   └── styles/
│       └── global.css       # Custom CSS framework
├── tests/
│   ├── validation.test.js   # Form validation tests
│   └── components.test.js   # Component structure tests
├── astro.config.mjs         # Astro configuration
├── package.json             # Dependencies and scripts
└── README.md                # This file
```

## Components

### Header

- Fixed position navigation
- Smooth scroll to sections
- Mobile hamburger menu with slide-in drawer
- Accessible with ARIA labels
- Closes on outside click or link selection

### Hero

- Gradient text effects
- Email signup form with validation
- Animated SVG illustration
- Responsive flex/grid layout

### Features

- 6 features in responsive grid (1 column mobile, 3 columns desktop)
- Custom SVG icons for each feature
- Hover effects on cards

### How It Works

- 3 numbered steps
- Visual connectors between steps (desktop only)
- Responsive layout

### Pricing

- 3 pricing tiers with feature lists
- "Most Popular" badge on Pro tier
- Hover effects and scaling
- Responsive grid layout

### Testimonials

- 3 customer testimonials
- Avatar initials with gradient backgrounds
- Trust badges at bottom

### FAQ

- 6 frequently asked questions
- Accordion expand/collapse
- Keyboard navigation (Arrow Up/Down, Home, End)
- ARIA attributes for accessibility
- Smooth animations

### Contact

- Form with name, email, and message fields
- Real-time validation with error messages
- Success message display
- Focus management
- ARIA attributes for screen readers

### Footer

- 4 link categories (Product, Company, Resources, Legal)
- 4 social media links (Twitter, LinkedIn, GitHub, YouTube)
- Copyright and attribution
- Responsive grid layout

## Accessibility Features

- Semantic HTML5 elements
- ARIA labels and roles
- Keyboard navigation support
- Focus visible indicators
- Skip to content link
- Form validation with error announcements
- Proper heading hierarchy
- Alt text and aria-hidden for decorative elements

## Form Validation

### Email Validation

- Required field
- Valid email format check
- Real-time error clearing

### Name Validation

- Required field
- Minimum 2 characters

### Message Validation

- Required field
- Minimum 10 characters

### Accessibility

- Error messages announced to screen readers
- Focus management on errors
- Clear error indicators
- ARIA invalid states

## Browser Support

- Chrome (latest)
- Firefox (latest)
- Safari (latest)
- Edge (latest)
- Mobile browsers (iOS Safari, Chrome Android)

## Performance

- Static site generation (no runtime JavaScript except for interactions)
- Optimized CSS (no unused styles)
- Inline critical CSS
- Minimal dependencies
- Fast page loads

## Customization

### Colors

Edit CSS custom properties in `src/styles/global.css`:

```css
:root {
  --color-primary: #4f46e5;
  --color-secondary: #06b6d4;
  /* ... more colors */
}
```

### Content

Update content directly in the component files in `src/components/`

### Styling

All styles are in component `<style>` blocks or in `src/styles/global.css`

## License

This is a demonstration project. Feel free to use it as a template for your own projects.

## Credits

Built with:
- [Astro](https://astro.build) - Static Site Generator
- [Vitest](https://vitest.dev) - Testing Framework
- Custom CSS (no frameworks)
