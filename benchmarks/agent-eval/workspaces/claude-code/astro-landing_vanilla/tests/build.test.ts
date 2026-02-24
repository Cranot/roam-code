import { describe, it, expect } from 'vitest';
import { readFileSync, existsSync, readdirSync } from 'fs';
import { join } from 'path';

const distDir = join(__dirname, '..', 'dist');
const srcDir = join(__dirname, '..', 'src');

describe('Build output', () => {
  it('produces dist/index.html', () => {
    expect(existsSync(join(distDir, 'index.html'))).toBe(true);
  });

  it('index.html contains the page title', () => {
    const html = readFileSync(join(distDir, 'index.html'), 'utf-8');
    expect(html).toContain('FlowBoard');
  });

  it('index.html contains all major sections', () => {
    const html = readFileSync(join(distDir, 'index.html'), 'utf-8');
    const sectionIds = [
      'hero',
      'features',
      'how-it-works',
      'pricing',
      'testimonials',
      'faq',
      'contact',
    ];
    for (const id of sectionIds) {
      expect(html).toContain(`id="${id}"`);
    }
  });

  it('index.html contains the navigation', () => {
    const html = readFileSync(join(distDir, 'index.html'), 'utf-8');
    expect(html).toContain('nav-header');
    expect(html).toContain('nav-toggle');
  });

  it('index.html contains the footer', () => {
    const html = readFileSync(join(distDir, 'index.html'), 'utf-8');
    expect(html).toContain('role="contentinfo"');
    expect(html).toContain('FlowBoard, Inc.');
  });

  it('produces CSS assets', () => {
    const assetsDir = join(distDir, '_astro');
    expect(existsSync(assetsDir)).toBe(true);
    const files = readdirSync(assetsDir);
    const cssFiles = files.filter((f: string) => f.endsWith('.css'));
    expect(cssFiles.length).toBeGreaterThan(0);
  });

  it('has inline scripts for interactivity', () => {
    const html = readFileSync(join(distDir, 'index.html'), 'utf-8');
    const scriptTags = html.match(/<script[\s>]/g) || [];
    expect(scriptTags.length).toBeGreaterThan(0);
  });
});

describe('HTML structure & accessibility', () => {
  const html = readFileSync(join(distDir, 'index.html'), 'utf-8');

  it('has lang attribute on html element', () => {
    expect(html).toMatch(/<html[^>]*lang="en"/);
  });

  it('has meta viewport tag', () => {
    expect(html).toContain('width=device-width');
  });

  it('has meta description', () => {
    expect(html).toMatch(/<meta[^>]*name="description"/);
  });

  it('has skip-to-content link', () => {
    expect(html).toContain('skip-link');
    expect(html).toContain('#main-content');
  });

  it('has main landmark with id', () => {
    expect(html).toContain('id="main-content"');
  });

  it('uses aria-label on navigation', () => {
    expect(html).toContain('aria-label="Main navigation"');
  });

  it('uses aria-labelledby on sections', () => {
    expect(html).toContain('aria-labelledby="hero-heading"');
    expect(html).toContain('aria-labelledby="features-heading"');
    expect(html).toContain('aria-labelledby="pricing-heading"');
  });

  it('has aria-expanded on mobile menu toggle', () => {
    expect(html).toContain('aria-expanded="false"');
  });

  it('has role="banner" on header', () => {
    expect(html).toContain('role="banner"');
  });

  it('has role="contentinfo" on footer', () => {
    expect(html).toContain('role="contentinfo"');
  });

  it('has aria-live regions for form errors', () => {
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain('role="alert"');
  });

  it('has sr-only class for screen reader labels', () => {
    expect(html).toContain('sr-only');
  });
});

describe('Content sections', () => {
  const html = readFileSync(join(distDir, 'index.html'), 'utf-8');

  it('hero section has headline, subheadline, and CTA', () => {
    expect(html).toContain('clarity and speed');
    expect(html).toContain('Start Free Trial');
    expect(html).toContain('hero-email');
  });

  it('features section has 6 features', () => {
    const features = [
      'Visual Boards',
      'Smart Automation',
      'Real-Time Analytics',
      'Team Collaboration',
      'Powerful Integrations',
      'Enterprise Security',
    ];
    for (const feature of features) {
      expect(html).toContain(feature);
    }
  });

  it('how-it-works section has 3 numbered steps', () => {
    expect(html).toContain('Create Your Workspace');
    expect(html).toContain('Organize Your Workflow');
    expect(html).toContain('Ship with Confidence');
  });

  it('pricing section has Free, Pro, and Enterprise tiers', () => {
    expect(html).toContain('$0');
    expect(html).toContain('$12');
    expect(html).toContain('$49');
    expect(html).toContain('Most Popular');
  });

  it('testimonials section has 3 testimonials', () => {
    expect(html).toContain('Sarah Chen');
    expect(html).toContain('Marcus Rodriguez');
    expect(html).toContain('Aisha Patel');
  });

  it('FAQ section has 6 questions', () => {
    const questions = [
      'How does the free trial work?',
      'Can I import data',
      'limit on the number of projects',
      'billing work for teams',
      'security certifications',
      'cancel my subscription',
    ];
    for (const q of questions) {
      expect(html).toContain(q);
    }
  });

  it('contact form has name, email, and message fields', () => {
    expect(html).toContain('id="contact-name"');
    expect(html).toContain('id="contact-email"');
    expect(html).toContain('id="contact-message"');
  });

  it('footer has social media links', () => {
    expect(html).toContain('aria-label="X (Twitter)"');
    expect(html).toContain('aria-label="GitHub"');
    expect(html).toContain('aria-label="LinkedIn"');
    expect(html).toContain('aria-label="YouTube"');
  });

  it('footer has link groups', () => {
    expect(html).toContain('Product');
    expect(html).toContain('Company');
    expect(html).toContain('Resources');
    expect(html).toContain('Legal');
  });
});

describe('CSS custom properties', () => {
  it('global.css defines required CSS variables', () => {
    const css = readFileSync(
      join(srcDir, 'styles', 'global.css'),
      'utf-8'
    );
    const requiredVars = [
      '--color-primary',
      '--color-bg',
      '--color-text',
      '--font-sans',
      '--max-width',
      '--header-height',
      '--shadow-md',
      '--radius-md',
      '--transition-base',
    ];
    for (const v of requiredVars) {
      expect(css).toContain(v);
    }
  });

  it('global.css includes reset styles', () => {
    const css = readFileSync(
      join(srcDir, 'styles', 'global.css'),
      'utf-8'
    );
    expect(css).toContain('box-sizing: border-box');
    expect(css).toContain('scroll-behavior: smooth');
  });

  it('global.css has focus-visible styles', () => {
    const css = readFileSync(
      join(srcDir, 'styles', 'global.css'),
      'utf-8'
    );
    expect(css).toContain(':focus-visible');
  });
});

describe('Component files', () => {
  const components = [
    'Navigation',
    'Hero',
    'Features',
    'HowItWorks',
    'Pricing',
    'Testimonials',
    'FAQ',
    'Contact',
    'Footer',
  ];

  for (const name of components) {
    it(`${name}.astro exists`, () => {
      expect(existsSync(join(srcDir, 'components', `${name}.astro`))).toBe(
        true
      );
    });
  }

  it('BaseLayout.astro exists', () => {
    expect(existsSync(join(srcDir, 'layouts', 'BaseLayout.astro'))).toBe(true);
  });
});

describe('Responsive design', () => {
  it('global.css has mobile-first media queries', () => {
    const css = readFileSync(
      join(srcDir, 'styles', 'global.css'),
      'utf-8'
    );
    expect(css).toContain('@media (min-width: 768px)');
  });

  it('Navigation component has responsive breakpoints', () => {
    const nav = readFileSync(
      join(srcDir, 'components', 'Navigation.astro'),
      'utf-8'
    );
    expect(nav).toContain('@media (min-width: 768px)');
    expect(nav).toContain('nav-toggle');
  });

  it('Features grid is responsive', () => {
    const features = readFileSync(
      join(srcDir, 'components', 'Features.astro'),
      'utf-8'
    );
    expect(features).toContain('grid-template-columns');
    expect(features).toContain('@media (min-width: 640px)');
    expect(features).toContain('@media (min-width: 1024px)');
  });

  it('Pricing grid is responsive', () => {
    const pricing = readFileSync(
      join(srcDir, 'components', 'Pricing.astro'),
      'utf-8'
    );
    expect(pricing).toContain('grid-template-columns');
    expect(pricing).toContain('@media (min-width: 768px)');
  });
});

describe('Interactive features', () => {
  it('Navigation has mobile menu toggle script', () => {
    const nav = readFileSync(
      join(srcDir, 'components', 'Navigation.astro'),
      'utf-8'
    );
    expect(nav).toContain('aria-expanded');
    expect(nav).toContain("classList.toggle('open')");
    expect(nav).toContain("key === 'Escape'");
  });

  it('Contact form has client-side validation', () => {
    const contact = readFileSync(
      join(srcDir, 'components', 'Contact.astro'),
      'utf-8'
    );
    expect(contact).toContain('validators');
    expect(contact).toContain('validateField');
    expect(contact).toContain('form-error');
  });

  it('FAQ uses details/summary for accordion', () => {
    const faq = readFileSync(
      join(srcDir, 'components', 'FAQ.astro'),
      'utf-8'
    );
    expect(faq).toContain('<details');
    expect(faq).toContain('<summary');
  });

  it('Hero has email signup form', () => {
    const hero = readFileSync(
      join(srcDir, 'components', 'Hero.astro'),
      'utf-8'
    );
    expect(hero).toContain('hero-signup-form');
    expect(hero).toContain('type="email"');
  });
});
