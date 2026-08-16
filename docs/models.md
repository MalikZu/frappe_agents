# Providers and models

Two records stand between an agent and a model. A **LLM Provider** is a host you
send prompts to. A **LLM Model Profile** is one model on that host.

The provider is a configuration field, not a dependency. The app adds no
provider SDK to your bench.

## Providers

**Agents → LLM Providers.**

### Provider Type

This is the wire format — the shape of the API, not the company.

| Type | Use it for |
|---|---|
| **OpenAI Compatible** | The chat-completions shape. OpenRouter, Ollama, and most others |
| **OpenAI Responses** | OpenAI's newer Responses API |
| **Anthropic** | Anthropic's messages API |

If you are unsure, start with OpenAI Compatible. Most hosts speak it.

### Base URL

The Base URL is the host that receives your prompts and your API key. Treat it
as a trust boundary, because it is one.

It must be an `https://` address on the public internet. Redirects are never
followed — a provider cannot move your key somewhere else after the fact.

### Self Hosted

Tick **Self Hosted** for a model you run yourself: Ollama on the same box, or
something on your own network.

This checkbox is what allows `http://`, and addresses like `localhost` or
`10.x`. It is checked again before every request, not only when you save.

### API Key

Stored as a password field. It is never written into an audit row.

### Enabled

Untick to stop every profile on this provider at once.

## Model profiles

**Agents → Model Profiles.** One profile is one model, plus what the app needs
to know about it.

### Model ID

Spell it exactly as the provider does.

### Context Limit

How much of a conversation is sent to the model.

A long conversation walks past every model's limit, so the agent is sent the end
of it rather than all of it. The oldest turns are dropped until the rest fits,
and the newest message is always sent. A run that dropped turns ticks **History
Truncated** on the Agent Run.

Leave it empty and the agent is sent the last twenty turns.

### Supports PDF and Supports Images

Keep these honest.

Document extraction sends a real PDF or image to the model. Nothing in the app
turns a PDF page into a picture first. A profile that claims support it does not
have fails at the moment somebody uses it. See
[Document extraction](extraction.md).

### Cost per million

**Cost Input Per Million** and **Cost Output Per Million** are what you pay.
They are what makes a token budget mean money rather than an abstract number.

### Allowed Roles

Who may pick this profile. Empty means anyone who may use the agent.

This is how you keep an expensive model away from casual use. It is checked
again every time somebody switches model mid-conversation.

### Enabled

Tick it when the key works.

## The shipped catalog

Installing the app seeds rows for providers and models we know about. All of
them arrive **disabled and without keys**.

Enabling one and pasting a key is usually quicker than starting from scratch,
and it saves you looking up a context limit. Nothing is sent anywhere until you
enable a row and add a key.

The seed is idempotent and never overwrites your edits. If you change a seeded
profile, your version stands.

## PDF parsing

**Agent Settings → PDF Parser Engine** decides how a PDF reaches the model.

- **native** — the document goes to the model itself. The model sees the page.
- The **OCR engines** hand the model a text layer instead. This is faster and
  cheaper, and it puts a step between the document and the answer. The reviewer
  never sees that text layer.

Use native when the layout carries meaning, which on an invoice it usually does.

## When a call fails

A failed model call is recorded on the Agent Run, and the chat shows it in the
log with **Try again**.

Some endpoints quote the credential they just rejected. Your API key is taken
back out of a provider's error text before it reaches the run, on all three
wires — so a failure message is not a place your key turns up.
