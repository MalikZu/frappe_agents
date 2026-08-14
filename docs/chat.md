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

Some models think before they answer. That shows as a strip above the answer:
open and running while it thinks, then folded into one line — **Thought for 4s**
— the moment it stops. Click the line to read it. The thinking is not the
answer, and it is never what the conversation is stored with.

## Coming back to a conversation

A conversation you reopen is drawn again in full: what you asked, what the agent
thought, every tool it called, everything it said, and any approval still
waiting. It comes from what the run recorded, so it survives a reload and a
different browser.

A document the agent had read comes back too, as the card it always was, in the
state the reading is in now — waiting for your review, accepted, or still being
read. That card is a record, not a message: switching conversations while a
document is being read no longer loses it.

A run that is still going has not recorded anything yet — it writes its history
when it ends. Until then the page remembers it for you, so switching to another
conversation and back picks the run up where it was and carries on live.
Reloading the page in the middle of a run loses that much; the answer lands on
the run either way, and the conversation shows it all once the run ends.

## The document a conversation is about

A chat opened from a form is about that document, and says so in a chip beside
the agent's name. The chip comes back when you reopen the conversation later.

Whether you may see it is asked again every time the conversation loads. If you
have lost read access, the chip says only that there is a document you can no
longer see — not which one.
