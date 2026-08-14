"""Reading an attached document back to the person who attached it.

Extraction turns a document into a draft nobody sees until a human reviews it.
Reading is the other half and a different question: someone asks what a file
says, and the answer is text in the conversation.

That is safe where extraction is walled because the wall is on the write path.
Reading returns content to a user who could already open the file — the same
resolver, the same File permission, the same anchor rule. The risk that is left
is prompt injection, so every lane's answer comes back inside
`<untrusted source="File …">`, under the standing rule that untrusted text is
never an instruction.

Nothing here is a new dependency: pypdf, openpyxl, xlrd, filetype and Pillow are
all pinned by frappe, and docx and pptx are read with `zipfile` and
`xml.etree` — about a hundred lines of our own.
"""
