# AEC-Geometric-Bench-15: the fifteen released sheets

The benchmark was run over 312 construction sheets. Fifteen of them are released here
with their ground truth, so the results can be reproduced and other systems can be
measured on the same data.

Each sheet ships as two files:

* `pdf/sheet_NN.pdf`, the source drawing, redacted (see **Redaction**)
* an entry in `annotations_15.xml`, the CVAT 1.1 export of the manual annotations,
  with window subtypes merged into `Window`

`annotations_15_scoring_ready.xml` is the same ground truth with the class rules already
applied, so it can be scored directly without reimplementing them.

Sheets keep the numbers `01` to `15` of the first release, which ordered them by the object
count of the annotation as first exported; the counts scored now differ slightly. The
identifiers the sheets carried in our annotation system are not published.

## Redaction

Read this before using the PDFs.

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

The drawings and the annotations in this directory are released under
**CC BY-NC 4.0**. Share and adapt them for non-commercial purposes with attribution to
Kamai; commercial use requires separate permission (research@kamai.io). This is the same
licence CubiCasa5K and FloorPlanCAD carry.

The scoring code is Apache 2.0 and carries no such restriction, so a commercial system
can be evaluated against this data without the data licence attaching to the software.

Residual risk, stated plainly: no OCR was run over the raster underlays, so small text
baked into a scanned tile could survive. Room labels that remain are generic, but a
person who already knows a building might recognise it.

## Scoring

Objects are scored on **ten** classes (Single Swing Door, Double Swing Door, Window,
Sink, Toilet, Bathtub, Shower, Cooktops, Elevator, Stairs) over 1,657 instances
on these fifteen sheets, with one-to-one matching and no confidence threshold. An object
is found when a delivered object of its class lands on it; the exact rules are in the
top-level README.

Window is a single class on both sides. `Wall` and `Area` are scored as pixel masks,
`Area` additionally as instances (860 of them after a stairs or elevator
shape is merged with the area it touches).

`annotation_review.json` lists the 22 annotation fixes, the 2 added windows and the 31
not-scored boxes that the scorer applies on top of `annotations_15.xml`. The export itself
is not edited.

## Class semantics

Two classes cover more than their name suggests, on both the ground-truth and the
prediction side:

* **Wall** covers walls and railings.
* **Area** covers rooms, shafts, balconies, elevator cores and stairs: a predicted
  `Balcony` counts as area exactly as an annotated one does. A stairs or elevator shape
  that touches another area is merged with it; ordinary rooms are never merged with each
  other.

`Sliding Door`, `Closet Door` and `Shower Door` are human-annotated but not scored; they
are kept because they are real annotation, not because the benchmark uses them.

Every page is scored in full. The only exception is the short list of not-scored boxes in
`annotation_review.json`, where an unmatched delivered object is neither found nor false.

## Results on this subset

| system | P | R | **F1** | wall px | area px | area inst |
|---|---|---|---|---|---|---|
| Kamai Nov26 | 0.962 | 0.959 | **0.961** | 0.947 | 0.981 | 0.937 |
| Kamai Aug26 | 0.944 | 0.935 | **0.940** | 0.931 | 0.987 | 0.898 |
| GPT-6 Astra | 0.892 | 0.864 | **0.877** | 0.837 | 0.945 | 0.844 |
| GPT-6.1 Sol | 0.906 | 0.865 | **0.885** | 0.824 | 0.936 | 0.891 |
| Claude Opus 5.5 | 0.902 | 0.886 | **0.894** | 0.862 | 0.928 | 0.847 |
| Gemini 4 Argon | 0.369 | 0.325 | **0.346** | 0.480 | 0.880 | 0.484 |

Object F1 per class:

| class | annotated | Kamai Nov26 | Kamai Aug26 | GPT-6 Astra | GPT-6.1 Sol | Claude Opus 5.5 | Gemini 4 Argon |
|---|---|---|---|---|---|---|---|
| Single Swing Door | 629 | 0.959 | 0.957 | 0.909 | 0.942 | 0.947 | 0.286 |
| Double Swing Door | 49 | 0.925 | 0.913 | 0.564 | 0.371 | 0.634 | 0.043 |
| Window | 479 | 0.954 | 0.884 | 0.828 | 0.842 | 0.807 | 0.534 |
| Sink | 193 | 0.974 | 0.987 | 0.946 | 0.938 | 0.927 | 0.172 |
| Toilet | 105 | 0.991 | 0.995 | 0.972 | 0.953 | 0.995 | 0.203 |
| Bathtub | 76 | 0.966 | 0.967 | 0.861 | 0.806 | 0.920 | 0.207 |
| Shower | 26 | 0.923 | 0.857 | 0.839 | 0.722 | 0.877 | 0.500 |
| Cooktops | 56 | 0.982 | 1.000 | 0.982 | 0.965 | 1.000 | 0.443 |
| Elevator | 7 | 0.833 | not supported | 0.857 | 0.857 | 0.857 | 0.714 |
| Stairs | 37 | 0.986 | not supported | 0.630 | 0.708 | 0.750 | 0.522 |

Fifteen sheets is a small sample and the per-class cells rest on few instances. The corpus
figures in the paper are pooled over all 312 sheets and are **not** the mean of these
fifteen.

## Files

```
pdf/sheet_01.pdf ... sheet_15.pdf   15 redacted source PDFs
annotations_15.xml                  CVAT 1.1 export: manual annotations, window subtypes merged
annotations_15_scoring_ready.xml    the same, class rules pre-applied
annotation_review.json              the review ledger the scorer applies
taxonomy.json                       the frozen class taxonomy and its SHA-256
per_sheet_scores.json               per-sheet scores, every system, all 15 sheets
time_and_price.json                 minutes and price per sheet, every system
manifest.json                       per-sheet dimensions, shape counts, SHA-256
```

`taxonomy.json` carries the class map that was frozen in August, before results were
computed, together with its SHA-256, the hash quoted in the paper. To check it, remove the
`sha256` and `sha256_recipe` fields, serialise the rest as JSON with `sort_keys=True`
and separators `(",", ":")`, and hash that; hashing the file as delivered gives a
different digest.

`per_sheet_scores.json` gives object TP/FP/FN and F1 plus wall and area figures for every
system on every released sheet. It is what `scoring/score.py` produces from the six folders
in `scoring/predictions/`, so the subset table can be recomputed independently.

The models were run on the original files, the same files Kamai received, not on the
redacted PDFs in `pdf/`. Page geometry is identical, so their answers are in the same frame.
