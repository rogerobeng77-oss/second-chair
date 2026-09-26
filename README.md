# Second Chair

A companion for the patient, or the family carer, at a medical appointment.

It announces itself to the clinician out loud, keeps that announcement as its first
record, and refuses to write a transcript until it has one. Afterwards it gives you a
recap in plain words, the medication changes as structured facts with the exact words
that were used, the questions you meant to ask and did not, and a narrower summary for
the relative who could not come.

It will not tell you what dose to take, whether to skip one, or whether a symptom is
serious. It quotes the room and cites, or it refuses.

Full design rationale, including the honest argument against the idea, is in
[SPEC.md](SPEC.md).

## Run it

Requires Python 3.11 or newer.

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./run.sh              # http://127.0.0.1:8412
```

Start at **`/replay`**. Press "Run the appointment" and watch the band across the top of
the page: it stays brass until the announcement has been recorded and an answer to it has
been recorded. Then change the dropdown to "No, I'd rather you didn't record this" and run
it again. Same code, other branch: everything is struck through and the consent record is
all that survives.

Running it there runs it for real, so **`/`** now holds the refused record — the consent
ledger and nothing else. Set the dropdown back to the answer the fixture holds, run it
once more, and go to **`/`** for the record itself. Try the ask bar at the bottom with
"should I stop taking the amlodipine?" to see the app refuse.

```bash
./venv/bin/python -m pytest        # 405 tests, 11 skipped, no AWS credentials needed
```

## The data is mocked, and here is exactly where

Every Bee developer path (`bee-cli`, `bee proxy`, the MCP server, the Bee Skill)
dead-ends at `bee login`, which needs the Bee iOS app with Developer Mode unlocked. We
have no device.

So **one thing is mocked: the HTTP call**. `MockBeeTransport` in `second_chair/bee.py`
answers the documented `/v1/*` routes with the documented shapes, reading them out of
`fixtures/consultation.json` and `fixtures/week.json`. `BeeClient` above it is a real
client: it builds paths, parses payloads and has no idea where the bytes came from.
Pointing Second Chair at a live Bee account is one line.

The mock answers the full documented set — `/v1/user`, `/v1/conversations`, the stream,
a conversation by id, and `GET`+`POST` on facts and on todos — but the product itself
calls three of those paths: the stream, and read-and-write on facts and on todos.
`week.json` exists because `collisions.py` reads facts from the days either side of the
appointment, and a fixture covering only the consultation would silently delete the
argument for using a wearable instead of a phone.

Two things about the fixture are deliberate and load-bearing:

- **Every speaker is `Unknown`**, which is what Bee's own sample output shows. Nothing in
  the application branches on the speaker field, and a test scrambles every label and
  asserts the output is identical.
- **Utterances are fragments, not sentences.** "and the water tablet, we'll leave that
  for now" is a whole event. That fragment is why there is an "unclear" medicine row.

Every fixture file carries a `_mock` key saying in prose that it is synthetic, and the UI
reprints it at the foot of every page. The consultation is invented: no real patient, no
real clinician, no audio.

## Amazon Bedrock

Region `us-east-1`, credentials from the environment. The model writes the recap and extracts the medicine
rows (Sonnet), and decides "covered" from "partly covered" on the question list (Opus).
On the ask surface it is allowed to choose which utterances to show and is never allowed
to write the answer, so the text on screen is copied out of storage by index.

Every call has a wall-clock deadline and one attempt. **Switch the model off in Settings,
or unplug the network, and every page still works.**

When Bedrock cannot be reached the recap section does not quietly degrade into a
rule-built paraphrase. It says, in as many words, that no recap was written and that
Second Chair will not assemble a summary of a medical appointment by rule and present it
as one, and then it shows the record itself in the words that were used, each line
quoted and cited. Every derived view says underneath it whether a model wrote it and
which one.

`us.anthropic.claude-sonnet-5` and `us.anthropic.claude-opus-5` are listed by both AWS
listing APIs and return `AccessDeniedException` on invoke. The client carries two
preference chains with those ids at the head, so the build upgrades itself when access
lands. Today, the writing path (recap, medicine extraction) falls through to
`us.anthropic.claude-sonnet-4-6` and `us.anthropic.claude-sonnet-4-5-20250929-v1:0`; the
judgement path (the covered/partly-covered call on the question list) falls through to
`us.anthropic.claude-opus-4-5-20251101-v1:0` first, then `us.anthropic.claude-sonnet-4-6`.

Environment variables (all optional):

| Variable | Default | Effect |
|---|---|---|
| `SECOND_CHAIR_REGION` | `us-east-1` | region Bedrock is called in |
| `SECOND_CHAIR_DEADLINE` | `25` (seconds) | wall-clock deadline on each Bedrock call |
| `PORT` | `8412` | port `run.sh` serves on |

## What is on each page

| Route | What it is |
|---|---|
| `/replay` | The demo. The consent gate running, live, with a refusal you can trigger. |
| `/` | The record: recap, medicines, questions, the week, the discard pile. |
| `/consent` | The consent ledger. The announcement, the answer, the statutes, what was thrown away and why, and the HIPAA problem it does not pretend to solve. |
| `/share` | The family summary. Derived rows only, no transcript, with an expiry. |
| `/history` | Search, the retention clock, the expiry sweep, and a delete that sticks. |
| `/refusals` | Everything the app declined to answer, kept where you can audit it. |
| `/settings` | Jurisdiction, retention, sharing, model tier. Four switches, all of which change behaviour. |
| `/export.txt` | The sheet to take back to the clinic and check against their summary. |

## Layout

```
second_chair/
  bee.py          the /v1/* boundary. The only mocked thing.
  consent.py      the gate. 632(b) and RCW 9.73.030(3) as code.
  guardrail.py    advice, attribution and citation guards.
  bedrock.py      model client: probe, deadline, fallback.
  recap.py        plain-language recap, cut down to what is supported.
  meds.py         medication rows, each verified against its quote.
  questions.py    what you meant to ask, reconciled against the room.
  collisions.py   the appointment against the rest of the week.
  relative.py     the narrower copy for the family.
  ask.py          question answering that only ever quotes.
  store.py        SQLite. Text only. Expires. Corrections. Refusal log.
  pipeline.py     one appointment, start to finish.
  web.py          FastAPI routes.
```

## Licence

MIT. See [LICENSE](LICENSE). Third-party font credits: see [`ATTRIBUTION.md`](ATTRIBUTION.md).
