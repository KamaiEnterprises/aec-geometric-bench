# Licence for the released drawings

The fifteen drawings in `pdf/`, the ground-truth annotations, the 300 dpi overlays
and the derived files in this directory are released under

**Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)**
https://creativecommons.org/licenses/by-nc/4.0/

You may share and adapt this material for non-commercial purposes, provided you
give appropriate credit to Kamai and indicate whether changes were made.
Commercial use requires separate permission: research@kamai.io.

This matches the licence carried by the comparable public floor-plan corpora,
CubiCasa5K and FloorPlanCAD.

The scoring code in `scoring/` is Apache 2.0 and carries no such restriction, so a
commercial system can be evaluated against this data without the licence on the
data attaching to the evaluation software.

## What has been removed from the drawings

These are real construction documents. Everything outside the annotated drawing
region has been removed by true redaction, meaning the content is deleted from the
file rather than covered over: title blocks, margin strips, firm logos,
professional seals and key plans go by construction rather than by searching for
identifying strings, because firm names are frequently drawn as vector outlines
that carry no extractable text.

In addition, for every sheet: annotations are deleted, link targets are deleted,
document metadata is cleared, and the PDF catalogue is stripped of bookmarks,
named destinations and optional-content group names, which in a CAD export carry
project numbers and site names even when the page pixels are clean.

Residual risk, stated plainly: no OCR was run over raster underlays, so small text
baked into a scanned tile could survive. Room labels that remain on the drawing
are generic (bedroom, kitchen) but a person who already knows a building might
recognise it.
