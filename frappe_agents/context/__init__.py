"""Document context assembly.

The manifest is the entry point: it tells the model what exists around a
document — counts, never content — so the model can ask for one slice at a time
instead of being handed a document dump it did not need.

Two rules run through every module here:

- Read permission on the focal document is checked once, up front, and nothing
  is assembled until it passes. Timeline, version and attachment rows are then
  fetched the way frappe core's `get_docinfo` does, with `get_all` and explicit
  limits, because `get_list` on Comment and Version raises for ordinary users
  and silently drops rows on Communication and ToDo. Every linked document is
  re-checked individually — a link is a different document, not part of this one.
- Text a human authored is data. It reaches the model wrapped in
  `<untrusted>` and never as instruction.
"""
