# HostAI Complete Design System & Plan

## Table of Contents
1. [Design Philosophy](#design-philosophy)
2. [Color System](#color-system)
3. [Typography](#typography)
4. [Component Library](#component-library)
5. [Layout System](#layout-system)
6. [Page Specifications](#page-specifications)
7. [Implementation Guide](#implementation-guide)

---

## Design Philosophy

### Core Principles
1. **Modern & Premium** - Dark theme, glassmorphism, smooth animations
2. **Consistent** - Same colors, spacing, components everywhere
3. **Accessible** - Good contrast, readable text, intuitive interactions
4. **Fast** - Smooth transitions, responsive, feels snappy
5. **Professional** - Build trust, look like a real product

### Design Inspiration
- **Inspired By:** Vercel, Stripe, Linear, OpenAI
- **Theme:** Modern dark mode SaaS
- **Vibe:** Clean, professional, forward-thinking

---

## Color System

### Primary Palette

```css
--bg: #0B0E14;              /* Main background */
--surface: #151A22;         /* Cards, modals, panels */
--surface-hover: #1A202A;   /* Hover state for surfaces */
--surface-light: #1F2937;   /* Lighter surface for inputs, tables */

--accent: #3B82F6;          /* Primary action color (Blue) */
--accent-glow: rgba(59, 130, 246, 0.4);
--accent-dark: #1E40AF;     /* Darker blue for hover */
```

### Status Colors

```css
--success: #10B981;         /* Positive actions, completed states */
--success-bg: rgba(16, 185, 129, 0.1);
--success-border: rgba(16, 185, 129, 0.2);

--warn: #F59E0B;            /* Warnings, attention needed */
--warn-bg: rgba(245, 158, 11, 0.1);
--warn-border: rgba(245, 158, 11, 0.2);

--danger: #EF4444;          /* Destructive actions, errors */
--danger-bg: rgba(239, 68, 68, 0.1);
--danger-border: rgba(239, 68, 68, 0.2);
```

### Text Colors

```css
--text-main: #FFFFFF;       /* Primary text */
--text-light: #E2E8F0;      /* Secondary text, labels */
--text-muted: #94A3B8;      /* Tertiary text, hints */
--text-dim: #64748B;        /* Disabled text, placeholders */
```

### Borders & Shadows

```css
--border: rgba(255, 255, 255, 0.08);      /* Default border */
--border-light: rgba(255, 255, 255, 0.12); /* Lighter border */

--shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);
--shadow-md: 0 4px 12px rgba(0, 0, 0, 0.15);
--shadow-lg: 0 20px 40px rgba(0, 0, 0, 0.25);
```

---

## Typography

### Font
- **Family:** Inter (all weights: 400, 500, 600, 700, 800)
- **Import:** `<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap">`

### Scale

```
H1:  2.5rem (40px) | 800 weight | -0.04em tracking
H2:  2.0rem (32px) | 800 weight | -0.03em tracking
H3:  1.3rem (21px) | 700 weight | -0.02em tracking
H4:  1.1rem (18px) | 700 weight | -0.01em tracking
Body: 1.0rem (16px) | 400 weight | 0 tracking
Small: 0.875rem (14px) | 500 weight
Tiny: 0.75rem (12px) | 600 weight
```

### Line Heights
- Headings: 1.1
- Body text: 1.6
- Compact: 1.4

---

## Component Library

### 1. BUTTONS

#### Button Variants
```
PRIMARY (Blue)
├─ Normal: background: #3B82F6, box-shadow: glow
├─ Hover: background: #1E40AF, transform: translateY(-2px)
└─ Disabled: opacity: 0.5

SECONDARY (Dark)
├─ Normal: background: #1A202A, border: 1px rgba(255,255,255,0.12)
├─ Hover: background: #1F2937, border: lighter
└─ Disabled: opacity: 0.5

OUTLINE (Transparent)
├─ Normal: background: transparent, border: 1px rgba(255,255,255,0.08)
├─ Hover: background: #151A22
└─ Disabled: opacity: 0.5

DANGER (Red)
├─ Normal: background: #EF4444
├─ Hover: background: #DC2626
└─ Disabled: opacity: 0.5

SUCCESS (Green)
├─ Normal: background: #10B981
└─ Hover: background: #059669
```

#### Button Sizes
```
Small:  padding: 0.5rem 1rem, font-size: 0.9rem
Normal: padding: 0.7rem 1.5rem, font-size: 0.95rem
Large:  padding: 1rem 2rem, font-size: 1.1rem
```

#### All transitions: 0.2s ease

---

### 2. CARDS

```css
.card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 1.5rem;
    transition: all 0.2s ease;
    box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}

.card:hover {
    border-color: var(--border-light);
    box-shadow: 0 10px 30px rgba(0,0,0,0.2);
    transform: translateY(-2px);
}
```

#### Card Header Pattern
```html
<div class="card-header">
    <h3 class="card-title">Title</h3>
    <span class="card-meta">Meta info</span>
</div>
```

---

### 3. FORM INPUTS

```css
input, textarea, select {
    width: 100%;
    padding: 0.75rem;
    background: var(--surface-light);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text-main);
    font-family: inherit;
    transition: all 0.2s ease;
}

input:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-glow);
    background: var(--surface);
}

input::placeholder {
    color: var(--text-dim);
}
```

#### Form Group
```html
<div class="form-group">
    <label for="field">Label Text</label>
    <input type="text" id="field" placeholder="...">
</div>
```

---

### 4. ALERTS

#### Alert Types
```
SUCCESS: background: rgba(16,185,129,0.1), border: 1px rgba(16,185,129,0.2), color: #86EFAC
WARNING: background: rgba(245,158,11,0.1), border: 1px rgba(245,158,11,0.2), color: var(--warn)
DANGER:  background: rgba(239,68,68,0.1), border: 1px rgba(239,68,68,0.2), color: #FCA5A5
INFO:    background: rgba(59,130,246,0.1), border: 1px rgba(59,130,246,0.2), color: var(--accent)
```

#### Alert Template
```html
<div class="alert alert-success">
    <span>✓</span>
    <span>Message text here</span>
</div>
```

---

### 5. BADGES

```css
.badge {
    display: inline-block;
    padding: 0.35rem 0.75rem;
    border-radius: 99px;
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.badge-primary { background: rgba(59,130,246,0.1); color: var(--accent); }
.badge-success { background: rgba(16,185,129,0.1); color: var(--success); }
.badge-warning { background: rgba(245,158,11,0.1); color: var(--warn); }
.badge-danger { background: rgba(239,68,68,0.1); color: var(--danger); }
```

---

### 6. TABLES

```css
table {
    width: 100%;
    border-collapse: collapse;
}

th {
    padding: 1rem;
    text-align: left;
    font-weight: 700;
    background: var(--surface-light);
    border-bottom: 2px solid var(--border);
}

td {
    padding: 1rem;
    border-bottom: 1px solid var(--border);
}

tr:hover {
    background: var(--surface-hover);
}
```

---

### 7. NAVIGATION

#### Navbar Pattern
```css
nav {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 1.5rem 5%;
    border-bottom: 1px solid var(--border);
    backdrop-filter: blur(10px);
    position: sticky;
    top: 0;
    z-index: 100;
}

.logo {
    font-size: 1.25rem;
    font-weight: 800;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.logo-icon {
    width: 24px;
    height: 24px;
    background: var(--accent);
    border-radius: 4px;
    box-shadow: 0 0 15px var(--accent-glow);
}

.nav-links {
    display: flex;
    gap: 2rem;
    align-items: center;
}

.nav-links a {
    color: var(--text-muted);
    text-decoration: none;
    font-weight: 500;
    transition: color 0.2s ease;
}

.nav-links a:hover {
    color: var(--text-main);
}
```

---

### 8. MODALS/DIALOGS

```css
.modal {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0, 0, 0, 0.7);
    backdrop-filter: blur(4px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
}

.modal-content {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 2rem;
    max-width: 500px;
    width: 90%;
    box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);
}

.modal-header {
    margin-bottom: 1.5rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--border);
}

.modal-footer {
    margin-top: 2rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
    display: flex;
    gap: 1rem;
    justify-content: flex-end;
}
```

---

### 9. TABS

```css
.tabs {
    display: flex;
    gap: 2rem;
    border-bottom: 1px solid var(--border);
    margin-bottom: 2rem;
}

.tab {
    padding: 1rem 0;
    font-weight: 600;
    color: var(--text-muted);
    cursor: pointer;
    border-bottom: 2px solid transparent;
    transition: all 0.2s ease;
}

.tab:hover {
    color: var(--text-main);
}

.tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
}

.tab-content {
    display: none;
}

.tab-content.active {
    display: block;
}
```

---

### 10. PAGINATION

```css
.pagination {
    display: flex;
    gap: 0.5rem;
    justify-content: center;
    align-items: center;
    margin: 2rem 0;
}

.pagination button, .pagination a {
    padding: 0.5rem 0.75rem;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-main);
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.2s ease;
}

.pagination button:hover, .pagination a:hover {
    background: var(--surface-hover);
    border-color: var(--text-muted);
}

.pagination .active {
    background: var(--accent);
    border-color: var(--accent);
    color: white;
}
```

---

## Layout System

### Spacing Scale
```
0.5rem  (8px)
1rem    (16px)
1.5rem  (24px)
2rem    (32px)
2.5rem  (40px)
3rem    (48px)
4rem    (64px)
5rem    (80px)
6rem    (96px)
```

### Grid System

#### Wide Layout (3-column)
```css
.grid-3 {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(350px, 1fr));
    gap: 1.5rem;
}
```

#### Medium Layout (2-column)
```css
.grid-2 {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
    gap: 1.5rem;
}
```

#### Tight Layout
```css
.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 1.5rem;
}
```

### Container
```css
.container {
    max-width: 1200px;
    margin: 0 auto;
    padding: 0 5%;
}

section {
    padding: 3rem 5%;
    max-width: 1200px;
    margin: 0 auto;
}
```

---

## Page Specifications

### 1. PUBLIC PAGES (No Auth Required)

#### index.html (Homepage)
**Status:** ✅ Complete
**Sections:**
- Navigation bar
- Hero (problem + value + CTA)
- Problem statement (Without/With)
- Integrations showcase
- Stats/proof section
- Features grid (6 cards)
- Testimonials (3 cards)
- Use cases (4 cards)
- Trust badges
- Pricing preview
- FAQ (6 items)
- Final CTA
- Footer

---

#### login.html
**Status:** ✅ Complete
**Design:**
- Centered card layout
- Glow effects behind
- Email + password form
- Single submit button
- Links to signup/forgot password
- Error alerts above form
- Professional, minimal

---

#### signup.html (To Create)
**Design Pattern:** Similar to login.html
**Fields:**
- Email
- Password
- Confirm Password
- Full Name
- Submit button
- Link to login

**Layout:**
```
[Glow effects]
     ↓
   NAV
     ↓
   [CARD]
  Sign up
  Subtitle

  Email field
  Name field
  Password field
  Confirm password field

  [Sign up button]

  Already have account? Sign in
```

---

#### pricing.html (To Redesign)
**Design Pattern:** 3-tier pricing cards
**Layout:**
```
NAV
  ↓
Hero (Simple, no glow)
  "Simple, transparent pricing"
  ↓
Pricing Grid (3 cards)
  ├─ Free (left, regular size)
  │  ├─ Name
  │  ├─ Price
  │  ├─ Description
  │  ├─ Features list (✓ icons)
  │  └─ CTA button
  │
  ├─ Pro (center, POPULAR badge, 1.05x scale)
  │  ├─ Name
  │  ├─ Price
  │  ├─ Description
  │  ├─ Features list (✓ icons)
  │  └─ CTA button (primary color)
  │
  └─ Growth (right, regular size)
     ├─ Name
     ├─ Price
     ├─ Description
     ├─ Features list (✓ icons)
     └─ CTA button

Features comparison table below
  ├─ Column: Feature name
  ├─ Column: Free
  ├─ Column: Pro
  └─ Column: Growth

  Rows: Each feature marked with ✓ or ✕

FAQ section below
  ├─ Billing questions
  ├─ Plan comparison
  └─ Upgrade/downgrade

FOOTER
```

---

### 2. ONBOARDING PAGES

#### onboarding.html (To Redesign)
**Design Pattern:** Multi-step wizard
**Layout:**
```
NAV

Step progress indicator
├─ Step 1 ← (active, blue)
├─ Step 2
├─ Step 3
└─ Step 4

[CARD]
  Step title
  Step subtitle

  Form fields for this step

  [Back] [Next]

Tips sidebar or help text
```

**Steps:**
1. Profile setup (name, phone, property location)
2. Property details (name, type, address, size)
3. Integration setup (Airbnb link, Vrbo, Booking)
4. Preferences (auto-send settings, team)

---

### 3. AUTHENTICATED PAGES

#### dashboard.html (To Redesign - MAIN APP)
**Design Pattern:** Dashboard with KPI cards + tables
**Layout:**
```
NAV

Search/filter bar

KPI Grid (4 cards)
├─ [Drafts Generated] ← number, % change
├─ [Guests Handled] ← number, % change
├─ [Response Time] ← 2.3s, trend
└─ [Satisfaction] ← 4.8/5, trend

Tabs:
├─ Inbox (active)
├─ Analytics
├─ Automations
└─ Settings

[TAB: Inbox]

  Filter/sort bar

  Drafts table
  ├─ Column: Time
  ├─ Column: Channel (icon + name)
  ├─ Column: Guest message
  ├─ Column: AI draft
  ├─ Column: Status (badge)
  └─ Column: Actions (buttons)

[TAB: Analytics]

  Date range picker

  Charts grid
  ├─ Response time trend (line)
  ├─ Drafts by type (pie)
  ├─ Satisfaction score (gauge)
  └─ Top issues (bar)

[TAB: Automations]

  Automation rules list
  ├─ Rule name
  ├─ Status (toggle)
  ├─ Last run
  └─ Actions (edit, delete)

FOOTER
```

**KPI Card Pattern:**
```html
<div class="kpi-card">
    <div class="kpi-header">
        <h3 class="kpi-title">Drafts Generated</h3>
        <span class="kpi-change">+12% this week</span>
    </div>
    <div class="kpi-value">1,247</div>
    <div class="kpi-chart">/* small sparkline */</div>
</div>
```

---

#### settings.html (To Redesign)
**Design Pattern:** Tabbed settings panel
**Layout:**
```
NAV

<div class="container">
  <h1>Settings</h1>

  Tabs:
  ├─ Account (active)
  ├─ Properties
  ├─ Integrations
  ├─ Billing
  └─ API

  [TAB: Account]
    <card>
      Profile section
      ├─ Avatar uploader
      ├─ Name field
      ├─ Email field
      ├─ Phone field
      └─ [Save changes]
    </card>

    <card>
      Password section
      ├─ Current password
      ├─ New password
      ├─ Confirm password
      └─ [Change password]
    </card>

    <card>
      Danger zone
      ├─ [Delete account] button (red)
      └─ Warning text

  [TAB: Properties]
    Properties grid (list or cards)
    ├─ Each property card
    ├─ Property name
    ├─ Address
    ├─ Status
    └─ [Edit] [Delete]

    [+ Add property] button

  [TAB: Integrations]
    Integration cards (available + connected)
    ├─ Airbnb (status indicator)
    ├─ Vrbo
    ├─ Booking.com
    ├─ WhatsApp
    ├─ SMS
    └─ Each shows [Connect] or [Disconnect]

  [TAB: Billing]
    Current plan card
    ├─ Plan name
    ├─ Price
    ├─ Next billing date
    └─ [Change plan] button

    Billing history table
    ├─ Date
    ├─ Amount
    ├─ Status
    └─ [Invoice]

  [TAB: API]
    API keys section
    ├─ API key (masked)
    ├─ [Copy] [Regenerate]

    Usage stats
    ├─ Calls this month
    ├─ Remaining quota
    ├─ Rate limit

FOOTER
```

---

#### reservations.html (To Redesign)
**Design Pattern:** Data table with filters
**Layout:**
```
NAV

Filter bar
├─ [Property selector dropdown]
├─ [Date range picker]
├─ [Status filter] (All, Upcoming, Active, Past)
└─ [Search input]

Reservations table
├─ Column: Guest name
├─ Column: Property
├─ Column: Check-in date
├─ Column: Check-out date
├─ Column: Status (badge)
├─ Column: Messages (counter)
├─ Column: Actions (dropdown menu)

Pagination at bottom

FOOTER
```

---

#### activity.html (To Redesign)
**Design Pattern:** Timeline view
**Layout:**
```
NAV

Timeline
├─ [Date header] 2026-03-22
├─ Timeline item
│  ├─ Timestamp 2:30 PM
│  ├─ Event type badge (Draft Approved)
│  ├─ Details
│  └─ Related info
│
├─ [Date header] 2026-03-21
├─ Timeline item
│  └─ ...

Filters (right sidebar or top)
├─ Event type filter
├─ Date range
├─ Property filter
└─ [Clear filters]

FOOTER
```

---

#### workflow.html (To Redesign)
**Design Pattern:** Queue/workflow cards
**Layout:**
```
NAV

Status filter tabs
├─ All
├─ Pending approval
├─ Sent
├─ Failed
└─ [View archived]

Queue grid (3-column)
├─ Card
│  ├─ Timestamp badge (red/yellow/green border)
│  ├─ Event type
│  ├─ Guest message
│  ├─ AI draft
│  └─ [Actions: Approve, Reject, Edit]
│
├─ Card
│  └─ ...

FOOTER
```

---

### 4. ADMIN PAGES

#### admin_overview.html (To Redesign)
**Layout:**
```
NAV
Sidebar (minimalist)
├─ Dashboard (active)
├─ System Health
├─ AI Engine
├─ Profitability
└─ API Health

Main content
├─ System stats (KPI cards)
├─ Active users (gauge)
├─ API health status
├─ Recent events (timeline)

FOOTER
```

---

#### admin_costs.html
**Status:** Partially done
**Should have:** Profitability breakdown by tier

---

#### admin_api.html
**Status:** Partially done
**Should have:** API health, model status, cost trends

---

### 5. UTILITY PAGES

#### error.html
**Design:** Centered error message
```
Error code (404/500/etc)
Error title
Error description
[Go home] [Contact support] buttons
```

#### verify_email.html
**Design:** Simple centered card
```
Email verification request
"We sent a verification email to..."
[Didn't receive? Resend]
```

#### forgot_password.html
**Design:** Similar to login
```
Forgot password form
Email input
[Send reset link]
Back to login link
```

#### reset_password.html
**Design:** Password form
```
New password input
Confirm password input
[Reset password]
```

---

## Implementation Guide

### Step 1: Setup Global Styles
```html
<head>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        /* Paste all color variables and utility classes here */
        :root { /* colors */ }
        body { /* base styles */ }
        /* All component styles */
    </style>
</head>
```

### Step 2: Create Reusable Components

#### Navigation Template
```html
<nav>
    <a href="/" class="logo">
        <div class="logo-icon"></div>
        HostAI
    </a>
    <div class="nav-links">
        <a href="/dashboard">Dashboard</a>
        <a href="/settings">Settings</a>
        <a href="/billing">Billing</a>
        <a href="/login" class="btn btn-outline">Sign In</a>
    </div>
</nav>
```

#### Card Template
```html
<div class="card">
    <div class="card-header">
        <h3 class="card-title">Title</h3>
        <span class="card-meta">Meta</span>
    </div>
    <p>Content here</p>
</div>
```

#### Form Template
```html
<div class="form-group">
    <label for="field">Label</label>
    <input type="text" id="field" placeholder="...">
</div>
```

### Step 3: Apply Consistent Patterns

**Every page should have:**
- Navbar at top (sticky)
- Container with max-width 1200px
- Padding of 5% horizontally
- Cards for grouping content
- Proper spacing (use scale)
- Buttons following variant rules
- Footer at bottom

### Step 4: Mobile Responsiveness

**Breakpoints:**
- Desktop: 1200px+ (unchanged)
- Tablet: 768px - 1199px
- Mobile: below 768px

**Mobile Rules:**
- Single column layouts
- Larger touch targets (buttons 44px min)
- Stacked navigation
- Smaller font sizes
- More spacing between elements

```css
@media (max-width: 768px) {
    .grid { grid-template-columns: 1fr; }
    .grid-2 { grid-template-columns: 1fr; }
    nav .nav-links { display: none; } /* Show hamburger instead */
    h1 { font-size: 1.8rem; }
    section { padding: 2rem 5%; }
}
```

---

## Priority Implementation Order

### Phase 1: Customer-Facing (2 hours)
1. ✅ index.html (done)
2. ✅ login.html (done)
3. signup.html (new)
4. pricing.html (redesign)
5. forgot_password.html (new design)

### Phase 2: Core App (4 hours)
6. dashboard.html (redesign - largest)
7. settings.html (redesign)
8. reservations.html (redesign)
9. activity.html (redesign)
10. workflow.html (redesign)

### Phase 3: Onboarding & Admin (2 hours)
11. onboarding.html (redesign)
12. admin_overview.html (redesign)
13. admin_system.html (redesign)
14. admin_tenant.html (redesign)

### Phase 4: Utility Pages (1.5 hours)
15. error.html (new design)
16. verify_email.html (new design)
17. reset_password.html (new design)
18. logout_confirm.html (new design)
19. All remaining pages

---

## Quality Checklist

Before considering a page "done":

- [ ] Uses correct color variables
- [ ] Uses Inter font
- [ ] Uses design system components
- [ ] Follows spacing scale (no random padding)
- [ ] Has proper contrast (text readable)
- [ ] Buttons have hover states
- [ ] Cards have shadow/hover effect
- [ ] Forms have focus states
- [ ] Mobile responsive (tested at 375px, 768px)
- [ ] Links work and styled correctly
- [ ] Error states shown properly
- [ ] Success/warning alerts visible
- [ ] Loading states indicated
- [ ] Consistent with other pages

---

## Design Tokens Summary

Copy this for quick reference:

```css
/* Colors */
--bg: #0B0E14
--surface: #151A22
--accent: #3B82F6
--success: #10B981
--warn: #F59E0B
--danger: #EF4444
--text-main: #FFFFFF
--text-muted: #94A3B8

/* Sizing */
--radius: 8px
--radius-lg: 16px
--shadow-md: 0 4px 12px rgba(0,0,0,0.15)

/* Transitions */
--t: 0.2s ease

/* Fonts */
Font: Inter
H1: 2.5rem 800
H2: 2rem 800
H3: 1.3rem 700
Body: 1rem 400
```

---

## Notes for Implementation

1. **Consistency is key** - Don't deviate from these specs
2. **Component reuse** - Use same button, card, form styles everywhere
3. **Spacing discipline** - Only use multiples of 0.5rem (8px)
4. **Color restraint** - Stick to the palette, don't add random colors
5. **Responsive first** - Test mobile views during development
6. **Performance** - Minimize animations for slower devices
7. **Accessibility** - Maintain good contrast, use semantic HTML
8. **Dark mode only** - No light mode variant (unless requested later)

---

**This is your complete design system. Follow it for all 23 pages and the product will look cohesive, professional, and premium.**

Ready to implement? 🚀
