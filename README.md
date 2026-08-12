# Frappe Agents

AI agents for Frappe sites. An agent is a record you create in your site,
not a service you deploy.

**Status: early development.** Nothing here is ready to install yet.

## The idea

- An agent runs as a real user. It can never see or do more than that user could.
- Agents draft documents. Only humans submit them.
- Every tool call an agent makes is logged on the site.
- Built for plain Frappe. ERPNext is optional, never required.

## Requirements

- Frappe v16
- Python 3.14+

## Install

Early software — expect sharp edges and breaking changes.

```bash
bench get-app https://github.com/MalikZu/frappe_agents
bench --site yoursite install-app frappe_agents
```

Installing creates the four roles (Agent Manager, Agent User, Agent Approver,
Agent Auditor) and registers the built-in read tools. Then, in the Desk:
create an LLM Provider, a Model Profile, and an Agent — and open Agent Chat.

## License

MIT
