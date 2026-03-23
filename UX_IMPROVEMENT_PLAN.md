# UX Improvement Plan: Asterisk WebUI

Revised plan based on review of the Jules draft. Prioritizes practical, high-impact
changes that respect the project's constraints: vanilla JS/CSS stack, SOHO PBX with
4 extensions, LAN-only access, server-side rendered Flask/Jinja2.

---

## Current Pain Points (Priority Order)

1. **Navigation overflow** — 18 top-bar links that wrap on most screens. This is the #1 usability issue.
2. **No inline audio playback** — MoH tracks and announcements open in a new tab.
3. **Inconsistent page widths** — some pages use `container` (960px), others `container-wide` (1400px).
4. **Missing confirmation on destructive actions** — some delete buttons have no confirmation.
5. **Flash messages are basic** — standard page-top alerts, no auto-dismiss.
6. **No audit log UI** — audit data exists (API + file) but has no browseable page.
7. **Empty states are bare** — blank pages when no data exists, no guidance for new users.

---

## Phase 1: Quick Wins (Low effort, high impact)

### 1.1 — Grouped Navigation
Replace the flat 18-link top bar with categorized dropdown groups:

| Group         | Items                                              |
|---------------|----------------------------------------------------|
| **Telephony** | Extensions, Trunks, Ring Groups, Conference         |
| **Routing**   | Inbound, Outbound, Time Groups, Holidays, Spam, IVR |
| **Media**     | MoH, Announcements, Voicemail                      |
| **System**    | Dashboard, Call Logs, Dialplan, Backups, Settings   |

Implementation: CSS-only dropdown menus (`:hover` / `:focus-within`), no JS needed.

### 1.2 — Inline Audio Player
Replace "Play in new tab" links with a native `<audio controls>` element.
Applies to: MoH track list, Announcements list, Voicemail messages.
No library needed — HTML5 `<audio>` with browser-native controls.

### 1.3 — Toast Notifications
Replace static `.alert` flash messages with floating toasts that auto-dismiss after 5s.
Implementation: small vanilla JS snippet (~30 lines) + CSS positioning/animation.

### 1.4 — Standardize Page Widths
- Forms/detail pages: `max-width: 720px` (current `form-card` is 640px — keep)
- List/table pages: `max-width: 1200px` (standardize on `container-wide`)
- Dashboard: `max-width: 1200px` (already done)

### 1.5 — Confirm Destructive Actions
Add `onclick="return confirm('...')"` to all delete buttons/forms that lack it.
No library needed.

---

## Phase 2: Functional Improvements (Medium effort)

### 2.1 — Audit Log Page
New page at `/audit` showing recent audit entries from `audit_log` table.
Columns: timestamp, user, action, target, details.
Paginated (reuse call logs pagination pattern). Filter by action type.

### 2.2 — Empty States with CTAs
When a list page has zero items, show a centered message with icon and
a "Create your first X" button. Applies to: extensions, trunks, ring groups,
MoH classes, announcements, time groups, outbound routes.

### 2.3 — Bulk Actions for Spam Prefixes
Already has bulk import. Add:
- Select-all checkbox in table header
- Per-row checkboxes
- "Delete selected" button
Vanilla JS, ~40 lines.

### 2.4 — Form UX Polish
- Add `placeholder` text to inputs where helpful (e.g., extension number, SIP password)
- Add `<small class="form-help">` hints below complex fields
- Group related fields with `<fieldset>` + `<legend>` on larger forms (trunks, inbound routes)

---

## Phase 3: Nice-to-Have (Lower priority)

### 3.1 — Color Palette Refresh
Soften the current Bootstrap-grey palette:
- Navbar: `#1e293b` (slate-800) instead of `#343a40`
- Primary: `#4f46e5` (indigo-600) instead of `#0d6efd`
- Success: `#059669` (emerald-600)
- Keep light background `#f8fafc`

### 3.2 — Card-Style List Items
For pages with few items (extensions, trunks, ring groups), offer a card
layout alternative to the table. Each card shows key info at a glance
(extension number, status badge, registered device count).

### 3.3 — Dashboard Stat Improvements
- Add sparkline-style indicators for call volume (last 24h from CDR)
- Show trunk registration status alongside endpoints
- Uptime as human-readable duration (already done, verify formatting)

### 3.4 — Keyboard Shortcut for Search
Add a simple text filter (client-side) on list pages. Type to filter
visible table rows. No command palette needed — just an input field
above each table.

---

## Rejected from Jules Draft

| Suggestion | Reason |
|------------|--------|
| Collapsible sidebar | Doesn't solve the problem — grouped nav is better |
| WebSockets/SSE for dashboard | Overkill for 4-extension SOHO PBX; 3s polling is fine |
| HTMX / SPA transitions | Adds dependency to zero-dep vanilla stack; pages render in <50ms |
| Waveform visualization | Nobody needs waveforms for hold music management |
| Interactive drag-and-drop flow editor | The inbound flow is fixed (spam→holiday→time→open/closed) |
| Dark mode | Nice-to-have at best, not a UX problem |
| Mobile/responsive | App runs on 127.0.0.1:8081, accessed from LAN desktop |
| Command palette (Ctrl+K) | Overkill for 18 nav items; grouping solves discoverability |
| WCAG compliance | Not a public-facing application |
| Skeleton screens | Server-side rendering is <50ms, no perceived loading |
| Breadcrumbs | Depth is max 2 levels; grouped nav provides sufficient context |

---

## Implementation Notes

- All changes are CSS + vanilla JS only — no new dependencies
- Each phase can be implemented independently
- Phase 1 items can be done in a single session
- Test with `python -m pytest tests/ -v` after each change
- Verify Asterisk WebUI service still runs: `curl http://127.0.0.1:8081/health`
