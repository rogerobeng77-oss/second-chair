# Friction log: Second Chair

Written as it happened, in order. Severity: 1 trivial, 2 annoying, 3 cost real time,
4 changed the design, 5 blocked the build.

---

## 1. Every Bee developer path dead-ends at `bee login`

- **When**: start of build, before any code.
- **Task**: get real `/v1/*` responses to build against.
- **Steps**: read `https://docs.bee.computer/`. Four entry points are documented:
  `bee-cli` (`npm i -g @beeai/cli`), `bee proxy` (local HTTP API on
  `127.0.0.1:8787` serving `/v1/*`), a stdio and HTTP MCP server, and the Bee Skill.
- **Expected**: at least one of the four to have a read-only or sample mode, or a
  documented fixture set, so a developer without hardware can see the response shape.
- **Actual**: all four require `bee login`, which requires the Bee iOS app with
  Developer Mode unlocked by tapping the version number five times. No device here, no
  iOS. There is no published OpenAPI document, no sample payload file, and no
  `--offline` or `--replay` flag on the CLI.
- **Severity**: 5. This is the whole data source.
- **Workaround**: mock strictly at the `/v1/*` boundary (`second_chair/bee.py`), using
  the response shapes the docs show, so the pipeline still exercises the real client
  contract. Swapping in an authenticated client is a one-file change.
- **Suggestion**: ship a `bee replay <fixture.json>` mode in `bee-cli`, and publish one
  real (scrubbed) `/v1/conversations` and one `new-utterance` payload in the docs. Right
  now a developer's first honest look at the wire format is a screenshot of `bee now` on
  the docs home page.

## 2. The docs show `Unknown` speakers but never say that is normal

- **When**: writing the fixture.
- **Task**: decide whether to build anything on speaker identity.
- **Expected**: a documented speaker model: enrolled voices, a `speaker_id`, a
  confidence score, something.
- **Actual**: the `bee now` sample output on Bee's own docs home page labels speakers
  `Unknown` with fragmentary utterances. There is no page explaining whether that is a
  cold-start state, a permanent limitation, or a privacy default. A developer could
  easily read it as "it will learn the voices" and build a product on attribution that
  never works, and a clinical room is exactly where that would matter most: a patient,
  a family carer and a clinician can all be talking within the same few seconds, and a
  product that silently guessed which one said "double the dose" would be worse than one
  that never tried.
- **Severity**: 4. It changed the design. Second Chair is now speaker-blind on purpose
  (see `SPEC.md`, "Speaker-blindness"), and never says who said a thing.
- **Workaround**: treat `speaker` as an opaque, untrusted string and route no decision
  through it. A test asserts the pipeline produces identical output when every speaker
  label is replaced with `Unknown`.
- **Suggestion**: one paragraph in the docs stating plainly what `speaker` can and
  cannot be relied on for. It would save every developer on this track a wrong turn.

## 3. Bedrock lists models it will not let you invoke, and strips your prefix in the error

- **When**: first Bedrock call.
- **Task**: call `anthropic.claude-sonnet-5`, which the build brief said was verified
  live in `us-east-1`.
- **Steps**:
  - `aws bedrock list-foundation-models --region us-east-1` lists
    `anthropic.claude-sonnet-5` and `anthropic.claude-opus-5`.
  - `aws bedrock list-inference-profiles --region us-east-1` also lists
    `us.anthropic.claude-sonnet-5`, `global.anthropic.claude-sonnet-5`,
    `us.anthropic.claude-opus-5`.
  - `aws bedrock-runtime invoke-model --model-id anthropic.claude-sonnet-5` →
    `AccessDeniedException: anthropic.claude-sonnet-5 is not available for this
    account.`
  - Same for `us.` and `global.` prefixed profile IDs, for both Sonnet 5 and Opus 5.
- **Expected**: a model that appears in *both* listing APIs to be invocable, or at
  minimum for the listings to carry an entitlement flag.
- **Actual**: two separate listing endpoints advertise an entitlement that does not
  exist. Worse, when you invoke `us.anthropic.claude-sonnet-5` the error message reads
  `anthropic.claude-sonnet-5 is not available`. It silently drops the `us.` prefix you
  typed, so it looks as if the CLI ignored your argument and you go round the loop again
  with the profile ARN.
- **Severity**: 3. About twenty minutes, most of it doubting the prefix rather than the
  entitlement.
- **Workaround**: `second_chair/bedrock.py` holds two preference chains rather than a
  constant, one per tier, and caches the first model in each that answers. `FAST_MODELS`
  (the writing path: recap, medicine rows, question list) tries `sonnet-5` (unentitled),
  then `sonnet-4-6`, `sonnet-4-5-20250929-v1:0` (about 1.5s), `haiku-4-5-20251001-v1:0`.
  `JUDGEMENT_MODELS` (the harder question-list reasoning) tries `opus-5` (unentitled),
  then `opus-4-5-20251101-v1:0`, then falls back into `sonnet-4-6`. Both unentitled `-5`
  ids stay at the head of their chain on purpose, so the build upgrades itself the day
  access lands with no code change; the cost is one failed probe per process, which is why
  `web.App` keeps one client per tier instead of building a fresh one per call. Verified by
  invocation on 2026-09-22: every id above answers except the two named `-5` with no date
  suffix, which both return `AccessDeniedException` regardless of chain.
- **Postscript**: the coordinator hit this independently and corrected the build brief
  while this app was being written, having checked the listing and never called one. Two
  people made the same mistake from the same listing output on the same day, which is
  about as clear a signal as an API gets that the listing is the problem.
- **Suggestion**: add an entitlement field to `ListFoundationModels`
  (`modelLifecycle.status` already exists and does not cover this), and echo the model
  ID the caller actually sent in the `AccessDeniedException` message.

## 3b. The account number ended up in source because the docs put it there

- **When**: a secrets scan on the repository, after the code was written.
- **What happened**: the AWS account id was in the module docstring of `bedrock.py` and
  in two markdown files, copied from the brief that verified the setup. Nothing needed
  it: boto3 resolves credentials from the environment and the client takes a region.
- **Severity**: 2, and it would have been worse in a public repository.
- **Workaround**: removed, and `tests/test_bedrock.py` now greps the module's own source
  for a twelve-digit number so it cannot come back.
- **Suggestion**: for us, not AWS. Any snippet that documents "Bedrock is working" should
  show a region and never an account, because that snippet is what gets pasted into a
  docstring.

## 4. `anthropic.claude-haiku-4-5-20251001-v1:0` is a ValidationException, not an AccessDenied

- **When**: same session as #3.
- **Task**: work out which IDs need the `us.` inference-profile prefix.
- **Actual**: the bare model ID returns `ValidationException` ("on-demand throughput
  isn't supported"), while an unentitled model returns `AccessDeniedException`. Two
  different failure classes for what a developer experiences as the same question
  ("what do I type here?"), and neither error names the profile ID that would work.
- **Severity**: 2.
- **Workaround**: probe both forms.
- **Suggestion**: when on-demand is unsupported, name the inference profile ID in the
  error. Bedrock already knows it.

## 5. `bedrock-runtime` has no per-request deadline, only socket timeouts

- **When**: adding the model call to the recap path.
- **Task**: guarantee the demo never hangs in front of a judge.
- **Expected**: a request-level deadline parameter.
- **Actual**: `botocore.config.Config` gives `connect_timeout`, `read_timeout` and
  `retries`. With streaming off and a long generation, `read_timeout` is per-read, so a
  slow model that keeps dribbling bytes can exceed any wall clock you had in mind. There
  is no `total_timeout`.
- **Severity**: 2.
- **Workaround**: `connect_timeout=4`, `read_timeout=25`, `retries={"max_attempts": 1}`,
  plus our own wall-clock guard in `bedrock.py` that runs the call on a worker thread and
  abandons it. Every derived view has a deterministic non-model fallback, so losing
  Bedrock degrades the output rather than the demo.
- **Suggestion**: a `total_timeout` in `botocore.config.Config`, or an explicit
  statement in the Bedrock runtime docs that callers must impose their own.

## 6. FastAPI + Jinja2: `TemplateResponse` argument order changed and the deprecation is silent in tests

- **When**: wiring the first page.
- **Actual**: `TemplateResponse(name, {"request": request, ...})` still works but is
  deprecated in favour of `TemplateResponse(request, name, {...})`. Under `pytest` with
  default warning filters the `DeprecationWarning` is swallowed, so the code looks fine
  until a future upgrade removes it.
- **Severity**: 1.
- **Workaround**: use the request-first form everywhere; `pytest.ini` sets
  `filterwarnings = error::DeprecationWarning` for our own modules so this class of
  thing fails loudly next time.
- **Suggestion**: Starlette could emit the warning at import of the deprecated form, not
  only at call time.

## 7. Nothing in the Bee data model carries consent, so we had to invent the record

- **When**: designing the consent ledger.
- **Task**: store the announcement and the response as Bee objects.
- **Expected**: some notion of a bystander, a participant, or a capture permission in
  `/v1/*`.
- **Actual**: the resources are `user`, `changes`, `facts`, `todos`, `journals`,
  `conversations`, `daily summaries`. None of them models anybody other than the wearer.
  Bee's own privacy notice has no bystander section at all. So the consent record is a
  Bee **fact** with a reserved text prefix, plus our own ledger table, because a fact is
  the only durable typed object available.
- **Severity**: 4. It is the reason `consent.py` owns its own storage rather than
  delegating to Bee.
- **Workaround**: dual-write. The ledger is authoritative locally; the fact is what a
  real Bee account would see. What the ledger has to hold is not generic "consent", it is
  the specific two-part record California Penal Code 632(b) and Washington RCW
  9.73.030(3) actually ask for: an announcement, and that the announcement itself was
  recorded. A fact object can carry the words; nothing in Bee's model marks one as *that*
  kind of record, so the ledger's schema, not Bee's, is what a disability-services officer
  or a judge would actually need to read.
- **Suggestion**: a first-class `participants` or `capture_consent` resource on `/v1/*`,
  typed so an announcement-and-response pair is distinguishable from an ordinary fact.
  Every all-party-consent state makes this a legal requirement rather than a nicety, and
  right now every developer on the platform has to build it from scratch and most will
  not.

## 8. Give a model a free-text field next to a medicine and it will write a prescription in it

- **When**: looking at the finished medicines panel in a browser, not at a test.
- **Task**: have the model return structured medication rows with an optional short
  practical note, and only that. The prompt said so in as many words: "'note' is at most
  one short sentence, and only for something practical that was said out loud, such as
  where to get the tablets."
- **Expected**: an empty string on most rows.
- **Actual**: `us.anthropic.claude-sonnet-4-6` returned, beside a real drug name,
  "Stop immediately, no taper needed." and "Take half a five milligram tablet until
  current box runs out, then pharmacy will supply two point five milligram strength."
  Both are faithful to the transcript and both are generated dosing instructions on a
  screen a patient reads. Every other field in the row was verified against a verbatim
  quote; this one was prose, so nothing checked it.
- **Severity**: 4. It is the exact failure the 18% discharge-instructions finding
  describes, and it got through three guards because the guards ran over the recap and
  not over a field nobody thought of as text.
- **Workaround**: the field is no longer read from the model at all. The prompt now says
  there is no notes field. `verify()` additionally strips any note that opens with an
  imperative or trips the advice or attribution guard, and eight tests pin it.
- **Suggestion**: for anyone building this shape. An optional free-text field in a
  structured extraction schema is an unguarded output channel, and it will be the one
  that hurts somebody. Either verify it like every other field or do not have it. Looking
  at the rendered page rather than the passing test suite is what found this.

## 9a. `run.sh` documents an off-switch that does not exist in the code

- **When**: audit pass, following `run.sh` and the README as a judge would rather than
  trusting that they match the settings module.
- **Task**: turn Bedrock off to check the app still renders every page from its written
  fallbacks.
- **Steps**: `run.sh:2` says, in a comment, to set `SECOND_CHAIR_TIER=off`. Exported it,
  ran `./run.sh`, watched the settings page and a recap render.
- **Expected**: the model off, every page rendered from written fallbacks, the same as
  running with `AWS_*` unset.
- **Actual**: the model stayed on. `SECOND_CHAIR_TIER` does not exist anywhere in
  `second_chair/`, checked with `grep -rn SECOND_CHAIR_TIER second_chair/` (no hits). The
  real control is a `model_tier` field in `second_chair/settings.py`
  (`off | fast | judgement`), set through the `/settings` form and read from there, with
  no environment-variable path to it at all. A developer, or a judge, who exports the
  variable the comment names and expects the model off gets the model on, silently,
  because there is no such variable to typo and nothing errors.
- **Severity**: 3. Nothing was unsafe — every model output still goes through the guards
  in #7 and #8 regardless of tier — but it is exactly the kind of gap that costs
  confidence in a submission that leads with how carefully it handles exactly this class
  of thing.
- **Workaround**: fixed the comment rather than the code, since the form-based control is
  the right shape for a setting a household actually needs to change at runtime, not
  something to script around. `run.sh:2` now reads "Bedrock is optional: set the model
  tier to `off` on the `/settings` page, or unplug the network, and every page still
  renders from the written fallbacks" — both of which are true and one of which was
  already tested by #5's wall-clock guard.
- **Suggestion**: for us, not Amazon. Worth recording because it is a documentation defect
  with a specific, reproducible shape — a comment naming an environment variable that was
  probably true in an earlier draft of the settings module and never updated when the
  control moved to a form. Grep every `README`/`run.sh` reference to an env var against
  the actual source before a submission, the same way #3b's secrets scan checked for
  account numbers.

## 9b. Following the platform's own consent research cost the free-text field, not the announcement

- **When**: design, before any code, re-read at the end against `research/PLATFORM-FACTS.md`'s Bee section on what makes a consent record legally load-bearing rather than decorative.
- **Task**: decide what "announces itself to the clinician out loud, keeps that
  announcement as its first record" actually has to do, given the research names five
  concrete design rules for exactly this shape of product: room-scoped not life-scoped,
  announce and log the announcement, discard segments with no expected speaker present,
  sit inside an institution's existing policy rather than inventing one, keep text, not
  audio.
- **What following them cost.** Room-scoped meant `startSession()`/`endSession()` had to
  be a real, user-triggered boundary rather than an always-on stream, which #2's
  speaker-blindness already forced structurally. Keeping text, not audio, meant the
  consent record itself is the transcript line and its timestamp, never a clip, which
  closes off the one form of evidence that would be hardest for anyone to dispute later —
  a real cost, accepted on purpose, and stated as such in `SPEC.md`. Discarding segments
  with no expected speaker present is the rule #8's free-text-field failure indirectly
  broke: a "note" field with no speaker check let a generated dosing instruction stand
  next to a drug name as if a clinician had said it, which is precisely the class of harm
  the discard-if-unexpected rule exists to prevent, and the fix in #8 (delete the field
  rather than guard it harder) is the discard rule applied one level down, to a field
  instead of a segment.
- **Severity**: not a bug — recorded because it is the honest answer to "did the legal
  research actually change the build", and the answer is yes, specifically, in ways that
  cost real features rather than just informing a privacy paragraph.
- **Suggestion**: none for Amazon; this is Bee's own absence of a bystander/consent
  resource (#7) forcing the cost onto the integrator rather than the platform.

## 10. `moto` does not stub `bedrock-runtime` `invoke-model` usefully

- **When**: writing the test that must pass with the model stubbed.
- **Actual**: the obvious route (stub at the AWS layer) is more machinery than the test
  is worth, and it still would not exercise our timeout guard.
- **Severity**: 1.
- **Workaround**: `bedrock.py` takes an injectable `invoke` callable. Tests pass a fake
  that returns canned JSON, raises, or sleeps past the deadline. All three paths are
  covered and no AWS credentials are needed to run the suite.
- **Suggestion**: none for AWS. This is a note to whoever reads the tests: the seam is
  deliberate.

## 11. The nearest comparable hardware stopped shipping while this was being built

- **When**: writing `SPEC.md`'s comparison of Bee against Limitless, Plaud, Omi and
  Abridge, then checking whether that list was still current before submission.
- **Task**: understand what a judge would already know about always-on wearable
  recorders, since Second Chair's whole pitch depends on the category being a real one.
- **Actual**: Limitless, the closest comparison point to Bee and the one most people who
  have heard of this category will have heard of, stopped selling its Pendant on
  5 December 2025 and withdrew the service from Brazil, China, the EU, Israel, South
  Korea, Turkey and the UK. It was then acquired by Meta, with no reason published on
  either company's side, and user data was deleted after 19 December 2025. Verified
  directly on Limitless's own FAQ, not a news summary.
- **Severity**: not a build blocker, but worth stating plainly: a judge who knows this
  category at all is as likely to have heard "Limitless shut down" as anything else
  about it, and a submission that leans on the comparison without acknowledging it reads
  as either unaware or hoping nobody checks.
- **Workaround**: none needed in the code, since Second Chair never assumes the hardware
  will still exist next year. It stores text and a hash chain, not audio, and every page
  degrades to written fallbacks with the network off (#5, #9a); nothing about the design
  depends on Bee, specifically, still shipping. That was a design choice made for other
  reasons and it happens to also be the right hedge against exactly this.
- **Suggestion**: none for AWS or Bee. Recorded so the next person reading `SPEC.md`'s
  competitor list does not have to independently discover that one of the four names in
  it no longer sells the product being compared.
