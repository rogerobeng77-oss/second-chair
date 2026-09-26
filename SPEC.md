# Second Chair: the spec

## What it is

A companion for the patient, or the family carer, at a medical appointment. It announces
itself to the clinician out loud, stores that announcement as its first record, and only
then writes anything down. Afterwards it produces four things:

1. a recap in plain words, every sentence tied to the utterance it came from;
2. the medication changes as structured facts, each nailed to a verbatim quote;
3. the questions the patient meant to ask and did not;
4. a narrower summary for the relative who could not be there.

It is built around always-on, announce-first capture. That is the argument, and section
"Why a wearable" below is where it is defended rather than assumed.

## Who for

The person who walks out of a consulting room holding nothing. BRFSS 2022: 11.3% of US
adults aged 45+ report confusion or memory loss getting worse; 31.4% of them need help
with daily activities; only 44.3% have told a clinician. 27.0% of older-adult carers are
caring for someone with a cognitive impairment and 36.5% give twenty hours a week or
more. A 2024 study of 42 adults tested on 20 items after a 45-minute consultation found
mean recall of 75%; three of 42 got all twenty.

Plaud and Abridge both work this room and both point the output at the clinician. The
patient is the unserved party, and none of Bee, Limitless, Plaud, Omi or Abridge
publishes a bystander consent mechanism at all.

## Why a wearable, honestly

The research file scored this idea's "does the track technology carry it" test at 1 and
said a sceptical reader should call the whole thing a 7. That is correct and it is worth
stating plainly here rather than hoping a judge does not ask.

A phone on the table with a record button captures the same audio. Plaud sells a better
recorder for $159. The entire case for the wearable rests on one behavioural claim: that
a person whose defining problem is forgetting will also forget to press record. That
claim is an assertion. Nobody verified it. Across 1,500 recent r/ADHD posts the word
"consent" or "privacy" appears zero times and "recording" seven, so this audience has not
asked for a microphone either.

So Second Chair does two things about it instead of arguing.

**It makes always-on capture the product rather than a convenience.** There is no start
button anywhere in the application. The session is a Bee conversation identifier and a
time window, and the appointment is found inside a stream that was already running. The
consent gate then does the work a record button would have done, except that it happens
by speaking a sentence out loud, which is a thing a forgetful person does in front of
somebody who is waiting for them, rather than a thing they do alone with a device in
their pocket. A record button fails silently when you forget it. An announcement fails
loudly, in front of a witness, which is the only kind of failure a forgetful person
recovers from.

**It builds the one feature a recorder cannot have.** "From the rest of the week" sets
the appointment against `GET /v1/facts` from the days either side: the ibuprofen question
you wrote down and did not ask, against the Sunday conversation where you were recorded
saying you take it most days. A standalone recorder holds one room. Bee holds the week.
That section is the answer to "why not a phone", and it is code, not a claim.

If a judge does not accept the behavioural claim, the honest position is that this is a
7 with a very good consent story, not a 9. It is written in the spec so it does not have
to be discovered.

## Consent, in the one room where the statute offers nothing

Every escape the other settings rely on closes at a consulting room door.

California Penal Code 632(c) excludes a communication made in a public gathering, or
anywhere the parties may reasonably expect to be overheard or recorded. A lecture theatre
qualifies. A consulting room is the opposite: the expectation of not being overheard is
the reason the door is shut, and a clinician has a professional duty that assumes it. So
632 applies in full, with 637.2 pricing a violation at $5,000 with no actual damages
required.

That leaves one route, and it is narrow. 632(b) excludes from the definition of "person"
anyone "known by all parties to a confidential communication to be overhearing or
recording the communication". RCW 9.73.030(3) supplies the procedure: announce "in any
reasonably effective manner", provided "said announcement shall also be recorded". Make
the announcement, record it, and the recorder is not a person under 632. Fail to, and
there is nothing else to fall back on.

Not a settings page, not a disclaimer. `second_chair/consent.py` is the first module the
pipeline calls and nothing downstream runs without its permission.

### Which is why the gate has to work on a stream that was already running

The person this is for does not open an app before an appointment. That is the premise:
they are the person who forgets, which is the whole reason a wearable is defensible here
at all. So there is no start button anywhere in Second Chair, and there is no moment when
a session gets constructed with an announcement handed to it.

Consent has to be found inside capture that was already happening. `establish()` reads the
stream for the announcement, takes the utterance that follows as the response, and rules
on it. That is a harder problem than requiring an announcement up front, and it is where
most of the tests are.

The ruling has four outcomes:

- **Granted.** Announced, recorded, answered affirmatively. The transcript may be
  retained.
- **Refused.** Answered in the negative. Everything is destroyed.
- **Unanswered.** Announced, but no response was recorded. Everything is destroyed.
- **Pending.** Nothing was announced. No transcript may exist.

Three of the four keep nothing, and in every case the consent record itself survives,
because it is the proof that nothing else did.

**An ambiguous answer falls to refusal, on purpose.** "No, that's fine" comes out REFUSED.
The asymmetry is deliberate: being wrong in that direction costs the wearer one recording,
and being wrong in the other direction costs a bystander their consent and the wearer
$5,000 under 637.2. The refusal patterns are tested before the assent patterns for the
same reason, and each alternative carries its own word boundaries. A single trailing `\b`
on the group silently broke the most important case, because "No," ends in a comma, so a
plain refusal read as no answer at all. That shipped for about ten minutes and the test
that caught it is named in `consent.py`.

`/replay` runs the refusal branch live, and it is the first thing the demo shows.

### Scoping, without the thing the rule actually asks for

A session requires `starts_at_ms` and `ends_at_ms` at construction and raises if the end
is not after the start. There is no code path that opens an unbounded capture.

Beyond the window, scoping is by Bee `conversation_uuid`, which changes when Bee decides a
new conversation has started. The waiting room and the corridor arrive under different
uuids and are dropped without being read.

**That is weaker than it should be, and the app says so on `/consent` rather than only
here.** The honest version of this rule is "drop capture when the person you came to speak
to is not present", and the honest implementation is voice attribution. We cannot do that
and neither can Bee: every speaker label is `Unknown`. So the rule is implemented on the
one boundary the API actually gives, and the gap is stated on screen instead of being
papered over.

`Session.institution_policy` names the clinic's own visitor policy, shown on the consent
page. Second Chair claims a permission the building already grants to a patient bringing a
relative who takes notes. It does not invent a new one.

No audio file is written by any code path. Bee produces text and text is what is kept. A
voiceprint is a biometric identifier in Illinois and Texas, and nothing in the product
needs one.

Getting past 632 does not get you out of the room, either. The next two sections cover
what is still unsolved: who Second Chair will not name, and the HIPAA problem consent
cannot touch.

## Speaker-blindness

Bee's own `bee now` output labels every speaker `Unknown`. Verified platform research is
blunt about it: speaker attribution is what the hardware is worst at, and a product that
depends on it is demoing a capability the device does not have.

So Second Chair never says who said anything. Not in the recap, not in a medicine row,
not in the consent ledger. The fixture carries `Unknown` on every utterance, an
`attribution_guard` rejects any generated sentence naming a speaker, and a test replaces
every speaker label in the stream and asserts the output is byte-identical.

This turns out to be the right design anyway. The statutory test in 632(b) is whether the
recording was known to all parties, which the announcement and a recorded answer
evidence. Whose mouth the answer came from is a different question, and we do not have
the hardware to answer it.

## It must never become the record of truth

45 CFR 164.508(a)(1) means a wearable in a consulting room creates protected health
information on a consumer device outside the covered entity's control. That is a patient
authorisation problem on every encounter and a business associate problem on the
platform, and an app that claimed to solve it would be lying. The education agent that
looked at this reached the same conclusion from the other direction.

Second Chair cannot make the problem go away, so it refuses to become the thing that
matters:

- the transcript expires, the clock is on the front page, and `purge_expired` destroys
  the words while keeping the consent ledger;
- any medicine row can be overruled by what the clinic's printed summary says, and the
  correction is what exports;
- the export opens and closes with "the clinic's own summary is the one that counts";
- the family summary carries no transcript at all, only derived rows with their quotes;
- "destroy everything" destroys the ledger too, and the app does not quietly re-ingest
  from Bee afterwards. Most apps with a delete button do.

## It never gives advice

A published study found potential harms in 18% of model-generated discharge
instructions. So:

- `advice_guard` rejects generated prose that instructs, doses, reassures or judges
  severity. Verbatim quotes are exempt, because repeating what was said is the product.
- `check_question` refuses "should I", "is it safe", "how much do I take", "what happens
  if I skip", "is this serious" before anything is searched, and logs the refusal.
- The ask surface lets the model choose which utterances to show and never lets it write
  the answer. The text on screen is copied out of storage by index.
- Unasked questions are ordered by whether the patient ticked "important" themselves.
  Second Chair does not rank anyone's questions, because ranking the ibuprofen question
  above the holiday question is a clinical opinion.
- Every removed sentence is counted and shown, with the reason.

## Amazon Bedrock

Region `us-east-1`, credentials from the environment, no account id in the code. Used in three places where the model is the product:

- the plain-language recap (Sonnet);
- the medication extraction into structured rows (Sonnet);
- deciding "covered" from "partly covered" for the question list (Opus, where judgement
  matters).

And one place where it is explicitly not allowed to change an outcome: the ask surface,
where it ranks utterances and the quotes come from storage.

Every call has a wall-clock deadline enforced in `bedrock.py` (botocore only offers
per-socket timeouts) and one attempt with no retries. 179 tests pass with no AWS
credentials.

**Losing Bedrock is a stated outcome, not a quiet downgrade.** In a clinical setting a
half-written summary is worse than no summary, so the recap does not fall back to a
paraphrase assembled by rule. It says that no recap was written, says why, and shows the
record itself in the words that were used, quoted and cited line by line. The medicine
rows and the question list do fall back to rules, because those produce structured facts
tied to verbatim quotes rather than prose, and both say on screen that the model was not
involved.

**The build brief's model ids are not invocable.** `anthropic.claude-sonnet-5` and
`anthropic.claude-opus-5` are listed by both `ListFoundationModels` and
`ListInferenceProfiles` and return `AccessDeniedException`. `bedrock.py` holds a
preference chain with those ids first, so the build upgrades itself the day access
lands, falling through today to `us.anthropic.claude-sonnet-4-6` and
`us.anthropic.claude-sonnet-4-5-20250929-v1:0`. Friction log items 3 and 3b.

## The one screen that carries the demo

`/replay`. It steps through the consultation utterance by utterance at the pace it
arrived. The band across the top of the page is brass and says the gate is shut. The
waiting room goes past struck through. The announcement lands and pins itself. The answer
lands and the band turns verdigris. Then the appointment runs and the lines are kept.

Change the dropdown to "No, I'd rather you didn't record this", run it again, and the
same code takes the other branch: the band turns oxblood, every subsequent line is struck
through as discarded, and the consent record is the only thing left. Three seconds, one
screen, and it is the legal position executing rather than being described.

## Mocked data

Every Bee developer path dead-ends at `bee login`, which needs the iOS app. Second Chair
mocks exactly one thing: the HTTP call. `MockBeeTransport` answers the documented
`/v1/*` routes from `fixtures/*.json`; `BeeClient` above it is the real client and knows
nothing about where the bytes came from. Pointing at a live account is
`BeeClient(HttpTransport("http://127.0.0.1:8787"))` and no other change.

The fixture is one synthetic twelve-minute cardiology follow-up, hand-written in Bee's
wire shape with `Unknown` speakers and fragmentary utterances. Every fixture file carries
a `_mock` key in prose and the UI reproduces it on every page.

## Deliberately not built

- **No speaker identification, ever.** Building it means voiceprinting everyone the
  wearer meets, which is the Illinois BIPA and Texas CUBI bullseye, and it has no 632(b)
  defence, because the bystander is identified precisely because they do not know.
- **No drug information.** Policy section 13 requires documented affiliation with the
  manufacturer for dosage content. Second Chair quotes the room and cites; it never
  explains a medicine.
- **No symptom checker, no severity triage, no "you should ask about X".**
- **No audio retention, no playback, no export of a recording.**
- **No EHR write-back.** The clinic's record is the clinic's, and the whole design rests
  on this artefact being the lesser one.
- **No Alexa surface.** The verified platform constraints rule it out: no proactive
  channel, no speaker identity, health is policy section 13, and you cannot guarantee a
  quoted instruction survives into the spoken response.

## Visual direction

Checked against the palette and accent choices already in use across this project's
sibling apps; nothing here collides with one.

**Palette, in words.** An oatmeal ground with paper-white cards. Verdigris, a deep
blue-green, for everything the product is sure about. Brass for the consent seal and for
anything the recording did not make clear. Oxblood for a refusal. Near-black ink that
carries a little green. No sibling uses a deep teal accent or a desaturated warm-grey
ground.

**Shell.** Not a sidebar, not a top nav with a metrics strip, not a hero with cards, not
a left rail. A sealed band across the top that reports the consent state and stays there
while you scroll, then a single reading column hung off a thin vertical spine with a node
at each section. It is laid out like a printed document because it is one, and the
consent seal is welded to the top of it for the same reason a notarised page has a stamp
at the head rather than a footnote.

**Type.** A serif for the body, a grotesque for labels and controls. Every other app in
this hackathon will be sans-serif throughout. Body text is 18px with a 1.62 line height
and the reader is 71, on a phone, at a bus stop.
