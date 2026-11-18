# Approvals

An agent can build a document. It cannot commit one. When an agent decides
something should be submitted or cancelled, that a workflow should move on, or
that fields on a live record should change, it writes a proposal and stops, and
a person decides.

```mermaid
flowchart TD
    A["The agent builds a draft"] --> B["The agent writes a proposal<br/>Agent Action, Pending"]
    B --> C{"A different person decides"}
    C -- "rejects, with a note" --> R["Rejected"]
    C -- "nobody, for 7 days" --> X["Expired"]
    C -- approves --> D{"Read permission · timestamp lock<br/>workflow · document state · kill switch"}
    D -- "a check fails" --> F["Failed<br/>reason on the row"]
    D -- "all pass" --> G["Submit, cancel, workflow action<br/>or field change runs as the approver"]
    G -- "the document refuses it" --> F
    G -- succeeds --> S["Applied"]
```

## What a proposal is

A proposal is an **Agent Action** row: a request, not an act. It names the
document, what it wants — a submit, a cancel, a workflow action, or a field
change — and the agent's reason. It also
stores the document's timestamp at the moment it was proposed — that snapshot
does more work than it looks like, below.

Nothing else can create one. The doctype grants create and write to no role at
all: a proposal arrives through the proposal tool and leaves through an
approval, a rejection, or expiry. There is no path where a row appears or
changes some other way.

Find them under **Agents → Review → Pending Actions**.

## Who may decide

You need the **Agent Approver** role, and you must not be any of these three
people:

- whoever asked for the proposal;
- whoever the run was acting as;
- whoever owns the agent.

An approval that none of those three could avoid making is not a review. If you
are blocked, the screen says which of the three you are.

## Who is told

A proposal nobody hears about waits until it expires. So the app tells people,
with frappe's own notifications: the bell at the top of the desk. These are
Alert notifications, and frappe does not send email for those — the bell is the
only place they appear.

**When a proposal is written,** the approvers who could decide it are told. That
is every enabled user with the Agent Approver role who also:

- is none of the three people above, who may not decide it;
- may read the document;
- may submit it (or cancel it, for a cancel proposal, or change it, for a field
  change — at the permission level of every field the change touches, and with
  the role the workflow state allows to edit, if a Workflow governs it).

For a workflow action, the last line is different. The approver must hold a role
the step is open to. They also need the right the step needs: submit when the
step submits the document, cancel when it cancels it, write otherwise. If the
document has already left the state the agent proposed from, nobody is told:
the approval would fail. The step's condition and self-approval are still the
Workflow's call, when the approver applies it.

An approver who fails any of these is not told. They could not approve it, and
an approver who may not submit the document would only turn the proposal into a
**Failed** one. At most 20 approvers are told, in alphabetical order; everyone
with the role still finds it under **Pending Actions**.

**When a proposal ends,** the person who asked is told: it was applied, rejected,
failed, or expired. So is the user the run was acting as, if that is someone
else. A rejection carries the start of the approver's note; the full note is on
the proposal.

A notification names the agent, the verb and the document, and the approver's
note on a rejection — nothing else. For a workflow action, the verb is the
workflow's own action and the state it starts from. It does not quote the agent's reason or any
field of the document. Open the
proposal to read those, behind the usual permission checks.

To stop these notifications for yourself:

1. Click the bell at the top of the desk.
2. Click the settings icon.
3. Clear **Enable System Notification**.
4. Click **Save**.

This switch stops all your desk notifications, not only the ones about proposals.
There is no separate switch for agent proposals.

## What you are agreeing to

Open the document and read it. The proposal is a request to act on *that record
as it now stands*, and approving is you saying you read it.

The app enforces that literally. Approving sends back the timestamp of the
version you looked at, and a document that changed underneath you refuses the
approval and asks you to read it again. This is a real check, not a courtesy:
frappe's own stale-document guard cannot fire on this path, so this timestamp is
the only thing standing between you and approving a version you never saw.

You also cannot approve a document you may not read. Read permission is proved
before anything is written — an approval of a record you cannot open is a
signature on a blank page.

## Approving

Approving applies the change **as you**. The submit or cancel runs in your own
session under your own permissions, with nothing bypassed. If you may not submit
that document by hand, approving does not submit it either.

That has a consequence worth expecting: **a failed apply is normal, not a bug.**
Submitting re-runs the document's full validation, so a draft that was valid
when the agent built it can legitimately fail now — a closed period, a credit
limit, a mandatory field somebody cleared. The proposal is recorded as **Failed**
with the reason on the row, and nothing is half-applied.

A few things are refused outright:

- **A submit or cancel on a document governed by an active Workflow.** Frappe
  does not stop a server-side submit on a workflow doctype — it would teleport
  the document past every approval step in the workflow. So submit and cancel
  proposals refuse those doctypes, and the refusal is checked again immediately
  before applying, in case the workflow was switched on in between. The agent
  proposes a workflow action instead, below.
- **A document no longer in the right state** — a submit proposal for something
  already submitted, a cancel for something that is not.
- **A document that no longer exists.**
- **Anything, while the kill switch is off.** A site that has stopped trusting
  the runtime should not be applying its output by hand either.

## Workflow actions

Leave Applications, Expense Claims, Purchase Orders and many custom doctypes
move through a **Workflow**: Draft, then Pending Approval, then Approved. On
those documents the agent proposes one of the workflow's own actions, such as
**Approve** or **Send for Review**, with the `propose_transition` tool.

The agent can only propose an action the workflow offers *the user it runs as*:
a transition out of the document's current state, open to one of that user's
roles, with its condition met. If the agent names another action, it is refused
and told which ones are open. Self-approval and document permissions are not
checked at this point. They are checked for the approver, below.

The proposal records the action **and the state it was proposed from**. Workflows
often reuse an action name: Draft → *Approve* → Manager Approved → *Approve* →
Approved. A proposal to approve out of Draft never becomes the second Approve.

The approver applies the action. Frappe's own workflow engine runs it in the
approver's session, so the workflow checks the approver, not the agent:

- the transition must be open to one of **the approver's roles**;
- the transition's **condition** must still hold;
- if the transition does not allow self-approval, the approver must not be the
  document's owner;
- the approver needs the document permission the next state needs — submit, if
  the action submits it.

Every check above this section still runs first: separation of duties, read
permission, the timestamp lock and the kill switch. Then two more:

- **The document must still be in the state the agent proposed from, under the
  same Workflow.** If someone moved it on, the approval fails. The agent can
  propose again from the new state.
- **The doctype must not submit in the background.** Frappe would queue the
  submit, and the approval could not tell whether it went through. Apply the
  action from the document itself.

The agent never applies a workflow action itself. No tool can.

## Field changes

A ticket, a lead, a task or a job applicant has no submit step. It stays a
draft for its whole life, so every save changes the record the business works
from. On those records the agent proposes a **field change** with the
`propose_update` tool: the fields, their new values, and its reason.

The proposal stores two values for each field: the new one, and the one the
field held when the agent looked. Both are hidden from the Agent Action form's
fields and from reports. The approval screen shows them to an approver who may
read the document and the field.

The new values also appear where every agent call appears: in the agent's own
tool call under **Agents → Activity → Tool Calls**, in the run's log, and on the
chat card of the person who asked. Agent Auditors read those. The values the
field held before are on the approval screen only.

### What the agent may propose

Plain fields only: text, numbers, dates, times, checkboxes, selects and links.
Dates and times are local, with no time zone.
The agent cannot propose a change to:

- a table, rich text, an attachment or any other structured field;
- a read-only, hidden or computed field, a field with a **Read Only Depends On**
  condition, or any field of a read-only doctype;
- a sensitive field — a Password, a masked field, or a field listed under
  **Agent Settings → Sensitive Fields**;
- the workflow state — the agent proposes a [workflow action](#workflow-actions)
  instead;
- a set-once field that is already set;
- a system field, such as the owner or the name.

One refused field refuses the whole proposal. The approver approves one exact
set of changes, so nothing is dropped quietly.

The agent needs **read** on the document and on each field's permission level,
and its access rule must tick **Read** as well as **Propose**. It does not need
write: writing is the approver's job. A link must name a record the agent's user
may read. When the agent's rules do not let it read the linked doctype, the link
is kept as written and checked at approval.

A record with a submit step is refused. The agent changes a draft with
`update_draft`. A submitted document is changed by cancelling and amending it.

One proposal changes at most 20 fields. Two pending proposals may not change the
same field on one record, and one record carries at most five pending field
changes. Two proposals sent at the same moment can both land; the second one
approved then fails, because its field moved.

### What you see

The proposal opens with a table: each field, its value **when proposed**, its
value **now**, and the **proposed** value. A field you may not read shows its
label and nothing else. The rest of the document follows under the table.

The save is an ordinary one. If the change sets a link, the fields the document
fills in from that link follow it, as they would in the form.

If a field changed after the agent proposed, its row is red and **Approve** is
not offered. Reject the proposal, or ask the agent to propose again.

If you may not read one of the fields, **Approve** is not offered either. You
would be approving a value you cannot see, so another approver has to decide.

### What approving checks

The change is saved as you, with an ordinary save. Every check in
[Approving](#approving) runs first. Then:

- **You need read and write** on the document, and at the permission level of
  every field in the change. Frappe's own save would quietly keep the old value of a
  field you may not write and save the rest. The app refuses the whole change
  instead.
- **Each field must still hold the value the agent saw.** A change to a field
  the proposal does not touch does not block it. It is recorded as
  **Edited Before Approval**.
- **On a doctype with an active Workflow,** you need the role the current
  state names under **Only Allow Edit For**. Frappe checks that role in the form
  only, so the app checks it here.
- **A link must name a record that exists and that you may read.**
- **Every value is read back after the save.** If the document's own rules
  changed a value while saving, the whole change is rolled back and the proposal
  is **Failed**.

If the document's own rules refuse the save, the row says so and nothing more.
Their message can quote field values, and the row is readable by people who may
not read those fields. You see the message when you click **Approve**.

A rollback undoes what was written to the database. It does not undo work the
document's code queued to run once the request finishes — a webhook, a
notification, a background job. Those can still run, and carry the values that
were rolled back. Submit and cancel proposals have the same limit.

### Edits that skip approval

`propose_update` adds a governed path. It does not close the direct one. An
agent with **Draft Edit** on a doctype with no submit step can still change its
records with `update_draft`, without approval: the records its user created, or
any record with **Any Draft** ticked.

To send every change on such a doctype through a person:

1. Open the access rule for that doctype.
2. Clear **Draft Edit**.
3. Tick **Propose**.
4. Save the rule.

## Rejecting

Rejecting requires a note. That is deliberate — the note is the answer to the
proposal, and the agent's reason deserves one back.

Rejection works even when the kill switch is off. Emptying the queue is exactly
what a stopped site should still be able to do.

## Expiry

A proposal nobody decides expires after seven days, changeable at **Agent
Settings → Action Expiry Days**. A daily job sweeps them; a proposal past its
window is refused even if the sweep has not run yet.

Expiry is not a decision. Nobody is recorded as deciding it, and the agent is
free to propose the same thing again.

## The states

| Status | Means |
|---|---|
| **Pending** | Waiting for a person |
| **Applied** | Approved, and the submit, cancel, workflow action or field change went through |
| **Failed** | Approved, but the document refused it — reason on the row |
| **Rejected** | A person said no, with a note |
| **Expired** | Nobody decided in time |

## Did anyone actually read it?

Every applied proposal records **Edited Before Approval**: whether the document
changed between the agent proposing it and the approval going through. It is
computed just before the apply, because submitting overwrites the timestamp it
is measured against.

**Agents → Review → Review Quality** reports on it. Read it the right way round.
A high edit rate is not a problem — it means reviewers are reading drafts and
correcting them, which is the system working. A rate at or near zero across many
approvals and many approvers is the signal worth investigating: it can mean the
agent is excellent, and it can mean nobody is looking.

It measures applied proposals only. A rejection applied nothing, so it is left
out rather than counted as an unedited approval.
