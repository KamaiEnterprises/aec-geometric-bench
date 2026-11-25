# The instructions the models received

Every model received the same instructions: a closed vocabulary of 23 class names, the conventions
the human annotators followed, a request for each element's real shape, and an instruction to
report what the drawing shows and nothing inferred. The files differ in how the sheet is
handed over and how the answer is returned, and in one more respect: Gemini 4 Argon is asked
for coordinates in its vendor's documented form, whole numbers on a 0 to 1000 grid, and its
file does not carry the sentence that asks the agents for decimals.

| file | system | how it was used |
|---|---|---|
| `agent-gpt.md` | GPT-6 Astra, GPT-6.1 Sol | the whole prompt of one agent session, followed by one line `Sheet id: <id>`; the answer had to satisfy `agent-gpt-output-schema.json` |
| `agent-claude.md` | Claude Opus 5.5 | read by the agent at the start of its session; the answer is a file `answer.json` in the working directory |
| `agent-claude-session-message.md` | Claude Opus 5.5 | the message that started each session and pointed the agent at `agent-claude.md` |
| `single-call-gemini.md` | Gemini 4 Argon | sent with the PDF in one request, with the parameters in `single-call-gemini-request.json`; coordinates in the vendor's documented order |

An agent session started in an otherwise empty working directory holding the one-page PDF as
`sheet.pdf`, with a Python environment carrying PyMuPDF, Pillow, NumPy and OpenCV, at high
reasoning effort, with no budget of turns, tokens or time (a two-hour ceiling on the GPT
sessions was never reached). Sessions were started by a script, one per sheet; the Claude
session message is that script's message, which is why it opens with a check for an existing
answer. `<PYTHON>` stands for the path of that Python, `<WORKDIR>` for the session's working
directory and `<the instructions file>` for the path of `agent-claude.md` on the test machine;
nothing else in these files was changed.

Two things the instructions do not say, and a reader should know: stairs and elevators are
described as regions, and are also counted as objects by the scorer; and `Opening`, which the
models are told not to emit, is an output class of Kamai Nov26 that the scorer credits when it
lies on an annotated window (see the top-level README, Scoring rules).

Coordinates in the instructions are on a 0 to 1000 scale of the displayed page. The files in
`scoring/predictions/` hold the same answers mapped into the pixel frame of each sheet in
`dataset/manifest.json`, which is what the scorer reads.
