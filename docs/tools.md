# Built-in tools

Seventeen tools ship with the app. You do not grant them one by one. You write
access rules, and the rules decide which tools an agent is offered — see
[What an agent may reach](access.md).

This page is the reference for what each one does, so you can read an audit row
or a chat log and know what happened.

## Capability classes

Every tool carries a capability. An agent's **Autonomy** sets the highest class
it may call at all.

| Class | Ships | Means |
|---|---|---|
| **Read** | 11 tools | Looks at data. Changes nothing. |
| **Draft** | 6 tools | Writes a draft, or writes a proposal. Commits nothing. |
| **Write** | none | Reserved for tools another app registers. |
| **Submit** | none, ever | No tool may hold it. Agents do not submit. |

Two things follow from that table, and both matter when you configure an agent:

- **Draft and Write autonomy currently reach the same tools**, because nothing
  that ships is Write class. Pick Draft.
- **Submit is not a class you can grant.** It exists so that the gate has
  something to refuse. Committing a document is
  [an approval](approvals.md), always.

A tool call is refused unless the capability passes, the access rules allow the
verb on that target, and the running user's own frappe permission allows it.
Every refusal is audited.

## Read

| Tool | Does |
|---|---|
| `find_doctypes` | Lists the doctypes this agent may work with and what it may do with each. Usually its first call. |
| `search_documents` | Lists documents of one doctype, one page at a time. Returns only rows and fields the running user may read. |
| `summarize_documents` | Counts and totals documents of one doctype, grouped by up to two fields. Answers "how many" and "how much". |
| `get_doctype_meta` | Describes a doctype's fields, so the model can build a correct search or draft. |
| `run_report` | Runs a saved Frappe report as the running user and returns its columns and rows, capped. |
| `read_document` | Reads an attached file and reports what it says. Needs **May Read Files**. |
| `get_document_context` | The entry point for a question about one document: core fields plus the shape of what is attached to it, as counts. |
| `get_document_slice` | Reads one part of a document after the context call said it was worth reading. |
| `list_site_doctypes` | Lists the doctypes on the site, for naming real records in a blueprint. |
| `list_site_reports` | Lists the saved reports the running user may run. |
| `describe_site_doctype` | Describes one doctype's fields for a blueprint. |

The last three exist for the **Agents Builder** and are not offered to an
ordinary agent.

`get_document_context` before `get_document_slice` is the deliberate shape: the
model is told what exists as counts, then asks for one slice, instead of being
handed a document dump it did not need.

### Pages and totals

`search_documents` returns one page of at most 50 rows. The result says:

- `returned` — how many rows are on this page. It is not a total.
- `has_more` — whether another page exists.
- `next_start` — the `start` value that fetches the next page.

A page used to come back with a `count` that was only the page size. A model
that read it as a total stated the wrong number. That field is gone.

For "how many" and "how much", the model calls `summarize_documents` instead.
It asks the database for the number, as the running user, so role permissions,
User Permissions and permission query conditions shape the totals exactly as
they shape a list view. It takes:

- `group_by` — up to two short fields: Link, Select, Data, Check, Date, Int,
  Autocomplete, Rating, or `owner`, `modified_by`, `docstatus`.
- `aggregates` — up to four of `count`, `sum`, `avg`, `min`, `max`. `sum` and
  `avg` take a number field. `min` and `max` take a number or a date field.
  `count` is always included.
- `filters` and `include_cancelled` — the same as `search_documents`.
  Cancelled documents are left out by default.

An aggregate reads every row it covers, so the tool has limits a list does not:

| Limit | What happens |
|---|---|
| **50 groups** | Largest first. `truncated` says groups were left out. `total` still covers all of them. A rule's row cap lowers the 50. |
| **Groups smaller than 3 documents** | The count comes back. `sum`, `avg`, `min` and `max` come back empty, and the row says `suppressed`. These groups go last. |
| **Fields the user may not read** | Refused, when the field is above the user's permission level. |
| **Withheld fields** | Refused as a group, an aggregate or a filter: masked fields, fields marked sensitive in **Agent Settings**, and Password fields. A count under a filter on a field answers "how many have this value", so filters are refused too. |
| **Free text** | Refused. Long text is not a category. |
| **Doctypes checked document by document** | Refused. Some doctypes, such as ToDo, File, Communication, Contact, Address, Event and Note, register a per-document permission hook. A list query cannot run that hook, so a count could include documents the user may not open. Use `search_documents` there: it checks every row. |

## Draft

| Tool | Does |
|---|---|
| `create_draft` | Creates one document as a draft, under the running user's own create permission. |
| `create_drafts` | Creates many drafts of one doctype in one call, each row on its own savepoint. |
| `update_draft` | Changes a document that is still a draft. Refuses submitted and cancelled documents. |
| `propose_submit` | Records a pending Agent Action asking a person to submit a draft. Submits nothing. |
| `propose_cancel` | Records a pending Agent Action asking a person to cancel a submitted document. Cancels nothing. |
| `extract_document` | Turns an attached PDF or image into a draft for review. See [Document extraction](extraction.md). |

`update_draft` defaults to drafts the agent itself created. A rule may widen it
to any draft.

The two `propose_*` tools are the only route to a submitted document, and they
produce a row, not a change.

## Reading a tool call

Open any row under **Agents → Activity → Tool Calls**, or expand a call in chat.
You get the tool, its arguments, the outcome, a summary of the result, the
documents touched, and how long it took.

Three outcomes:

- **Success** — it ran.
- **Denied** — a permission, a rule, the capability gate, or the kill switch
  refused it. The error says which.
- **Error** — it ran and failed.

Sensitive values read `[redacted]`. What the call *did* stays legible. See
[How the agent is held to account](governance.md).

## Tools from other apps

**Agent Tool** rows carry a `provider_app`. Another Frappe app can register a
tool, and it passes through the same capability gate, the same chokepoint and
the same audit row as a built-in one.

An agent's **Tools** table is for those. The generic tools are offered by the
access rules and are not selected there.
