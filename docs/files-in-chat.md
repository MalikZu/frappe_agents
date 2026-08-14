# Files in chat

An agent can be asked to read a file that is already in your system. It cannot
reach anything else.

## Naming a file

Anywhere a tool takes a file you can write it the way you would say it:

- the File record name — `d4b2320660`
- a link to it on this site — `/private/files/CV.pdf` or `/files/logo.png`
- the same link with the site in front — `https://your-site/private/files/CV.pdf`
- the filename on its own — `CV.pdf`, as long as only one file has it

If several files share a name, the agent is told which ones and asks you to pick.
A link to another site is refused: only files already in this system can be read.
Nothing is fetched from the internet, so what a reviewer opens later is the same
bytes the agent was given.

Naming a file is not a way in. Every form ends at the same File record and the
same permission check — if you could not open it in the desk, you cannot hand it
to an agent.

## Uploading from the chat

The paperclip in the composer uploads a file. It is stored privately and attached
to a record:

- in a plain chat, to **the conversation** you are in
- in **Ask Agent** on a form, to **the document you have open** — the better
  anchor, because the file belongs to that record

A chip appears under the message box for each file, and the message you send
names them, so the agent knows what it was handed. Take a chip off with `×` if
you change your mind before sending; the file stays where it was uploaded.

Attaching a file to an empty chat opens the conversation first — a file has to
hang off something. The conversation gets its name from your first message, as
it always did.

## Why it is attached to something

An agent may only read a file that is attached to a record you can read. A loose
private file has no provenance: nothing says where it came from or who supplied
it. A conversation does — it records who uploaded the file, to which agent, and
what was being discussed around it, which is what a reviewer checks before
accepting anything extracted from it.

Files uploaded in chat are visible exactly where files are visible today, and
nothing new is shared: the conversation is yours, so a file on it is not readable
by someone who cannot read the conversation. Deleting a conversation follows
Frappe's normal attachment lifecycle.
