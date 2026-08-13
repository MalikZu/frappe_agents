// Shared chat renderer. The Agent Chat page and the form panel both mount this,
// so a change to how a turn is drawn only has to be made once.
frappe.provide("frappe_agents");

const STYLE_ID = "frappe-agents-chat-styles";

const STYLES = `
	.agent-chat { display: flex; flex-direction: column; height: calc(100vh - 220px); min-height: 320px; }
	.agent-chat.agent-chat-compact { height: 55vh; min-height: 260px; }
	.agent-chat-head { display: flex; align-items: center; gap: 10px; padding-bottom: 8px; border-bottom: 1px solid var(--border-color); }
	.agent-chat-title { font-weight: 600; }
	.agent-chat-context { display: inline-flex; }
	.agent-chat-doc {
		display: inline-flex; align-items: center; gap: 6px; max-width: 320px;
		background: var(--bg-blue); border: 1px solid var(--blue-300, var(--border-color));
		color: var(--text-on-blue); border-radius: 999px; padding: 2px 10px;
		font-size: var(--text-sm); text-decoration: none;
	}
	.agent-chat-doc:hover { color: var(--text-on-blue); text-decoration: none; }
	.agent-chat-doc.is-restricted { background: var(--control-bg); border-color: var(--border-color); color: var(--text-muted); }
	.agent-chat-doc-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.agent-chat-doc svg { flex: none; }
	.agent-chat-log { flex: 1; overflow-y: auto; padding: 8px 0; }
	.agent-chat-composer {
		position: relative; margin-top: 8px; background: var(--card-bg, var(--control-bg));
		border: 1px solid var(--border-color); border-radius: var(--border-radius-lg);
	}
	.agent-chat-input.form-control { border: none; background: transparent; resize: none; padding: 10px 12px 2px; }
	.agent-chat-input.form-control:focus { box-shadow: none; }
	.agent-chat-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; padding: 4px 8px 8px; }
	.agent-chat-send { margin-left: auto; }
	.agent-chat-chip {
		display: inline-flex; align-items: center; gap: 5px; max-width: 260px;
		border: 1px solid var(--border-color); background: var(--control-bg); border-radius: 999px;
		padding: 2px 10px; font-size: var(--text-sm); color: var(--text-color); cursor: pointer;
	}
	.agent-chat-chip:hover { background: var(--highlight-color); }
	.agent-chat-chip.is-static, .agent-chat-chip.is-static:hover { cursor: default; background: var(--control-bg); }
	.agent-chat-chip-key { color: var(--text-muted); }
	.agent-chat-chip-value { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.agent-chat-chip-caret { color: var(--text-light, var(--text-muted)); font-size: 9px; }
	.agent-chat-pop {
		display: none; position: absolute; bottom: calc(100% + 6px); z-index: 10;
		min-width: 250px; max-width: 340px; max-height: 300px; overflow-y: auto; padding: 6px;
		background: var(--card-bg, var(--control-bg)); border: 1px solid var(--border-color);
		border-radius: var(--border-radius-lg); box-shadow: var(--shadow-lg);
	}
	.agent-chat-pop.is-open { display: block; }
	.agent-chat-pop-label { font-size: var(--text-xs); font-weight: 600; text-transform: uppercase; letter-spacing: 0.07em; color: var(--text-muted); padding: 6px 10px 4px; }
	.agent-chat-pop-opt { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 6px 10px; border-radius: 6px; font-size: var(--text-sm); }
	.agent-chat-pop-opt.is-clickable { cursor: pointer; }
	.agent-chat-pop-opt.is-clickable:hover { background: var(--highlight-color); }
	.agent-chat-pop-text { min-width: 0; }
	.agent-chat-pop-sub { display: block; color: var(--text-muted); font-size: var(--text-xs); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.agent-chat-pop-tick { color: var(--text-on-green); font-weight: 600; }
	.agent-chat-pop-note { font-size: var(--text-xs); color: var(--text-muted); padding: 6px 10px; margin-top: 4px; border-top: 1px solid var(--border-color); }
	.agent-chat-cap { flex: none; font-size: var(--text-xs); font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; border-radius: 4px; padding: 1px 6px; background: var(--control-bg); color: var(--text-muted); }
	.agent-chat-cap.is-read { background: var(--bg-blue); color: var(--text-on-blue); }
	.agent-chat-cap.is-draft { background: var(--bg-green); color: var(--text-on-green); }
	.agent-chat-cap.is-write, .agent-chat-cap.is-submit { background: var(--bg-orange); color: var(--text-on-orange); }
	.agent-chat-row { margin-bottom: 10px; display: flex; }
	.agent-chat-row.is-user { justify-content: flex-end; }
	.agent-chat-bubble { max-width: 78%; padding: 8px 12px; border-radius: 10px; white-space: pre-wrap; word-break: break-word; background: var(--control-bg); }
	.agent-chat-row.is-user .agent-chat-bubble { background: var(--bg-light-gray, var(--control-bg)); }
	.agent-chat-row.is-error .agent-chat-bubble { background: var(--bg-red, var(--control-bg)); color: var(--text-on-red, var(--text-color)); }
	.agent-chat-tool { font-size: var(--text-sm); color: var(--text-muted); margin: 2px 0 6px 2px; cursor: pointer; font-family: var(--font-stack-mono, monospace); }
	.agent-chat-tool-args { display: none; margin: 4px 0 8px 14px; padding: 6px 8px; background: var(--control-bg); border-radius: 6px; font-size: var(--text-sm); white-space: pre-wrap; }
	.agent-chat-status { font-size: var(--text-sm); color: var(--text-muted); margin-bottom: 10px; }
	.agent-chat-empty { color: var(--text-muted); padding: 24px 0; text-align: center; }
	.agent-chat-action { border: 1px solid var(--border-color); border-left: 3px solid var(--orange-500, var(--border-color)); border-radius: 8px; padding: 8px 12px; margin: 4px 0 10px 0; background: var(--card-bg, var(--control-bg)); }
	.agent-chat-action-title { font-weight: 600; }
	.agent-chat-action-target { font-size: var(--text-sm); color: var(--text-muted); }
	.agent-chat-action-reason { margin-top: 4px; white-space: pre-wrap; word-break: break-word; }
	.agent-chat-action-link { display: inline-block; margin-top: 6px; font-size: var(--text-sm); }
	.agent-chat-action.is-extraction { border-left-color: var(--blue-500, var(--border-color)); }
	.agent-chat-action.is-extraction.is-review { border-left-color: var(--orange-500, var(--border-color)); }
	.agent-chat-action.is-extraction.is-failed { border-left-color: var(--red-500, var(--border-color)); }
	.agent-chat-action-line { font-size: var(--text-sm); margin-top: 2px; }
	.agent-chat-action-line.is-alarm { color: var(--text-on-red, var(--text-color)); background: var(--bg-red, var(--control-bg)); border-radius: 6px; padding: 4px 6px; margin-top: 4px; }
`;

function add_styles() {
	if (document.getElementById(STYLE_ID)) return;
	$(`<style id="${STYLE_ID}">${STYLES}</style>`).appendTo(document.head);
}

frappe_agents.RUN_UPDATE_EVENT = "frappe_agents:run_update";
frappe_agents.EXTRACTION_UPDATE_EVENT = "frappe_agents:extraction_update";

// Tools whose result is a proposal waiting for a person. Their finished event is
// what a reopened conversation redraws its approval cards from.
const PROPOSAL_TOOLS = ["propose_submit", "propose_cancel"];

/** Tool arguments as pretty JSON, whatever shape they arrived in. */
function pretty_args(args) {
	try {
		return JSON.stringify(typeof args === "string" ? JSON.parse(args) : args || {}, null, 2);
	} catch (e) {
		return String(args || "");
	}
}

/** Those arguments on one line, for the collapsed tool line. */
function args_summary(pretty) {
	const flat = pretty
		.replace(/\s+/g, " ")
		.replace(/^\{\s*|\s*\}$/g, "")
		.trim();
	return flat.length > 60 ? flat.slice(0, 60) + "…" : flat;
}

/** How one tool call reads in the log, running or finished. */
function tool_line_text(tool, pretty, running) {
	return `→ ${tool}(${args_summary(pretty)})${running ? " …" : ""}`;
}

/** Tool lines are held per run, so a second run cannot resolve the first one's line. */
function tool_line_key(run, id) {
	return `${run}::${id}`;
}

/** The text a harness tool result carried. */
function result_text(result) {
	const content = (result && result.content) || [];
	return content
		.filter((block) => block && block.type === "text")
		.map((block) => block.text || "")
		.join("");
}

/** The proposal a finished tool call produced, or null if it made none. */
function proposal_from_event(event) {
	if (!PROPOSAL_TOOLS.includes(event.toolName) || event.isError) return null;
	let payload = null;
	try {
		payload = JSON.parse(result_text(event.result));
	} catch (e) {
		return null;
	}
	const result = payload && payload.ok ? payload.result : null;
	return result && result.action ? result : null;
}

/** The little document mark on the context chip. */
function doc_icon() {
	return $(
		`<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4">
			<path d="M4 1.5h5.5L13 5v9.5H4z"/><path d="M9.5 1.5V5H13"/>
		</svg>`
	);
}

/** Extraction status as a line a person can read. */
function extraction_title(status) {
	switch (status) {
		case "Needs Review":
			return __("A document is extracted and waiting for your review");
		case "Accepted":
			return __("Extraction accepted");
		case "Discarded":
			return __("Extraction discarded");
		case "Failed":
			return __("Extraction failed");
		case "Running":
			return __("Reading the document…");
		default:
			return __("Document queued for extraction");
	}
}

frappe_agents.ChatUI = class ChatUI {
	/**
	 * @param {Object} opts
	 * @param {HTMLElement|jQuery} opts.parent   where the widget is mounted
	 * @param {string} [opts.agent]              agent to talk to
	 * @param {string} [opts.conversation]       conversation to rehydrate on load
	 * @param {string} [opts.context_doctype]    focal document passed to start_run
	 * @param {string} [opts.context_name]
	 * @param {boolean} [opts.compact]           shorter layout, for the form panel
	 * @param {Function} [opts.on_conversation]  called with (conversation, data|null)
	 */
	constructor(opts) {
		opts = opts || {};
		this.parent = $(opts.parent);
		this.agent = opts.agent || null;
		this.agent_label = opts.agent_label || null;
		this.conversation = opts.conversation || null;
		this.context_doctype = opts.context_doctype || null;
		this.context_name = opts.context_name || null;
		this.compact = Boolean(opts.compact);
		this.placeholder = opts.placeholder || __("Send a message to start.");
		this.on_conversation = opts.on_conversation || null;
		this.on_agent_change = opts.on_agent_change || null;
		// What the composer chips are drawn from: one list_agents row per agent,
		// carrying the tools it may call and the models this user may pick for it.
		// A surface that passes none gets a composer with no chips.
		this.agents = opts.agents || [];
		this.model_profile = opts.model_profile || null;
		// The document this conversation is about: seeded from the form panel, and
		// on a reload told to us by the server, which asks the read permission again.
		this.context = this.seed_context();
		this.clear_state();

		this.make();
		this.bind();
		this.refresh_composer();

		if (this.conversation) {
			this.load_conversation(this.conversation);
		} else {
			this.show_empty(this.agent ? this.placeholder : __("Select an agent to start."));
		}
	}

	/** Forget everything drawn for a conversation. Called wherever the log is emptied. */
	clear_state() {
		this.pending = {};
		// One card per extraction, redrawn in place as its status moves.
		this.extractions = {};
		// One card per proposal, so replaying a log cannot draw a second one.
		this.actions = {};
		// Tool lines drawn when the tool started, keyed by run and tool call id,
		// plus the queue the finished event finds them by.
		this.tool_lines = {};
		this.tool_waiting = {};
	}

	make() {
		add_styles();

		this.$body = $(`
			<div class="agent-chat">
				<div class="agent-chat-head">
					<span class="agent-chat-title"></span>
					<span class="agent-chat-context"></span>
				</div>
				<div class="agent-chat-log"></div>
				<div class="agent-chat-composer">
					<div class="agent-chat-pop"></div>
					<textarea class="form-control agent-chat-input" rows="2"></textarea>
					<div class="agent-chat-bar">
						<span class="agent-chat-chips"></span>
						<button class="btn btn-primary btn-sm agent-chat-send"></button>
					</div>
				</div>
			</div>
		`).appendTo(this.parent);

		if (this.compact) this.$body.addClass("agent-chat-compact");

		this.$head = this.$body.find(".agent-chat-head");
		this.$title = this.$body.find(".agent-chat-title");
		this.$context = this.$body.find(".agent-chat-context");
		this.$log = this.$body.find(".agent-chat-log");
		this.$input = this.$body.find(".agent-chat-input");
		this.$chips = this.$body.find(".agent-chat-chips");
		this.$pop = this.$body.find(".agent-chat-pop");
		this.$send = this.$body.find(".agent-chat-send");

		this.$input.attr("placeholder", __("Ask the agent something…"));
		this.$send.text(__("Send"));
		this.render_head();
		this.render_chips();
	}

	bind() {
		this.$send.on("click", () => this.send());
		this.$input.on("keydown", (e) => {
			if (e.key === "Enter" && !e.shiftKey) {
				e.preventDefault();
				this.send();
			}
		});

		// A chip popover closes on anything outside it — a chip with a menu of its
		// own stops the click before it gets here. Held on the instance so destroy()
		// takes this listener off the document with it.
		this.pop_dismiss_handler = (e) => {
			if (!this.pop_owner) return;
			if ($(e.target).closest(".agent-chat-pop").length) return;
			this.close_pop();
		};
		this.pop_escape_handler = (e) => {
			if (e.key === "Escape") this.close_pop();
		};
		$(document).on("click", this.pop_dismiss_handler);
		$(document).on("keydown", this.pop_escape_handler);

		// Kept on the instance so destroy() can unbind exactly this listener —
		// otherwise every panel open leaves another one rendering into a dead log.
		this.run_update_handler = (data) => this.on_run_update(data);
		frappe.realtime.on(frappe_agents.RUN_UPDATE_EVENT, this.run_update_handler);

		// Extraction runs as its own job, so its progress arrives on its own event
		// and usually after the run that asked for it has already finished.
		this.extraction_update_handler = (data) => this.on_extraction_update(data);
		frappe.realtime.on(frappe_agents.EXTRACTION_UPDATE_EVENT, this.extraction_update_handler);
	}

	destroy() {
		if (this.run_update_handler) {
			frappe.realtime.off(frappe_agents.RUN_UPDATE_EVENT, this.run_update_handler);
			this.run_update_handler = null;
		}
		if (this.extraction_update_handler) {
			frappe.realtime.off(frappe_agents.EXTRACTION_UPDATE_EVENT, this.extraction_update_handler);
			this.extraction_update_handler = null;
		}
		if (this.pop_dismiss_handler) {
			$(document).off("click", this.pop_dismiss_handler);
			$(document).off("keydown", this.pop_escape_handler);
			this.pop_dismiss_handler = null;
			this.pop_escape_handler = null;
		}
		this.clear_state();
		if (this.$body) this.$body.remove();
	}

	/** The document this chat was opened on, before the server has said anything. */
	seed_context() {
		if (!this.context_doctype || !this.context_name) return null;
		return { doctype: this.context_doctype, name: this.context_name, title: this.context_name };
	}

	/** Who you are talking to, and which document about. */
	render_head() {
		const title = this.agent_label || this.agent || "";
		this.$title.text(title);
		this.render_context();
		this.$head.toggle(Boolean(title || this.context));
	}

	/**
	 * The chip for the document this conversation is about.
	 *
	 * Drawn from whatever `this.context` holds, which on a reload is the server's
	 * answer rather than anything the surface remembered. A conversation the user
	 * may no longer read says only that there is a document: no doctype, no name.
	 */
	render_context() {
		this.$context.empty();
		if (!this.context) {
			this.$context.hide();
			return;
		}
		this.$context.show();

		if (this.context.restricted) {
			$("<span class='agent-chat-doc is-restricted'></span>")
				.attr("title", __("This conversation was started on a document you can no longer open."))
				.append(doc_icon())
				.append(
					$("<span class='agent-chat-doc-label'></span>").text(__("a document you can no longer see"))
				)
				.appendTo(this.$context);
			return;
		}

		const doctype = this.context.doctype;
		const name = this.context.name;
		$("<a class='agent-chat-doc'></a>")
			.attr("href", `/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}`)
			.attr("title", __("This conversation is about this document"))
			.append(doc_icon())
			.append(
				$("<span class='agent-chat-doc-label'></span>").text(
					`${__(doctype)}: ${this.context.title || name}`
				)
			)
			// Mounted in the form panel the route changes behind the dialog, so step
			// out of the way and let the router carry on.
			.on("click", () => this.$body.closest(".modal").modal("hide"))
			.appendTo(this.$context);
	}

	/** The list_agents row for the agent in hand, when the surface gave us one. */
	agent_info() {
		return this.agents.find((agent) => agent.name === this.agent) || null;
	}

	/** Take a fresh list_agents payload: what an agent may do can change under us. */
	set_agents(agents) {
		this.agents = agents || [];
		const info = this.agent_info();
		if (info) this.agent_label = info.agent_name || info.name;
		this.render_head();
		this.render_chips();
	}

	/** The chips beside the message box: who, on which model, with which tools. */
	render_chips() {
		this.close_pop();
		this.$chips.empty();
		const info = this.agent_info();
		if (!info) return;
		this.render_agent_chip(info);
		this.render_model_chip(info);
		this.render_tools_chip(info);
	}

	render_agent_chip(info) {
		const many = this.agents.length > 1;
		const $chip = this.make_chip("", info.agent_name || info.name, many)
			.attr("title", info.description || __("The agent you are talking to"))
			.appendTo(this.$chips);
		if (many) $chip.on("click", (e) => this.toggle_pop("agent", $chip, () => this.build_agent_pop(), e));
	}

	build_agent_pop() {
		this.pop_label(__("Talk to"));
		this.agents.forEach((agent) => {
			const $opt = this.pop_option(agent.agent_name || agent.name, agent.description);
			if (agent.name === this.agent) {
				$("<span class='agent-chat-pop-tick'>✓</span>").appendTo($opt);
			} else {
				$opt.addClass("is-clickable").on("click", () => {
					this.close_pop();
					this.choose_agent(agent.name);
				});
			}
		});
		this.pop_note(__("A conversation belongs to one agent, so switching starts a new one."));
	}

	/**
	 * Switch agents. A conversation is audited against one agent's grants, so this
	 * never continues the current one — it starts a new conversation, and says so
	 * before throwing away what is on screen.
	 */
	choose_agent(name) {
		if (!name || name === this.agent) return;
		const chosen = this.agents.find((agent) => agent.name === name);
		const label = chosen ? chosen.agent_name || chosen.name : name;
		if (!this.has_messages()) {
			this.switch_agent(name);
			return;
		}
		frappe.confirm(__("Start a new conversation with {0}? This one stays in your list.", [label]), () =>
			this.switch_agent(name)
		);
	}

	switch_agent(name) {
		this.set_agent(name);
		if (this.on_agent_change) this.on_agent_change(this.agent);
		this.focus();
	}

	has_messages() {
		return Boolean(this.conversation) || this.$log.find(".agent-chat-row").length > 0;
	}

	/**
	 * The model this conversation runs on. Label only when the agent's owner has
	 * configured no alternate this user may pick — there is nothing to choose from,
	 * and a menu of one is a promise the governance does not make.
	 */
	render_model_chip(info) {
		const choices = info.model_choices || [];
		const current = this.current_profile(info);
		if (!current) return;

		const many = choices.length > 1;
		const $chip = this.make_chip(__("model"), current, many).appendTo(this.$chips);
		if (many) {
			$chip.on("click", (e) => this.toggle_pop("model", $chip, () => this.build_model_pop(info), e));
		} else {
			$chip.attr("title", __("The model this agent runs on"));
		}
	}

	current_profile(info) {
		const choices = info.model_choices || [];
		const first = choices.length ? choices[0].name : "";
		return this.model_profile || info.model_profile || first || "";
	}

	build_model_pop(info) {
		const current = this.current_profile(info);
		this.pop_label(__("Model for this conversation"));
		(info.model_choices || []).forEach((choice) => {
			const sub = [choice.default ? __("default") : __("alternate"), choice.model_id]
				.filter(Boolean)
				.join(" · ");
			const $opt = this.pop_option(choice.name, sub);
			if (choice.name === current) {
				$("<span class='agent-chat-pop-tick'>✓</span>").appendTo($opt);
			} else {
				$opt.addClass("is-clickable").on("click", () => {
					this.close_pop();
					this.choose_model(choice.name);
				});
			}
		});
		this.pop_note(
			__(
				"Only models this agent's owner allows, filtered by your role. Every run records the model that actually ran."
			)
		);
	}

	/**
	 * Pin this conversation to a model. A conversation that does not exist yet is
	 * pinned by the first message instead; either way the server checks the choice
	 * against the agent's list and this user's roles, and a refusal changes nothing.
	 */
	choose_model(profile) {
		if (!profile) return;
		if (!this.conversation) {
			this.model_profile = profile;
			this.render_chips();
			return;
		}
		frappe.call({
			method: "frappe_agents.api.set_conversation_model",
			args: { conversation: this.conversation, model_profile: profile },
			callback: (r) => {
				if (!r.message) return;
				this.model_profile = r.message.model_profile || null;
				this.render_chips();
			},
		});
	}

	/** What the agent can do, as a count you can open. Read-only, always. */
	render_tools_chip(info) {
		const tools = info.tools || [];
		if (!tools.length) return;
		const $chip = this.make_chip(__("tools"), String(tools.length), true).appendTo(this.$chips);
		$chip.on("click", (e) => this.toggle_pop("tools", $chip, () => this.build_tools_pop(info), e));
	}

	build_tools_pop(info) {
		const tools = info.tools || [];
		this.pop_label(
			__("{0} can use — {1} tools", [info.agent_name || info.name, tools.length])
		);
		tools.forEach((tool) => {
			const $opt = this.pop_option(tool.tool_name, tool.description);
			if (!tool.capability) return;
			$("<span class='agent-chat-cap'></span>")
				.addClass(`is-${tool.capability.toLowerCase()}`)
				.text(__(tool.capability))
				.appendTo($opt);
		});
		this.pop_note(
			__("Read-only. Granting and scoping happen on the Agent form, where changes are versioned.")
		);
	}

	make_chip(key, value, has_menu) {
		const $chip = $("<span class='agent-chat-chip'></span>");
		if (key) $("<span class='agent-chat-chip-key'></span>").text(key).appendTo($chip);
		$("<span class='agent-chat-chip-value'></span>").text(value).appendTo($chip);
		if (has_menu) {
			$("<span class='agent-chat-chip-caret'>▾</span>").appendTo($chip);
		} else {
			$chip.addClass("is-static");
		}
		return $chip;
	}

	/** One popover, reused: opening another closes the one before it. */
	toggle_pop(owner, $anchor, build, event) {
		// The document listener closes the popover on every other click, and this
		// click is the one that opens it.
		if (event) event.stopPropagation();
		if (this.pop_owner === owner) {
			this.close_pop();
			return;
		}
		this.close_pop();
		this.pop_owner = owner;
		this.$pop.addClass("is-open").css("left", Math.max(0, $anchor.position().left));
		build();
	}

	close_pop() {
		if (!this.$pop) return;
		this.pop_owner = null;
		this.$pop.removeClass("is-open").empty();
	}

	pop_label(text) {
		$("<div class='agent-chat-pop-label'></div>").text(text).appendTo(this.$pop);
	}

	/** One row of a popover. The sub line is kept to one line, in full on hover. */
	pop_option(label, sub) {
		const $opt = $("<div class='agent-chat-pop-opt'></div>");
		const $text = $("<span class='agent-chat-pop-text'></span>").appendTo($opt);
		$("<span></span>").text(label).appendTo($text);
		if (sub) {
			$("<span class='agent-chat-pop-sub'></span>").text(sub).attr("title", sub).appendTo($text);
		}
		return $opt.appendTo(this.$pop);
	}

	pop_note(text) {
		$("<div class='agent-chat-pop-note'></div>").text(text).appendTo(this.$pop);
	}

	set_agent(agent) {
		agent = agent || null;
		if (agent === this.agent) return;
		this.agent = agent;
		const info = this.agent_info();
		this.agent_label = info ? info.agent_name || info.name : null;
		// A model chosen for one agent means nothing to the next one.
		this.model_profile = null;
		this.reset();
	}

	reset() {
		this.conversation = null;
		this.clear_state();
		this.$log.empty();
		// A new conversation is about whatever this surface was opened on, and
		// nothing else: the last conversation's document does not follow it.
		this.context = this.seed_context();
		this.render_head();
		this.render_chips();
		this.refresh_composer();
		this.show_empty(this.agent ? this.placeholder : __("Select an agent to start."));
	}

	refresh_composer() {
		this.$send.prop("disabled", !this.agent);
		this.$input.prop("disabled", !this.agent);
	}

	/** Redraw a past conversation from the server: one user bubble and one reply per run. */
	load_conversation(conversation) {
		if (!conversation) return;
		this.conversation = conversation;
		this.clear_state();
		this.$log.empty();
		// Whatever the last conversation was about is not this one's business until
		// the server says so.
		this.context = this.seed_context();
		this.render_head();
		this.show_empty(__("Loading conversation…"));

		frappe.call({
			method: "frappe_agents.api.get_conversation",
			args: { conversation: conversation },
			callback: (r) => {
				// The user may have switched agent or started a new chat meanwhile.
				if (this.conversation !== conversation) return;
				this.render_conversation(r.message);
			},
			error: () => {
				if (this.conversation !== conversation) return;
				this.conversation = null;
				this.show_empty(__("That conversation could not be loaded."));
			},
		});
	}

	render_conversation(data) {
		this.$log.empty();
		if (!data) {
			this.show_empty(this.placeholder);
			return;
		}
		if (data.agent) this.agent = data.agent;
		this.agent_label = data.agent_name || this.agent_label;
		this.model_profile = data.model_profile || null;
		// The server has just re-checked who may see this document, so its answer
		// replaces whatever this surface was carrying.
		this.context = data.context || this.seed_context();
		this.render_head();
		this.render_chips();
		this.refresh_composer();

		const runs = data.runs || [];
		if (!runs.length) {
			this.show_empty(this.placeholder);
		} else {
			runs.forEach((run) => this.render_past_run(run));
		}

		if (this.on_conversation) this.on_conversation(this.conversation, data);
		this.scroll_to_bottom();
	}

	render_past_run(run) {
		if (run.input_message) {
			this.$log.append(this.make_bubble(run.input_message, "is-user"));
		}
		this.replay_run_events(run);
		if (run.output_message) {
			this.$log.append(this.make_bubble(run.output_message, ""));
			return;
		}
		if (run.error) {
			this.$log.append(this.make_bubble(run.error, "is-error"));
			return;
		}
		// Still in flight: keep a pending line so realtime updates land in place.
		if (["Queued", "Running"].includes(run.status)) {
			const $pending = $("<div class='agent-chat-status'></div>")
				.text(`${__(run.status)}…`)
				.attr("data-run", run.name);
			this.$log.append($pending);
			this.pending[run.name] = $pending;
		}
	}

	/**
	 * Redraw what a finished run left behind that its own row does not hold.
	 *
	 * A proposal card is published while the run is going. Reopening the
	 * conversation used to lose it, so a person came back to a chat that never
	 * mentioned the thing waiting for their approval. The run's event log has it.
	 */
	replay_run_events(run) {
		const events = Array.isArray(run.event_log) ? run.event_log : [];
		// The reason the agent gave is an argument of the call, not part of its
		// result, so it is picked up when the call starts.
		const reasons = {};

		events.forEach((event) => {
			if (!event || !event.type) return;
			if (event.type === "tool_execution_start") {
				reasons[event.toolCallId] = (event.args || {}).reason;
				return;
			}
			if (event.type !== "tool_execution_end") return;

			const proposal = proposal_from_event(event);
			if (!proposal) return;
			this.render_action_proposed({
				run: run.name,
				action: proposal.action,
				action_type: proposal.action_type,
				target_doctype: proposal.target_doctype,
				target_name: proposal.target_name,
				reason: reasons[event.toolCallId] || "",
			});
		});
	}

	show_empty(text) {
		this.$log.empty().append($("<div class='agent-chat-empty'></div>").text(text));
	}

	clear_empty() {
		this.$log.find(".agent-chat-empty").remove();
	}

	focus() {
		this.$input.focus();
	}

	send() {
		const message = (this.$input.val() || "").trim();
		if (!this.agent) {
			frappe.show_alert({ message: __("Select an agent first"), indicator: "orange" });
			return;
		}
		if (!message) return;

		this.clear_empty();
		this.add_bubble(message, "is-user");
		this.$input.val("");
		this.set_busy(true);

		const $pending = this.add_status(__("Queued…"));

		const args = { agent: this.agent, message: message };
		if (this.conversation) args.conversation = this.conversation;
		// Carried on every turn, not only the first: the server checks the choice
		// again each time, because an agent's alternates and a user's roles move.
		if (this.model_profile) args.model_profile = this.model_profile;
		if (this.context_doctype && this.context_name) {
			args.context_doctype = this.context_doctype;
			args.context_name = this.context_name;
		}

		frappe.call({
			method: "frappe_agents.api.start_run",
			args: args,
			callback: (r) => {
				if (!r.message || !r.message.run) {
					$pending.remove();
					return;
				}
				const is_new = this.conversation !== r.message.conversation;
				this.conversation = r.message.conversation;
				$pending.attr("data-run", r.message.run);
				this.pending[r.message.run] = $pending;
				if (is_new && this.on_conversation) this.on_conversation(this.conversation, null);
			},
			error: () => $pending.remove(),
			always: () => this.set_busy(false),
		});
	}

	set_busy(busy) {
		this.$send.prop("disabled", busy || !this.agent);
		if (!busy) this.$input.focus();
	}

	on_run_update(data) {
		if (!data || !data.run) return;
		if (data.conversation && this.conversation && data.conversation !== this.conversation) return;
		if (!data.conversation && !this.pending[data.run]) return;

		switch (data.type) {
			case "harness_event":
				this.on_harness_event(data);
				break;
			case "tool_call":
				this.render_tool_call(data);
				break;
			case "message":
				this.render_message(data);
				break;
			case "error":
				this.render_error(data);
				break;
			case "action_proposed":
				this.render_action_proposed(data);
				break;
			default:
				this.render_status(data);
		}
		this.scroll_to_bottom();
	}

	render_status(data) {
		const status = data.status || data.value || "";
		const $pending = this.pending[data.run];
		if (["Completed", "Failed", "Cancelled"].includes(status)) {
			if ($pending) {
				if (status === "Completed") {
					$pending.remove();
				} else {
					$pending.text(__("Run {0}", [__(status)]));
				}
			}
			delete this.pending[data.run];
			return;
		}
		if ($pending && status) {
			$pending.text(`${__(status)}…`);
		}
	}

	/**
	 * The loop's own events. Only the tool lifecycle is drawn from them: the
	 * legacy tool_call event is published once a tool has already returned, so
	 * the started event is the only thing that can show a tool while it runs.
	 */
	on_harness_event(data) {
		const event = data.event || {};
		if (event.type === "tool_execution_start") {
			this.render_tool_started(data.run, event);
		} else if (event.type === "tool_execution_end") {
			// Usually already finished by the legacy event just before this one.
			// Not always: a tool the kill switch refused publishes no legacy event.
			this.finish_tool_line(this.tool_lines[tool_line_key(data.run, event.toolCallId)]);
		}
	}

	/** Draw a tool the agent has just started calling. It resolves in place when it returns. */
	render_tool_started(run, event) {
		const id = event.toolCallId;
		const key = tool_line_key(run, id);
		if (!id || this.tool_lines[key]) return;

		const line = this.make_tool_line(event.toolName || "tool", event.args || {}, true);
		this.insert_before_pending(run, line.$line);
		this.insert_before_pending(run, line.$args);

		this.tool_lines[key] = line;
		const waiting = tool_line_key(run, line.tool);
		this.tool_waiting[waiting] = this.tool_waiting[waiting] || [];
		this.tool_waiting[waiting].push(key);
	}

	render_tool_call(data) {
		const tool = data.tool || data.name || "tool";
		const args = data.args !== undefined ? data.args : data.args_json;

		// This call may already be on screen as a started line. Finish that one
		// rather than drawing the same call a second time.
		const started = this.take_started(data.run, tool);
		if (started) {
			this.finish_tool_line(started, args);
			return;
		}

		const line = this.make_tool_line(tool, args, false);
		this.insert_before_pending(data.run, line.$line);
		this.insert_before_pending(data.run, line.$args);
	}

	/** One tool line: the call, and its full arguments behind a click. */
	make_tool_line(tool, args, running) {
		const pretty = pretty_args(args);
		const $line = $("<div class='agent-chat-tool'></div>").text(tool_line_text(tool, pretty, running));
		const $args = $("<pre class='agent-chat-tool-args'></pre>").text(pretty);
		$line.on("click", () => $args.toggle());
		return { $line: $line, $args: $args, tool: tool, args: args, done: !running };
	}

	/** The oldest started line for this tool on this run that has not finished yet. */
	take_started(run, tool) {
		const queue = this.tool_waiting[tool_line_key(run, tool)] || [];
		while (queue.length) {
			const line = this.tool_lines[queue.shift()];
			if (line && !line.done) return line;
		}
		return null;
	}

	/** Resolve a started line into the finished one, with the arguments it ran with. */
	finish_tool_line(line, args) {
		if (!line || line.done) return;
		line.done = true;
		if (args !== undefined) {
			const pretty = pretty_args(args);
			line.$line.text(tool_line_text(line.tool, pretty, false));
			line.$args.text(pretty);
			return;
		}
		line.$line.text(tool_line_text(line.tool, pretty_args(line.args), false));
	}

	/**
	 * The agent has asked for a submit or a cancel. That is not a message and not
	 * a tool result — it is a thing waiting for a person, so it gets a card with
	 * the agent's stated reason and a way through to the approval form.
	 */
	render_action_proposed(data) {
		if (!data.action) return;
		// The same proposal reaches here twice when a run that is still going is
		// also replayed from its log. One proposal is one card.
		if (this.actions[data.action]) return;
		this.clear_empty();

		const verb = data.action_type === "Cancel" ? __("Cancel") : __("Submit");
		const $card = $("<div class='agent-chat-action'></div>");
		$("<div class='agent-chat-action-title'></div>")
			.text(__("Waiting for your approval: {0}", [verb]))
			.appendTo($card);

		if (data.target_doctype && data.target_name) {
			$("<div class='agent-chat-action-target'></div>")
				.text(`${__(data.target_doctype)}: ${data.target_name}`)
				.appendTo($card);
		}
		if (data.reason) {
			$("<div class='agent-chat-action-reason'></div>").text(data.reason).appendTo($card);
		}

		// A plain desk link: the router picks it up, so it works in the page and
		// in the form panel without either of them knowing about routes.
		$("<a class='agent-chat-action-link'></a>")
			.attr("href", `/app/agent-action/${encodeURIComponent(data.action)}`)
			.text(__("Review the proposal"))
			// Mounted in the form panel the route changes behind the dialog, so
			// step out of the way and let the router carry on.
			.on("click", () => this.$body.closest(".modal").modal("hide"))
			.appendTo($card);

		this.insert_before_pending(data.run, $card);
		this.actions[data.action] = $card;
	}

	/**
	 * An extraction moved. It belongs in the log when this conversation asked for
	 * it, or when it carries no attribution at all — realtime is delivered to one
	 * user, so an unattributed update is this user's own document.
	 */
	on_extraction_update(data) {
		if (!data) return;
		const name = data.extraction || data.name;
		if (!name) return;
		if (data.conversation) {
			if (data.conversation !== this.conversation) return;
		} else if (data.run) {
			if (!this.pending[data.run] && !this.extractions[name]) return;
		} else if (!this.conversation && !this.extractions[name]) {
			return;
		}

		this.render_extraction_update(name, data);
		this.scroll_to_bottom();
	}

	/**
	 * Everything on this card came out of the document or out of the model, so it
	 * is written with .text(). The card says what is waiting for a person; the
	 * values themselves are for the review form, next to the source file.
	 */
	render_extraction_update(name, data) {
		this.clear_empty();

		const status = data.status || "";
		const $card = $("<div class='agent-chat-action is-extraction'></div>");
		if (status === "Needs Review") $card.addClass("is-review");
		if (status === "Failed") $card.addClass("is-failed");

		$("<div class='agent-chat-action-title'></div>").text(extraction_title(status)).appendTo($card);

		if (data.target_doctype) {
			$("<div class='agent-chat-action-target'></div>")
				.text(data.file_name ? `${__(data.target_doctype)} — ${data.file_name}` : __(data.target_doctype))
				.appendTo($card);
		} else if (data.file_name) {
			$("<div class='agent-chat-action-target'></div>").text(data.file_name).appendTo($card);
		}

		const sensitive = Array.isArray(data.sensitive_fields)
			? data.sensitive_fields.length
			: cint(data.sensitive_count);
		if (sensitive) {
			$("<div class='agent-chat-action-line'></div>")
				.text(__("{0} values are held back until you confirm them one by one.", [sensitive]))
				.appendTo($card);
		}

		const mismatches = Array.isArray(data.mismatched_fields) ? data.mismatched_fields.length : cint(data.mismatches);
		if (mismatches) {
			$("<div class='agent-chat-action-line is-alarm'></div>")
				.text(__("{0} of them disagree with the record on file. Check before you confirm anything.", [mismatches]))
				.appendTo($card);
		}

		if (data.duplicate || cint(data.duplicate_count)) {
			$("<div class='agent-chat-action-line'></div>")
				.text(__("This looks like a document that was already extracted."))
				.appendTo($card);
		}

		if (data.error) {
			$("<div class='agent-chat-action-reason'></div>").text(data.error).appendTo($card);
		}

		$("<a class='agent-chat-action-link'></a>")
			.attr("href", `/app/document-extraction/${encodeURIComponent(name)}`)
			.text(status === "Needs Review" ? __("Review the extraction") : __("Open the extraction"))
			// In the form panel the route changes behind the dialog, so step aside
			// and let the router carry on.
			.on("click", () => this.$body.closest(".modal").modal("hide"))
			.appendTo($card);

		const $previous = this.extractions[name];
		if ($previous && $previous.parent().length) {
			$previous.replaceWith($card);
		} else {
			this.insert_before_pending(data.run, $card);
		}
		this.extractions[name] = $card;
	}

	render_message(data) {
		const text = data.text || data.message || data.output_message || "";
		if (text) {
			this.clear_empty();
			this.insert_before_pending(data.run, this.make_bubble(text, ""));
		}
		const $pending = this.pending[data.run];
		if ($pending) {
			$pending.remove();
			delete this.pending[data.run];
		}
	}

	render_error(data) {
		const text = data.error || data.text || __("The run failed.");
		this.clear_empty();
		this.insert_before_pending(data.run, this.make_bubble(text, "is-error"));
		const $pending = this.pending[data.run];
		if ($pending) {
			$pending.remove();
			delete this.pending[data.run];
		}
	}

	make_bubble(text, cls) {
		const $row = $(`<div class='agent-chat-row ${cls}'></div>`);
		$("<div class='agent-chat-bubble'></div>").text(text).appendTo($row);
		return $row;
	}

	add_bubble(text, cls) {
		const $row = this.make_bubble(text, cls);
		this.$log.append($row);
		this.scroll_to_bottom();
		return $row;
	}

	add_status(text) {
		const $el = $("<div class='agent-chat-status'></div>").text(text);
		this.$log.append($el);
		this.scroll_to_bottom();
		return $el;
	}

	insert_before_pending(run, $el) {
		const $pending = this.pending[run];
		if ($pending && $pending.parent().length) {
			$pending.before($el);
		} else {
			this.$log.append($el);
		}
	}

	scroll_to_bottom() {
		this.$log.scrollTop(this.$log[0].scrollHeight);
	}
};
