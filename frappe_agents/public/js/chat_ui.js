// Shared chat renderer. The Agent Chat page and the form panel both mount this,
// so a change to how a turn is drawn only has to be made once.
frappe.provide("frappe_agents");

const STYLE_ID = "frappe-agents-chat-styles";

const STYLES = `
	.agent-chat { display: flex; flex-direction: column; height: calc(100vh - 220px); min-height: 320px; }
	.agent-chat.agent-chat-compact { height: 55vh; min-height: 260px; }
	.agent-chat-context { font-size: var(--text-sm); color: var(--text-muted); padding-bottom: 6px; }
	.agent-chat-log { flex: 1; overflow-y: auto; padding: 8px 0; }
	.agent-chat-composer { display: flex; gap: 8px; align-items: flex-end; padding-top: 8px; border-top: 1px solid var(--border-color); }
	.agent-chat-input { resize: vertical; }
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
`;

function add_styles() {
	if (document.getElementById(STYLE_ID)) return;
	$(`<style id="${STYLE_ID}">${STYLES}</style>`).appendTo(document.head);
}

frappe_agents.RUN_UPDATE_EVENT = "frappe_agents:run_update";

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
		this.conversation = opts.conversation || null;
		this.context_doctype = opts.context_doctype || null;
		this.context_name = opts.context_name || null;
		this.compact = Boolean(opts.compact);
		this.placeholder = opts.placeholder || __("Send a message to start.");
		this.on_conversation = opts.on_conversation || null;
		this.pending = {};

		this.make();
		this.bind();
		this.refresh_composer();

		if (this.conversation) {
			this.load_conversation(this.conversation);
		} else {
			this.show_empty(this.agent ? this.placeholder : __("Select an agent to start."));
		}
	}

	make() {
		add_styles();

		this.$body = $(`
			<div class="agent-chat">
				<div class="agent-chat-context"></div>
				<div class="agent-chat-log"></div>
				<div class="agent-chat-composer">
					<textarea class="form-control agent-chat-input" rows="2"></textarea>
					<button class="btn btn-primary btn-sm agent-chat-send"></button>
				</div>
			</div>
		`).appendTo(this.parent);

		if (this.compact) this.$body.addClass("agent-chat-compact");

		this.$context = this.$body.find(".agent-chat-context");
		this.$log = this.$body.find(".agent-chat-log");
		this.$input = this.$body.find(".agent-chat-input");
		this.$send = this.$body.find(".agent-chat-send");

		this.$input.attr("placeholder", __("Ask the agent something…"));
		this.$send.text(__("Send"));
		this.render_context();
	}

	bind() {
		this.$send.on("click", () => this.send());
		this.$input.on("keydown", (e) => {
			if (e.key === "Enter" && !e.shiftKey) {
				e.preventDefault();
				this.send();
			}
		});

		// Kept on the instance so destroy() can unbind exactly this listener —
		// otherwise every panel open leaves another one rendering into a dead log.
		this.run_update_handler = (data) => this.on_run_update(data);
		frappe.realtime.on(frappe_agents.RUN_UPDATE_EVENT, this.run_update_handler);
	}

	destroy() {
		if (this.run_update_handler) {
			frappe.realtime.off(frappe_agents.RUN_UPDATE_EVENT, this.run_update_handler);
			this.run_update_handler = null;
		}
		this.pending = {};
		if (this.$body) this.$body.remove();
	}

	render_context() {
		if (!this.context_doctype || !this.context_name) {
			this.$context.hide();
			return;
		}
		this.$context.text(__("About {0}: {1}", [__(this.context_doctype), this.context_name])).show();
	}

	set_agent(agent) {
		agent = agent || null;
		if (agent === this.agent) return;
		this.agent = agent;
		this.reset();
	}

	reset() {
		this.conversation = null;
		this.pending = {};
		this.$log.empty();
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
		this.pending = {};
		this.$log.empty();
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

	render_tool_call(data) {
		const tool = data.tool || data.name || "tool";
		const args = data.args !== undefined ? data.args : data.args_json;
		let pretty = "";
		try {
			pretty = JSON.stringify(typeof args === "string" ? JSON.parse(args) : args || {}, null, 2);
		} catch (e) {
			pretty = String(args || "");
		}
		const flat = pretty
			.replace(/\s+/g, " ")
			.replace(/^\{\s*|\s*\}$/g, "")
			.trim();
		const summary = flat.length > 60 ? flat.slice(0, 60) + "…" : flat;

		const $line = $("<div class='agent-chat-tool'></div>").text(`→ ${tool}(${summary})`);
		const $args = $("<pre class='agent-chat-tool-args'></pre>").text(pretty);
		$line.on("click", () => $args.toggle());

		this.insert_before_pending(data.run, $line);
		this.insert_before_pending(data.run, $args);
	}

	/**
	 * The agent has asked for a submit or a cancel. That is not a message and not
	 * a tool result — it is a thing waiting for a person, so it gets a card with
	 * the agent's stated reason and a way through to the approval form.
	 */
	render_action_proposed(data) {
		if (!data.action) return;
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
