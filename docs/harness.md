# The agent harness

`frappe_agents/harness/` is the loop that drives a run — message models, tool
protocol, event types — vendored from [huggingface/tau](https://github.com/huggingface/tau)
at tag `v0.3.9`, commit `420ae60`, MIT licensed. It imports pydantic and the
standard library only, so the app still needs no pip dependency of its own.

It is a pin, not a fork we track: every local change is marked
`# frappe_agents patch:`, so an upgrade stays a deliberate cherry-pick. The
event contract is `harness/events.py` and `harness/provider_events.py`. The
tests here are named `*_test.py` because Frappe's runner imports every `test_*`
file it finds and these want pytest, which the bench does not have — run them
yourself with `pytest frappe_agents/harness/tests`.
