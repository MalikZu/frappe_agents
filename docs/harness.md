# The agent harness

The loop that drives a run — the message models, the tool protocol and the
event types — is vendored from [huggingface/tau](https://github.com/huggingface/tau)
at tag `v0.3.9`, MIT licensed. It lives in `frappe_agents/harness/`.

It is a pin, not a fork we track. Upstream's `session/` package is left out:
session state is Frappe doctypes here. Local edits are marked
`# frappe_agents patch:` so a deliberate cherry-pick from a later tag can find
them. The package imports pydantic and the standard library only, so the app
still needs no pip dependency of its own.

What the harness never decides: who a run acts as, whether it may run, which
tools exist, and what a tool call is allowed to do. That all stays in
`frappe_agents/runner/` and `frappe_agents/tools/`.
