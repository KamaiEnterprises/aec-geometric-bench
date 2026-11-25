# Blueprint element extraction — one sheet

You are extracting architectural elements from ONE construction drawing. Your output is scored
as F1 against human annotations. Other systems get the same sheet and the same instructions.

## The sheet

The drawing is the file `sheet.pdf` in your working directory, a one-page PDF. Use it any way you
like: view it, render it at any zoom, crop it, extract its text or its vector content, run code on
it. A Python with PyMuPDF, Pillow, NumPy and OpenCV is at `<PYTHON>`.
Any tool is allowed. The only source is this PDF: do not read other files on this machine (outside
your working directory and that Python) and do not use the network.

## Class vocabulary — exactly these 23 strings, character-for-character

```
Wall                Room                Single Swing Door   Double Swing Door
Sliding Door        Closet Door         Shower Door         Casement Window
Sliding Window      Swing Window        Double Window       Safe Room Window
Sink                Toilet              Bathtub             Shower
Cooktops            Stairs              Elevator            Balcony
Railing             Shaft               Urinals
```

Note `Cooktops` is plural. Anything off this list is counted against you. Do not emit `Opening`.

## Shapes — give each element the shape it really has

Every element carries exactly one of two shapes:

- `bbox`: `[x_min, y_min, x_max, y_max]`, for an element that is an upright rectangle on the page.
- `polygon`: `[[x, y], [x, y], ...]`, the element's outline, at least three points in order around
  it, for anything that is not an upright rectangle: a room, an angled wall, a rotated door.

Coordinates are on a 0-1000 scale of the page as it is displayed (page rotation applied): x from
the left edge, y from the TOP edge. Use decimals (`412.6`), since a wall can be thinner than one unit.

## Conventions (how the humans annotated — follow these)

- **Room** — one polygon per enclosed space, following the inside faces of its walls, so an
  L-shaped room is an L. Include **small rooms, WC stalls and shower cubicles**. A typical sheet
  has dozens; most are small.
- **Balcony, Shaft, Elevator, Stairs** — the outline of the region, as a polygon (a `bbox` when it
  is an upright rectangle).
- **Wall** — the body of each wall, between its two faces. A straight wall run can be one long
  rectangle or several; an angled or curved wall is a polygon. Scored as a pixel mask, so coverage
  matters and how you split a wall does not. It is the largest class. **Railing** the same way.
- **Windows** — **per individual window unit**, not per glazed run: a long facade of glazing is
  many instances. The shape covers that unit within the wall. A unit is typically 15-35 PDF points
  across; a typical sheet has about 24.
- **Doors** — per door leaf assembly. Single Swing is roughly square (the leaf plus its arc);
  Double Swing is wider; Sliding Door is a thin elongated shape. A door in an angled wall gets a
  four-point polygon.
- **Fixtures** (Sink, Toilet, Bathtub, Shower, Cooktops, Urinals) — the symbol's extent.

## The rule that matters most: report what the drawing shows

If you believe something must be present by architectural reasoning but cannot actually find it
on the drawing, **do not emit it** — put it in `notes` instead. Do not infer fixtures from room
type, windows from facade logic, or doors from circulation logic.

Equally: do not pad the output and do not stop early. A sheet can hold several hundred elements,
and all of them count.

Window subtypes are scored as one class. If you can see a window, report it: use the subtype
when you can tell it and `Casement Window` when you cannot.

## Output

Return every element as `{"class": ..., "bbox": [...] or null, "polygon": [[x, y], ...] or null}`,
with exactly one of `bbox` and `polygon` set. Set `truncated` to true if you stopped before listing
everything on the sheet, and use `notes` for what you could not resolve.

## What these pages are like

Real construction sheets, mostly ISO A-series, predominantly Israeli/European, text often in
Hebrew. Human annotators marked a median of 305 instances per sheet.
