# Privacy

*Last updated: 2026-08-16.*

This app is software you install on your own Frappe site. It is not a service we
run for you. There is no account with us, no server of ours in the path, and
nothing to sign up for.

So the useful question is not "what do we collect" — we collect nothing — but
"where does your data go when the app runs". That is what this page answers.

## What stays on your site

Everything the app creates lives in your own site's database and nowhere else:

- agents, access rules and access profiles
- conversations and the messages in them
- runs, and the audit row written for every tool call
- document extractions and the values held back for review
- your provider records, including the API key, stored as a password field

Uninstalling the app or deleting the records removes them, the same as any other
Frappe data. We have no copy.

## What leaves your site, and to whom

**One destination: the model provider you configure yourself.**

When an agent runs, the app sends that provider whatever the model needs to
answer — your instructions, the conversation, the document fields and file
contents the agent was allowed to read, and your API key so the request is
authorised.

That is the significant privacy fact of using this app, and it is worth being
blunt about: **the provider you choose can see the data your agents read.**
Choose one whose terms you have read, and use the access rules to limit what
agents may reach in the first place.

Which provider is entirely your choice. The app ships a catalog of known
providers, but every one of them arrives **disabled and without a key**, and
nothing is contacted until you enable a row and add your own key. You can point
it at a model you run yourself, in which case nothing leaves your network at all.

Two things the app does to keep that boundary honest:

- A provider's Base URL must be an `https://` address on the public internet
  unless you mark the provider **Self Hosted**, and that is re-checked before
  every request.
- Redirects are never followed. A host that tries to bounce the request
  somewhere else gets an error instead of your API key.

## What we receive

Nothing.

The app contains no telemetry, no analytics, no usage reporting and no
phone-home of any kind. It makes exactly two kinds of outbound request, both to
the provider you configured. It does not contact us, and there is no mechanism
in it by which it could.

If you report a bug, you choose what to put in the report. Please redact
anything you would not want public — see [SECURITY.md](SECURITY.md) for how to
report something privately.

## Who can see what, inside your site

Your own Frappe permissions decide this, and the app narrows them further rather
than widening them. Two roles are worth knowing about:

- **Agent Auditor** can read the tool-call trail across every user's runs, which
  includes argument and result summaries. It is a privileged role.
- **Agent Approver** sees the documents proposed for approval.

Sensitive values are replaced with `[redacted]` as an audit row is written. See
[Running the app](docs/admin.md) for the full picture, including the difference
between Frappe v15 and v16 here.

## Changes

This page describes the software as it behaves today. If a release changes where
data goes, this page changes in the same release, and the release notes say so.
