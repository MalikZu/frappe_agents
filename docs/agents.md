# Configuring an agent

The Agent form, field by field. What the agent may reach is the largest decision
and has its own page: [What an agent may reach](access.md).

![The Agent form, showing Run As, Model Profile, Alternate Model Profiles,
Autonomy, Instructions, Allowed Roles, and the start of the Access
section](images/agent-form.png)

## Identity

**Run As** decides whose permissions the agent uses.

- **Session User** — the agent acts as the person chatting. Use this unless you
  have a reason not to.
- **Service User** — the agent acts as one account you name.

A service user runs with no human in the loop. Its permissions are the whole
blast radius, so the form checks it:

- A service user is required when you pick this mode.
- Administrator and Guest are refused.
- An account with **System Manager** is refused. Use an account with narrow
  roles.
- An account with no User Permissions gets a warning. It can reach every record
  its roles allow.

## Autonomy

**Autonomy** sets the highest class of tool the agent may call.

| Autonomy | The agent may call |
|---|---|
| **Suggest** | Read tools |
| **Draft** | Read and Draft tools |
| **Write** | Read, Draft and Write tools |

Every built-in tool is Read or Draft. Creating a draft, editing one, extracting
a document and proposing a submit are all **Draft**. Nothing that ships today is
Write.

So Draft and Write currently reach the same tools. Write exists as the ceiling
for a Write-class tool another app registers. Choose **Draft** unless you have
such a tool and mean to allow it. See [Built-in tools](tools.md).

No level lets an agent submit, cancel or delete. To commit something, the agent
writes a proposal and a different person decides. See
[Approvals](approvals.md).

Autonomy and access rules are two different limits. Autonomy sets the strongest
verb the agent may use anywhere. A rule sets which doctypes it may use verbs on.
The agent gets the smaller of the two.

## Instructions

**Instructions** is the system prompt. Write what this agent is for, in plain
words.

Short and specific beats long and hopeful. The app already tells the model the
rules it runs under — that an agent proposes rather than submits, and that
document text is data. You do not need to repeat those.

## Model

**Model Profile** is the model the agent runs on.

**Alternate Profiles** are the other profiles a person may switch to during a
conversation. Leave it empty and the model chip in chat shows a name and nothing
to click.

A switch is checked twice: the profile must be on the agent's alternate list,
and the person's roles must pass the profile's own **Allowed Roles**. Both are
re-checked at the time of the switch, because an alternate list and a person's
roles both change. Every run records the profile it actually ran with.

## Who may use it

**Allowed Roles** limits which people may pick this agent. Empty means anyone
with the Agent User role.

This narrows *which agents a person sees*. It does not widen what the agent can
do. The ceiling is still the running user's own permissions.

## Files

**May Read Files** lets the agent open attachments.

Reading a file is not doctype-shaped, so it is one switch rather than a rule.
With it off, the agent cannot read an attachment even when it may read the
document the file hangs off. See [Files in chat](files-in-chat.md).

## Where it appears

**Show in Forms** offers the agent from the **Ask Agent** button on documents.
**Form Doctypes** limits that button to the doctypes you name.

## Skills

A **skill** is a reusable block of instructions. Write one once, attach it to
several agents.

A skill reaches a model only while its status is **Approved**, and approving one
has rules:

- Only an **Agent Manager** may approve.
- The approver cannot be the person who wrote it.
- Every skill is created as **Draft**, whatever created it. A fixture, an
  import, or an API call cannot produce an approved skill.
- Changing the body sends the skill back to Draft. An approval covers one exact
  text; change the text and the approval is spent.

**Applies To Doctypes** limits a skill to the doctypes you name. Empty means
every doctype.

The **Notes** field is for reviewers. It is never sent to a model.

## Cost and runaway stops

**Max Steps** limits how many turns one run may take. A model that loops stops
here.

**Daily Token Budget** limits the agent across a day, over all its runs. Leave it
empty to use the default in Agent Settings.

Set both before the first real conversation. They are the only two things
standing between a misconfigured agent and a large bill.

## Enabling

**Enabled** switches the agent on. A disabled agent does not appear in chat.

A new agent reaches nothing until you give it access rules. That is deliberate:
the first thing you write on an agent is what it is for.
