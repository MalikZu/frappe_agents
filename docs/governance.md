# How the agent is held to account

This is the page to read before you trust the app with a real site. Everything
else in these docs describes a feature; this describes the guarantees the
features are built on, and the places where a guarantee stops.

## An agent has no rights of its own

An agent is a record, not a service account. It never holds a permission. Every
run is bound to a real user, and that binding is what every check downstream
means something against.

An agent runs one of two ways, set by **Run As** on the Agent:

- **Session User** — the run acts as the person chatting. This is the normal
  case and the one to prefer.
- **Service User** — the run acts as a dedicated account you name and restrict
  yourself. It is still a real user with real permissions; there is no mode in
  which a run is unbound.

A run that a [trigger](triggers.md) starts is bound the same way, to the person
whose save or upload fired it. Its model holds no tools, and the runtime writes
its answer into one field.

A run refuses to start as nobody, as Administrator or Guest, or as a user who is
disabled or missing. It fails on the Agent Run rather than falling back to
anything wider.

So the ceiling is: **the agent can never see or do more than the person it runs
for could do by hand.** Not usually — ever. If they cannot open a document,
neither can the agent, whatever else is configured.

The tool chokepoint checks this itself. A tool call made by anyone other than
the run's user is refused and audited, whoever made it. That matters once a
second door exists: [outside MCP clients](mcp.md) reach the same tools, bound to
the user whose API key they hold, and never past Draft.

## Rules narrow that ceiling, they never raise it

Access rules are the second half. Effective access is:

> what the user could do by hand **∩** what the agent's access rules allow

```mermaid
flowchart LR
    U["What the running user<br/>could do by hand"] --> X(("∩"))
    R["What the agent's<br/>access rules allow"] --> X
    X --> C{"Autonomy<br/>ceiling"}
    C --> E["What the agent<br/>may actually do"]
```

Turning on a rule hands nobody a permission. It only decides how much of their
own the agent may use. Both checks run on every call — frappe's permission
check and the rule check, never one instead of the other. See
[What an agent may reach](access.md) for the rules themselves.

## Agents draft, humans submit

Every tool carries a capability class, and the agent's **Autonomy** decides
which classes it may call at all:

| Autonomy | May call |
|---|---|
| Suggest | Read |
| Draft | Read, Draft |
| Write | Read, Draft, Write |

There is no autonomy level that grants Submit. No code path lets an agent
submit, cancel or delete a document. The most an agent can do is write a
proposal that a **different** person decides — see [Approvals](approvals.md).

That is a structural claim, not a policy one: the capability gate refuses a
Submit-class tool at every autonomy level, and no such tool is registered.

## Every call is on the record

Every tool call goes through one function, and that function always writes one
**Agent Tool Call** row — on success, on denial, and on error. A call torn down
mid-flight, because the run was cancelled, gets a row saying so. An auditor sees
attempts, not gaps.

```mermaid
flowchart TD
    A["The model asks for a tool call"] --> B{"Kill switch on?"}
    B -- no --> S["Run stops"]
    B -- yes --> C{"Tool granted<br/>to this agent?"}
    C -- no --> D["Denied"]
    C -- yes --> E{"Autonomy allows<br/>this capability?"}
    E -- no --> D
    E -- yes --> F{"Access rule allows<br/>this verb on this target?"}
    F -- no --> D
    F -- yes --> G{"Frappe permission<br/>for the running user?"}
    G -- no --> D
    G -- yes --> H["The tool runs"]
    H --> L["Agent Tool Call row"]
    D --> L
    S --> L
```

Every path ends at the same row. There is no branch that does something and
writes nothing.

The row records the run, the tool, the arguments, the outcome, a summary of what
came back, which documents were touched, how long it took, and the error if
there was one. Long values are stored cut short and say where they were cut.

Agent Action rows behave the same way. The doctype grants create and write to no
role at all: a proposal can only arrive through the tool that makes one, and can
only leave through an approval, a rejection, or expiry.

### What the audit row holds back

A tool call is stored in two places: its **Agent Tool Call** row, and the
**Event Log** on its **Agent Run**. The log keeps the arguments and the result a
second time, so the model's turns can be read back in order. Both copies are
durable and an auditor reads both, so in both, sensitive values are replaced
with `[redacted]` as they are written. What the call *did* stays legible — the
tool, the doctype, the field names, the outcome. Only values are removed.

The event log applies the same rules as the row, and goes one step further: a
value it holds back is also taken out of the model's later text in that run,
because a model that read a value can repeat it. The run's stored answer and
error follow the same rule, so they match what the log says. A result that is
too long for the model is cut short, and is redacted before it is cut.

What the text scrub takes out, exactly:

- **Text values** of 4 characters or more, wherever they appear. Dates, times
  and durations count as text, in the form they are stored in: a held-back
  birth date is taken out of `1990-05-17`.
- **Number values** of 4 significant digits or more, in any form that has the
  same value. A held-back amount of 98765 is taken out of `98765`, `98765.00`,
  `98,765.00`, `AED98765`, `98765AED`, `98765/month`, `amount:98765` and
  `98765-99000`. A unit or a label next to a number never keeps it:
  `98765/month/employee`, `AED-98765/month` and `net-pay-98765` lose it too.

What it keeps:

- A number that is part of a longer number: a held-back 2025 stays in `12025`
  and `2025.5`.
- A number that is one part of a date or a reference joined by hyphens or
  slashes. A date is three number parts with the year first or last:
  `2025-01-02`, `02/01/2025`. A reference is one or more word parts, then two
  or more number parts: `ORD-2025-0001`. A held-back 2025 stays in all three.
- Shorter values, numbers written in words, and number formats other than the
  ones above, such as `98 765` or `98.765,00`.

If a stored event or result cannot be redacted, its values are never learned.
Then everything the model writes after it in that run, and the stored answer,
is kept as `[redacted]`.

A value is treated as sensitive when it is a Password field on the doctype the
call names, when an administrator marked it sensitive in **Agent Settings**, or
when its argument is named `password`, `api_key`, `token` or `secret`. Child
tables count at any depth: a payment detail inside a line item is the same value
with more steps.

Two deliberate limits worth knowing:

- The by-name fallback list is **short on purpose.** A rule wide enough to catch
  every secret by its name would redact half the audit trail, and a row an
  auditor cannot read is its own kind of failure. Mark the fields that matter in
  Agent Settings rather than relying on the names.
- Redaction happens at the write and nowhere else. **The tool's own return value
  goes to the model untouched.** This protects the audit trail, not the model
  call — what an agent may read is decided by permissions and rules, upstream.
- **The live stream is not redacted.** While a turn runs, its events and its
  answer go to the chat of the user who ran it, and to nobody else. That user
  already holds every value in them. Only the stored copy is redacted, so the
  same user sees `[redacted]` when they reopen the conversation later.
- A failed turn is replayed from its stored log, and a reopened conversation is
  drawn from it. Both show `[redacted]` where a sensitive value was.

A run's **Proposal Updates** field holds what the agent was told about its
decided proposals: the approver's note and the reason an apply failed. Either
can quote a document's fields. Before the note is stored, the text scrub takes
out the sensitive values of each document it names. The agent is sent the same
scrubbed note. If a document's values cannot be read, its line keeps the
outcome and leaves the note and the reason out.

#### Logs stored before redaction

Older releases stored the event log without redaction. The migrate after you
upgrade redacts every stored log again, and the answer and error of its run. It uses the **Sensitive Fields** list in
Agent Settings as it is at that moment. A field you remove from the list before
you upgrade is not redacted in old logs. A field you add after you upgrade is
redacted only in logs that are written after you add it. Set the list before
you upgrade.

## One switch turns it all off

**Agent Settings → Global Enabled** is the kill switch. It is on when the app is
installed. Clear it and every tool call is refused immediately, including calls
inside a run that is already in flight.

That last part is the hard part, and it is why the switch is read the way it is.
A background job holds one database transaction from start to finish, so a value
another connection wrote afterwards is invisible to it however often it asks —
and a cached copy of the settings is per process. So the switch is published to
the shared cache every time Agent Settings is **saved**, and the runtime counts
as on only when nothing says it is off.

The consequence to plan around: **flip the switch by saving Agent Settings.** A
value written straight into the database, bypassing a save, is not published,
and a run already going will not see it.

The switch also stops an approver applying an agent's proposal. A site that
distrusts the runtime should not have humans applying its output either.
Rejecting a proposal still works with the switch off — clearing the queue is
exactly what a stopped site should still be able to do.

## Document text is data, never instructions

Everything a person wrote — a comment, an email body, a description field —
reaches the model wrapped as untrusted content, tagged with where it came from.
Any attempt to close the wrapper early from inside the text is defused first.

This covers the long free-text fieldtypes, the ones with room to hide an
instruction: Text, Small Text, Long Text, Text Editor, HTML Editor, Markdown
Editor and Code. Data and Select values are short and structured and are
usually not wrapped. The exceptions are below.

Every read tool that returns this text wraps it:

- `search_documents` wraps each free-text field in each row. It also wraps the
  doctype's title field, whatever its type. A title is usually a Data field,
  and it holds whatever its author typed.
- `get_document_context`, `get_document_slice` and `read_document` wrap fields,
  comments, emails and file text.
- `run_report` wraps each cell in a free-text column. A report column often has
  no type, or a type that does not say what it holds, so the rule is reversed
  there. A cell is left alone when its column is a number, date, time, check or
  duration type, or when the cell is a short label: 24 characters or fewer, or
  one word of up to 140 characters, such as a document name. Every other text
  cell is wrapped.

`search_documents` and `run_report` cut wrapped text to 2,000 characters. That
is the limit a document's fields slice uses.

Be clear about what this is: it is a strong, structural mitigation against
prompt injection, and it is **not a proof**. A model can still be talked into
proposing something foolish by text it was handed. That is why the draft/submit
wall exists — the wrapping reduces how often a model is fooled, and the approval
step is what makes being fooled survivable.

You can test how often your agent is fooled. The prompt-injection pack in
[Rehearsals](rehearsals.md) hides instructions in document text and checks, from
the audit rows, that the agent did not follow them. A pass is a sample on one
model, not a proof.

## The model is only handed what it asks for

A document does not arrive as a dump. The model first gets a manifest — what
exists around the document, as counts, never content — and then asks for one
slice at a time.

Read permission on the document is checked once, up front, before anything is
assembled. Every linked document is then checked individually, because a link is
a different document, not part of this one. Rows the permission system hid are
reported as a count, so the model knows something is there without learning what.

## What this does not guarantee

Worth stating plainly, because a docs page that only lists strengths is not
much use when you are deciding whether to trust something:

- **The model can be wrong.** Nothing here makes an extracted figure correct or
  a draft sensible. The guarantees are about authority and traceability, not
  accuracy.
- **Untrusted wrapping is a mitigation.** See above.
- **An auditor sees a lot.** Agent Tool Call rows carry argument and result
  summaries across every user's runs. That is the point of an audit trail, and
  it means the Agent Auditor role is a privileged one — grant it accordingly.
- **A permission you granted is a permission the agent can use.** The ceiling is
  the running user's own rights. If those are broad, narrowing the agent's
  access rules is the only thing keeping the agent narrow.
- **Cost is bounded but not zero.** `Max Steps` and the daily token budget stop
  a runaway loop; they do not make a misconfigured agent free.
