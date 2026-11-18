# Agent Chat

Agent Chat is the desk page where a person talks to an agent. The same chat also
opens inside a form, from the **Ask Agent** button.

## Your conversations

The rail on the left lists your own conversations, most recent first, under
Today, Yesterday and Earlier. Each row names the agent, the first thing you
said, and how long ago it happened. Click a row to carry on with it; the page
does not reload. Hover a row and click the pencil to give it a name of your
own — no model ever writes a title. **New chat** sits at the top of the
rail and at the top right of the page. The `«` button folds the rail away, and
it stays folded until you open it again.

You see your conversations and nobody else's, and no conversation is deleted
from here: an approval record links back to the run that asked for it.

## The composer

The paperclip attaches a file. It is uploaded privately and kept on the
conversation — or on the document, when the chat was opened from a form — and
the message you send names it, so the agent knows what it was handed. See
[Files in chat](files-in-chat.md).

Three chips sit next to the message box.

**The agent.** Click it to talk to a different agent. That starts a new
conversation rather than continuing this one, because a conversation is audited
against one agent's grants — you are asked first if the current one has anything
in it.

**The model.** It shows the model profile this conversation runs on. If the
agent's owner has listed alternates and your roles pass them, click to choose
one; the choice sticks for the rest of the conversation, and every run still
records the profile it actually ran with. An agent with no alternates shows the
name and nothing to click.

**The tools.** A count you can open: every tool this agent may call, with its
one-line description and whether it reads, drafts, writes or submits. It is a
list, not a switch. Granting and scoping happen on the Agent form, where changes
are versioned.

## Watching the agent work

The answer appears a few words at a time, as the model writes it. Under it sit
the tool calls, one line each. Click a line — or reach it with the keyboard and
press Enter — and it opens: the arguments the call ran with, and what it handed
back. That is the same call your own permissions carried, so it shows you
exactly what ran on your behalf. A long result is stored cut short, and the box
says so where it was cut.

The log follows the text down the page, unless you have scrolled up to read
something earlier; then it stays where you put it.

Anything the agent set in `code` — a File name, a fieldname, a doctype — copies
when you click it. It stays ordinary text otherwise: select it, read it, leave
it. Hover an answer and a copy button appears in its corner; that one copies the
whole message, as the markdown it was written in, and it is the one the keyboard
reaches.

Some models think before they answer. That shows as a strip above the answer:
open and running while it thinks, then folded into one line — **Thought for 4s**
— the moment it stops. Click the line to read it. The thinking is not the
answer, and it is never what the conversation is stored with.

## When the agent asks for an approval

Some things an agent only asks for: a submit, a cancel, a workflow action, or a
change to a live record such as a ticket. When it asks, the chat shows a card:
**Waiting for an approver**, the document, and the agent's reason. A card for a
field change also lists each field and its new value. Someone else decides it —
not you, if you asked, and not the person who owns the agent. The approvers who
could decide it are notified, and you are notified when it is decided or
expires. The card changes when that happens. See
[After a proposal is decided](#after-a-proposal-is-decided) and
[Approvals](approvals.md).

## When something does not go through

A message that could not be sent says so in the log, in the server's own words,
with **Try again** beside it. What you typed and the files you attached go back
into the composer, so nothing is retyped, and one failure offers one retry —
you cannot end up with the same message running twice. A file that could not be
attached and a model the server would not switch to are said the same way.

## Coming back to a conversation

A conversation you reopen is drawn again from its end: what you asked, what the
agent thought, every tool it called, everything it said, and any approval still
waiting. It comes from what the run recorded, so it survives a reload and a
different browser.

A long conversation comes back as its last turns, not all of them, with **Show
earlier messages** above them. Click it and the turns before those are added on
top, without moving what you were reading. Click again for the ones before
those. The button goes when you reach the start of the conversation. Nothing was
ever deleted — the runs are also in the Agent Run list, under the conversation.

A document the agent was asked to turn into a draft comes back too, as the card
it always was, in the state that extraction is in now — waiting for your review,
accepted, or still being read. The card is drawn from the extraction record, not
from the message: switching conversations while a document is being read no
longer loses it.

A run that is still going has not recorded anything yet — it writes its history
when it ends. Until then the page remembers it for you, so switching to another
conversation and back picks the run up where it was and carries on live.
Reloading the page in the middle of a run loses that much; the answer lands on
the run either way, and the conversation shows it all once the run ends.

## After a proposal is decided

A proposal card follows the proposal to its end. Its title says where it stands:

| Title | Means |
|---|---|
| **Waiting for an approver** | Nobody has decided yet. |
| **Approved and applied** | An approver approved it, and the change went through. |
| **Rejected** | An approver said no. The card shows what they wrote. |
| **Approved, but it did not go through** | The document refused the change. The card shows why. |
| **Expired with no decision** | Nobody decided in time. |

For a workflow action, the title names the action and the state it was
proposed from, for example **Rejected: Approve (from Draft)**.

The card changes as soon as the decision is made, while the chat is open. A
reloaded chat shows the state the proposal is in now. A reloaded card for a
field change names the fields only. The new values show only on the card drawn
while the agent was proposing, and on the approval form.

**Review the proposal** shows only to a person who could decide the proposal.
You cannot decide a proposal you asked for, so on your own cards it does not
show. Once the proposal is decided, **Open the proposal** opens the record.

If you can no longer read the document, the card keeps its title and says only
that it is about a document you cannot read.

The agent is told too. Its next turn starts with a short note, from the system
and not from you, that says how each decided proposal ended. The approver's note
is marked as text a person wrote, so the agent reads it and does not obey it.
The agent does not propose a rejected action again unless you ask it to. See
[Approvals](approvals.md#what-the-agent-learns).

## How much of the conversation the agent reads

Every model has a context limit, and a long conversation walks past it. So the
agent is sent the end of the conversation rather than all of it: the oldest
turns are left out until the rest fits, and what you just asked is always sent.
A run that left turns out ticks **History Truncated** on the Agent Run, which is
how you tell a conversation that was cut from a model that forgot.

The limit comes from the model profile the conversation runs on. A profile with
no limit set sends the last twenty turns, as it always has.

## The document a conversation is about

A chat opened from a form is about that document, and says so in a chip beside
the agent's name. The chip comes back when you reopen the conversation later.

Whether you may see it is asked again every time the conversation loads. If you
have lost read access, the chip says only that there is a document you can no
longer see — not which one.
