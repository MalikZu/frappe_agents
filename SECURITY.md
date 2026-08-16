# Security

This app gives a language model a way into a Frappe site. The permission model
is the product, so a hole in it is the most serious kind of bug this project
can have. Reports are welcome.

## Reporting

**Do not open a public issue.**

Use GitHub's private vulnerability reporting: go to the **Security** tab of
[the repository](https://github.com/MalikZu/frappe_agents), then **Report a
vulnerability**. That opens a private thread with the maintainer.

If you do not see that option, open a normal issue asking for a security
contact — the title alone, no details — and you will get a private channel to
reply on.

Please include the version, what an attacker can do, and the smallest steps that
show it. A tool call or an Agent Tool Call row that demonstrates it is ideal.

One maintainer reads these. Expect an acknowledgement within a few days, not
within hours. If a fix is needed, it ships as a patch release and the advisory
is published once sites have had a chance to upgrade.

There is no bug bounty. Credit is offered in the advisory unless you prefer not
to be named.

## Supported versions

Pre-1.0. **Only the latest release gets fixes.** Pin a release on a real site
and upgrade to take a security fix — see [Running the app](docs/admin.md).

## What counts as a vulnerability

Anything that breaks one of the guarantees the app is built on:

- **Privilege escalation.** An agent seeing or doing something the user it runs
  as could not do by hand.
- **A submit, cancel or delete without an approval**, or an approval applied by
  the person who asked for it.
- **An access-rule bypass** — a tool reaching a doctype or a verb the rules do
  not grant.
- **A missing or forgeable audit row**, or any path that edits or deletes one.
- **A secret in a readable place** — an API key or a password field reaching an
  audit row, an error message, or a model.
- **The sensitive-field gate writing a value nobody confirmed**, or leaking a
  masked master value into the flags.
- **Prompt injection that escalates.** Text in a document that makes the agent
  reach data or an action outside its grants. Injection that merely produces a
  wrong or rude answer is a bug, not a vulnerability.
- **A self-hosted provider check that can be bypassed** to make the server send
  prompts or keys somewhere it should refuse.

## What does not count

- **The model being wrong.** Nothing here makes an extracted figure correct or a
  draft sensible. That is why a human approves.
- **A permission you granted.** An agent using access its user genuinely has,
  because the rules were written wide, is configuration — start with the
  [shipped profiles](docs/access.md).
- **An Agent Auditor seeing a lot.** The audit trail carries argument and result
  summaries across everyone's runs. That is the design; grant the role
  accordingly.
- **Anything requiring System Manager**, or a bench shell. Someone with those
  does not need this app to do damage.

If you are not sure which side of the line something falls on, report it
privately and we will work it out.
