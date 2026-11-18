# Outside clients (MCP)

An MCP client is an AI assistant that runs outside your site: Claude Code, an
IDE, a desktop assistant. The Model Context Protocol lets it call tools on a
server. This app can be that server, for any client that can send an API key in
a header. Hosted connectors that only sign in with OAuth cannot connect yet.

The client calls the same tools the in-app agent calls, through the same door.
Every call runs as a real user, is checked against that user's permissions and
the agent's access rules, and leaves an Agent Tool Call row. Drafts stay drafts.
Proposals wait for a different person to approve them.

It is off by default, on the site and on every agent.

## The trust boundary

In chat, you choose the model and you write its instructions. Over MCP, you do
neither. The model belongs to whoever runs the client. Plan around three
consequences.

**Document text leaves for the client's model.** Whatever a tool returns goes to
the vendor the client uses, not to a provider you configured. Turning MCP on is
a decision about where your data may go.

**The model may ignore you.** The endpoint sends the same rules chat sends: text
in `<untrusted>` tags is data, and a proposal is not an act. A client is free to
drop them. So nothing depends on them. What holds is the wall itself: the user's
permissions, the agent's rules, the Draft cap below, and the human who approves.

**The wall is the same, and a little narrower.** Over MCP an agent:

- never goes past Draft, whatever its Autonomy says;
- is not offered `extract_document` or `read_document`, because both can spend
  your model tokens for a client that brings its own model, and a session does
  not count tokens against the budget;
- is not offered the Agents Builder tools, and may not draft an Agent Blueprint
  even when a rule allows it, because designing agents is configuration, not
  work;
- is not offered tools other apps registered.

These limits live in the tool chokepoint, not only in the tool list. A client
that calls a hidden tool by name is refused, and the refusal is audited.

| | Chat | MCP |
|---|---|---|
| Who the run acts as | The person chatting | The user whose API key the client holds |
| Highest capability | The agent's Autonomy | Draft |
| Who writes the model's instructions | You | The client |
| Where document text goes | Your provider | The client's vendor |
| Audit row on every call | Yes | Yes |
| Submit, cancel, delete | Never | Never |

## Who the client acts as

Every request carries a Frappe API key and secret. The endpoint resolves the key
to a user and binds the session to that user. It refuses Administrator, Guest,
disabled users and website users.

Give each client its own user, with only the roles it needs. A client holds a
long-lived key, and anyone who copies the key gets that user.

**Never give an MCP key to a user who holds Agent Approver.** The key also opens
Frappe's ordinary REST API, and an approver's key there can approve other
people's proposals. A model fooled by text it read could do exactly that.

## Turn it on

An administrator does this once per site.

1. Open **Agent Settings**.
2. Tick **MCP Enabled**.
3. Check **MCP Requests per User per Minute**. The default is 60.
4. Check **MCP Tool Calls per Session**. The default is 50.
5. Save.

Then open each agent a client may use.

1. Open the **Agent**.
2. Confirm that **Run As** is **Session User**. A service-user agent cannot be
   used over MCP.
3. Confirm that its access rules are the ones you want a client to have.
4. Tick **Allow MCP**.
5. Save.

## Connect a client

Do these steps as the person who will use the client.

1. Ask an administrator for a user that holds **Agent User** and the roles the
   work needs.
2. Open that user in the Desk.
3. Go to **Settings**, then **API Access**.
4. Click **Generate Keys**.
5. Copy the API secret now. Frappe shows it once.
6. Copy the API key from the form.
7. Build the endpoint address from your site and the agent's name:

   ```text
   https://your.site/api/method/frappe_agents.mcp.server.handle?agent=Site%20Reader
   ```

   Write a space in the agent's name as `%20`.

8. Add a server in your client with transport **HTTP** (also called
   *Streamable HTTP*).
9. Set the address to the one from step 7.
10. Add the header `Authorization: token <api_key>:<api_secret>`.

For Claude Code, steps 8 to 10 are one command:

```bash
claude mcp add --transport http frappe "https://your.site/api/method/frappe_agents.mcp.server.handle?agent=Site%20Reader" --header "Authorization: token <api_key>:<api_secret>"
```

A client that only connects through OAuth, such as a hosted connector, cannot
connect yet. See [What it does not do yet](#what-it-does-not-do-yet).

## Sessions and limits

A client opens a session when it connects. The session is one **Agent Run** with
**Surface** set to **MCP**, and every tool call in it is an Agent Tool Call row
under that run.

A session ends when:

- the client disconnects with DELETE;
- nobody uses it for 30 minutes;
- it is 8 hours old;
- it reaches **MCP Tool Calls per Session**;
- the same user opens a sixth session on the same agent. The oldest ends;
- the kill switch is cleared. The run is then **Cancelled**.

The client then gets a 404. A client that follows the protocol opens a new
session by itself. If yours shows an error instead, reconnect it. An hourly job
ends sessions a client abandoned.

Each user may send **MCP Requests per User per Minute** requests. Past that the
client gets a 429. The agent's **Daily Token Budget** is checked before every
tool call. Nothing offered over MCP calls your model, so MCP sessions add no
tokens to that budget.

## Reading what a client did

1. Open the **Agent Run** list.
2. Filter **Surface** to **MCP**.
3. Open a run.

The run's **Input Message** names the client and the address it connected from.
The client reports its own name, so read it as a label, not as proof.

The tool calls under the run read exactly like chat's. See
[Reading a tool call](tools.md#reading-a-tool-call).

## What it does not do yet

- **No OAuth.** Only API key and secret. OAuth comes with a scope of its own, so
  that a token issued for something else cannot reach the tools.
- **No streaming.** One JSON answer per request. No event stream, no messages
  from the server, no batches.
- **Tools only.** No resources, prompts, sampling or elicitation.
- **No skills.** The agent's approved skills are not sent to the client. Its
  own instructions are.
- **No verified client.** The audit names the user and a self-reported client
  name. It cannot prove which program held the key.

See [How the agent is held to account](governance.md) for the guarantees this
builds on.
