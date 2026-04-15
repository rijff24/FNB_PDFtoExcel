# Frontend

The frontend consists of two HTML pages served by the FastAPI backend, a bundled Firebase Auth module, and shared CSS.

## Pages

### Upload Page (`app/templates/index.html`)

The landing page provides:
- **Sign-in UI**: Google Sign-In button and email/password form.
- **Upload form**: PDF file input, OCR toggle checkbox, "Download Excel" and "Preview & review" buttons.
- **Auth state management**: UI toggles between signed-out and signed-in states based on Firebase auth state changes.

### Review Page (`app/templates/review.html`)

An interactive review interface with two panels:

**Left panel — PDF Viewer**:
- Renders the uploaded PDF using [PDF.js](https://mozilla.github.io/pdf.js/) (v3.11.174, loaded from CDN).
- Zoom slider (75%–250%) with live re-render.
- Highlight overlay: semi-transparent cyan rectangles drawn over bounding-box regions.
- Smooth scroll to highlighted region.

**Right panel — Transactions Table**:
- Bank-specific column templates loaded from `BANK_TABLE_TEMPLATES` (FNB, Capitec Business, Capitec Personal, Standard Bank each have their own column layout).
- Rows with `review_state: "needs"` (or legacy `needs_review: true`) get a yellow background.
- Per-cell hover highlights: hovering a cell highlights that field's bounding box on the PDF.
- Row click highlights the entire row's bounding box.
- Debounced clear (60ms) prevents flicker when moving between cells.
- Editable spreadsheet behavior: double-click to edit, Enter/Escape/blur commit flow.
- Toolbar actions: Undo, Redo, Save, Download Excel, unsaved-changes indicator.
- For Standard Bank:
  - `Service fee` displays `#` markers.
  - `Debits` and `Credits` highlight independently (separate bbox fields).
- Review column tri-state checkbox:
  - `blank`: empty checkbox
  - `needs`: orange warning checkbox and highlighted row
  - `done`: green checked checkbox

**Row Selection and Management**:
- Click a row to select it (blue highlight). Click again to deselect.
- Ctrl+Click (Cmd+Click on Mac) to toggle individual rows without deselecting others.
- Shift+Click to select a range from the last-clicked row.
- Escape clears all selections.
- Toolbar buttons (enabled when rows are selected):
  - **+ Above**: inserts an empty row above each selected row.
  - **+ Below**: inserts an empty row below each selected row.
  - **Delete**: removes all selected rows.
- Delete key also removes selected rows (when not editing a cell).
- All insert/delete operations are fully undoable/redoable.

**Highlighting Toggle**:
- A "Highlighting" toggle button in the toolbar enables/disables the PDF highlight overlay.
- When unchecked, hover and click no longer draw bounding-box highlights on the PDF.
- If scroll mode is `Auto` when highlighting is turned off, the UI switches to `Synced` mode to avoid highlight-driven auto-scroll behavior.
- Re-enabling highlighting does not force a scroll mode change.

**Scroll Modes** (segmented control in the toolbar, next to the Highlighting toggle):
- **Auto** (default): hovering/clicking a table row scrolls the PDF viewer to center the matching bounding box vertically and horizontally.
- **Sync**: bidirectional synchronized scrolling using center-based mapping and smoothing. Vertical and horizontal movement in one panel nudges the other panel to corresponding center positions.
- **None**: no automatic scrolling. Highlighting still works if enabled, but neither panel auto-moves.

**Table Zoom**:
- A "Table Zoom" slider (50%–250%) in the toolbar applies a CSS `transform: scale()` to the transactions table.
- Ctrl+Scroll (or trackpad pinch) over the table panel zooms the table.
- Ctrl+Scroll (or trackpad pinch) over the PDF panel zooms the PDF.

**Sync Zoom**:
- A "Sync Zoom" checkbox links the PDF zoom and table zoom through a calibrated mapping instead of a strict 1:1 percentage.
- Calibration target currently matches observed visual parity in review sessions: approximately `PDF 100% ≈ Table 53%` and `PDF 250% ≈ Table 123%`.
- When checked, changing either zoom (via slider, Ctrl+Scroll, or pinch) updates both panels through this mapping.
- Ctrl+Scroll/pinch uses a reduced zoom step (25% of the original increment) and applies a short zoom guard to prevent sync-scroll handlers from reacting to zoom-induced layout changes.

## Firebase Auth Bundle

**Source**: `frontend/auth.js`
**Output**: `app/static/firebase-auth.js`
**Bundler**: [esbuild](https://esbuild.github.io/)

### Firebase Configuration

The Firebase config is hardcoded in `frontend/auth.js`:
```javascript
const firebaseConfig = {
  apiKey: "AIzaSyDI_iSjY-jJoGCif8YvNHfy7UqOP25Jj3c",
  authDomain: "fnb-pdf-to-excel-prod-491212.firebaseapp.com",
  projectId: "fnb-pdf-to-excel-prod-491212",
  // ...
};
```

### Auth Flow

1. User clicks "Sign in with Google" or enters email/password.
2. Firebase SDK authenticates the user and provides an ID token.
3. On `POST /extract` or `POST /extract/preview`, the browser attaches `Authorization: Bearer <ID token>` to the request headers.
4. Backend verifies the token (see [authentication.md](authentication.md)).

### Building the Bundle

From the `frontend/` directory:

```bash
npx esbuild auth.js --bundle --platform=browser --format=iife --outfile=../app/static/firebase-auth.js
```

This bundles the Firebase SDK and auth logic into a single IIFE that is loaded by `index.html` via `<script src="/static/firebase-auth.js">`.

### Dependencies

```json
{
  "dependencies": {
    "firebase": "^12.11.0"
  },
  "devDependencies": {
    "esbuild": "^0.27.4"
  }
}
```

## Static Assets

| File | Description |
|---|---|
| `app/static/style.css` | Shared CSS for both pages (layout, cards, buttons, inputs) |
| `app/static/firebase-auth.js` | Bundled Firebase Auth (do not edit directly — rebuild from `frontend/auth.js`) |

## Review UI — Highlight System

The review page implements a per-field highlight system across a multi-page scrollable PDF viewer:

1. All PDF pages are rendered into the scrollable `.pdf-canvas-wrapper` container. Each page has its own canvas and overlay stored in the `pageElements` array.
2. Each transaction cell stores its bounding box in a `data-bbox` attribute (JSON string). Each table row stores its `data-page-index`.
3. On `mouseenter` (when highlighting is enabled), the bbox is parsed and `drawHighlight(bbox, pageIndex)` is called.
4. `drawHighlight` creates an absolutely-positioned `<div class="pdf-highlight">` inside the target page's overlay, positioned using normalized bbox coordinates mapped to the canvas dimensions.
5. When scroll mode is `Auto`, `drawHighlight` scrolls to center the highlighted bbox (both vertical and horizontal axes). In `Sync` mode, scrolling is driven by bidirectional sync logic. In `None` mode, no auto-scrolling occurs.
6. On `mouseleave`, a 60ms debounce timer (`scheduleClear`) clears the highlight. Moving to an adjacent cell cancels the timer (`cancelClear`), preventing flicker.

### Coordinate System

All bounding boxes use **normalized coordinates** (0.0–1.0):
- `x_min`, `x_max`: horizontal position relative to page width.
- `y_min`, `y_max`: vertical position relative to page height.

The highlight overlay converts these to pixel positions using the canvas's `clientWidth` and `clientHeight`.

## Review Editing Workflow

The review screen keeps an in-memory mutable `transactions` array as the source of truth.

### Editing

- Double-click editable cells (`date`, `description`, `amount`, `balance`, `charges`) to enter inline edit mode.
- Numeric cells preserve display formatting in the editor and parse on commit.
- Enter/blur commits, Escape cancels.
- Unsaved changes are tracked and shown in the toolbar.

### Undo / Redo

- Action stacks (`undoStack` / `redoStack`) track up to 50 actions.
- Supports single-cell edits (field change) and bulk operations (row insert/delete).
- Bulk actions store the affected rows and their indices so undo can fully restore them.
- Keyboard shortcuts:
  - `Ctrl+Z`: undo
  - `Ctrl+Y` or `Ctrl+Shift+Z`: redo

### Save / Download

- Save sends `PUT /preview/data/{session_id}` with the edited transaction array.
- Download uses `GET /preview/download/{session_id}` to export the edited data to Excel.

### Review State Rules

- Users can toggle only between `blank` and `done`.
- `needs` is system-driven; users can clear it by setting a row to `done`.
- `needs_review` is kept in sync for backward compatibility (`needs` => `needs_review: true`).

### Column and panel resizing

- The table columns are user-resizable by dragging header-edge resizers.
- Double-clicking a column resizer auto-fits that column to its widest visible content.
- The PDF/table split is user-resizable via the vertical handle between panels.
- Column widths are fixed during edit mode so entering edit mode does not cause column expansion.

### Horizontal scrolling

- Both the PDF content wrapper and transactions table wrapper expose horizontal scrollbars.
- `scrollbar-gutter: stable both-edges` is applied so horizontal scrollbar space stays reserved and avoids layout jitter.

### Description wrapping behavior

- Parsed descriptions are rendered as normal wrapped text (`white-space: normal`).
- Explicit parser line breaks are flattened, so visible breaks occur only when text reaches cell width.

### Keyboard and gesture shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Z` | Undo |
| `Ctrl+Y` / `Ctrl+Shift+Z` | Redo |
| `Delete` (when rows selected) | Delete selected rows |
| `Escape` | Clear selection |
| `Ctrl+Scroll` over PDF | Zoom PDF in/out |
| `Ctrl+Scroll` over table | Zoom table in/out |
| Trackpad pinch over either panel | Same as Ctrl+Scroll (browser fires `wheel` with `ctrlKey`) |
