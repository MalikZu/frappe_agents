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
