// Page shell only. The chat itself is frappe_agents.ChatUI from the desk bundle,
// which the form panel mounts too. Page scripts run after the boot bundles, so
// the global is always there.
frappe.provide("frappe_agents");

const RAIL_STYLE_ID = "frappe-agents-rail-styles";
// Remembered per browser, not per user record: which furniture is open is not
// something to spend a server round trip on.
const RAIL_COLLAPSED_KEY = "frappe_agents:rail_collapsed";
// Below this the layout has one column, so the toggle stops collapsing the rail
// and starts opening it over the chat.
const RAIL_DRAWER_QUERY = "(max-width: 720px)";

const RAIL_STYLES = `
	.agent-chat-layout {
		display: grid; grid-template-columns: 250px minmax(0, 1fr);
		/* The same budget the chat widget keeps, restated: the rail is a sibling
		   of that widget, not a child, so it cannot inherit the values. */
		--agent-chat-quick: 120ms;
		--agent-chat-enter: 140ms;
		--agent-chat-ease: cubic-bezier(0.25, 1, 0.5, 1);
		/* The drawer comes in from the edge it is anchored to. A transform has no
		   logical form, so the direction is a value and RTL flips its sign. */
		--agent-chat-drawer-from: -8px;
	}
	[dir="rtl"] .agent-chat-layout { --agent-chat-drawer-from: 8px; }
	@keyframes agent-chat-drawer-in {
		from { opacity: 0; transform: translateX(var(--agent-chat-drawer-from)); }
	}
	.agent-chat-layout.is-collapsed { grid-template-columns: 40px minmax(0, 1fr); }
	.agent-chat-rail {
		display: flex; flex-direction: column; gap: 6px; padding: 6px 0; padding-inline-end: 8px;
		border-inline-end: 1px solid var(--border-color); height: var(--agent-chat-height, calc(100dvh - 220px)); min-height: 320px;
	}
	.agent-chat-rail-top { display: flex; align-items: center; gap: 6px; }
	.agent-chat-rail-toggle { flex: none; padding: 2px 6px; color: var(--text-muted); }
	.agent-chat-new { flex: 1; display: flex; justify-content: space-between; align-items: center; font-weight: 600; }
	.agent-chat-layout.is-collapsed .agent-chat-new,
	.agent-chat-layout.is-collapsed .agent-chat-rail-list { display: none; }
	.agent-chat-rail-list { flex: 1; overflow-y: auto; }
	.agent-chat-rail-group {
		font-size: var(--text-xs); font-weight: 600; letter-spacing: 0.07em; text-transform: uppercase;
		color: var(--text-muted); margin: 12px 6px 4px;
	}
	/* The gap belongs between one day and the next, not above the first one. */
	.agent-chat-rail-group:first-child { margin-block-start: 0; }
	.agent-chat-rail-empty { color: var(--text-muted); font-size: var(--text-sm); padding: 10px 6px; }
	.agent-chat-convo-row { position: relative; margin-bottom: 2px; }
	.agent-chat-convo {
		flex: 1; min-width: 0; padding: 6px 8px; text-align: start; font: inherit; color: inherit;
		background: none; border-radius: var(--border-radius-md, 6px); cursor: pointer;
		/* Carried by every row, so selecting one costs no space and the rows
		   under it do not shift. */
		border: 1px solid transparent;
		/* Colour only: the row keeps its box, so hovering down a long list
		   never reflows the list. */
		transition: background-color var(--agent-chat-quick) var(--agent-chat-ease),
			border-color var(--agent-chat-quick) var(--agent-chat-ease);
	}
	.agent-chat-convo:hover { background: var(--highlight-color); }
	.agent-chat-convo.is-active { background: var(--card-bg, var(--control-bg)); border-color: var(--border-color); }
	.agent-chat-convo-head { display: flex; justify-content: space-between; align-items: baseline; gap: 6px; }
	.agent-chat-convo-agent { font-weight: 600; font-size: var(--text-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
	.agent-chat-convo-when { flex: none; font-size: var(--text-xs); color: var(--text-muted); white-space: nowrap; }
	.agent-chat-convo-snippet { display: flex; align-items: baseline; gap: 6px; font-size: var(--text-sm); color: var(--text-muted); }
	.agent-chat-convo-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	/* The rename floats over the row's trailing edge instead of costing every
	   row a column: a sibling for accessibility, an overlay for geometry. */
	.agent-chat-convo-actions {
		position: absolute; inset-inline-end: 4px; inset-block-start: 4px;
		display: flex; align-items: center;
	}
	.agent-chat-rename {
		flex: none; opacity: 0; font: inherit; font-size: var(--text-xs); color: var(--text-muted);
		border: none; border-radius: var(--border-radius-tiny); background: var(--card-bg);
		min-width: 24px; min-height: 24px; display: inline-grid; place-items: center;
		padding: 0 4px; cursor: pointer; box-shadow: 0 0 0 1px var(--border-color);
		transition: opacity var(--agent-chat-quick) var(--agent-chat-ease);
	}
	.agent-chat-convo-row:hover .agent-chat-rename,
	.agent-chat-rename:focus { opacity: 1; }
	/* Desk restores a focus ring only for .btn, and neither of these is one. The
	   ring sits inside the button: this list scrolls, and an outset ring on a
	   row the full width of it would put a scrollbar under the conversations. */
	.agent-chat-convo:focus-visible,
	.agent-chat-rename:focus-visible { outline: 2px solid var(--text-color); outline-offset: -2px; }
	/* No hover to reveal it on a touch screen, and a control you cannot see but
	   can hit by accident is worse than one always on show. */
	@media (hover: none) { .agent-chat-rename { opacity: 1; } }
	.agent-chat-pane { min-width: 0; padding-inline-start: 14px; }
	@media (max-width: 720px) {
		.agent-chat-layout, .agent-chat-layout.is-collapsed {
			grid-template-columns: minmax(0, 1fr); position: relative;
		}
		/* No room for a column. The rail keeps its toggle and nothing else, and
		   that toggle opens the list as a drawer over the chat — a width that
		   hides the conversations still has to have a way back to them. */
		.agent-chat-layout .agent-chat-rail,
		.agent-chat-layout.is-collapsed .agent-chat-rail {
			height: auto; min-height: 0; padding: 0 0 6px; border-inline-end: none;
		}
		.agent-chat-layout .agent-chat-new,
		.agent-chat-layout .agent-chat-rail-list { display: none; }
		.agent-chat-layout.is-drawer .agent-chat-rail {
			position: absolute; inset-inline-start: 0; top: 0; z-index: 20;
			width: 250px; max-width: 86%; max-height: calc(100vh - 260px); padding: 6px 8px;
			background: var(--card-bg, var(--control-bg)); border: 1px solid var(--border-color);
			border-radius: var(--border-radius-md, 6px); box-shadow: var(--shadow-lg);
			/* It is a layer over the chat, not a part of it, and arriving from
			   the edge is what says so. */
			animation: agent-chat-drawer-in var(--agent-chat-enter) var(--agent-chat-ease);
		}
		.agent-chat-layout.is-drawer .agent-chat-new { display: flex; }
		.agent-chat-layout.is-drawer .agent-chat-rail-list { display: block; }
		.agent-chat-pane { padding-inline-start: 0; }
	}
	/* The rail is its own sheet, so it needs its own answer to the same request.
	   Scoped rather than written as *: this sheet is injected into the whole
	   desk and the rest of the desk is not ours to quieten. */
	@media (prefers-reduced-motion: reduce) {
		.agent-chat-layout, .agent-chat-layout * {
			animation-duration: 0.01ms !important;
			animation-iteration-count: 1 !important;
			transition-duration: 0.01ms !important;
		}
	}
`;

function add_rail_styles() {
	if (document.getElementById(RAIL_STYLE_ID)) return;
	$(`<style id="${RAIL_STYLE_ID}">${RAIL_STYLES}</style>`).appendTo(document.head);
}

/** Whole days between a timestamp and today, in the user's own timezone. */
function days_ago(value) {
	if (!value) return null;
	const local = frappe.datetime.convert_to_user_tz(value);
	const then = new Date(
		String(local)
			.replace(/-/g, "/")
			.replace(/[TZ]/g, " ")
			.replace(/\.[0-9]*/, "")
	);
	if (isNaN(then.getTime())) return null;
	const now = new Date(frappe.datetime.now_datetime().replace(/-/g, "/"));
	const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
	const day = new Date(then.getFullYear(), then.getMonth(), then.getDate());
	return Math.floor((today - day) / 86400000);
}

/** Which heading a conversation sits under. */
function group_of(value) {
	const days = days_ago(value);
	if (days === 0) return "today";
	if (days === 1) return "yesterday";
	return "earlier";
}

frappe.pages["agent-chat"].on_page_load = function (wrapper) {
	frappe_agents.chat_page = new frappe_agents.AgentChatPage(wrapper);
};

frappe.pages["agent-chat"].on_page_show = function () {
	if (frappe_agents.chat_page) {
		frappe_agents.chat_page.refresh();
	}
};

frappe_agents.AgentChatPage = class AgentChatPage {
	constructor(wrapper) {
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("Agent Chat"),
			single_column: true,
		});
		this.agents = [];
		this.conversations = [];
		this.make();
		this.refresh();
	}

	make() {
		add_rail_styles();
		// The same words as the rail's own control, and one translation key.
		this.page.set_secondary_action(__("New chat"), () => this.new_chat());

		// The chat fills whatever is below the page chrome. A fixed guess breaks
		// the moment the shell around the Desk adds a toolbar, so measure instead:
		// where the layout starts is where the chrome ends.
		this.fit_height = () => {
			const el = this.$layout && this.$layout[0];
			if (!el || !el.isConnected) return;
			const top = Math.max(0, Math.round(el.getBoundingClientRect().top));
			el.style.setProperty("--agent-chat-height", `calc(100dvh - ${top + 12}px)`);
		};
		this.$layout = $(`
			<div class="agent-chat-layout">
				<aside class="agent-chat-rail" id="agent-chat-rail">
					<div class="agent-chat-rail-top">
						<button class="btn btn-default btn-xs agent-chat-rail-toggle"
							aria-controls="agent-chat-rail"></button>
						<button class="btn btn-default btn-xs agent-chat-new"></button>
					</div>
					<div class="agent-chat-rail-list"></div>
				</aside>
				<div class="agent-chat-pane"></div>
			</div>
		`).appendTo(this.page.main);

		this.fit_height();
		requestAnimationFrame(this.fit_height);
		$(window).on("resize.agent-chat-fit", this.fit_height);

		this.$rail = this.$layout.find(".agent-chat-rail-list");
		this.$toggle = this.$layout.find(".agent-chat-rail-toggle");
		const $new = this.$layout.find(".agent-chat-new").on("click", () => this.new_chat());
		$("<span></span>").text(__("New chat")).appendTo($new);
		$("<span></span>").text("+").appendTo($new);
		this.drawer_open = false;
		this.$toggle.on("click", () => {
			if (this.is_narrow()) this.toggle_drawer(!this.drawer_open);
			else this.toggle_rail(!this.collapsed);
		});
		this.toggle_rail(localStorage.getItem(RAIL_COLLAPSED_KEY) === "1");
		this.bind_drawer();

		this.chat = new frappe_agents.ChatUI({
			parent: this.$layout.find(".agent-chat-pane"),
			on_conversation: (conversation, data) => this.on_conversation(conversation, data),
			on_agent_change: () => this.on_agent_change(),
		});
	}

	toggle_rail(collapsed) {
		this.collapsed = Boolean(collapsed);
		this.$layout.toggleClass("is-collapsed", this.collapsed);
		localStorage.setItem(RAIL_COLLAPSED_KEY, this.collapsed ? "1" : "0");
		this.sync_toggle();
	}

	/** One column: the rail opens over the chat instead of beside it. */
	is_narrow() {
		return Boolean(window.matchMedia && window.matchMedia(RAIL_DRAWER_QUERY).matches);
	}

	toggle_drawer(open) {
		this.drawer_open = Boolean(open);
		this.$layout.toggleClass("is-drawer", this.drawer_open);
		this.sync_toggle();
	}

	/** The same control either way, so it says what it will do either way. */
	sync_toggle() {
		const open = this.is_narrow() ? this.drawer_open : !this.collapsed;
		const label = open ? __("Hide conversations") : __("Show conversations");
		this.$toggle.text(open ? "«" : "»").attr({
			title: label,
			"aria-label": label,
			"aria-expanded": open ? "true" : "false",
		});
	}

	/** A drawer closes the way a drawer closes: click away, or Escape. */
	bind_drawer() {
		$(document).on("click.agent-chat-drawer", (e) => {
			if (!this.drawer_open) return;
			if ($(e.target).closest(".agent-chat-rail").length) return;
			this.toggle_drawer(false);
		});
		$(document).on("keydown.agent-chat-drawer", (e) => {
			if (e.key !== "Escape" || !this.drawer_open) return;
			this.toggle_drawer(false);
			this.$toggle.focus();
		});

		if (!window.matchMedia) return;
		// Widened back out: the rail has its column again, so the overlay goes.
		const query = window.matchMedia(RAIL_DRAWER_QUERY);
		const on_change = () => {
			if (this.is_narrow()) this.sync_toggle();
			else this.toggle_drawer(false);
		};
		if (query.addEventListener) query.addEventListener("change", on_change);
		else if (query.addListener) query.addListener(on_change);
	}

	refresh() {
		this.load_conversations();
		this.load_agents()
			.then(() => this.rehydrate())
			.catch((error) => console.error("frappe_agents: could not load agents", error));
	}

	load_agents() {
		return frappe.xcall("frappe_agents.api.list_agents").then((agents) => {
			this.agents = agents || [];
			// The picker lives on the composer now, so the page hands the whole
			// payload over and the chat draws its own chips from it.
			this.chat.set_agents(this.agents);

			if (!this.agents.length) {
				this.chat.set_agent(null);
				this.chat.show_empty(__("No agents are available to you."));
				return;
			}
			if (!this.agents.some((agent) => agent.name === this.chat.agent)) {
				this.chat.set_agent(this.agents[0].name);
			}
		});
	}

	load_conversations() {
		return frappe
			.xcall("frappe_agents.api.list_conversations")
			.then((rows) => {
				this.conversations = rows || [];
				this.render_rail();
			})
			.catch((error) => console.error("frappe_agents: could not load conversations", error));
	}

	/** Your own conversations, newest activity first, under the day they happened. */
	render_rail() {
		this.$rail.empty();
		if (!this.conversations.length) {
			$("<div class='agent-chat-rail-empty'></div>")
				.text(__("Your conversations will show up here."))
				.appendTo(this.$rail);
			return;
		}

		const headings = {
			today: __("Today"),
			yesterday: __("Yesterday"),
			earlier: __("Earlier"),
		};
		let group = null;
		this.conversations.forEach((row) => {
			const when = row.last_activity || row.modified;
			const key = group_of(when);
			if (key !== group) {
				group = key;
				$("<div class='agent-chat-rail-group'></div>").text(headings[key]).appendTo(this.$rail);
			}
			this.render_row(row, when).appendTo(this.$rail);
		});
		this.mark_active();
	}

	/**
	 * One conversation: a button, with its rename beside it rather than inside it.
	 * A control within a control has no accessible reading, and the keys the inner
	 * one answered used to switch the conversation underneath its own dialog.
	 */
	render_row(row, when) {
		const $wrapper = $("<div class='agent-chat-convo-row'></div>");
		const $row = $("<button type='button' class='agent-chat-convo'></button>")
			.attr("data-conversation", row.name)
			.on("click", () => this.open_conversation(row.name))
			.appendTo($wrapper);

		const $line = $("<div class='agent-chat-convo-head'></div>").appendTo($row);
		// Both of these clip, and a clipped name with no way back to the whole of
		// it is how two conversations stop being tellable apart.
		const agent = row.agent_name || row.agent || "";
		$("<span class='agent-chat-convo-agent'></span>")
			.text(agent)
			.attr("title", agent)
			.appendTo($line);
		$("<span class='agent-chat-convo-when'></span>")
			.text(frappe.datetime.prettyDate(when, true))
			.attr("title", frappe.datetime.str_to_user(when))
			.appendTo($line);

		const $snippet = $("<div class='agent-chat-convo-snippet'></div>").appendTo($row);
		const snippet = row.snippet || __("No messages yet");
		$("<span class='agent-chat-convo-text'></span>")
			.text(snippet)
			.attr("title", snippet)
			.appendTo($snippet);
		$("<div class='agent-chat-convo-actions'></div>")
			.append(
				$("<button type='button' class='agent-chat-rename'></button>")
					.text("✎")
					.attr("aria-label", __("Rename this conversation"))
					.attr("title", __("Rename this conversation"))
					.on("click", () => this.rename(row))
			)
			.appendTo($wrapper);

		return $wrapper;
	}

	/** A name the user gives it. No model ever writes a conversation's title. */
	rename(row) {
		frappe.prompt(
			{
				fieldtype: "Data",
				fieldname: "title",
				label: __("Title"),
				default: row.title || row.snippet || "",
				reqd: 1,
			},
			(values) => {
				frappe.call({
					method: "frappe_agents.api.rename_conversation",
					args: { conversation: row.name, title: values.title },
					callback: () => this.load_conversations(),
				});
			},
			__("Rename conversation"),
			__("Rename")
		);
	}

	mark_active() {
		const current = this.chat ? this.chat.conversation : null;
		this.$rail.find(".agent-chat-convo").each((index, element) => {
			const $element = $(element);
			$element.toggleClass("is-active", $element.attr("data-conversation") === current);
		});
	}

	/** Switch conversations in place: no reload, the route just follows along. */
	open_conversation(name) {
		if (!name || name === this.chat.conversation) return;
		// Picking a row is what the drawer was opened for.
		this.toggle_drawer(false);
		this.chat.load_conversation(name);
		this.mark_active();
		if (this.route_conversation() !== name) frappe.set_route("agent-chat", name);
	}

	/** A different agent is a different conversation, so the route stops naming one. */
	on_agent_change() {
		this.mark_active();
		if (this.route_conversation()) frappe.set_route("agent-chat");
	}

	/** Reopen the conversation named in the route, so a reload or a deep link resumes it. */
	rehydrate() {
		const conversation = this.take_route_conversation();
		if (!conversation || conversation === this.chat.conversation) return;
		this.chat.load_conversation(conversation);
		this.mark_active();
	}

	/** route_options is a one-shot channel, so reading it consumes it. */
	take_route_conversation() {
		const from_route = this.route_conversation();
		if (from_route) return from_route;
		if (frappe.route_options && frappe.route_options.conversation) {
			const conversation = frappe.route_options.conversation;
			delete frappe.route_options.conversation;
			return conversation;
		}
		return null;
	}

	route_conversation() {
		const route = frappe.get_route() || [];
		return route[0] === "agent-chat" && route[1] ? decodeURIComponent(route[1]) : null;
	}

	on_conversation(conversation, data) {
		// Rehydrated: the chat follows the conversation's own agent, and the chips
		// are drawn from it. The rail only has to show which row you are reading.
		if (data) {
			this.mark_active();
			return;
		}
		// Freshly created by the first message: make it linkable, and list it.
		if (conversation && conversation !== this.route_conversation()) {
			frappe.set_route("agent-chat", conversation);
		}
		this.load_conversations();
	}

	new_chat() {
		this.toggle_drawer(false);
		this.chat.reset();
		this.chat.focus();
		this.mark_active();
		if (this.route_conversation()) {
			frappe.set_route("agent-chat");
		}
	}
};
