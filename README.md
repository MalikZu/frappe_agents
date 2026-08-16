<div align="center">
  <img src="https://raw.githubusercontent.com/MalikZu/frappe_agents/main/frappe_agents/public/icons/desktop_icons/solid/frappe_agents.svg" height="72" alt="Agents App for Frappe"/>
  <h1>Agents App for Frappe</h1>
  <p><b>AI agents for Frappe sites. Agents draft. Humans submit. Every call is on the record.</b></p>
  <p>
    <a href="https://github.com/MalikZu/frappe_agents/actions/workflows/server-tests.yml"><img src="https://github.com/MalikZu/frappe_agents/actions/workflows/server-tests.yml/badge.svg" alt="Server tests"/></a>
    <a href="https://github.com/MalikZu/frappe_agents/actions/workflows/harness-tests.yml"><img src="https://github.com/MalikZu/frappe_agents/actions/workflows/harness-tests.yml/badge.svg" alt="Harness tests"/></a>
  </p>
</div>

An agent is a record you create in your site, not a service you deploy.
The permission model is the product.

## Why this one

Most ERP chat assistants are a text box bolted onto a service account with
broad rights. This app is built the other way around:

- **An agent has no rights of its own.** Every run is bound to a real user and
  can never see or do more than that user could by hand.
- **Agents draft. Humans submit.** No code path submits, cancels, or deletes
  without a proposal that a *different* person approves — applied under the
  approver's own permissions.
- **Every call is on the record.** Each tool call writes an audit row — on
  success, denial, and error — that nobody can edit or delete. A shipped report
  shows whether reviewers actually review or just click approve.
- **Document text is data, never instructions.** Everything people wrote —
  comments, emails, field values — reaches the model wrapped as untrusted.
- **One switch turns it all off.** A kill switch a running job actually sees.

## What you get

- **Agent Chat** and an **Ask Agent** panel on any document — with conversation
  history, per-conversation model choice, and live tool progress.
- **A permission matrix, not a tool list.** Per doctype: read, draft new, edit
  drafts, propose, extract — plus row caps. Reusable access profiles, and an
  effective-access panel on the agent that says what the rules actually come to.
- **Approval queue** for anything that commits the business, with separation of
  duties and an edited-before-approval quality metric.
- **Document extraction**: an attached PDF or image becomes a draft for human
  review, with bank and payment details held back until confirmed by hand.
- **Any model provider** over three wire formats (OpenAI-compatible, OpenAI
  Responses and Anthropic) — OpenRouter, Ollama and friends included. The
  provider is a config field, not a dependency.
- **Zero pip dependencies.** The agent loop is a vendored, tested harness;
  nothing new enters your bench's shared environment.

## Requirements

- Frappe v16
- Python 3.14+

## Install

Early software — expect sharp edges and breaking changes. On a real site, pin
a release: `main` moves whenever something lands.

```bash
bench get-app --branch v0.6.0 https://github.com/MalikZu/frappe_agents
bench --site yoursite install-app frappe_agents
```

Installing creates the four roles (Agent Manager, Agent User, Agent Approver,
Agent Auditor) and registers the built-in tools. Then, in the Desk: create an
LLM Provider, a Model Profile, and an Agent — and open Agent Chat.

A new agent reaches nothing until you give it **access rules**: per doctype, may
it read, draft, edit drafts, propose a submit, extract from a file. Attach the
shipped **Personal Organizer** or **Site Reader** profile to start, or enable the
**Agents Builder** and let it interview you. See [What an agent may
reach](docs/access.md).

A provider's **Base URL** is the host that receives your prompts and your API
key, so it must be an `https://` address on the public internet. To point at a
model you run yourself — Ollama on the same box, something on your own
network — tick **Self Hosted** on the provider. That checkbox is what allows
`http://` and addresses like `localhost` or `10.x`, and it is checked again
before every request. Redirects are never followed.

## Docs

**Start here**

- [Getting started](docs/getting-started.md) — install, the four roles, and the
  path from an empty site to an agent that answers.
- [How the agent is held to account](docs/governance.md) — identity binding, the
  draft/submit wall, the audit trail, the kill switch, and where each guarantee
  stops.

**Setting it up**

- [What an agent may reach](docs/access.md) — access rules, profiles, the two
  shipped profiles, and the Agents Builder.
- [Configuring an agent](docs/agents.md) — the Agent form field by field:
  autonomy, skills, alternates, and the two cost stops.
- [Providers and models](docs/models.md) — the three wire formats, the
  self-hosted trust boundary, and the shipped catalog.

**Using it**

- [Agent Chat](docs/chat.md) — conversations, the composer chips, and the
  document a conversation is about.
- [Files in chat](docs/files-in-chat.md) — naming a file an agent may read,
  uploading one, and where uploads live.
- [Approvals](docs/approvals.md) — proposals, who may decide one, and what the
  Review Quality report is actually telling you.
- [Document extraction](docs/extraction.md) — an attachment becomes a draft, and
  the sensitive-field gate that holds payment details back.

**Running it**

- [Running the app](docs/admin.md) — settings, the audit trail, scheduled work,
  and upgrading.
- [The agent harness](docs/harness.md) — the vendored run loop.

## License

MIT
