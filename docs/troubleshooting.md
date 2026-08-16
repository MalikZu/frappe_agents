# Troubleshooting

Symptoms first. Most of these are configuration, and the app usually tells you
which one — the denial message names what was missing.

## Nothing happens at all

**Every agent is silent, on every site.**

Check **Agent Settings → Global Enabled**. That is the kill switch, and with it
clear every tool call is refused.

**You cleared the switch but a run keeps going.**

Flip it by **saving Agent Settings**, not by writing the field some other way. A
value written straight into the database is not published to a job already
running. See [Running the app](admin.md).

## The agent cannot see something

**A tool call comes back Denied.**

Read the message — it names the grant that was missing. Then open the Agent and
look at **Effective Access**. It lists every target the rules grant, what the
agent may do with each, and where the grant came from.

Three things it separates for you:

- **The rules do not grant it.** Add a rule, or attach a profile.
- **The user cannot do it either.** The panel shows this as rows you personally
  could not use. Fix the user's permissions, not the agent.
- **Autonomy is withholding it.** A Suggest agent is not offered the proposal
  tools whatever the rules say.

**A new agent reaches nothing.** That is deliberate. Attach **Personal
Organizer** or **Site Reader** to start. See
[What an agent may reach](access.md).

**The agent cannot open an attachment** even though it can read the document.
Tick **May Read Files** on the Agent. Reading a file is a switch, not a rule.

## The agent does not appear

**It is missing from the chat picker.**

- **Enabled** is unticked.
- **Allowed Roles** is set and you do not hold one of them.
- It has no **Model Profile**, or the profile's own **Allowed Roles** exclude
  you.

**Agents you do not recognise are in the picker** — names beginning `FA `.

The test suite creates records. Do not run it against a site you care about; use
a scratch site. Disable or delete the leftovers.

## The Agents tile or workspace is missing

The workspace lives at `/app/agents`. If the desk tile is gone or 404s, go there
directly.

If the sidebar entry is missing too, run `bench --site yoursite migrate` and then
`bench --site yoursite clear-cache`. On a site upgraded from an older release the
sidebar row may need the migrate to appear.

## The model call fails

**An error mentioning the provider.**

- Check the **Base URL**. It must be `https://` on the public internet unless
  **Self Hosted** is ticked.
- Check the key. A provider that rejects a credential reports it here — your key
  itself is stripped from that message.
- Check the **Provider Type** matches the API the host actually speaks. Most
  hosts are OpenAI Compatible.

**A run ends on a rate limit.** A busy provider can end a run. Try again, and
consider a profile on a less contended model for high-volume agents.

**The run stops at a step count.** That is **Max Steps** on the Agent doing its
job. Raise it only if the agent genuinely needs more turns.

**Everything stops at the same time each day.** That is the **Daily Token
Budget**, on the Agent or the fallback in Agent Settings.

## Extraction

**It refuses before reading anything.**

In the order the checks run: read permission on the file, permission to create
the target doctype, file size, page count, then your daily cap. All four limits
are in Agent Settings.

**It refuses the file type.** Extraction takes PDFs and images only. That is
narrower than what an agent can read into a conversation.

**It fails as soon as the model is called.** The model profile claims a format it
does not have — check **Supports PDF** and **Supports Images**.

**The draft is missing the bank details.** That is the gate working, not a bug.
A field you named in **Sensitive Fields** is never written by extraction. Confirm
it field by field on the review screen.

**Nothing is ever withheld.** **Agent Settings → Sensitive Fields** is empty. It
is the entire configuration of that gate. See
[Document extraction](extraction.md).

## Approvals

**Approving fails and the row says Failed.**

Often correct. Submitting re-runs the document's full validation, so a draft
that was valid when the agent built it can fail now — a closed period, a credit
limit, a field someone cleared. The reason is on the row.

**You cannot approve.** You are the person who asked, the user the run acted as,
or the agent's owner. Separation of duties needs someone else.

**It asks you to read the document again.** It changed after the agent proposed
it. That check is deliberate — approving means approving the version you saw.

**A workflow doctype is refused.** Use the workflow's own transitions. A
server-side submit would jump every approval step in it.

**Nothing can be approved.** The kill switch is off. Rejecting still works.

## Still stuck

Open **Agents → Activity → Tool Calls** and read the run. Every call is there —
success, denial and error — with its arguments and what came back. That log
answers "it cannot see this" versus "it did not think to look" faster than
anything else.

If it looks like a bug, [open an issue](https://github.com/MalikZu/frappe_agents/issues)
with the version and the relevant row. If it looks like a security problem, do
not open a public issue — see [SECURITY.md](../SECURITY.md).
