# Running the app

Settings, the audit trail, and the switch that stops everything.

## The kill switch

**Agent Settings → Global Enabled** stops every agent at once. It is on when you
install the app.

Clear it and:

- every tool call is refused, including calls inside a run already going;
- an approver cannot apply an agent's proposal;
- rejecting a proposal still works, so you can empty the queue.

**Flip it by saving Agent Settings.** The switch is published to every process
when the record is saved. A value written straight into the database, without a
save, does not reach a run that is already going.

Why it works that way: a background job holds one database transaction from
start to end, so a value another connection wrote afterwards is invisible to it.
Publishing on save is what crosses that gap. See
[How the agent is held to account](governance.md).

## Agent Settings

| Setting | Default | What it does |
|---|---|---|
| **Global Enabled** | on | The kill switch |
| **Max Depth** | 3 | How deep runs may nest when one run starts another |
| **Default Daily Token Budget** | empty | Fallback budget for an agent that sets none |
| **Action Expiry Days** | 7 | How long a proposal waits for a decision |
| **Suppress On Import** | on | Skip agent triggers during data import and migration |
| **Sensitive Fields** | empty | Fields extraction may never write on its own |
| **Max Extraction Pages** | 20 | Longer PDFs are refused |
| **Max Extraction File MB** | 10 | Larger files are refused |
| **PDF Parser Engine** | native | How a PDF reaches the model |
| **Extractions Per User Per Day** | 50 | Per-person daily cap |

**Sensitive Fields is empty on a new site, and it is the whole configuration of
the extraction gate.** Until you fill it in, extraction holds nothing back. On a
site with bank or payment details, set it first. See
[Document extraction](extraction.md).

## The audit trail

Four record types make up the trail.

| Record | Holds |
|---|---|
| **Agent Run** | One run: who it acted as, the model, status, tokens, steps |
| **Agent Tool Call** | One tool call: arguments, outcome, result summary, duration, error |
| **Agent Action** | One proposal and its decision |
| **Document Extraction** | One file read, and what was held back |

Find them under **Agents → Activity**.

### Nobody can edit or delete it

No role in this app has write or delete permission on Agent Run, Agent Tool Call
or Agent Action. Not Agent Manager, not Agent Auditor, not System Manager.
Status changes are written by the engine itself and nowhere else.

### Who reads what

- **Agent User** reads their own runs, tool calls and conversations only.
- **Agent Auditor** reads everybody's.
- **System Manager** reads everybody's.

Agent Auditor is a privileged role. The rows carry argument and result summaries
from every user's runs, so an auditor sees a lot of the site's data through
them. Grant it the way you would grant any role that sees everything.

Sensitive values are replaced with `[redacted]` as the row is written. What the
call *did* stays readable — the tool, the doctype, the field names, the outcome.

## Scheduled work

One daily job: **expire stale actions**. It moves proposals nobody decided to
Expired after the number of days in Agent Settings.

The job is safe to run by hand and safe to run twice. Expiry is the clock, not a
decision — nobody is recorded as having decided, and the agent may propose the
same thing again.

## On Frappe v15

This is the `version-15` line of the app. It is the same app, with two
differences the framework forces.

**No desk tile.** v15 has no Workspace Sidebar, so the app does not create one.
The workspace is at `/app/agents` — bookmark it, or add your own shortcut.

**Masked fields are not redacted automatically.** v16 lets you mask a field, and
the app feeds that list into two protections: what is replaced with `[redacted]`
in an audit row, and the comparison the extraction gate makes against a master
record. v15 has no such list, so **that source is empty**.

Nothing breaks, but a protection you would get on v16 is not there. Compensate
by naming those fields yourself in **Agent Settings → Sensitive Fields**, which
works identically on both versions and is what the extraction gate reads first.

Everything else on this page applies unchanged.

## Upgrading

Pin a release. `main` moves whenever something lands.

```bash
bench get-app --branch v0.6.0 https://github.com/MalikZu/frappe_agents
bench --site yoursite migrate
```

Agents made before access rules existed keep working. The upgrade writes rules
for the agents whose reach it can state exactly, and names the ones it could
not. Give those rules by hand — until you do, they still reach everything their
user can. See [What an agent may reach](access.md).

## Turning it off

You do not have to uninstall to stop agents. Clear **Global Enabled** and
nothing runs.

To go further, disable each Agent record. The app's own records stay, so the
audit trail of what already happened survives.
