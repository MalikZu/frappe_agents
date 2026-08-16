# Contributing

Thanks for looking. This is early software with one maintainer, so this page is
short and honest about what helps.

## Open an issue before a pull request

**The most useful thing you can send is a good issue, not a patch.**

Deciding the right change here is usually harder than writing it. A bug report
with the version, the site setup, what you expected, what happened, and the
relevant Agent Tool Call row is worth more than a fix for the wrong problem.

So: open an issue first and let's agree on the shape. Then send the PR. A PR
that arrives with no prior discussion may still be welcome, but expect it to
take longer, and expect the answer sometimes to be "the fix belongs elsewhere".

This is not a closed project. It is a small one.

## What a change must not break

These are the guarantees the app exists to make. A change that weakens one is
not a trade-off to be argued at review time — it is the thing we will not do.

- **An agent has no rights of its own.** Every run is bound to a real user, and
  it can never see or do more than that user could by hand.
- **Agents draft, humans submit.** No code path may submit, cancel, or delete
  without an approval a different person gives.
- **One door to data.** Tool code uses the permission-checked ORM:
  `frappe.get_list`, or `frappe.get_doc` after `has_permission`. Never
  `frappe.db.sql`, `frappe.get_all`, or `ignore_permissions=True`.
- **Every call is on the record.** A tool call writes its audit row on success,
  denial and error alike.
- **Document text is data.** Text a person authored reaches the model wrapped as
  untrusted, never as instruction.
- **Frappe only.** Never import or depend on `erpnext`. ERP-specific behavior
  goes in optional integration modules, off by default.
- **Zero new dependencies.** Nothing new enters a customer's bench environment.

If your change needs one of these to bend, say so in the issue up front. That is
the conversation worth having, and it is a design decision, not a review nit.

[How the agent is held to account](docs/governance.md) explains why each of
these exists.

## Setting up

```bash
bench get-app https://github.com/MalikZu/frappe_agents
bench --site yoursite install-app frappe_agents
```

Requires Frappe v16 and Python 3.14+.

## Running the tests

```bash
bench --site yoursite run-tests --app frappe_agents
```

Run them against a site you do not mind polluting. The suite creates records.

A bug fix should come with a test that fails before your change and passes
after. That test is the part that stops the bug coming back.

## Before you push

CI runs three things, and you can run all of them locally:

```bash
pre-commit run --all-files
```

That covers formatting and linting (ruff) and the security rules (semgrep,
using frappe's own ruleset).

Commit messages are linted too. The format is `<type>: <what happened>`, with
the type one of `feat` `fix` `docs` `chore` `refactor` `test` `ci` `perf`:

```
fix: keep a cancelled run from writing its last tool call

- check the kill switch again before the audit row is written
- add a test that cancels mid-call
```

Keep commits small and focused — one coherent change each, rather than a whole
branch squashed into one. Do not add `Co-Authored-By` trailers.

## Docs ship with the change

**If a user can see your change, its doc goes in the same PR.** If there is no
page for the thing you changed, that is the finding — write it or say so in the
PR. A screenshot in `docs/images/` your change invalidates counts too.

Docs are for users: short sentences, plain words, no filler. Procedures follow
ASD-STE100 principles — one instruction per sentence, active voice, one term per
concept. Explanatory pages keep their reasoning.

## Review

One maintainer, reviewing around other work. Expect days, not hours.

If a PR grows past what the issue discussed, expect to be asked to split it.
Merges are squashed.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).

## Licence

MIT. By contributing you agree your work ships under it. There is no CLA to
sign.
