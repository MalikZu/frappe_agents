# What an agent may reach

An agent is not given tools. It is given a list of records and, per record, what
it may do with them. The tools follow from that list. A record nobody granted
does not exist as far as the agent is concerned.

A new agent reaches nothing until you say what it is for. That is deliberate:
the first thing you write on an agent is its access.

## The rules

Access lives in **Access Rules** on the Agent, and in **Agent Access Profiles**
you attach to it. A profile is a reusable set of rules, the way a Role Profile
is a reusable set of roles. Attach the same profile to five agents and edit it
once.

One rule row names one target and ticks what is allowed on it:

| Tick | The agent may |
|---|---|
| Read | list and read documents of this doctype |
| Draft New | create documents, always as drafts |
| Draft Edit | change a draft it or its user already made |
| Propose | ask a person to submit or cancel — never do it |
| Extract | read an attached file into a draft of this doctype |

Two more columns narrow a row:

- **Any Draft** — off by default. The agent edits the drafts its own user
  created. Tick it to let the agent edit a colleague's draft.
- **Max Rows Per Call** — 0 means the tool's own limit. Anything else caps a
  single call at that many rows, whichever is smaller.

A rule can also name a **Report** instead of a DocType. That one grants running
that report and nothing else.

## Rules only narrow

Effective access is **what the user could do by hand ∩ what the rules allow**.
An agent never exceeds the person it runs for: if they cannot see a document,
neither can the agent, whatever the rules say. Turning a rule on does not hand
anybody a permission — it only decides how much of their own the agent may use.

The Agent form shows the result. The **Effective Access** panel lists every
granted target, where the grant came from, and which rows the person looking at
the form could not use anyway. That is the answer to "why can't my agent see
this".

![The Effective Access panel on an Agent, listing each granted doctype, what the
agent may do with it, the limits that apply, and which rule granted
it](images/effective-access.png)

The panel also names the tools the rules add up to, and says when autonomy is
overriding them — a Suggest agent is not offered the proposal tools whatever its
rules say.

## What is never granted

Two sets of doctypes cannot be named by a rule, and the check runs again on
every call:

- everything this app ships — agents, runs, tool calls, access rules — so an
  agent cannot widen its own grant or edit the record of what it did. The one
  exception is **Agent Blueprint**, which is a proposal on paper;
- the site's security surface: User, Role, permissions, scripts, workflows.

A report over one of those doctypes is refused too.

## Profiles you already have

Installing the app creates two starting points. Both are inert until an agent
carries one:

- **Personal Organizer** — reads todos, notes, events and contacts, drafts new
  ones, and edits the todo and note drafts its own user made.
- **Site Reader** — reads todos, notes, contacts and addresses. Writes nothing.

Copy one and narrow it rather than granting more than a job needs.

## Files

File reading is not doctype-shaped, so it rides one checkbox: **May Read Files**
on the Agent. With it off, the agent cannot read an attachment even when it may
read the document the file hangs off.

## Let an agent design one

The app also ships **Agents Builder**, switched off and without a model. Give it
a model profile and enable it, and it interviews you about the job, looks up the
doctypes your site actually has, and writes an **Agent Blueprint** — a proposal.
It creates nothing. Open the blueprint and press **Create Agent**: only an Agent
Manager can, and the agent that appears is disabled until somebody finishes it.

## Upgrading an older site

Agents made before access rules existed keep working: an agent with no rules and
a tool selection behaves the way it did. The upgrade writes rules for the agents
whose reach it can state exactly, and prints the names of the ones it could not.
Give those rules by hand — until you do, they still reach everything their user
can.
