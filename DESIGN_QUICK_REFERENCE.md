# Design System - Quick Reference Card

## Colors (Copy-Paste Ready)
```css
:root {
  --bg: #0B0E14;
  --surface: #151A22;
  --surface-hover: #1A202A;
  --accent: #3B82F6;
  --accent-dark: #1E40AF;
  --success: #10B981;
  --warn: #F59E0B;
  --danger: #EF4444;
  --text-main: #FFFFFF;
  --text-light: #E2E8F0;
  --text-muted: #94A3B8;
  --border: rgba(255, 255, 255, 0.08);
}
```

## Buttons (Copy-Paste Ready)
```html
<!-- Primary (Blue) -->
<button class="btn btn-primary">Click me</button>

<!-- Secondary (Dark) -->
<button class="btn btn-secondary">Click me</button>

<!-- Outline (Transparent) -->
<button class="btn btn-outline">Click me</button>

<!-- Danger (Red) -->
<button class="btn btn-danger">Delete</button>

<!-- Success (Green) -->
<button class="btn btn-success">Approve</button>
```

```css
.btn {
  padding: 0.7rem 1.5rem;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
  border: none;
  transition: all 0.2s ease;
}

.btn-primary {
  background: var(--accent);
  color: white;
  box-shadow: 0 4px 14px rgba(59, 130, 246, 0.4);
}
.btn-primary:hover {
  background: var(--accent-dark);
  transform: translateY(-2px);
}

.btn-secondary {
  background: var(--surface-hover);
  color: var(--text-main);
  border: 1px solid var(--border);
}

.btn-outline {
  background: transparent;
  color: var(--text-main);
  border: 1px solid var(--border);
}
```

## Cards (Copy-Paste Ready)
```html
<div class="card">
  <div class="card-header">
    <h3 class="card-title">Card Title</h3>
    <span class="card-meta">Meta info</span>
  </div>
  <p>Card content here</p>
</div>
```

```css
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 1.5rem;
  transition: all 0.2s ease;
}
.card:hover {
  border-color: rgba(255, 255, 255, 0.12);
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
}
```

## Forms (Copy-Paste Ready)
```html
<div class="form-group">
  <label for="email">Email</label>
  <input type="email" id="email" placeholder="you@example.com">
</div>
```

```css
input, textarea, select {
  width: 100%;
  padding: 0.75rem;
  background: var(--surface-light);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text-main);
  transition: all 0.2s ease;
}
input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.4);
}
```

## Alerts (Copy-Paste Ready)
```html
<!-- Success -->
<div class="alert alert-success">✓ Operation completed</div>

<!-- Warning -->
<div class="alert alert-warn">⚠️ Warning message</div>

<!-- Error -->
<div class="alert alert-danger">✕ Error occurred</div>

<!-- Info -->
<div class="alert alert-info">ℹ️ Information</div>
```

```css
.alert {
  padding: 1rem;
  border-radius: 8px;
  margin-bottom: 1rem;
  display: flex;
  gap: 0.75rem;
}
.alert-success {
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.2);
  color: #86EFAC;
}
.alert-danger {
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.2);
  color: #FCA5A5;
}
```

## Navigation (Copy-Paste Ready)
```html
<nav>
  <a href="/" class="logo">
    <div class="logo-icon"></div>
    HostAI
  </a>
  <div class="nav-links">
    <a href="/dashboard">Dashboard</a>
    <a href="/settings">Settings</a>
    <a href="/login" class="btn btn-primary">Sign In</a>
  </div>
</nav>
```

```css
nav {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1.5rem 5%;
  border-bottom: 1px solid var(--border);
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

.nav-links {
  display: flex;
  gap: 2rem;
  align-items: center;
}
```

## Badges (Copy-Paste Ready)
```html
<span class="badge badge-primary">ACTIVE</span>
<span class="badge badge-success">APPROVED</span>
<span class="badge badge-warn">PENDING</span>
<span class="badge badge-danger">REJECTED</span>
```

```css
.badge {
  display: inline-block;
  padding: 0.35rem 0.75rem;
  border-radius: 99px;
  font-size: 0.75rem;
  font-weight: 700;
  text-transform: uppercase;
}
.badge-primary {
  background: rgba(59, 130, 246, 0.1);
  color: var(--accent);
}
.badge-success {
  background: rgba(16, 185, 129, 0.1);
  color: var(--success);
}
```

## Typography (Copy-Paste Ready)
```html
<h1>Heading 1 - 2.5rem</h1>
<h2>Heading 2 - 2rem</h2>
<h3>Heading 3 - 1.3rem</h3>
<p>Body text - 1rem</p>
<small>Small text - 0.875rem</small>
```

```css
h1, h2, h3 { font-weight: 800; letter-spacing: -0.04em; }
h1 { font-size: 2.5rem; }
h2 { font-size: 2rem; }
h3 { font-size: 1.3rem; }
body { font-family: 'Inter', sans-serif; }
```

## Grid System (Copy-Paste Ready)
```html
<!-- 3-column grid -->
<div class="grid">
  <div class="card">Item 1</div>
  <div class="card">Item 2</div>
  <div class="card">Item 3</div>
</div>

<!-- 2-column grid -->
<div class="grid-2">
  <div class="card">Item 1</div>
  <div class="card">Item 2</div>
</div>
```

```css
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 1.5rem;
}

.grid-2 {
  grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
}

@media (max-width: 768px) {
  .grid, .grid-2 { grid-template-columns: 1fr; }
}
```

## Utilities (Copy-Paste Ready)
```html
<div class="text-center">Centered text</div>
<div class="text-muted">Muted text</div>
<div class="mb-3">Margin bottom 1.5rem</div>
<div class="flex gap-2">Flex with gap</div>
<div class="hidden">Hidden</div>
```

```css
.text-center { text-align: center; }
.text-muted { color: var(--text-muted); }
.mb-1 { margin-bottom: 0.5rem; }
.mb-2 { margin-bottom: 1rem; }
.mb-3 { margin-bottom: 1.5rem; }
.mb-4 { margin-bottom: 2rem; }
.mt-1 { margin-top: 0.5rem; }
.flex { display: flex; }
.flex-center { display: flex; align-items: center; justify-content: center; }
.gap-1 { gap: 0.5rem; }
.gap-2 { gap: 1rem; }
.hidden { display: none; }
```

## Font Import (Add to <head>)
```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
```

## Base Template (Copy-Paste)
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Page Title</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0B0E14;
      --surface: #151A22;
      --surface-hover: #1A202A;
      --accent: #3B82F6;
      --accent-dark: #1E40AF;
      --success: #10B981;
      --warn: #F59E0B;
      --danger: #EF4444;
      --text-main: #FFFFFF;
      --text-light: #E2E8F0;
      --text-muted: #94A3B8;
      --border: rgba(255, 255, 255, 0.08);
      --radius: 8px;
      --radius-lg: 16px;
      --t: 0.2s ease;
    }

    * { margin: 0; padding: 0; box-sizing: border-box; }

    body {
      font-family: 'Inter', sans-serif;
      background: var(--bg);
      color: var(--text-main);
      line-height: 1.6;
    }

    a { color: var(--accent); text-decoration: none; }
    a:hover { color: #60A5FA; }

    /* Paste all component styles here */
  </style>
</head>
<body>
  <nav>
    <a href="/" class="logo">
      <div class="logo-icon"></div>
      HostAI
    </a>
    <div class="nav-links">
      <a href="/dashboard">Dashboard</a>
      <a href="/settings">Settings</a>
    </div>
  </nav>

  <section class="container">
    <h1>Page Title</h1>
    <!-- Your content -->
  </section>

  <footer>
    <p>&copy; 2024 HostAI. All rights reserved.</p>
  </footer>
</body>
</html>
```

---

## Dos & Don'ts

### ✅ DO
- Use color variables (--accent, --success, etc)
- Use spacing scale (0.5rem, 1rem, 1.5rem, 2rem, etc)
- Use border-radius 8px for regular, 16px for large
- Use transition 0.2s ease for all interactive elements
- Use box shadows from the palette
- Match font sizes to typography scale
- Keep margins/padding consistent

### ❌ DON'T
- Add random colors not in the palette
- Use odd spacing (1.2rem, 0.9rem, etc)
- Mix fonts (only use Inter)
- Add too many shadows
- Use hard black or white (use our darker/lighter variants)
- Forget hover states
- Add transitions > 0.3s (feel sluggish)
- Use different button styles for same action

---

**This is everything you need to redesign all 23 pages. Copy-paste these snippets into each page and customize the content.**
