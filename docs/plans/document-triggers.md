# Plan: agents that start on document events

Status: proposal, not implemented. Nothing in this page exists in a release yet.
Written against `main` at `9fd06e4` (v0.6.1).

## The problem

Today a run starts only when a person starts it: Agent Chat, or the Ask Agent
panel on a document. Many useful jobs start with an event instead. A scan is
attached to a record, and somebody has to type what the scan says into the
record's fields.

The first use case this plan is written for:

> When a document record is saved with a scan in its file field, and the record
> is still waiting for verification, read the scan and fill the record's empty
> fields (number, dates, name, nationality) for a person to check.

The app must stay generic. Nothing here may name a specific HR app, doctype or
field. A site describes its own case in a trigger record.

## What this plan keeps

These are the guarantees in [How the agent is held to account](../governance.md).
A trigger must not weaken any of them:

- Every run is bound to a real user and never exceeds that user's rights.
- Agents draft, humans submit. A trigger adds no path to submit, cancel or delete.
- Every call writes an audit row, on success, denial and error.
- Document text is data, never instructions.
- The kill switch stops a run that is already going.

A trigger adds one new risk that chat does not have: **nobody is watching when
it runs.** In chat, a person sees every call and can press Stop. A trigger fires
on a save, maybe hundreds of times during a bulk upload, maybe at night. So
most of this plan is about the limits that replace the person watching.

## The shape: a fill action, not a free agent loop

There are two ways a trigger could do the job.

**A. Start an ordinary agent run with a prompt.** The model gets tools
(`read_document`, `update_draft`) and decides what to do.

**B. Run one extraction into the record that fired.** No tool loop. One model
call with a schema built from a fixed list of fields, through the extraction
pipeline that already exists.

This plan recommends **B for the first release**, for four reasons:

1. **The scan never sits in a context that holds a tool.** In A, a hostile page
   is transcribed and handed back into a loop that can call `update_draft`. In
   B, the worst a hostile page can do is put a wrong value into a field the
   trigger already allowed, for a person to catch.
2. **`update_draft` has no sensitive-field gate.** It strips system fields, but
   it writes any other field it is given, sensitive or not. The gate exists only
   on the extraction path. B inherits the gate. A would need a second gate.
3. **The multi-page scan bug is in the `read_document` lane**, not in the
   extraction pipeline. A depends on that fix landing. B does not.
4. **Cost is one call, known in advance.** A can loop up to Max Steps.

A stays possible later as a second action type on the same trigger record, once
the prerequisites below have landed. The trigger record is designed so that
adding it is a new option, not a redesign.

## The trigger record

A new doctype, **Agent Trigger**. Agent Manager creates and edits it. Because
it lives in this app's module, `access/exclusions.py` puts it in the
never-grantable set automatically, so no agent can create or widen a trigger.

| Field | Type | Notes |
|---|---|---|
| `trigger_name` | Data | |
| `enabled` | Check | Off on create. Cannot be ticked until the checks under *Enabling* pass. |
| `agent` | Link Agent | Supplies the identity rule, access rules, autonomy, allowed roles and budget. |
| `document_type` | Link DocType | The doctype that fires. Refused if it is in the never-grantable set, is a child table, a Single, or is submittable *and* the action writes (an agent never writes a submitted document). |
| `event` | Select | `File attached` only in the first release. See below. |
| `file_field` | Data (fieldname) | An Attach or Attach Image field on `document_type`. Validated against the meta on save. |
| `condition` | Code | Optional Python expression, evaluated with `frappe.safe_eval` and `doc` in scope, the same way Notification and Webhook evaluate theirs. Example: `doc.status == "Unverified"`. |
| `action` | Select | `Fill this document from the file` only in the first release. |
| `fill_fields` | Table (fieldname) | The only fields the model is asked for and the only fields that may be written. Each is validated on save: exists, not a system field, not read-only, not hidden from the agent's user by permlevel. |
| `context_fields` | Table (fieldname) | Short values from the record handed to the model as hints, for example a document-type field. Data and Select only, so nothing long enough to carry an instruction. |
| `skill` | Link Agent Skill | Optional. Must be Approved. Its text is appended to the fixed extraction instructions. This is the only free-text channel into the call, and an Agent Manager wrote it, not the document. |
| `model_profile` | Link LLM Model Profile | Required. Pinned on the trigger, not read from the agent, so changing the agent's default model never silently sends documents to a new host. |
| `destination` | Read-only HTML | Where the file goes: the provider's host, whether it is Self Hosted, and the PDF parser engine with its sub-processor when one applies. |
| `destination_acknowledged` | Check | Required to enable. Cleared automatically when `model_profile`, the profile's provider, or the provider's Base URL changes. |
| `require_self_hosted` | Check | When ticked, the trigger refuses to send anything unless the provider is Self Hosted, at save and again before every call. |
| `max_runs_per_hour` / `max_runs_per_day` | Int | Defaults 30 and 200. See *Limits*. |
| `status` | Read-only Select | `Active`, `Paused`, `Disabled`. |
| `paused_reason` | Read-only Small Text | Set by the circuit breaker. |

### The event: "File attached"

One event is enough for the first use case, and it avoids the trap a plain "on
save" trigger walks into: a condition like `doc.status == "Unverified"` stays
true on every save until somebody verifies the record, so an "on save" trigger
would fire on every edit a person makes while checking the values.

"File attached" fires on `after_insert` and `on_update` when **all** of these
hold:

- `file_field` holds a value;
- the value resolves to a File row attached to this record (provenance, the
  same `require_provenance` check extraction uses);
- this trigger has not already handled this record with this file's content
  hash (see *Loop prevention*);
- the condition, if any, is true.

A person who replaces the scan gets a new run. A person who saves the record
again with the same scan does not.

Later event types (`Created`, `Field changed`, `Submitted`, `Communication
received`) are new options on the same Select. Each needs its own answer to the
"fires on every save" problem before it ships.

### Enabling

The `enabled` box is refused unless:

- the agent is enabled and its autonomy includes Draft;
- the agent holds **Extract** and **Draft Edit** on `document_type`, through its
  own rules or a profile;
- the model profile is enabled, its provider is enabled, and it declares
  Supports PDF or Supports Images;
- `destination_acknowledged` is ticked;
- with `require_self_hosted`, the provider is Self Hosted.

Every one of these is checked again in the worker. Enabling is a convenience
check, not the guarantee.

## Who the run acts as

The governance rule does not change: every run is bound to a real user and
never exceeds that user's rights. The question is which user.

The Agent already has **Run As**, and the trigger reuses it rather than adding a
third mode:

- **Session User**: the run acts as **the user whose save fired the event**.
  That is the person who attached the scan. It is the honest attribution: the
  audit trail says the agent read the file on behalf of the person who uploaded
  it, with exactly their rights.
- **Service User**: the run acts as the agent's named service user. Chat
  already refuses these agents. A trigger accepts them.

Not the document's owner. The owner is whoever created the row, which after a
data import is often an administrator or an integration user, and is not the
person who just attached a file.

The existing identity assertion applies unchanged: a run refuses to start as
Administrator, Guest, a disabled user or nobody. A scan attached while logged in
as Administrator therefore produces a visible **Skipped** row saying so, not a
run. This is deliberate. Setting up a site as Administrator must not quietly
send every document to a provider.

The firing user must also pass the agent's **Allowed Roles**, if it sets any.
Otherwise every user who can save the doctype spends the agent's budget. A user
who fails it gets a Skipped row, not a run.

Which mode to recommend is an open decision (see the end). Session User keeps
the rights ceiling personal and needs no standing account. Service User works
when the people uploading have narrow rights, such as a self-service portal
where an employee uploads their own passport.

## Approvals and the audit trail

A fill writes values into a record that is still open for editing. It does not
submit, cancel, or change a status. So no Agent Action is created. The record's
own review step (a verification button, a workflow, a submit) is the human
decision, and it stays outside this app.

The trail for one firing:

1. **Agent Trigger Event** (new, read-only to every role, like Agent Run). One
   row per match: trigger, doctype, name, file, content hash, firing user,
   status (`Queued`, `Skipped`, `Running`, `Done`, `Failed`, `Dropped`), reason.
   This is the idempotency ledger and the answer to "why didn't it fill".
2. **Agent Run** with a new surface `Trigger`, `effective_user` set as above,
   and a new `trigger_event` link. No conversation.
3. One **Agent Tool Call** for a new Draft-class tool, `fill_from_file`, written
   by the one chokepoint `execute_tool`. The trigger job calls the tool through
   `execute_tool`, so the capability gate, the grant check, the kill switch and
   the audit row are the same code chat uses.
4. One **Document Extraction** with the new `target_name` field set, holding
   the extracted values, confidence, withheld sensitive values and conflicts.

Agent User sees their own rows, Agent Auditor sees all, as today.

## From the save to the worker

### The hook

`hooks.py` gains `doc_events = {"*": {"after_insert": ..., "on_update": ...}}`.
A wildcard hook runs on every save on the site, so it must cost almost nothing
when no trigger applies:

1. Return at once if any suppression flag is set (next section).
2. Return at once if the doctype has no enabled trigger. The map of doctype to
   triggers is cached the way Webhook caches its own (`frappe.client_cache` on
   v16), and cleared when an Agent Trigger is saved or deleted.
3. Return at once if `doc.flags.frappe_agents_fill` is set, or if
   `frappe.flags.frappe_agents_run` is set (see *Loop prevention*).
4. For each matching trigger: read the file field, resolve the File, compute the
   key, check the ledger, evaluate the condition.
5. Insert one Agent Trigger Event row and enqueue one job.

The hook **never raises into the user's save.** Every step is wrapped, and an
unexpected error is logged and written as a Failed event row where possible. A
person attaching a passport must never see an agent error on their save.

The event row is inserted in the same transaction as the save. If the save rolls
back, the row goes with it. The job is enqueued with `enqueue_after_commit=True`,
so a rolled-back save never starts a job.

### The job

Queue `long`, like runs and extractions today. The job id is derived from the
idempotency key with `deduplicate=True`, so the same match cannot be queued
twice even if two saves race.

The job:

1. Re-reads the event row. Anything but Queued means a person or an earlier job
   already decided it. Stop.
2. Re-checks the kill switch, the trigger (`enabled`, `status`), the agent
   (enabled, autonomy, grants), the user (the identity assertion), the profile
   and provider (enabled, Self Hosted when required, allowed roles), the limits
   and the budget.
3. Re-reads the record **as the effective user** and re-evaluates the condition
   and the file. If either changed (the record was verified, the scan was
   replaced), the event becomes **Dropped** with the reason. No model call.
4. Creates the Agent Run and calls `fill_from_file` through `execute_tool`.
5. The tool queues the in-place extraction. From here the existing pipeline
   runs, with the worker-time reauthorization from the prerequisites.

### Retries

The run engine's rule stays: **one attempt per run**, because a retry repeats
every side effect already taken. A fill has exactly one side effect, the write,
and it comes last. So one narrow retry is safe:

- A provider error that is **transient** (timeout, connection reset, HTTP 429
  without a quota message, HTTP 5xx) and happened **before any write** is
  retried up to 2 times, with 1 and 5 minute backoff, as a new extraction
  attempt on the same event.
- Anything else is final: a refusal, a quota or billing error, a permission
  denial, a validation error, a failed write.

Every final failure is visible: the event row is Failed with the reason, and the
record gets a timeline comment, "The agent could not read the attached file:
<reason>", so the person checking the record knows to type the values by hand.

A person with write permission on the record may press **Run again** on a Failed
or Dropped event. That creates a new event with a new key suffix, through the
same checks. It is the only way to re-run the same file.

## Limits

A trigger can spend money with nobody watching. Four limits, cheapest first:

1. **Per trigger:** `max_runs_per_hour` and `max_runs_per_day`. Over the limit,
   the event is Skipped with "hourly limit reached". Not queued for later: a
   bulk upload of 500 scans should stop and ask, not quietly drain overnight.
2. **Per agent:** the existing daily token budget. **This needs a fix first:**
   today `_check_budget` sums Agent Run tokens only, and extraction tokens are
   recorded on Document Extraction, so extraction spend never counts toward any
   budget. The fix sums both, joined through `agent_run`.
3. **Per user:** Extractions Per User Per Day. With a Service User every
   triggered extraction counts against one user, so the trigger's own limits are
   the real ones there. Whether triggered extractions should count against a
   Session User's personal cap is an open decision.
4. **Per site:** the pages and file-size caps. A file over them is Skipped
   before the model is called.

### The circuit breaker

The case this exists for: the provider account runs out of credit. Without a
breaker, every upload after that fails one by one, each one a Failed row nobody
reads, and the people uploading assume the values are coming.

After **5 consecutive final failures** of the same trigger whose cause is the
provider (refusal of the key, quota, billing, endpoint error), the trigger moves
to **Paused** with the last reason in `paused_reason`. A paused trigger records
matches as Skipped ("trigger paused"), queues nothing, and sends one
notification (a Notification Log and an email) to every Agent Manager. An Agent
Manager resumes it by hand. It never resumes itself.

## Loop prevention

The fill saves the record. Saving the record fires `on_update`. Four layers
stop that from becoming a loop, and any one of them is enough on its own:

1. **The write is flagged.** The fill sets `doc.flags.frappe_agents_fill = True`
   before `save()`, and the hook ignores a document carrying it.
2. **No triggers inside a run.** Runs and extraction jobs set
   `frappe.flags.frappe_agents_run` for their duration, and the hook does
   nothing while it is set. In the first release a trigger can never start
   another trigger, so chains do not exist. The existing `max_depth` stays for
   runs that start runs.
3. **The ledger.** One event per (trigger, doctype, name, file content hash),
   ever. The fill does not change the file, so the save it causes matches an
   existing key.
4. **The event type.** "File attached" only fires when the file is new to this
   trigger. A save that only changed other fields is not an event.

Other apps' hooks may save the record again (for example to make a scan
private). Layers 3 and 4 cover that, because the file content is the same.

## Suppression during import and migrate

**Agent Settings → Suppress On Import** exists today and nothing reads it. The
hook reads it. When on (the default), the hook returns at once if any of these
is set: `frappe.flags.in_import`, `in_patch`, `in_install`, `in_migrate`. That is
the same list Webhook checks.

When an administrator turns it off, imports fire triggers like any other save,
**and the per-trigger limits still apply.** Turning suppression off cannot make a
1,000-row import send 1,000 documents in one go.

Tests are not suppressed. `in_test` is not on the list, so the suite exercises
the real hook.

Records loaded while suppressed are not lost. A later **Run on existing
records** action on the trigger (second release) shows how many records match
and the worst-case token cost, and asks before queueing them, within the same
limits.

## The kill switch

Unchanged in meaning, applied at every new step:

- **Global Enabled off**: the hook records nothing and queues nothing. A job
  already queued stops at its first check. An extraction already at the model
  stops before the write, as it does today. A fill never writes after the switch
  went off.
- **Trigger disabled or Paused**: same, for that trigger.
- **Agent disabled**: same, for every trigger using the agent.

The switch is read through `runtime_enabled()`, the published value, never a
cached copy of Agent Settings. That is what makes it reach a job that is already
running.

## What a fill may write

### Not `update_draft`, an in-place Document Extraction

The fill goes through the extraction pipeline, extended with one new field on
Document Extraction: **`target_name`**. Without it, extraction creates a new
draft, as today. With it, extraction fills the named record instead.

Why not `update_draft`: it has no sensitive-field gate, it writes whatever
fields it is given, and it is built for a model in a tool loop. The extraction
path already has the gate, a fixed instruction text with no channel from the
model into it, a schema built from the target's meta, link resolution, and the
review screen. A new draft would be wrong for this case: the record already
exists, and a second record holding the same document is exactly the duplicate
the person is trying to avoid.

### The rules of an in-place fill

1. **Only `fill_fields`.** The schema sent to the model contains those fields
   and nothing else. Check fields are refused in `fill_fields`, because 0 and
   "not set" cannot be told apart.
2. **Only empty fields.** A value a person already typed is never overwritten.
   When the model read something different, that is a **conflict**: it is stored
   on the extraction row and named in the timeline comment, and the field keeps
   the person's value.
3. **Sensitive fields are withheld**, exactly as today: values for fields named
   in Agent Settings → Sensitive Fields go onto the extraction row, flagged, and
   reach the record only when a person confirms them field by field in the
   review screen. `apply_extraction` gains the in-place case.
4. **The write window is re-checked right before the write.** The record is
   re-read as the effective user, the trigger's condition is evaluated again,
   and the file must still be the same file. If the record moved on (verified,
   scan replaced, deleted), the result is dropped and the row says so. Nothing
   is written.
5. **The write is an ordinary save as the effective user.** No
   `ignore_permissions`, no `ignore_validate`, no `ignore_mandatory`. The
   record's own validation runs, so an app that locks a verified record refuses
   a late fill by itself.
6. **The record must be editable.** A submitted or cancelled record is refused.
   Note what this does *not* protect: on a doctype that is not submittable,
   every record is at docstatus 0 forever, so "it is a draft" means nothing.
   There, rule 4 (the condition, re-checked) is the real guard. That is why a
   fill trigger on a non-submittable doctype requires a condition.
7. **The audit row holds no values.** `fill_from_file` returns field names,
   counts and the extraction name. The values live on the Document Extraction,
   which has its own permissions. So the Agent Tool Call row, which an Agent
   Auditor reads across the whole site, does not become a list of passport
   numbers.

After the fill, the record gets one timeline comment: which fields were filled,
which were withheld for confirmation, which conflicted, with a link to the
extraction. That is how the person verifying the record knows which values
came from the model.

### Who may read the extraction row

Today Document Extraction is read by its owner and by auditors. With a Service
User, the owner is the service account, so the person who uploaded the scan
could not open the review screen. In-place extractions therefore add a
permission rule: a user who may read the target record may read its
extraction, and a user who may write the target record may review it.

### The open form

A person usually still has the record open when the fill saves it. The desk
already tells an open form that the record changed. A clean form reloads; a
form with unsaved edits warns on save. The fill shortens the window by being
fast, and the first release adds a realtime "Reading the attached file…" note
on the form while the event is Queued or Running. A field-level highlight of
what was filled is a later improvement.

## Privacy: where the scan goes

A fill sends a document, often an identity document, to the model provider
named on the trigger. The plan makes that a decision someone signs, not a side
effect of which model happened to be the agent's default:

- **The trigger names the profile.** It is pinned on the trigger, shown with its
  host, and must be acknowledged before the trigger can be enabled. Changing the
  profile, its provider, or the provider's Base URL clears the acknowledgment
  and disables the trigger.
- **Sub-processors are shown.** When the PDF parser engine is not `native` and
  the provider is OpenRouter, the file is parsed by a further service (the OCR
  engine OpenRouter runs). The destination panel says so.
- **Self-hosted is a first-class option.** `require_self_hosted` refuses any
  provider not marked Self Hosted, at save and before every call.
- **The whole file is sent**, within the page cap. A PDF that bundles several
  documents sends all of them. Sending only selected pages needs the subset
  helper from the page-selection fix; a trigger could later take a page range.
- **What the provider keeps is outside this app.** A site states its provider's
  retention terms in its own privacy notice.

### The self-hosted gap, stated plainly

The app has no rasterisation, by design: nothing in a Frappe environment turns
a PDF page into an image, and the app adds no dependency to do it. Extraction
sends the file as a file. A hosted provider with native PDF input reads it. A
typical self-hosted server speaking the OpenAI-compatible wire takes **images,
not PDF file parts** (this needs confirming per server before it is promised).
So today a self-hosted trigger can read photographed or scanned **images**, but
a **scanned PDF** would be refused at the capability check. Closing that gap is
an open decision, below.

## What must land first

Six fixes were already planned against the extraction and document-reading
paths. Triggers change how much each one matters, because a trigger runs them
unattended.

| Fix | Before triggers? | Why |
|---|---|---|
| **Re-authorise queued extraction in the worker** (agent enabled, autonomy, Extract grant, profile roles, file provenance, all re-read before the bytes leave the site) | **Must** | A trigger makes the gap between queue and worker routine and unwatched. The worker re-check is the guarantee the whole trigger design leans on. |
| **Count extraction tokens in the agent's daily budget** (new, found writing this plan) | **Must** | Without it a trigger's spend is invisible to the only money limit the app has. |
| **Verify the OpenAI Responses PDF contract** | **Must, for OpenAI** | Extraction on a Responses profile currently posts a chat-completions file payload. An unattended trigger on an unverified wire fails every upload. Triggers on an Anthropic profile do not depend on it. |
| **One function for the tools an agent can call now** | Should | The trigger's enabling check asks the same question ("may this agent extract here?"). It should call the same function, not a fifth copy. |
| **Multi-page scan detection in `read_document`** | Not for the fill action. **Must** before an agent-loop action. | The bug is in the reader's text-layer lane. The fill sends the file to the model directly and never takes that lane. The trigger tests still include a multi-page image-only PDF, to prove it. |
| **Honour page selection in the vision lane** | Not for the fill action. **Must** before an agent-loop action. | Same lane. Its page-subset helper is also what a later "pages 1–2 only" trigger option would use. |
| Builder: keep decisions, separate source from target | No | Unrelated to triggers. |
| Pin the Communication attachment workflow | No | Becomes relevant when a `Communication received` event is added. |

## Tests

All synthetic: generated blank and image-only PDFs, sentinel text, the fake
provider from `tests/fixtures.py`. No real identity document, ever.

**The hook**
- A save on a doctype with no trigger runs no query beyond the cached map.
- Each suppression flag (`in_import`, `in_patch`, `in_install`, `in_migrate`)
  stops it when Suppress On Import is on, and none does when it is off.
- The kill switch, a disabled trigger, a Paused trigger and a disabled agent
  each record nothing and queue nothing.
- A false condition, an empty file field, and a file not attached to the record
  each produce no event.
- A save as Administrator, and by a user outside Allowed Roles, each produce a
  Skipped event and no job.
- A save that raises after the hook ran leaves no event row and no job.
- A hook error never reaches the user's save.

**Idempotency and loops**
- The same file saved twice is one event. A replaced file is a second event.
- Two saves racing on the same file queue one job.
- The fill's own save fires nothing.
- Another app's `on_update` that saves the record again fires nothing.
- A save made inside a run or an extraction job fires nothing.

**Identity**
- Session User mode runs as the firing user; Service User mode as the service
  user; never as the owner.
- A firing user without write permission on the record gets a Denied tool-call
  row and a Failed event, and the provider is never called.

**Worker re-checks**, each with an assertion that the provider was not called:
- agent disabled, autonomy lowered to Suggest, Extract or Draft Edit removed;
- profile disabled, provider disabled, profile role removed from the user;
- Base URL changed (acknowledgment cleared), `require_self_hosted` with a hosted
  provider;
- record verified or scan replaced between the queue and the worker (Dropped).

**Writes**
- Only `fill_fields` are in the schema and only they are written.
- A field a person filled is kept; the different model value is a conflict.
- A sensitive field is withheld, and written only when confirmed in review.
- The condition turning false between the model call and the write drops the
  result and writes nothing.
- The record's own validation refusing the save fails the event and leaves the
  record unchanged.
- A Check field in `fill_fields` is refused when the trigger is saved.
- A non-submittable doctype without a condition is refused when the trigger is
  saved.
- The Agent Tool Call row contains no extracted value.
- A multi-page image-only PDF reaches the model as the file.

**Limits**
- The hourly and daily caps produce Skipped events.
- The agent budget counts extraction tokens.
- A transient provider error is retried at most twice and never after a write.
- Five provider failures in a row pause the trigger, notify Agent Managers, and
  the trigger stays paused until a person resumes it.

## The v15 line

**Decision: port, in the same release.** Nothing here needs a v16-only feature.
The differences are mechanical:

- the trigger map cache: `frappe.client_cache` on v16, `frappe.cache` on v15;
- no PEP 695 or PEP 758 syntax;
- no Workspace Sidebar entry for the new doctypes on v15;
- the audit redaction of masked fields is already absent on v15, and the fill's
  audit row carries no values anyway, so nothing new is lost there.

Wildcard `doc_events`, `frappe.safe_eval`, RQ deduplication and
`enqueue_after_commit` behave the same on both. The suite runs on both lines in
CI already.

## Delivery

1. **The prerequisites**, each its own PR: worker re-authorisation, budget
   counting extraction tokens, the Responses PDF contract, the callable-tool
   function.
2. **Triggers, fill action only.** Agent Trigger, Agent Trigger Event,
   `target_name` on Document Extraction, the hook, the job, `fill_from_file`,
   the circuit breaker, and the user docs in the same PR: a new
   `docs/triggers.md`, plus changes to `governance.md` (a run can now start
   without a person), `admin.md` (Suppress On Import now does something),
   `extraction.md` (in-place fill), `tools.md` (the new tool) and
   `troubleshooting.md` ("why did it not fill").
3. **Later:** Run on existing records; the agent-loop action, after the two
   reader fixes; more event types.

## Open decisions

1. **Run As for triggers.** Recommendation: the firing user (Session User). It
   keeps the rights ceiling personal and needs no standing account. Service User
   stays available for self-service uploads.
2. **Write at once, or only after review?** Recommendation: write non-sensitive
   values at once, because the record's own verification is the review step and
   the timeline comment marks what the model wrote. The stricter option holds
   every value on the extraction row until a person accepts it, which is safer
   and turns "fill" into "propose".
3. **Do triggered extractions count against the per-user daily extraction
   cap?** Recommendation: no. They count against the trigger's own limits and
   the agent's budget. Otherwise a person doing a morning of uploads hits a cap
   meant for chat.
4. **What happens over a limit.** Recommendation: Skip and say so. The
   alternative, queue for later, drains a bulk upload overnight with nobody
   watching.
5. **The self-hosted PDF gap.** Options: accept images-only for self-hosted
   triggers and document it; add an optional rasterisation dependency, which
   ends "zero pip dependencies"; or add a self-hosted text-extraction service as
   a new provider kind. Recommendation: accept and document for the first
   release, and confirm what common self-hosted servers actually accept before
   choosing.
6. **Circuit-breaker threshold and channel.** Proposed: 5 consecutive provider
   failures, Notification Log plus email to Agent Managers.
