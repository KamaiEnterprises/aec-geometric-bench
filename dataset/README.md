# AEC blueprint extraction — 15-sheet release

The benchmark was run over 312 construction sheets. Fifteen of them are released here
with their ground truth, so the results can be reproduced and other systems can be
measured on the same data.

Each sheet ships as two files:

* `pdf/sheet_NN.pdf` — the source drawing, redacted (see **Redaction**)
* an entry in `annotations_15.xml` — CVAT 1.1 export of the manual annotations,
  with window subtypes merged into `Window`

`annotations_15_scoring_ready.xml` is the same ground truth with the scoring rules already
applied, so it can be scored directly without reimplementing them.


Sheets are numbered `01`–`15` by annotated-object count ascending. The identifiers they
carried in our annotation system are not published.

## Redaction

— read this before using the PDFs

These are real construction documents. Everything **outside the annotated drawing region** has been
removed: title blocks, margin copyright strips, firm logos, professional seals and key plans. This is
done by construction rather than by searching for identifying strings, because firm names are
frequently drawn as **vector outlines** that carry no extractable text and would survive any
text-based scan.

In addition, for every sheet: all annotations are deleted (AutoCAD SHX annotations carry a second
machine-readable copy of outlined text), all link targets are deleted (they leak project and drawing
numbers), all document metadata is cleared (it names individuals), and each file is rewritten with
garbage collection so objects detached by redaction do not survive in the file.

Redaction is true content deletion via PyMuPDF, not a white rectangle drawn over the content.
**Page geometry is unchanged, so the CVAT coordinates in `annotations_15.xml` remain valid.**

## Licence

The drawings, the annotations and the overlays in this directory are released under
**CC BY-NC 4.0**. Share and adapt them for non-commercial purposes with attribution to
Kamai; commercial use requires separate permission (research@kamai.io). This is the same
licence CubiCasa5K and FloorPlanCAD carry.

The scoring code is Apache 2.0 and carries no such restriction, so a commercial system
can be evaluated against this data without the data licence attaching to the software.

Residual risk, stated plainly: no OCR was run over the raster underlays, so small text
baked into a scanned tile could survive. Room labels that remain are generic, but a
person who already knows a building might recognise it.

## Scoring

Objects are scored on **eight** classes — Single Swing Door, Double Swing Door, Window,
Sink, Toilet, Bathtub, Shower, Cooktops — over 1,632 instances at
IoU 0.50 with unique greedy matching and no confidence threshold.

Window is a single class, merged into a
single `Window` class **on both sides**. `Wall` and `Area` are scored as pixel masks,
`Area` additionally as instances (747 of them).

Object F1 is **pooled** across sheets, not averaged per sheet.

## Class semantics

Two classes cover more than their name suggests, on both the ground-truth and the
prediction side:

* **Wall** covers walls and railings.
* **Area** covers rooms, shafts, balconies, elevator cores and stairs, with touching
  instances merged into one polygon per space — annotators frequently drew a single
  space as several adjoining boxes.

## Results on this subset

| system | P | R | **F1** | wall px | area px | area inst |
|---|---|---|---|---|---|---|
| Kamai | 0.940 | 0.919 | **0.929** | 0.931 | 0.987 | 0.941 |
| Gemini 3.7 Flash | 0.074 | 0.020 | **0.031** | 0.238 | 0.797 | 0.077 |
| Gemini 3.1 Pro | 0.025 | 0.010 | **0.015** | 0.182 | 0.548 | 0.116 |
| Claude Opus 5 | 0.061 | 0.015 | **0.024** | 0.278 | 0.782 | 0.227 |
| Claude Fable 5 | 0.066 | 0.007 | **0.013** | 0.287 | 0.780 | 0.186 |
| GPT-5.6 Sol | 0.007 | 0.002 | **0.003** | 0.089 | 0.741 | 0.096 |

Per class, for the pipeline:

| class | GT | F1 |
|---|---|---|
| Single Swing Door | 629 | 0.957 |
| Double Swing Door | 49 | 0.913 |
| Window | 498 | 0.860 |
| Sink | 193 | 0.976 |
| Toilet | 105 | 0.976 |
| Bathtub | 76 | 0.967 |
| Shower | 26 | 0.857 |
| Cooktops | 56 | 1.000 |

Fifteen sheets is a small sample and the per-class cells rest on few instances.

## Files

```
pdf/sheet_01.pdf … sheet_15.pdf   15 redacted source PDFs
annotations_15.xml                CVAT 1.1 export: manual annotations,
                                  window subtypes merged
annotations_15_scoring_ready.xml  same, scoring rules pre-applied
taxonomy.json                     the frozen class taxonomy and its SHA-256
per_sheet_scores.json             per-sheet scores, every system, all 15 sheets
manifest.json                     per-sheet dimensions, shape counts, SHA-256
```

### What the extra classes are for

`Sliding Door`, `Closet Door` and `Shower Door` are human-annotated but not scored; they are
kept because they are real annotation, not because the benchmark uses them.

**Area** covers rooms, shafts, balconies, elevator cores and stairs on both sides: a
predicted `Balcony` counts as area exactly as an annotated one does.

Every page is scored in full. There is no masked-out region: a prediction anywhere on the
sheet is matched against ground truth like any other.

`taxonomy.json` carries the class map that was frozen before results were computed,
together with its SHA-256 — the hash quoted in the paper. To check it, remove the
`sha256` and `sha256_recipe` fields, serialise the rest as JSON with `sort_keys=True`
and separators `(",", ":")`, and hash that; hashing the file as delivered gives a
different digest. `per_sheet_scores.json`
gives object TP/FP/FN and F1 plus wall and area figures for every system on every
released sheet, so the subset table can be recomputed independently.

Note that the corpus figures in the paper are pooled over all 312 sheets and are **not**
the mean of these fifteen.
