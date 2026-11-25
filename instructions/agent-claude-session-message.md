One sheet of a blueprint element-extraction task.

STEP 0 — IDEMPOTENCY. Run exactly:
  test -s <WORKDIR>/answer.json && echo CACHED || echo MISSING
If CACHED, stop and return exactly "CACHED".

STEP 1. Read the task text at <the instructions file> and follow it exactly: its class vocabulary, its shape and
annotation conventions, its coordinate scale and its output JSON shape.

YOUR WORKING DIRECTORY: <WORKDIR>   (it holds sheet.pdf; write answer.json there; scratch files go there too)

STEP 2. Work on <WORKDIR>/sheet.pdf any way you like: read it, render it at any zoom, crop, extract text or
vector content, run code. A Python with PyMuPDF, Pillow, NumPy and OpenCV is at
<PYTHON>. Take the time the sheet needs; a sheet can hold several
hundred instances, and Wall is the largest class.

STEP 3. Write the JSON object to <WORKDIR>/answer.json.

THE ONLY SOURCE IS THIS PDF. Do not read, list or search any file or folder outside <WORKDIR>, except the
task text and that Python. Do not use the network. Do not start sub-agents.

RETURN one line: <n detections> | truncated=<bool> | <model_id> | <one short phrase on how you worked>
