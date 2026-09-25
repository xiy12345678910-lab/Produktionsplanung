# UI Deep Audit & Improvement Plan – Produktionsplanung V12.7.4

Date: 25.09.2026 · Scope: `index.html` (the whole UI: ~405 KB, 994 lines, 71 CSS lines / ~760 JS lines, 82 lines longer than 1,000 characters)

## How the audit was done

- Ran `server.py` locally with a fresh database and an admin account, then drove the UI with Playwright (Chromium).
- Screenshots of every view at **1440×900 (desktop)** and **390×844 (phone)**: login, Wochenplan, Aufträge, Projekte, Personal, GF, System, Produktion, "Neuer Auftrag" dialog.
- DOM checks in the live page: labels, ARIA, focus order, dialogs, font sizes, horizontal overflow.
- Static checks on the source: colour tokens, media queries, blocking dialogs, render path.
- Not covered: the other roles (only admin was tested) and screens with real data. Screens with many orders and projects should be re-checked once phase 1 is done.

## Key numbers

| Metric | Value | Target |
|---|---|---|
| Form fields without a label linked to them (`<label for>` / `aria-label`) | **161 of 168** | 0 |
| ARIA attributes / `role` in the whole UI | **0** | dialogs, tabs, live regions, icon buttons |
| Dialogs with `role="dialog"` + `aria-modal` | 0 of 9 | 9 of 9 |
| Escape closes a dialog | no | yes |
| Page width on a 390 px phone | **621 px** (the page scrolls sideways) | 390 px |
| Different font sizes in use | 15 (9 px to 30 px) | about 6 steps on a scale |
| Hard-coded hex colours in the CSS | 239 (103 different ones) | tokens only |
| Different `border-radius` values | 13+ | 3–4 tokens |
| Inline `style=""` attributes | 123 | fewer than 10 |
| Native `confirm()` / `prompt()` calls | 9 / 3 | 0 (use the app's own dialog) |
| Dark mode / `prefers-reduced-motion` | none / none | both |
| Deep links (URL hash per view) | none | `#/plan`, `#/orders?dept=cnc` … |

---

## Findings

Severity: **P1** = blocks a user or causes errors · **P2** = clear friction or inconsistency · **P3** = polish.

### A. Navigation & information architecture

| # | Sev | Finding | Evidence |
|---|---|---|---|
| A1 | P1 | **Two navigation systems overlap.** The "Planung / Produktion" mode switch and the sidebar both change the view. In Produktion mode the sidebar still highlights "Planung". | screenshot `production` |
| A2 | P2 | **Historie, Report and Änderungen are hidden.** `navHistory` and `navReport` are `display:none`. They can only be reached through a second button row inside System ("Einstellungen · Änderungen · Historie · Report") and through GF buttons. So System has **two tab bars next to each other**. | System screenshot |
| A3 | P2 | **Page title and section heading repeat each other** on every view: "Aufträge" / "Auftragsliste", "GF Übersicht" / "Geschäftsführung · Bereiche & Personal", "Einstellungen" / "System". The sidebar says "System" but the page title says "Einstellungen". | all views |
| A4 | P2 | **Actions drift between views:** "+ Auftrag" (top bar) vs "+ Neuer Auftrag" (list); "Heute" (Wochenplan) vs "Diese Woche" (Personal); week navigation is "KW + date field + Heute + PDF" in one view and "KW + ‹ Diese Woche ›" in the other. | Plan vs Personal |
| A5 | P2 | **No deep links / URL state.** A reload or a shared link cannot open "Aufträge, Bereich CNC, KW 41". The back button does nothing inside the app. | no `location.hash` or `pushState` |
| A6 | P3 | **The product name is inconsistent:** the sidebar says "PP · Produktionsplanung", the login and print headers say "MP · Maschinenplanung", and the `<title>` says "Produktionsplanung V12.7.4 – Bereiche · Personal · Multiuser". | login vs sidebar |
| A7 | P3 | "+ Neuer Eingang" on Projekte is left over from the old sales flow. Since V12.7.3 PM creates projects directly, so the button should read "+ Neues Projekt". | Projekte |

### B. Responsive / small screens

| # | Sev | Finding | Evidence |
|---|---|---|---|
| B1 | P1 | **The page scrolls sideways on a phone or narrow tablet** (scroll width 621 px at 390 px). The week toolbar (KW, date, Heute, PDF) does not wrap and pushes the layout wider. | `m_01_navPlan` |
| B2 | P1 | **Mobile navigation cuts items off:** the top bar shows only 4 icon buttons. GF and System are off-screen, with no labels and no "more" menu. | `m_01_navPlan` |
| B3 | P2 | On a phone the week board shows about 1.5 days. There is no day-by-day or list view for the shop floor. | `m_01_navPlan` |
| B4 | P2 | **The resource column is 150 px wide**, so "Linie Konfektion 1 – Thomsen" wraps onto 3 lines and each row gets tall. The group header above already names the Bereich, so the "Linie Konfektion 1 – " prefix is redundant. | Wochenplan |
| B5 | P3 | The weekend columns (Sa/So) are squeezed, so their dates wrap ("Sa / 26.09."). | Wochenplan |

### C. Accessibility (WCAG 2.1 AA)

| # | Sev | Finding | Fix |
|---|---|---|---|
| C1 | P1 | 161 of 168 fields have no label linked to them. The `<label>` elements are siblings and have no `for=`, so screen readers and click-on-label do not work. | Add `for`/`id` to every field, or wrap the input inside its `<label>`. |
| C2 | P1 | Dialogs are plain `div.modal` elements: no `role="dialog"`, no `aria-modal`, no focus trap, **Escape does nothing**, and focus does not return to the button that opened them. This affects 9 dialogs. | One shared `openModal(id)` / `closeModal()` helper. |
| C3 | P1 | Symbol-only buttons (🔑, ↪, ‹, ›, ×, ◉) rely on `title` alone. `personnelPrevWeek` / `personnelNextWeek` do not even have a `title`. | Add `aria-label` to every symbol-only button. |
| C4 | P2 | **Drag & drop is the only way to reorder** priority (`tr[draggable]`) and to move board cards. There is no keyboard or touch alternative. | Add ↑/↓ buttons (or Alt+↑/↓) for priority; the "Termin" dialog already covers moving a card. |
| C5 | P2 | The tab bars (Personal, System) are buttons with no `role="tablist"`/`tab`/`aria-selected` and no arrow-key support. | Tabs pattern. |
| C6 | P2 | The toast has no `role="status"` / `aria-live`, so screen readers never hear save and error messages. | `aria-live="polite"`; errors `assertive`. |
| C7 | P2 | Text that is too small: 9 px and 10 px appear (chip subtitles, `.fPill`, `.qtyPill`). The `--muted` colour (#5f6f84) passes, but only just: 4.77:1 on `--bg` and 4.50:1 on `--brand2`. The login footer (`.loginFoot`, #7a8799, 10 px) is 3.65:1 on white, which **fails**. | 11 px minimum for text, 12 px for anything you act on; check contrast per token. |
| C8 | P2 | Focus ring: the browser default, 1 px `auto`. On the dark sidebar and the blue primary buttons it is barely visible. | Global `:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px }` plus a light variant in the sidebar. |
| C9 | P3 | Status is shown by colour alone in the legend and on the chips (geplant/freigegeben/läuft/pausiert). "AV-Termin überschritten" only has a red border. | Add an icon or pattern to each status (⏵ running, ⏸ paused, ✓ done, ⚠ late). |
| C10 | P3 | Headings skip levels: 3× `h1` (print-only), 2× `h2`, 35× `h3`. Only 2 landmarks exist (`aside`, `main`); there is no `nav`. | `<nav>` around the sidebar; one `h1` per view. |

### D. Visual consistency / design system

| # | Sev | Finding |
|---|---|---|
| D1 | P2 | There are tokens (`--brand`, `--ok`, …) but also **239 hard-coded hex colours** (103 different ones), e.g. `#10213d` for the sidebar and board groups, `#13233e` for the toast, `#f7f9fc` / `#fbfcfe` / `#fafbfd` for almost the same light greys. |
| D2 | P2 | 15 font sizes and 13+ radius values with no scale (6, 7, 8, 9, 10, 11, 12, 13, 18, 24 px, 99 px, 999 px, 50 %). |
| D3 | P2 | **Controls have different weights:** filter `<select>`s in the order list are bold and large while the search field next to them is regular. The "Alternative Maschine" select in the order dialog is taller than its neighbours and sits out of line. The datetime fields under "Maschinenstillstand" touch the "Grund" field with no gap. |
| D4 | P2 | The CSS was written in layers: 16 `@media` blocks spread across the file, 13× `!important`, and one-line rules up to 11,587 characters long (line 15). Changes are hard to review and diffs are unreadable. |
| D5 | P3 | Nav icons mix Unicode symbols (▦ ☰ ▤ ◫ ⚙) with colour emoji (👥, 📅, 🩺, 🔑, 🖨), so they look different on every OS. "Aufträge" (☰) and "Projekte" (▤) look almost the same. |
| D6 | P3 | GF KPI grid: 6 cards laid out 4 + 2 with an empty gap. Each Bereich card shows the same "Maschinenplanung" badge (since V12.7 every Bereich plans by machine, so the badge carries no information). |
| D7 | P3 | The login screen shows the app behind a transparent overlay with a placeholder state ("Offline · Lesend", 0 values). This looks like broken data. The background should be blurred or neutral. |

### E. Interaction, feedback & data safety

| # | Sev | Finding |
|---|---|---|
| E1 | P1 | **`renderAll()` rebuilds every view with `innerHTML`** (56 places, including hidden views) after each change and after each server revision from the long-poll. Nothing keeps focus, the cursor position or unsaved input, so a colleague saving at the wrong moment can wipe a field the user is typing in (for example a machine name in System). *Verify with two browsers; this is suspected from the code, not reproduced.* |
| E2 | P2 | 9× `confirm()` and 3× `prompt()` use the browser's own dialog. They do not match the app's look, cannot be styled, do not keep the MP error code layout, and `prompt()` for "Neues Passwort" shows the password in plain text. |
| E3 | P2 | **No loading or busy states:** there is no spinner, skeleton or disabled state while saving. The only feedback is the dot in the sidebar footer ("Server gespeichert"). Double clicks on "Anlegen" / "Regel speichern" can send twice. |
| E4 | P2 | **Empty states are noisy or give no next step.** Personal shows "⚠ 15 Schicht(en) unterbesetzt" plus 15 red chips when **no employee exists yet**. Aufträge and Projekte show only "—" or a sentence with no call to action. |
| E5 | P2 | **The helper text is out of date:** the under-staffing hint points to "System → Personalbedarf je Maschine". That list was removed in V12.7.3; it is now the "Personal/Schicht" column under Maschinen & Linien. |
| E6 | P3 | Date and time fields follow the browser locale (`09/21/2026` in an en-US browser) even though the UI is German. This is fine on German Windows, but a formatted KW/date label next to the input would make it unambiguous. |
| E7 | P3 | The order dialog shows every field at once, including "Starttermin" when Planart is "Auto". Fields that do not apply should be hidden or disabled depending on the Planart. |

### F. Code structure (makes all of the above expensive)

| # | Sev | Finding |
|---|---|---|
| F1 | P2 | One 405 KB file with minified-style lines. Every UI fix touches the same few huge lines, so merge conflicts and review risk are high. |
| F2 | P2 | There are no UI tests. `tests/test_regression.py` covers only the server/API. |

---

## Improvement roadmap

Guiding rules: **no framework migration and no build step.** The app is deployed by copying files to a Windows LAN server, so that must stay possible. Keep `index.html` as one deliverable if that matters for `UPDATE_LIVE.ps1`, but organise its contents. Every phase ships as its own V12.7.x release with notes in `RELEASE_NOTES.txt`.

### Phase 0 – Groundwork (≈1–2 days, no visible change)
1. **Format the CSS and JS** in `index.html` onto one rule / one statement per line (a mechanical change, no behaviour change). Group the CSS into sections: tokens → base → layout → components → views → media → print. *Enables every later diff.*
2. **Add a UI smoke test** (`tests/ui_smoke.mjs`, Playwright, optional in `release_gates.py`). It starts the server on loopback, logs in, opens every view at 1440 and 390 px, and fails on JS errors or on `scrollWidth > innerWidth`. Keep the screenshots as review artefacts.
3. Add design tokens to `:root`: a type scale (`--fs-xs 11px … --fs-xl 24px`), radius (`--r-sm 8px, --r-md 12px, --r-lg 18px, --r-pill 999px`), spacing, `--sidebar`, `--surface-2`, `--focus`.

### Phase 1 – P1 fixes (≈3–4 days) → V12.7.5
| Task | Findings |
|---|---|
| Shared `Modal` helper: `role="dialog"`, `aria-modal`, `aria-labelledby`, focus trap, Escape, return focus, close on backdrop click for dialogs with no edits. Use it for all 9 dialogs. | C2 |
| An in-app `confirmDialog()` / `promptDialog()` that returns a Promise. Replace all 12 native calls; the password prompt gets a real `type=password` field. | E2 |
| Link every label to its field (a script-assisted pass: add `id` + `for`). Add `aria-label` to symbol-only buttons. | C1, C3 |
| Keep focus and unsaved input during re-renders: skip or merge the re-render of the view that contains `document.activeElement` while it is dirty, and render **only the active view** (the others render when opened). | E1 |
| Mobile: let the week toolbar and the legend wrap. The board scrolls inside its own container (it already has `.boardWrap`), never the page. Add a mobile nav with labels plus a "Mehr" overflow for GF / System / Historie / Report. | B1, B2 |
| Global `:focus-visible` style. | C8 |

### Phase 2 – Navigation & consistency (≈3–5 days) → V12.8.0
1. **One navigation model:** the sidebar lists every destination (Wochenplan, Produktion, Aufträge, Projekte, Personal, GF, Historie, Report, System), grouped as *Planen* / *Ausführen* / *Auswerten* / *Verwalten*. Remove the Planung/Produktion toggle, or make it a filter inside Wochenplan. Hide items by role, not by view state. (A1, A2)
2. **Hash routing** `#/view?dept=…&kw=…`. It restores the view on reload, makes the browser back button work and gives shareable links. `data.ui` keeps only preferences. (A5)
3. **One page-header pattern:** title + subtitle + primary action + a shared **week navigator component** (‹ KW 39 › · Heute · date picker) used by Wochenplan, Personal and GF. Remove the duplicate section headings. (A3, A4)
4. Settle the naming: product name (pick *Produktionsplanung* or *Maschinenplanung* for sidebar, login, print and `<title>`), "+ Auftrag" everywhere, "+ Neues Projekt", sidebar label = page title. (A4, A6, A7)
5. One icon set: inline SVG icons (for example about 12 Lucide-style paths embedded as a `<symbol>` sprite; no CDN, because the app runs LAN-only). (D5)

### Phase 3 – Visual system & feedback (≈3–4 days) → V12.8.1
1. Replace the 239 hex colours with tokens. Merge near-duplicate greys into `--surface`, `--surface-2`, `--surface-3`. (D1)
2. Apply the type and radius scales. Minimum 11 px, and 12 px for anything you act on. Check the contrast of every text/background token pair. (D2, C7)
3. Make form controls the same: one `.field` / `.input` / `.select` height (36 px desktop, 44 px touch) and one weight. Fix the misalignments in the order dialog and the Maschinenstillstand row. (D3)
4. Busy states: `button[aria-busy]` + disabled while a request is in flight. Toast with `aria-live`. The save indicator moves from the sidebar footer into the header. (E3, C6)
5. Empty states with a next step. Personal with no employees shows one line, "Noch keine Mitarbeiter – + Mitarbeiter anlegen", instead of 15 warnings. Group under-staffing by machine ("Maschine 1: 5 Schichten"), not one chip per shift. Correct the outdated "Personalbedarf je Maschine" hint. (E4, E5)
6. Status icons next to colours on board chips and in the legend. (C9)
7. `prefers-reduced-motion` and, optionally, a dark theme via the tokens. Shop-floor screens are often bright or glossy, so light stays the default.

### Phase 4 – View-level UX (≈1–2 weeks, prioritise with users)
| View | Improvement |
|---|---|
| **Wochenplan** | Wider resource column (180–200 px), dropping the Bereich prefix inside a group; sticky header row and first column; today's column highlighted over its full height; zoom control (day / week / 2 weeks); keyboard move (arrow keys on a selected chip → opens the "Termin prüfen" dialog). |
| **Produktion** (shop floor) | A touch-first layout: 44–56 px buttons, cards grouped by Bereich like the board, running orders first, empty machines collapsed into one line ("5 Maschinen ohne freigegebenen Auftrag"). |
| **Aufträge** | Keyboard/touch alternative for priority (↑/↓), sticky table header, remember column filters in the URL, bulk actions (freigeben, verschieben). |
| **Projekte** | Useful empty columns ("Noch keine Projekte – + Neues Projekt"), card density toggle, "Gespeicherte Prozessabläufe" moved into System or its own tab. |
| **Personal** | Tabs as a real tablist; the week grid shows employees × days with shift chips; the under-staffing summary as one bar per Bereich. |
| **GF** | KPI row of 6 in one row (or 3 × 2); Bereich cards without the redundant badge, with a load bar; empty panels collapsed; report and history as tabs inside GF instead of links out. |
| **System** | One tab bar only; unsaved-changes guard on each tab (a `beforeunload` or tab-switch warning); machine rows as a compact editable table with inline validation. |
| **Neuer Auftrag dialog** | Two steps: *Was* (Bereich, Maschine, FS, Projekt, Menge, Sollstunden) → *Wann* (Planart; show only the anchor fields the Planart needs; show the result "voraussichtlich fertig am …" before saving). |
| **Login** | Neutral or blurred background instead of the placeholder app; show Caps Lock state; `aria-describedby` for the error text. |

### Phase 5 – Optional structural step
Split `index.html` into `app.css`, `app.js` (or a few ES modules: `api`, `schedule`, `render/*`, `ui/modal`) and serve them from `server.py`. `UPDATE_LIVE.ps1` then copies 3–5 files instead of 1. Do this only if Phase 0 formatting is not enough to keep merges manageable.

---

## Suggested order & success criteria

| Release | Content | Done when |
|---|---|---|
| V12.7.5 | Phase 0 + Phase 1 | UI smoke test green; 0 unlabelled fields; Escape closes every dialog; no sideways scroll at 390 px; no native `confirm`/`prompt` |
| V12.8.0 | Phase 2 | every view reachable from the sidebar and by URL; one week navigator; one product name |
| V12.8.1 | Phase 3 | no hex colours outside `:root`; ≤ 7 font sizes; busy states on all save buttons |
| V12.9.x | Phase 4 per view | agreed with the Bereich leads and the shop floor (a short test session per role) |

## Quick wins (under 1 hour each, can go first)
- Escape closes dialogs (one `keydown` listener on `document`).
- `aria-label` on the 20 symbol-only buttons.
- `role="status" aria-live="polite"` on `#toast`.
- Correct the "System → Personalbedarf je Maschine" hint.
- Hide the under-staffing list when there are 0 employees.
- Sidebar highlights "Produktion" while in Produktion mode.
- `:focus-visible` outline.
- Rename "+ Neuer Eingang" → "+ Neues Projekt".
