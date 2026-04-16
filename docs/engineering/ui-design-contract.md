# UI Design Contract

Owner: Engineering
Last reviewed: 2026-04-16

Use this contract when maintaining or extending the current Swan-style business-app UI.

## Architecture

- Keep FastAPI, Jinja templates, vanilla JavaScript, and the existing Firebase Auth bundle.
- Prefer shared CSS classes and design tokens over page-specific one-off styling.
- Keep shared visual styling in `app/static/style.css`; keep review-workspace styling in `app/static/review.css`.
- Extract large inline JavaScript only when the behavior is already understood and covered by tests.
- Keep the review workspace functional first; visual changes must not break editing, save, export, PDF rendering, highlighting, row selection, or zoom/scroll modes.

## Visual Direction

- Use a clean professional business-app style suitable for accountants and beta business users.
- Favor neutral backgrounds, strong readability, clear table hierarchy, and restrained accent colors.
- Keep border radii at 8px or less for buttons and cards.
- Avoid decorative gradients, blobs, and one-note color palettes.

## Components

- Page shell: consistent max-width for standard pages; full-width constrained workspace for review.
- Header: page title, short context text, and consistent back/help/admin links.
- Buttons: primary, secondary, subtle/icon, and danger variants with stable dimensions.
- Forms: clear labels, help text when needed, grouped controls, and visible validation/status states.
- Tables: sticky headers where useful, numeric tabular alignment, explicit empty states, stable row height expectations, and horizontal overflow for dense data.
- Review workspace: equal-height PDF and transaction panels on desktop, fit-width PDF baseline at `100%`, stable toolbar groups, and date-like columns wide enough for full `YYYY-MM-DD` values.
- Status messages: separate loading, success, warning, and error styles.
- Modals: focus management, Escape/overlay close where safe, and consistent action rows.

## Accessibility And Layout

- Preserve visible focus states on links, buttons, inputs, selects, toolbar buttons, and editable review cells.
- Use text labels or accessible names for icon-only controls.
- Do not rely on color alone for review/error state.
- Keep controls layout-stable as labels, counts, and disabled states change.
- Ensure text fits on mobile and desktop; allow wrapping before shrinking meaning.

## Acceptance Criteria For Future UI Changes

- Upload, review, billing, help, register, and admin pages share one coherent visual system.
- Review toolbar controls remain usable at desktop widths and wrap predictably on narrower screens.
- Billing and admin tables remain readable with horizontal overflow instead of broken columns.
- Existing tests pass, and visual smoke checks cover upload, review, and billing at desktop and mobile sizes.
