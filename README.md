<div align="center">
  <img src="frappe_agents/public/icons/desktop_icons/solid/frappe_agents.svg" height="72" alt="Agents App for Frappe"/>
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
- **Approval queue** for anything that commits the business, with separation of
  duties and an edited-before-approval quality metric.
- **Document extraction**: an attached PDF or image becomes a draft for human
  review, with bank and payment details held back until confirmed by hand.
- **Any model provider** over two wire formats (OpenAI-compatible and
  Anthropic) — OpenRouter, Ollama and friends included. The provider is a
  config field, not a dependency.
- **Zero pip dependencies.** The agent loop is a vendored, tested harness;
  nothing new enters your bench's shared environment.

## Requirements

- Frappe v16
- Python 3.14+

## Install

Early software — expect sharp edges and breaking changes. On a real site, pin
a release: `main` moves whenever something lands.

```bash
bench get-app --branch v0.4.0 https://github.com/MalikZu/frappe_agents
bench --site yoursite install-app frappe_agents
```

Installing creates the four roles (Agent Manager, Agent User, Agent Approver,
Agent Auditor) and registers the built-in tools. Then, in the Desk: create an
LLM Provider, a Model Profile, and an Agent — and open Agent Chat.

## Docs

- [Agent Chat](docs/chat.md) — conversations, the composer chips, and the
  document a conversation is about.
- [The agent harness](docs/harness.md) — the vendored run loop.

## License

MIT
