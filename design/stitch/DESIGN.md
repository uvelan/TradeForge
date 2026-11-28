# TradeForge Design Brief & System Specification

## 1. Product, Audience, and Tone
- **Product**: TradeForge — local single-user Windows quantitative & discretionary FX research terminal.
- **Audience**: Quantitative and discretionary FX/futures/metals researchers who require causal data provenance, deterministic replay, immutable trade hypothesis versioning, and zero-lookahead evaluation.
- **Tone**: Precision institutional research workstation — calm, dense, high-signal, auditable, non-promotional ("Execution Locked — Read-only research environment"). No flashy retail-trading hype or unverified profit claims.

## 2. Platform Targets
- **Desktop Web** (`1024px`, `1440px+`): Fixed 256px left navigation sidebar (collapsible to a 56px icon rail on Market Terminal), sticky 64px top bar with breadcrumb + Command Palette (`Ctrl+K`) + status pill + UTC/local clock + refresh, fluid main canvas (`max-width: 1680px`, full-bleed on Terminal).
- **Tablet** (`768px`): Single-column stacked panels, collapsible drawer sidebar, horizontal scrollable tables & timeframe controls.
- **Mobile** (`320px`, `375px`): Off-canvas slide-over navigation drawer, compact 56px header, stacked cards, responsive 2-col or 1-col metric grids, 44px minimum touch targets everywhere.

## 3. Design Direction & Tokens
- **Color Palette (Dark Obsidian-Slate + Precision Mint)**:
  - `--bg-canvas`: `#080f18` (app backdrop)
  - `--bg-surface-0`: `#0c1521` (sidebar, chart plot, input background)
  - `--bg-surface-1`: `#111c2b` (primary cards, panels)
  - `--bg-surface-2`: `#172536` (elevated surfaces, table headers, interactive controls)
  - `--bg-surface-3`: `#1f3146` (hover states, active row highlights)
  - `--border-subtle`: `#1f3042`
  - `--border-default`: `#2c4257`
  - `--border-strong`: `#3e5b75`
  - `--text-primary`: `#eef5fc` (contrast >= 14:1 on `#0c1521`)
  - `--text-secondary`: `#a3b8cc` (contrast >= 6.8:1 on `#111c2b`)
  - `--text-muted`: `#7c95ab` (contrast >= 4.7:1 on `#111c2b` — WCAG AA compliant)
  - `--accent-primary`: `#36d6ad` (Precision Mint — primary CTAs, active nav bar, focus rings `#41e1ba`)
  - `--accent-primary-ink`: `#042920` (on-primary text, >= 10:1 contrast)
  - `--accent-primary-soft`: `#133832` (tinted badges/active nav bg)
  - `--semantic-bull`: `#34d399` / `--semantic-bear`: `#f87171` / `--semantic-warn`: `#fbbf24` / `--semantic-info`: `#60a5fa`
- **Typography**:
  - Headlines (`h1`, `h2`, `h3`, KPI values): `'Space Grotesk', system-ui, sans-serif` (tight tracking `-0.025em`)
  - Body & UI Controls: `'DM Sans', system-ui, sans-serif` (base `14px`, line-height `1.5`, tabular-nums on all numeric readouts)
  - Code, IDs, SHA-256, Timestamps, Prices: `'JetBrains Mono', ui-monospace, monospace`
  - Minimum readable font size: `11px` for uppercase eyebrows/badges (`12px` for body meta/table headers).
- **Spacing Scale**: `4px` base grid (`4, 8, 12, 16, 20, 24, 32, 40`). Card padding `20px` desktop / `16px` mobile. Section gap `24px`.
- **Radius**: `6px` (`--radius-sm` inputs/badges/small tools), `8px` (`--radius-md` buttons/nav items), `12px` (`--radius-lg` cards/panels), `16px` (`--radius-xl` modals/feature banners).
- **Elevation**: Subtle border-defined layering + crisp top-edge highlight; `0 12px 32px rgba(2, 6, 12, 0.45)` on overlays/drawers.
- **Iconography**: `lucide-react` 1.75px–2px stroke, `16px`–`18px` inside controls, `20px` inside metric badges.

## 4. Layout Rules
- Consistent `PageHeading` on every screen: uppercase eyebrow (`#36d6ad`), `28px` Space Grotesk `h1`, concise 1-line subtitle (`#a3b8cc`), right-aligned primary action button (desktop) / full-width or stacked (mobile).
- Persistent Left Sidebar: Brand mark (`TradeForge` + `RESEARCH TERMINAL`), Workspace badge, two grouped nav sections (`WORKSPACE` and `LIBRARY & CONFIG`), bottom `Execution Locked` safety card + `Local Trader` footer.
- Sticky Top Bar: Mobile menu / Terminal rail toggle, Breadcrumb (`Workspace > Section`), Command Palette trigger (`Commands Ctrl+K`), `LOCAL MODE` live pill, date/timezone readout, refresh icon button, user avatar.

## 5. Component Rules
- **Buttons**: `min-height: 44px` on touch/primary actions (compact toolbar pills `min-height: 36px` desktop / `44px` on coarse pointer/mobile); clear hover, disabled (`opacity: 0.45`), and `2px solid #41e1ba` `:focus-visible` ring.
- **Inputs & Selects**: Consistent `44px` min-height, `#0c1521` fill, `#2c4257` border, `#41e1ba` focus border, explicit visible `<label>` above every control.
- **Tables**: `.table-scroll` horizontal container, sticky header `#172536`, `12px` uppercase column labels (`#9cb2c7`), `48px` row height, tabular numbers, clear row hover.
- **Cards**: `#111c2b` fill, `1px solid #1f3042`, `12px` radius, `SectionTitle` header with eyebrow + title + right action.
- **Modals & Drawers**: Backdrop blur `#030911c4`, ESC key close, explicit `aria-label` on close buttons, `680px` slide-over drawer for Plan Builder.
- **Empty / Loading / Error States**: Every async section has a structured skeleton/spinner loading state, descriptive empty state with icon + guidance, and high-contrast red-tinted error banner with retry or remediation hint.

## 6. Accessibility
- WCAG AA contrast (>= 4.5:1 text, >= 3:1 UI borders/icons) across all text, placeholders, badges, and table headers.
- `44px` touch targets on mobile/tablet and primary interactive elements.
- Visible `:focus-visible` outline (`2px solid #41e1ba`, `2px` offset) on every focusable element.
- Semantic landmarks (`<aside>`, `<main>`, `<header>`, `<nav>`, `<footer>`), proper `aria-label` / `aria-pressed` / `aria-expanded` / `role="status"` / `role="alert"`.
