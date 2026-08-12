frappe.provide("frappe_agents");

frappe.pages["agent-chat"].on_page_load = function (wrapper) {
	frappe_agents.chat = new frappe_agents.AgentChat(wrapper);
};

frappe.pages["agent-chat"].on_page_show = function () {
	if (frappe_agents.chat) {
		frappe_agents.chat.load_agents();
	}
};

frappe_agents.AgentChat = class AgentChat {
	constructor(wrapper) {
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("Agent Chat"),
			single_column: true,
		});
		this.agents = [];
		this.agent = undefined;
		this.conversation = null;
		this.pending = {};
		this.make();
		this.bind();
		this.load_agents();
	}

	make() {
		this.add_styles();

		this.agent_field = this.page.add_field({
			fieldtype: "Select",
			fieldname: "agent",
			label: __("Agent"),
			options: [],
			change: () => this.set_agent(this.agent_field.get_value()),
		});

		this.page.set_secondary_action(__("New Chat"), () => this.new_chat());

		this.$body = $(`
			<div class="agent-chat">
				<div class="agent-chat-log"></div>
				<div class="agent-chat-composer">
					<textarea class="form-control agent-chat-input" rows="2"></textarea>
					<button class="btn btn-primary btn-sm agent-chat-send"></button>
				</div>
			</div>
		`).appendTo(this.page.main);

		this.$log = this.$body.find(".agent-chat-log");
		this.$input = this.$body.find(".agent-chat-input");
		this.$send = this.$body.find(".agent-chat-send");

		this.$input.attr("placeholder", __("Ask the agent something…"));
		this.$send.text(__("Send"));
	}

	add_styles() {
		if (document.getElementById("agent-chat-styles")) return;
		$(`<style id="agent-chat-styles">
			.agent-chat { display: flex; flex-direction: column; height: calc(100vh - 220px); min-height: 320px; }
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
		</style>`).appendTo(document.head);
	}

	bind() {
		this.$send.on("click", () => this.send());
		this.$input.on("keydown", (e) => {
			if (e.key === "Enter" && !e.shiftKey) {
				e.preventDefault();
				this.send();
			}
		});
		frappe.realtime.on("frappe_agents:run_update", (data) => this.on_run_update(data));
	}

	load_agents() {
		frappe.call({
			method: "frappe_agents.api.list_agents",
			callback: (r) => {
				this.agents = r.message || [];
				const options = this.agents.map((a) => ({
					value: a.name,
					label: a.agent_name || a.name,
				}));
				this.agent_field.df.options = options;
				if (this.agent_field.set_options) {
					this.agent_field.set_options();
				} else {
					this.agent_field.refresh();
				}

				if (!this.agents.length) {
					this.set_agent(null);
					this.show_empty(__("No agents are available to you."));
					return;
				}
				const current = this.agents.find((a) => a.name === this.agent);
				const chosen = current ? current.name : this.agents[0].name;
				this.agent_field.set_value(chosen);
				this.set_agent(chosen);
			},
		});
	}

	set_agent(agent) {
		agent = agent || null;
		if (agent === this.agent) return;
		this.agent = agent;
		this.conversation = null;
		this.pending = {};
		this.$log.empty();
		this.$send.prop("disabled", !this.agent);
		this.$input.prop("disabled", !this.agent);
		if (this.agent) {
			this.show_empty(__("Send a message to start."));
		}
	}

	new_chat() {
		this.conversation = null;
		this.pending = {};
		this.$log.empty();
		this.show_empty(__("Send a message to start."));
		this.$input.focus();
	}

	show_empty(text) {
		this.$log.empty().append($("<div class='agent-chat-empty'></div>").text(text));
	}

	clear_empty() {
		this.$log.find(".agent-chat-empty").remove();
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

		frappe.call({
			method: "frappe_agents.api.start_run",
			args: args,
			callback: (r) => {
				if (!r.message || !r.message.run) {
					$pending.remove();
					return;
				}
				this.conversation = r.message.conversation;
				$pending.attr("data-run", r.message.run);
				this.pending[r.message.run] = $pending;
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
		const flat = pretty.replace(/\s+/g, " ").replace(/^\{\s*|\s*\}$/g, "").trim();
		const summary = flat.length > 60 ? flat.slice(0, 60) + "…" : flat;

		const $line = $("<div class='agent-chat-tool'></div>").text(`→ ${tool}(${summary})`);
		const $args = $("<pre class='agent-chat-tool-args'></pre>").text(pretty);
		$line.on("click", () => $args.toggle());

		this.insert_before_pending(data.run, $line);
		this.insert_before_pending(data.run, $args);
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
