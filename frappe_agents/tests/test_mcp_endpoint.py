# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The MCP endpoint: an outside client reaches the tools through the same door.

Each test builds the request the way Frappe hands it over: the request object
set, and the session user already logged in from the Authorization header — or
left as Guest when there was none. What these tests prove is everything the
endpoint does after that point: that it resolves the credential again and binds
the run to it, that the agent and the site both opted in, that what is listed is
what the grants allow, and that every tools/call ends in the same audit row and
the same draft/propose wall chat ends in.
"""

import base64
import json
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime
from frappe.utils import set_request as build_request
from frappe.utils.password import set_encrypted_password

from frappe_agents.access.grants import GENERIC_TOOLS, MCP_HIDDEN_TOOLS, MCP_TOOLS
from frappe_agents.mcp import server
from frappe_agents.mcp.server import (
	INVALID_PARAMS,
	INVALID_REQUEST,
	METHOD_NOT_FOUND,
	PARSE_ERROR,
	REFUSED,
	SESSION_HEADER,
	handle,
)
from frappe_agents.tests.fixtures import (
	DISABLED_USER,
	DRAFT_USER,
	OPEN_USER,
	ORDER_DT,
	PROJECT_ALPHA,
	AgentTestCase,
	as_user,
	extraction_settings,
	make_matrix_agent,
	make_order_draft,
	make_run,
	rule,
	set_kill_switch,
	tool_calls_for,
)
from frappe_agents.tools.base import execute_tool

MCP_AGENT = "FA MCP Agent"
SECOND_MCP_AGENT = "FA MCP Agent Two"
WEBSITE_USER = "fa-mcp-website@example.com"
BLUEPRINT = "Agent Blueprint"
PATH = "/api/method/frappe_agents.mcp.server.handle"


def give_api_key(user: str) -> str:
	"""An API key and secret for `user`, as the Authorization header carries them."""
	key = frappe.generate_hash(length=15)
	secret = frappe.generate_hash(length=15)
	frappe.db.set_value("User", user, "api_key", key, update_modified=False)
	set_encrypted_password("User", user, secret, "api_secret")
	return f"token {key}:{secret}"


def make_mcp_agent(
	autonomy: str = "Draft",
	allow_mcp: int = 1,
	rules: list | None = None,
	name: str = MCP_AGENT,
	may_read_files: int = 0,
):
	agent = make_matrix_agent(
		rules=rules
		if rules is not None
		else [
			rule(ORDER_DT, can_read=1, can_create_draft=1, can_propose=1, can_extract=1),
			rule(BLUEPRINT, can_create_draft=1),
		],
		name=name,
		autonomy=autonomy,
		may_read_files=may_read_files,
	)
	agent.db_set("allow_mcp", allow_mcp, update_modified=False)
	frappe.clear_document_cache("Agent", agent.name)
	return agent


def make_website_user() -> str:
	if not frappe.db.exists("User", WEBSITE_USER):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": WEBSITE_USER,
				"first_name": "FA Website",
				"user_type": "Website User",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
	return WEBSITE_USER


@contextmanager
def logged_in_as(user: str):
	"""The session user Frappe's own login left behind, put back afterwards."""
	original = frappe.session.user
	frappe.set_user(user)
	try:
		yield
	finally:
		frappe.set_user(original)


class TestMCPEndpoint(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.enterContext(
			extraction_settings(mcp_enabled=1, mcp_requests_per_minute=100_000, mcp_calls_per_session=50)
		)
		self.agent = make_mcp_agent()
		self.credential = give_api_key(DRAFT_USER)

		previous = getattr(frappe.local, "request", None)
		self.addCleanup(setattr, frappe.local, "request", previous)

	# --- helpers ---------------------------------------------------------------

	def post(
		self,
		body=None,
		*,
		user: str = DRAFT_USER,
		credential: str | None = "default",
		agent: str | None = MCP_AGENT,
		session: str | None = None,
		method: str = "POST",
		headers: dict | None = None,
		raw: str | None = None,
	):
		"""One request to the endpoint, as `user` after Frappe's login ran."""
		sent = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
		credential = self.credential if credential == "default" else credential
		if credential:
			sent["Authorization"] = credential
		if session:
			sent[SESSION_HEADER] = session
		sent.update(headers or {})

		data = raw if raw is not None else (json.dumps(body) if body is not None else "")
		build_request(
			method=method,
			path=PATH,
			query_string={"agent": agent} if agent else None,
			headers=sent,
			data=data,
		)
		with logged_in_as(user):
			response = handle()
		text = response.get_data(as_text=True)
		return response, (json.loads(text) if text else None)

	def rpc(self, method: str, params: dict | None = None, request_id: int = 1, **kwargs):
		body = {"jsonrpc": "2.0", "id": request_id, "method": method}
		if params is not None:
			body["params"] = params
		return self.post(body, **kwargs)

	def open_session(self, **kwargs) -> str:
		response, body = self.rpc(
			"initialize",
			{
				"protocolVersion": "2025-11-25",
				"capabilities": {},
				"clientInfo": {"name": "FA Test Client", "version": "1.0"},
			},
			**kwargs,
		)
		self.assertEqual(response.status_code, 200, body)
		return response.headers[SESSION_HEADER]

	def call(self, session: str, tool: str, arguments: dict, **kwargs):
		return self.rpc("tools/call", {"name": tool, "arguments": arguments}, session=session, **kwargs)

	def mcp_runs(self) -> list[str]:
		return frappe.get_all("Agent Run", filters={"surface": "MCP", "agent": MCP_AGENT}, pluck="name")

	# --- the door is shut by default --------------------------------------------

	def test_the_endpoint_does_not_exist_until_the_site_opens_it(self):
		with extraction_settings(mcp_enabled=0):
			response, _ = self.rpc("initialize", {})

		self.assertEqual(response.status_code, 404)
		self.assertEqual(self.mcp_runs(), [])

	def test_an_agent_that_did_not_opt_in_is_refused(self):
		make_mcp_agent(allow_mcp=0)

		response, body = self.rpc("initialize", {})

		self.assertEqual(response.status_code, 403)
		self.assertIn("not open to MCP", body["error"]["message"])
		self.assertEqual(self.mcp_runs(), [])

	def test_the_agent_comes_from_the_url_only(self):
		response, body = self.rpc("initialize", {"agent": MCP_AGENT}, agent=None)

		self.assertEqual(response.status_code, 400)
		self.assertIn("?agent=", body["error"]["message"])

	# --- identity -----------------------------------------------------------------

	def test_a_request_without_a_credential_is_refused(self):
		response, body = self.rpc("initialize", {}, user="Guest", credential=None)

		self.assertEqual(response.status_code, 401)
		self.assertIn("token <api_key>:<api_secret>", body["error"]["message"])
		self.assertEqual(self.mcp_runs(), [])

	def test_a_browser_session_without_a_key_is_refused(self):
		"""Logged in by cookie is not logged in by key. The endpoint takes keys only."""
		response, _ = self.rpc("initialize", {}, credential=None)

		self.assertEqual(response.status_code, 401)

	def test_a_key_that_names_someone_else_is_refused(self):
		"""Frappe keeps the session it already has when the header names another user."""
		other = give_api_key(OPEN_USER)

		response, _ = self.rpc("initialize", {}, user=DRAFT_USER, credential=other)

		self.assertEqual(response.status_code, 401)
		self.assertEqual(self.mcp_runs(), [])

	def test_a_wrong_secret_is_refused(self):
		key = self.credential.split(" ", 1)[1].split(":")[0]

		response, _ = self.rpc("initialize", {}, credential=f"token {key}:not-the-secret")

		self.assertEqual(response.status_code, 401)

	def test_a_bearer_token_is_refused_in_this_version(self):
		response, body = self.rpc("initialize", {}, credential="Bearer some-oauth-token")

		self.assertEqual(response.status_code, 401)
		self.assertIn("OAuth", body["error"]["message"])

	def test_administrator_is_refused_even_with_a_valid_key(self):
		admin = give_api_key("Administrator")

		response, body = self.rpc("initialize", {}, user="Administrator", credential=admin)

		self.assertEqual(response.status_code, 403)
		self.assertIn("Administrator", body["error"]["message"])
		self.assertEqual(self.mcp_runs(), [])

	def test_a_basic_credential_is_accepted(self):
		pair = self.credential.split(" ", 1)[1]
		basic = "Basic " + base64.b64encode(pair.encode()).decode()

		response, _ = self.rpc("initialize", {}, credential=basic)

		self.assertEqual(response.status_code, 200)

	def test_a_key_looked_up_on_another_doctype_is_refused(self):
		response, _ = self.rpc("initialize", {}, headers={"Frappe-Authorization-Source": "Contact"})

		self.assertEqual(response.status_code, 401)

	def test_a_disabled_user_is_refused(self):
		credential = give_api_key(DISABLED_USER)

		response, _ = self.rpc("initialize", {}, user=DISABLED_USER, credential=credential)

		self.assertEqual(response.status_code, 401)
		self.assertEqual(self.mcp_runs(), [])

	def test_a_website_user_is_refused(self):
		user = make_website_user()
		credential = give_api_key(user)

		response, body = self.rpc("initialize", {}, user=user, credential=credential)

		self.assertEqual(response.status_code, 403)
		self.assertIn("system users", body["error"]["message"])

	def test_a_page_on_another_site_is_refused(self):
		response, _ = self.rpc("initialize", {}, headers={"Origin": "https://evil.example"})

		self.assertEqual(response.status_code, 403)

	# --- sessions -----------------------------------------------------------------

	def test_initialize_opens_one_mcp_run_bound_to_the_key_user(self):
		session = self.open_session()

		run = frappe.get_doc("Agent Run", session)
		self.assertEqual(run.surface, "MCP")
		self.assertEqual(run.effective_user, DRAFT_USER)
		self.assertEqual(run.agent, MCP_AGENT)
		self.assertEqual(run.status, "Running")
		self.assertIn("FA Test Client 1.0", run.input_message)

	def test_initialize_answers_with_the_negotiated_version_and_the_wall(self):
		_, body = self.rpc("initialize", {"protocolVersion": "1999-01-01"})

		result = body["result"]
		self.assertEqual(result["protocolVersion"], server.PROTOCOL_VERSIONS[0])
		self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
		self.assertIn("<untrusted>", result["instructions"])
		self.assertIn("propose_submit", result["instructions"])

	def test_a_session_belongs_to_its_user(self):
		session = self.open_session()
		other = give_api_key(OPEN_USER)

		response, _ = self.rpc("tools/list", session=session, user=OPEN_USER, credential=other)

		self.assertEqual(response.status_code, 404)

	def test_a_session_ends_at_its_lifetime_however_busy(self):
		session = self.open_session()
		frappe.db.set_value(
			"Agent Run",
			session,
			"started_at",
			add_to_date(now_datetime(), hours=-(server.LIFETIME_HOURS + 1)),
			update_modified=False,
		)

		response, _ = self.rpc("tools/list", session=session)

		self.assertEqual(response.status_code, 404)
		self.assertEqual(frappe.db.get_value("Agent Run", session, "status"), "Completed")

	def test_a_session_belongs_to_its_agent(self):
		make_mcp_agent(name=SECOND_MCP_AGENT)
		session = self.open_session()

		response, _ = self.rpc("tools/list", session=session, agent=SECOND_MCP_AGENT)

		self.assertEqual(response.status_code, 404)

	def test_nobody_else_can_end_a_session(self):
		session = self.open_session()
		other = give_api_key(OPEN_USER)

		response, _ = self.post(session=session, method="DELETE", user=OPEN_USER, credential=other)

		self.assertEqual(response.status_code, 404)
		self.assertEqual(frappe.db.get_value("Agent Run", session, "status"), "Running")

	def test_old_sessions_end_when_too_many_are_open(self):
		sessions = [self.open_session() for _ in range(server.MAX_OPEN_SESSIONS + 1)]

		statuses = [frappe.db.get_value("Agent Run", name, "status") for name in sessions]

		self.assertEqual(statuses.count("Running"), server.MAX_OPEN_SESSIONS)
		self.assertEqual(statuses[0], "Completed")

	def test_tools_need_a_session(self):
		response, body = self.rpc("tools/list")

		self.assertEqual(response.status_code, 400)
		self.assertEqual(body["error"]["code"], INVALID_REQUEST)

	def test_an_idle_session_expires(self):
		session = self.open_session()
		frappe.db.set_value(
			"Agent Run",
			session,
			"modified",
			add_to_date(now_datetime(), minutes=-(server.IDLE_MINUTES + 1)),
			update_modified=False,
		)

		response, _ = self.rpc("tools/list", session=session)

		self.assertEqual(response.status_code, 404)
		self.assertEqual(frappe.db.get_value("Agent Run", session, "status"), "Completed")

	def test_a_session_ends_after_its_call_cap(self):
		session = self.open_session()
		with extraction_settings(mcp_calls_per_session=1):
			first, _ = self.call(session, "search_documents", {"doctype": ORDER_DT, "fields": ["name"]})
			second, _ = self.call(session, "search_documents", {"doctype": ORDER_DT, "fields": ["name"]})

		self.assertEqual(first.status_code, 200)
		self.assertEqual(second.status_code, 404)
		self.assertEqual(len(tool_calls_for(session)), 1)

	def test_the_call_cap_holds_when_the_cache_lost_the_count(self):
		"""The cache count restarts from what the run recorded, not from zero."""
		session = self.open_session()
		self.call(session, "search_documents", {"doctype": ORDER_DT, "fields": ["name"]})
		frappe.cache.delete(frappe.cache.make_key(f"frappe_agents:mcp_calls:{session}"))

		with extraction_settings(mcp_calls_per_session=1):
			response, _ = self.call(session, "search_documents", {"doctype": ORDER_DT, "fields": ["name"]})

		self.assertEqual(response.status_code, 404)
		self.assertEqual(len(tool_calls_for(session)), 1)

	def test_a_late_count_never_lowers_the_recorded_one(self):
		"""Two calls that finish out of order each write their count; the higher stands."""
		session = self.open_session()

		server._record_count(session, 3)
		server._record_count(session, 2)

		self.assertEqual(frappe.db.get_value("Agent Run", session, "steps_taken"), 3)

	def test_delete_ends_the_session(self):
		session = self.open_session()

		response, _ = self.post(session=session, method="DELETE")

		self.assertEqual(response.status_code, 204)
		self.assertEqual(frappe.db.get_value("Agent Run", session, "status"), "Completed")

	def test_the_sweep_ends_sessions_nobody_closed(self):
		session = self.open_session()
		frappe.db.set_value(
			"Agent Run",
			session,
			"modified",
			add_to_date(now_datetime(), hours=-1),
			update_modified=False,
		)

		server.close_idle_sessions()

		self.assertEqual(frappe.db.get_value("Agent Run", session, "status"), "Completed")

	# --- tools/list ---------------------------------------------------------------

	def test_tools_list_offers_what_the_grants_allow_and_nothing_mcp_hides(self):
		session = self.open_session()

		_, body = self.rpc("tools/list", session=session)

		names = {tool["name"] for tool in body["result"]["tools"]}
		self.assertTrue({"search_documents", "create_draft", "propose_submit"} <= names, names)
		# Granted to the agent, and still never offered over MCP.
		self.assertNotIn("extract_document", names)
		self.assertNotIn("list_site_doctypes", names)
		# Never granted at all.
		self.assertNotIn("update_draft", names)
		self.assertNotIn("run_report", names)

		by_name = {tool["name"]: tool for tool in body["result"]["tools"]}
		self.assertEqual(by_name["search_documents"]["inputSchema"]["type"], "object")
		self.assertTrue(by_name["search_documents"]["annotations"]["readOnlyHint"])
		self.assertFalse(by_name["create_draft"]["annotations"]["destructiveHint"])

	def test_file_reading_is_not_offered_even_when_the_agent_may_read_files(self):
		make_mcp_agent(may_read_files=1)
		session = self.open_session()

		_, body = self.rpc("tools/list", session=session)

		self.assertNotIn("read_document", {tool["name"] for tool in body["result"]["tools"]})

	def test_a_suggest_agent_lists_read_tools_only(self):
		make_mcp_agent(autonomy="Suggest")
		session = self.open_session()

		_, body = self.rpc("tools/list", session=session)

		names = {tool["name"] for tool in body["result"]["tools"]}
		self.assertIn("search_documents", names)
		self.assertNotIn("create_draft", names)
		self.assertNotIn("propose_submit", names)

	# --- tools/call ---------------------------------------------------------------

	def test_a_read_call_runs_as_the_user_and_is_audited(self):
		order = make_order_draft(user=DRAFT_USER)
		session = self.open_session()

		response, body = self.call(session, "search_documents", {"doctype": ORDER_DT, "fields": ["name"]})

		self.assertEqual(response.status_code, 200)
		self.assertFalse(body["result"]["isError"], body)
		self.assertIn(order.name, body["result"]["content"][-1]["text"])
		calls = tool_calls_for(session)
		self.assertEqual([(c.tool, c.outcome) for c in calls], [("search_documents", "Success")])
		self.assertEqual(frappe.db.get_value("Agent Run", session, "steps_taken"), 1)

	def test_a_draft_call_creates_a_draft_and_nothing_more(self):
		session = self.open_session()

		_, body = self.call(
			session,
			"create_draft",
			{
				"doctype": ORDER_DT,
				"values": {
					"order_title": "FA MCP Draft",
					"project": PROJECT_ALPHA,
					"amount": 10,
					"items": [{"item": "FA Widget", "qty": 1}],
				},
			},
		)

		self.assertFalse(body["result"]["isError"], body)
		name = json.loads(body["result"]["content"][-1]["text"])["result"]["name"]
		self.assertEqual(frappe.db.get_value(ORDER_DT, name, "docstatus"), 0)
		self.assertEqual(frappe.db.get_value(ORDER_DT, name, "owner"), DRAFT_USER)
		calls = tool_calls_for(session)
		self.assertEqual([(c.tool, c.outcome) for c in calls], [("create_draft", "Success")])

	def test_a_propose_call_records_an_agent_action_and_submits_nothing(self):
		order = make_order_draft(user=DRAFT_USER)
		session = self.open_session()

		_, body = self.call(
			session,
			"propose_submit",
			{"doctype": ORDER_DT, "name": order.name, "reason": "Matches the signed quotation."},
		)

		self.assertFalse(body["result"]["isError"], body)
		action = json.loads(body["result"]["content"][-1]["text"])["result"]["action"]
		action = frappe.get_doc("Agent Action", action)
		self.assertEqual(action.status, "Pending")
		self.assertEqual(action.run, session)
		self.assertEqual(action.requested_by, DRAFT_USER)
		self.assertEqual(frappe.db.get_value(ORDER_DT, order.name, "docstatus"), 0)

	def test_a_tool_mcp_hides_is_refused_and_audited(self):
		session = self.open_session()

		_, body = self.call(session, "extract_document", {"file": "nothing", "target_doctype": ORDER_DT})

		self.assertEqual(body["error"]["code"], INVALID_PARAMS)
		calls = tool_calls_for(session)
		self.assertEqual([(c.tool, c.outcome) for c in calls], [("extract_document", "Denied")])

	def test_a_call_past_the_daily_token_budget_is_refused(self):
		session = self.open_session()
		self.agent.db_set("daily_token_budget", 10, update_modified=False)
		frappe.clear_document_cache("Agent", MCP_AGENT)
		spent = make_run(effective_user=DRAFT_USER, agent=MCP_AGENT)
		spent.db_set("tokens_in", 100, update_modified=False)

		_, body = self.call(session, "search_documents", {"doctype": ORDER_DT})

		self.assertEqual(body["error"]["code"], REFUSED)
		self.assertIn("budget", body["error"]["message"])
		self.assertEqual(tool_calls_for(session), [])

	def test_the_instructions_are_written_for_mcp_not_borrowed_from_chat(self):
		"""Chat's prompt describes notes only chat adds. A client told to trust them would."""
		from frappe_agents.runner import run

		text = server.instructions(self.agent)

		self.assertIn("<untrusted>", text)
		self.assertIn("propose_", text)
		self.assertNotIn(run.TOOL_DISCIPLINE, text)
		self.assertNotIn(run.DRAFT_DISCIPLINE, text)

	def test_an_agent_blueprint_cannot_be_drafted_over_mcp(self):
		session = self.open_session()

		_, body = self.call(
			session, "create_draft", {"doctype": BLUEPRINT, "values": {"blueprint_name": "FA MCP Blueprint"}}
		)

		self.assertTrue(body["result"]["isError"])
		self.assertIn(BLUEPRINT, body["result"]["content"][-1]["text"])
		self.assertEqual([c.outcome for c in tool_calls_for(session)], ["Denied"])

	def test_the_kill_switch_stops_a_session_and_is_audited(self):
		session = self.open_session()
		set_kill_switch(0)

		_, body = self.call(session, "search_documents", {"doctype": ORDER_DT})

		self.assertTrue(body["result"]["isError"])
		self.assertIn("switched off", body["result"]["content"][-1]["text"])
		calls = tool_calls_for(session)
		self.assertEqual([(c.tool, c.outcome) for c in calls], [("search_documents", "Denied")])
		self.assertEqual(frappe.db.get_value("Agent Run", session, "status"), "Cancelled")

		response, _ = self.rpc("initialize", {})
		self.assertEqual(response.status_code, 503)

	def test_a_user_is_rate_limited(self):
		with extraction_settings(mcp_requests_per_minute=2):
			# A window of its own, so earlier requests in this minute do not count.
			window = 4_000_000_000.0 + int(frappe.generate_hash(length=6), 16) * 60
			with patch.object(server, "_clock", return_value=window):
				statuses = [self.rpc("ping")[0].status_code for _ in range(3)]

		self.assertEqual(statuses[-1], 429)

	# --- malformed JSON-RPC ---------------------------------------------------------

	def test_a_body_that_is_not_json_is_a_parse_error(self):
		response, body = self.post(raw="{not json")

		self.assertEqual(response.status_code, 400)
		self.assertEqual(body["error"]["code"], PARSE_ERROR)

	def test_a_batch_is_refused(self):
		response, body = self.post([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])

		self.assertEqual(response.status_code, 400)
		self.assertEqual(body["error"]["code"], INVALID_REQUEST)

	def test_a_message_that_is_not_json_rpc_is_refused(self):
		response, body = self.post({"id": 1, "method": "ping"})

		self.assertEqual(response.status_code, 400)
		self.assertEqual(body["error"]["code"], INVALID_REQUEST)

	def test_an_unknown_method_is_method_not_found(self):
		_, body = self.rpc("resources/list")

		self.assertEqual(body["error"]["code"], METHOD_NOT_FOUND)

	def test_params_must_be_an_object(self):
		_, body = self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": [1]})

		self.assertEqual(body["error"]["code"], INVALID_PARAMS)

	def test_tool_arguments_must_be_an_object(self):
		session = self.open_session()

		_, body = self.rpc("tools/call", {"name": "search_documents", "arguments": "x"}, session=session)

		self.assertEqual(body["error"]["code"], INVALID_PARAMS)
		self.assertEqual(tool_calls_for(session), [])

	def test_a_notification_is_accepted_without_a_body(self):
		response, body = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})

		self.assertEqual(response.status_code, 202)
		self.assertIsNone(body)

	def test_an_unsupported_protocol_version_is_refused(self):
		response, _ = self.rpc("ping", headers={"MCP-Protocol-Version": "1999-01-01"})

		self.assertEqual(response.status_code, 400)

	def test_get_offers_no_stream(self):
		response, _ = self.post(method="GET")

		self.assertEqual(response.status_code, 405)
		self.assertIn("POST", response.headers["Allow"])


class TestToolChokepointIdentity(AgentTestCase):
	"""What `execute_tool` now checks itself, for every caller and not only MCP."""

	def test_a_call_as_someone_other_than_the_run_user_is_denied_and_audited(self):
		run = make_run(effective_user=OPEN_USER)

		with as_user(DRAFT_USER):
			payload = execute_tool(run, "search_documents", {"doctype": ORDER_DT})

		self.assertFalse(payload["ok"])
		self.assertIn("acts for", payload["error"])
		self.assertEqual([c.outcome for c in tool_calls_for(run.name)], ["Denied"])

	def test_a_call_as_administrator_is_denied_and_audited(self):
		run = make_run(effective_user="Administrator")

		with as_user("Administrator"):
			payload = execute_tool(run, "search_documents", {"doctype": ORDER_DT})

		self.assertFalse(payload["ok"])
		self.assertIn("Administrator", payload["error"])
		self.assertEqual([c.outcome for c in tool_calls_for(run.name)], ["Denied"])

	def test_an_mcp_run_never_goes_past_draft_even_for_a_write_agent(self):
		"""Nothing that ships is Write class, so one tool is made Write for this test."""
		make_mcp_agent(autonomy="Write")
		frappe.db.set_value("Agent Tool", "create_draft", "capability", "Write", update_modified=False)
		frappe.clear_document_cache("Agent Tool", "create_draft")
		self.addCleanup(frappe.clear_document_cache, "Agent Tool", "create_draft")

		mcp_run = make_run(effective_user=DRAFT_USER, agent=MCP_AGENT)
		mcp_run.db_set("surface", "MCP", update_modified=False)
		chat_run = make_run(effective_user=DRAFT_USER, agent=MCP_AGENT)
		args = {
			"doctype": ORDER_DT,
			"dry_run": 1,
			"values": {"order_title": "FA MCP Write", "project": PROJECT_ALPHA, "amount": 1},
		}

		with as_user(DRAFT_USER):
			over_mcp = execute_tool(mcp_run, "create_draft", args)
			in_chat = execute_tool(chat_run, "create_draft", args)

		self.assertFalse(over_mcp["ok"])
		self.assertIn("never go past Draft", over_mcp["error"])
		# Chat holds Write autonomy to Write: whatever the draft itself says, the
		# capability gate let it through.
		self.assertNotIn("capability", in_chat["error"] or "")

	def test_an_atomic_call_that_fails_leaves_only_its_audit_row(self):
		run = make_run(effective_user=DRAFT_USER)
		marker = f"FA atomic {frappe.generate_hash(length=8)}"

		def half_then_fail(_args):
			frappe.get_doc({"doctype": "ToDo", "description": marker}).insert(ignore_permissions=True)
			raise ValueError("failed after writing")

		with patch("frappe_agents.tools.base._resolve_handler", return_value=half_then_fail):
			with as_user(DRAFT_USER):
				payload = execute_tool(run, "search_documents", {}, atomic=True)

		self.assertFalse(payload["ok"])
		self.assertFalse(frappe.db.exists("ToDo", {"description": marker}))
		self.assertEqual([c.outcome for c in tool_calls_for(run.name)], ["Error"])


class TestMCPToolDecision(AgentTestCase):
	"""Every generic tool is offered to MCP or withheld from it, on purpose."""

	def test_every_generic_tool_has_an_mcp_decision(self):
		undecided = GENERIC_TOOLS - MCP_TOOLS - MCP_HIDDEN_TOOLS
		self.assertEqual(
			undecided,
			set(),
			"Add each new tool to MCP_TOOLS or MCP_HIDDEN_TOOLS in access/grants.py, "
			"and to the list in docs/mcp.md.",
		)

	def test_no_tool_is_both_offered_and_withheld(self):
		self.assertEqual(MCP_TOOLS & MCP_HIDDEN_TOOLS, set())
