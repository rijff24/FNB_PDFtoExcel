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
- Columns: `#`, `Date`, `Description`, `Amount`, `Balance`, `Charges`, `Review`.
- Rows with `review_state: "needs"` (or legacy `needs_review: true`) get a yellow background.
- Per-cell hover highlights: hovering a cell (date, description, amount, balance, charges) highlights that field's bounding box on the PDF.
- Row click highlights the entire row's bounding box.
- Debounced clear (60ms) prevents flicker when moving between cells.
- Editable spreadsheet behavior: double-click to edit, Enter/Escape/blur commit flow.
- Toolbar actions: Undo, Redo, Save, Download Excel, unsaved-changes indicator.
- Review column tri-state checkbox:
  - `blank`: empty checkbox
  - `needs`: orange warning checkbox and highlighted row
  - `done`: green checked checkbox

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

The review page implements a per-field highlight system:

1. Each transaction cell stores its bounding box in a `data-bbox` attribute (JSON string).
2. On `mouseenter`, the bbox is parsed and `drawHighlight(bbox)` is called.
3. `drawHighlight` creates an absolutely-positioned `<div class="pdf-highlight">` inside `#pdfOverlay`, positioned using the normalized bbox coordinates mapped to the canvas dimensions.
4. The PDF viewer scrolls to center the highlighted region.
5. On `mouseleave`, a 60ms debounce timer (`scheduleClear`) clears the highlight. Moving to an adjacent cell cancels the timer (`cancelClear`), preventing flicker.

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
- The PDF/table split is user-resizable via the vertical handle between panels.
- Column widths are fixed during edit mode so entering edit mode does not cause column expansion.
