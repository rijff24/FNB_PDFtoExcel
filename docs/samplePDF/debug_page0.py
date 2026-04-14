"""Debug script: inspect Document AI lines/tokens for page 0 of the sample PDF."""
import sys
sys.path.insert(0, ".")

from app.services.document_ai import _process_document
from pathlib import Path

pdf_bytes = Path("docs/samplePDF/FNB.pdf").read_bytes()
doc = _process_document(pdf_bytes)
page = doc.pages[0]

print(f"Page 0: {len(page.lines)} lines, {len(page.tokens)} tokens")
print()

print("=" * 120)
print("LINES (with normalized bbox):")
print("=" * 120)
for i, line in enumerate(page.lines):
    layout = line.layout
    ta = layout.text_anchor
    segs = ta.text_segments or []
    if not segs:
        continue
    start = int(segs[0].start_index or 0)
    end = int(segs[-1].end_index or 0)
    text = doc.text[start:end].strip()
    if not text:
        continue
    nv = layout.bounding_poly.normalized_vertices
    xs = [v.x for v in nv]
    ys = [v.y for v in nv]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    print(f"L{i:3d}  x=[{x0:.3f}-{x1:.3f}]  y=[{y0:.4f}-{y1:.4f}]  '{text}'")

print()
print("=" * 120)
print("TOKENS (first 120, with x-position):")
print("=" * 120)
for i, token in enumerate(page.tokens[:120]):
    layout = token.layout
    ta = layout.text_anchor
    segs = ta.text_segments or []
    if not segs:
        continue
    start = int(segs[0].start_index or 0)
    end = int(segs[-1].end_index or 0)
    text = doc.text[start:end].strip()
    nv = layout.bounding_poly.normalized_vertices
    xs = [v.x for v in nv]
    ys = [v.y for v in nv]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    print(f"T{i:3d}  x=[{x0:.3f}-{x1:.3f}]  y=[{y0:.4f}-{y1:.4f}]  '{text}'")
