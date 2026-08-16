# Getting started

From an empty site to an agent that answers a question. About fifteen minutes,
most of it deciding what the agent is for.

## Before you start

- Frappe **v16**
- Python **3.14** or newer
- An API key for a model provider, or a model you run yourself

## Install

Early software — expect sharp edges and breaking changes. On a real site, pin a
release: `main` moves whenever something lands.

```bash
bench get-app --branch v0.6.0 https://github.com/MalikZu/frappe_agents
bench --site yoursite install-app frappe_agents
```

Installing sets up four roles, two starting access profiles, a catalog of known
providers and models — all switched off and without keys — and the **Agents**
workspace. It changes nothing about how your site already works: until you
create an agent and give it access, there is nothing to run.

## The four roles

| Role | Does what |
|---|---|
| **Agent Manager** | Creates and configures agents, and approves skills |
| **Agent User** | Talks to agents in chat and from a document form |
| **Agent Approver** | Decides the proposals agents write |
| **Agent Auditor** | Reads the tool-call trail |

All four need Desk access. Agent Approver and Agent Auditor are both privileged
in their own way — an approver commits things to your business, and an auditor
reads argument and result summaries across everyone's runs. Grant them the way
you would grant any other role that sees everything.

Keep them on different people where you can. The app enforces that an approver
is not the person who asked for a proposal, but it cannot enforce that your
approver is a genuinely independent reviewer.

## 1. A provider

**Agents → LLM Providers → New.** A provider is a host that will receive your
prompts and your API key.

- **Provider Type** — the wire format: OpenAI Compatible, OpenAI Responses, or
  Anthropic. This is about the shape of the API, not the company: OpenRouter,
  Ollama and most others speak OpenAI Compatible.
- **Base URL** — must be an `https://` address on the public internet.
- **Self Hosted** — tick this only for a model you run yourself. It is what
  allows `http://` and addresses like `localhost` or `10.x`, and it is checked
  again before every request. Redirects are never followed.
- **API Key** — stored as a password field.

The install seeded rows for the providers we know about. Enabling one and
pasting a key is usually quicker than starting from scratch.

## 2. A model profile

**Agents → Model Profiles → New.** A profile is one model on one provider, plus
what the app needs to know about it.

- **Model ID** — as the provider spells it.
- **Context Limit** — how much of a conversation is sent. With none set, the
  agent is sent the last twenty turns.
- **Supports PDF** and **Supports Images** — leave these honest. Document
  extraction sends a real PDF or image to the model, so a profile that claims
  support it does not have fails at the point of use.
- **Allowed Roles** — who may pick this profile. Empty means anyone who may use
  the agent. This is how you keep an expensive model away from casual use.

Tick **Enabled** when the key works.

## 3. An agent

**Agents → Agents → New.** Three decisions matter more than the rest.

**What it may reach.** A new agent reaches nothing. Attach the shipped
**Personal Organizer** or **Site Reader** profile to start, or write access rules
by hand. This is the important field on the form and it has its own page:
[What an agent may reach](access.md).

**Autonomy.** Suggest reads only. Draft reads and creates drafts. Write adds
editing existing drafts. No level lets an agent submit anything — see
[How the agent is held to account](governance.md).

**Instructions.** The system prompt: what this agent is for, in plain words.
Short and specific beats long and hopeful.

Then the rest: a **Model Profile**, optionally **Alternate Profiles** people may
switch to mid-conversation, **Allowed Roles** for who may use it at all, **Show
in Forms** to offer it from the **Ask Agent** button on documents, and **May Read
Files** if it should be able to open attachments.

Two fields are your cost stops. **Max Steps** bounds one run's tool calls.
**Daily Token Budget** bounds the agent across a day, falling back to the
default in Agent Settings when empty. Set them before the first real
conversation, not after the first surprising bill.

Tick **Enabled**.

## 4. Talk to it

**Agents → Agent Chat.** Pick the agent, ask it something it has access to.

![Agent Chat: the conversation rail on the left, and a composer with three chips
— the agent, the model, and the tool count](images/agent-chat.png)

The three chips under the message box are the agent, the model it runs on, and
how many tools its rules add up to. Click the tool count to see them.

Under the answer you will see each tool call it made, one line each. Open one:
those are the arguments it ran with and what came back, under your own
permissions. That log is the fastest way to tell "it cannot see this" from "it
did not think to look" — see [Agent Chat](chat.md).

If a tool call comes back denied, the message says which grant was missing. The
**Effective Access** panel on the Agent form is the other half of that answer:
it lists every granted target and which rows you personally could not use anyway.

## Let an agent set one up

The app also ships **Agents Builder**, switched off and without a model. Give it
a model profile and enable it, and it interviews you about the job and writes an
**Agent Blueprint** — a proposal. It creates nothing. An Agent Manager opens the
blueprint and presses **Create Agent**, and what appears is disabled until
somebody finishes it.

## Where to go next

- [What an agent may reach](access.md) — access rules and profiles
- [How the agent is held to account](governance.md) — the guarantees, and their edges
- [Approvals](approvals.md) — what happens when an agent proposes something
- [Document extraction](extraction.md) — an attachment becomes a draft
- [Agent Chat](chat.md) — the chat surface in detail
