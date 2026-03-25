# UX/UI Visual Improvement Plan

**Goal:** Transform the AsterUIX from functional to beautiful — modern, polished, professional.

---

## Current Visual Issues

1. **Dated color palette** — Bootstrap defaults (#0d6efd, #343a40), grey-heavy
2. **Plain dashboard cards** — No icons, no visual hierarchy, flat stats
3. **Basic navbar** — Dark grey bar, no branding, no visual depth
4. **Tables lack polish** — No rounded corners, basic borders, no subtle shadows
5. **Typography is generic** — System fonts, no visual personality
6. **No micro-interactions** — Buttons, links, cards lack hover/active states
7. **Flat badges** — No visual distinction beyond color

---

## Phase 1: Foundation — Modern Color Palette & Typography

### 1.1 Color Palette

Replace Bootstrap greys with a refined, cohesive palette:

```css
/* Primary brand */
--color-primary: #6366f1;        /* indigo-500 */
--color-primary-hover: #4f46e5;  /* indigo-600 */

/* Surface colors */
--color-bg: #f1f5f9;             /* slate-100 */
--color-surface: #ffffff;
--color-surface-alt: #f8fafc;    /* slate-50 */

/* Text colors */
--color-text: #0f172a;           /* slate-900 */
--color-text-muted: #64748b;     /* slate-500 */

/* Navbar */
--color-navbar-bg: #1e1b4b;      /* indigo-950 */
--color-navbar-text: #e0e7ff;    /* indigo-100 */

/* Status colors */
--color-success: #10b981;        /* emerald-500 */
--color-success-bg: #d1fae5;     /* emerald-100 */
--color-danger: #ef4444;         /* red-500 */
--color-danger-bg: #fee2e2;      /* red-100 */
--color-warning: #f59e0b;        /* amber-500 */
--color-warning-bg: #fef3c7;     /* amber-100 */
--color-info: #3b82f6;           /* blue-500 */
--color-info-bg: #dbeafe;        /* blue-100 */

/* Borders */
--color-border: #e2e8f0;         /* slate-200 */
```

### 1.2 Typography

Add Google Fonts for visual personality:

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
```

```css
body {
  font-family: 'Inter', sans-serif;
  font-size: 0.9375rem;  /* 15px */
  line-height: 1.6;
  color: var(--color-text);
}

code, .monospace {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.8125rem;  /* 13px */
}

h1 { font-size: 1.5rem; font-weight: 600; letter-spacing: -0.02em; }
h2 { font-size: 1.125rem; font-weight: 600; letter-spacing: -0.01em; }
```

---

## Phase 2: Dashboard Redesign

### 2.1 Stat Cards with Icons & Visual Hierarchy

**Current:** Plain cards with text header + stat.

**New design:**
```
┌─────────────────────────┐
│ 📞  Extensions          │  ← Icon + label row
│                         │
│    4 / 5                │  ← Large stat with color
│    registered           │  ← Muted subtext
│                         │
│  ████████░░  80%        │  ← Optional progress bar
└─────────────────────────┘
```

**CSS:**
```css
.stat-card {
  background: var(--color-surface);
  border-radius: 12px;
  padding: 1.25rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  border: 1px solid var(--color-border);
  transition: transform 0.15s, box-shadow 0.15s;
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}

.stat-card-icon {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.25rem;
  margin-bottom: 0.75rem;
}

.stat-card-icon.primary { background: var(--color-info-bg); }
.stat-card-icon.success { background: var(--color-success-bg); }
.stat-card-icon.danger { background: var(--color-danger-bg); }
.stat-card-icon.warning { background: var(--color-warning-bg); }

.stat-card-value {
  font-size: 2rem;
  font-weight: 700;
  color: var(--color-text);
  line-height: 1.2;
}

.stat-card-label {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  margin-top: 0.25rem;
}
```

### 2.2 Active Calls Section — Visual Priority

When calls are active, make it **visually prominent**:

```css
.active-calls-alert {
  background: linear-gradient(135deg, #fee2e2 0%, #fef3c7 100%);
  border: 1px solid #fca5a5;
  border-radius: 12px;
  padding: 1rem 1.25rem;
  margin-bottom: 1.5rem;
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.active-calls-alert::before {
  content: "🔴";
  font-size: 1.25rem;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
```

### 2.3 Endpoint Table — Card-Style Rows

Transform table rows into card-like items:

```css
.table-modern {
  background: var(--color-surface);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  border: none;
}

.table-modern thead th {
  background: var(--color-surface-alt);
  color: var(--color-text-muted);
  font-weight: 600;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--color-border);
}

.table-modern tbody td {
  padding: 1rem;
  border-bottom: 1px solid var(--color-border);
  vertical-align: middle;
}

.table-modern tbody tr:last-child td {
  border-bottom: none;
}

.table-modern tbody tr:hover {
  background: var(--color-surface-alt);
}
```

---

## Phase 3: Navigation & Layout

### 3.1 Refined Navbar

**Current:** Flat dark bar.

**New design:** Subtle gradient, better spacing, visual depth.

```css
.navbar {
  background: linear-gradient(135deg, #1e1b4b 0%, #312e81 100%);
  padding: 0.75rem 1.5rem;
  box-shadow: 0 2px 8px rgba(0,0,0,0.15);
  gap: 2.5rem;
}

.navbar-brand {
  font-weight: 700;
  font-size: 1.125rem;
  color: #fff;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.navbar-brand::before {
  content: "☎️";
  font-size: 1.25rem;
}

.navbar-nav a {
  color: #c7d2fe;  /* indigo-200 */
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  transition: background 0.15s, color 0.15s;
}

.navbar-nav a:hover {
  background: rgba(255,255,255,0.1);
  color: #fff;
}
```

### 3.2 Page Headers with Breadcrumbs

```html
<div class="page-header">
  <div>
    <nav class="breadcrumb">
      <a href="/">Home</a> / 
      <span class="current">Extensions</span>
    </nav>
    <h1>Extensions</h1>
  </div>
  <a href="/extensions/new" class="btn btn-primary">+ New Extension</a>
</div>
```

```css
.breadcrumb {
  font-size: 0.8125rem;
  color: var(--color-text-muted);
  margin-bottom: 0.5rem;
}

.breadcrumb a {
  color: var(--color-primary);
  text-decoration: none;
}

.page-header h1 {
  margin: 0;
  font-size: 1.5rem;
}
```

---

## Phase 4: Forms & Inputs

### 4.1 Modern Form Styling

```css
.form-control {
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 0.625rem 0.875rem;
  font-size: 0.9375rem;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.form-control:focus {
  outline: none;
  border-color: var(--color-primary);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
}

.form-card {
  background: var(--color-surface);
  border-radius: 12px;
  box-shadow: 0 4px 16px rgba(0,0,0,0.08);
  border: 1px solid var(--color-border);
  padding: 1.5rem;
}
```

### 4.2 Button Refinements

```css
.btn {
  border-radius: 8px;
  padding: 0.5625rem 1.125rem;
  font-weight: 500;
  font-size: 0.9375rem;
  transition: all 0.15s;
  border: none;
  cursor: pointer;
}

.btn-primary {
  background: var(--color-primary);
  color: #fff;
}

.btn-primary:hover {
  background: var(--color-primary-hover);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.35);
}

.btn-danger {
  background: var(--color-danger);
  color: #fff;
}

.btn-danger:hover {
  background: #dc2626;
  transform: translateY(-1px);
}
```

---

## Phase 5: Status Badges & Indicators

### 5.1 Refined Badges

```css
.badge {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.25rem 0.625rem;
  border-radius: 9999px;  /* Pill shape */
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.badge-ok {
  background: var(--color-success-bg);
  color: #065f46;  /* emerald-800 */
}

.badge-ok::before {
  content: "✓";
  font-size: 0.625rem;
}

.badge-off {
  background: var(--color-surface-alt);
  color: var(--color-text-muted);
}

.badge-off::before {
  content: "○";
  font-size: 0.5rem;
}
```

---

## Phase 6: Micro-interactions & Polish

### 6.1 Hover Effects

```css
/* Cards lift on hover */
.card, .stat-card {
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.card:hover, .stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0,0,0,0.12);
}

/* Button press effect */
.btn:active {
  transform: translateY(0);
}

/* Link underline animation */
.navbar-nav a {
  position: relative;
}

.navbar-nav a::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 50%;
  width: 0;
  height: 2px;
  background: currentColor;
  transition: width 0.2s, left 0.2s;
}

.navbar-nav a:hover::after {
  width: 80%;
  left: 10%;
}
```

### 6.2 Loading States

```css
.skeleton {
  background: linear-gradient(
    90deg,
    #e2e8f0 25%,
    #f1f5f9 50%,
    #e2e8f0 75%
  );
  background-size: 200% 100%;
  animation: skeleton-loading 1.5s infinite;
  border-radius: 4px;
}

@keyframes skeleton-loading {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
```

---

## Phase 7: Dark Mode (Optional)

```css
@media (prefers-color-scheme: dark) {
  :root {
    --color-bg: #0f172a;
    --color-surface: #1e293b;
    --color-surface-alt: #334155;
    --color-text: #f1f5f9;
    --color-text-muted: #94a3b8;
    --color-border: #475569;
  }
}
```

---

## Implementation Order

| Priority | Task | Files | Time |
|----------|------|-------|------|
| 1 | Color palette + typography | `style.css`, `base.html` | 30 min |
| 2 | Dashboard stat cards | `dashboard.html`, `style.css` | 1 hr |
| 3 | Navbar refinement | `base.html`, `style.css` | 30 min |
| 4 | Table modernization | `style.css`, all `*_list.html` | 1 hr |
| 5 | Form styling | `style.css`, all `*_form.html` | 45 min |
| 6 | Badges + buttons | `style.css` | 30 min |
| 7 | Micro-interactions | `style.css` | 30 min |

**Total:** ~4.5 hours for complete visual refresh

---

## Visual References

- **Inspiration:** Linear.app, Vercel Dashboard, Modern SaaS aesthetics
- **Principles:** Subtle shadows, rounded corners, generous whitespace, refined colors
- **Avoid:** Gradients on buttons, heavy borders, pure black, visual clutter

---

## Notes

- Keep all changes CSS-only (no JS framework dependencies)
- Maintain existing functionality — this is visual polish only
- Test on LAN desktop browser (no mobile optimization needed)
- Preserve SSR pattern — no SPA transitions
