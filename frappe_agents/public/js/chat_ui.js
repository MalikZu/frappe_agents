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
	.agent-chat-sr {
		position: absolute; width: 1px; height: 1px; overflow: hidden;
		clip-path: inset(50%); white-space: nowrap;
	}
	.agent-chat-composer {
		position: relative; margin-top: 8px; background: var(--card-bg, var(--control-bg));
		border: 1px solid var(--border-color); border-radius: var(--border-radius-lg);
	}
	.agent-chat-input.form-control { border: none; background: transparent; resize: none; padding: 10px 12px 2px; }
	/* The input is borderless inside the composer, so the composer wears the ring
	   for it — Bootstrap's own focus shadow would sit in mid-air. */
	.agent-chat-input.form-control:focus { box-shadow: none; }
	.agent-chat-composer:focus-within { outline: 2px solid var(--text-color); outline-offset: 2px; }
	/* Desk drops the browser's focus ring on links and buttons and puts one back
	   only for .btn. Nothing on this screen is a .btn, so it puts its own back. */
	.agent-chat-doc:focus-visible,
	.agent-chat-action-link:focus-visible,
	.agent-chat-chip:focus-visible,
	.agent-chat-attach:focus-visible,
	.agent-chat-file-drop:focus-visible,
	.agent-chat-pop-opt:focus-visible,
	.agent-chat-tool-head:focus-visible,
	.agent-chat-think-head:focus-visible { outline: 2px solid var(--text-color); outline-offset: 2px; }
	.agent-chat-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; padding: 4px 8px 8px; }
	.agent-chat-chips { display: inline-flex; align-items: center; flex-wrap: wrap; gap: 6px; min-width: 0; }
	.agent-chat-send { margin-inline-start: auto; }
	.agent-chat-attach {
		flex: none; display: inline-flex; align-items: center; justify-content: center;
		width: 26px; height: 26px; padding: 0; border: none; background: none;
		border-radius: var(--border-radius-md, 6px); color: var(--text-muted); cursor: pointer;
	}
	.agent-chat-attach:hover { background: var(--highlight-color); color: var(--text-color); }
	.agent-chat-attach:disabled { opacity: 0.5; cursor: default; background: none; }
	.agent-chat-files { display: none; flex-wrap: wrap; gap: 6px; padding: 6px 10px 0; }
	.agent-chat-files.is-filled { display: flex; }
	.agent-chat-file {
		display: inline-flex; align-items: center; gap: 4px; max-width: 240px;
		border: 1px solid var(--border-color); background: var(--control-bg);
		border-radius: 999px; padding: 2px 4px; padding-inline-start: 10px; font-size: var(--text-sm);
	}
	.agent-chat-file-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.agent-chat-file-drop {
		flex: none; border: none; background: none; padding: 0 4px; line-height: 1;
		font: inherit; color: var(--text-muted); cursor: pointer;
	}
	.agent-chat-file-drop:hover { color: var(--text-color); }
	.agent-chat-chip {
		display: inline-flex; align-items: center; gap: 5px; max-width: 260px;
		border: 1px solid var(--border-color); background: var(--control-bg); border-radius: 999px;
		padding: 2px 10px; font: inherit; font-size: var(--text-sm); color: var(--text-color); cursor: pointer;
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
	.agent-chat-tool { margin: 2px 0 6px; margin-inline-start: 2px; }
	.agent-chat-tool-head {
		display: inline-flex; align-items: flex-start; gap: 6px; max-width: 100%;
		padding: 1px 0; border: none; background: none; text-align: start;
		font: inherit; font-family: var(--font-stack-mono, monospace); font-size: var(--text-sm);
		color: var(--text-muted); cursor: pointer;
	}
	.agent-chat-tool-head:hover { color: var(--text-color); }
	.agent-chat-tool-caret { display: inline-block; font-size: 9px; padding-top: 3px; }
	.agent-chat-tool.is-open .agent-chat-tool-caret { transform: rotate(90deg); }
	.agent-chat-tool-detail { display: none; margin: 2px 0 8px; margin-inline-start: 14px; }
	.agent-chat-tool.is-open .agent-chat-tool-detail { display: grid; grid-template-columns: 1fr 1fr; gap: 0 10px; }
	.agent-chat-tool-col { min-width: 0; }
	@media (max-width: 700px) { .agent-chat-tool.is-open .agent-chat-tool-detail { grid-template-columns: 1fr; } }
	.agent-chat-tool-label { font-size: var(--text-xs); font-weight: 600; text-transform: uppercase; letter-spacing: 0.07em; color: var(--text-muted); margin-top: 6px; }
	.agent-chat-tool-block {
		max-height: 260px; overflow: auto; margin: 2px 0 0; padding: 6px 8px;
		background: var(--control-bg); border-radius: 6px;
		font-family: var(--font-stack-mono, monospace); font-size: var(--text-sm);
		white-space: pre-wrap; word-break: break-word;
	}
	.agent-chat-tool-block.is-waiting { color: var(--text-muted); font-style: italic; }
	.agent-chat-tool-block.is-error { background: var(--bg-red, var(--control-bg)); color: var(--text-on-red, var(--text-color)); }
	.agent-chat-think { margin: 0 0 8px; margin-inline-start: 2px; }
	.agent-chat-think-head {
		display: inline-flex; align-items: center; gap: 6px; padding: 1px 0;
		border: none; background: none; font: inherit; font-size: var(--text-sm);
		color: var(--text-muted); cursor: pointer;
	}
	.agent-chat-think-head:hover { color: var(--text-color); }
	.agent-chat-think-caret { display: inline-block; font-size: 9px; }
	.agent-chat-think.is-open .agent-chat-think-caret { transform: rotate(90deg); }
	.agent-chat-think-body {
		display: none; max-height: 220px; overflow-y: auto; margin: 4px 0 0; margin-inline-start: 14px;
		padding: 6px 8px; background: var(--control-bg); border-radius: 6px;
		font-family: var(--font-stack-mono, monospace); font-size: var(--text-sm);
		color: var(--text-muted); white-space: pre-wrap; word-break: break-word;
	}
	.agent-chat-think.is-open .agent-chat-think-body { display: block; }
	@keyframes agent-chat-think-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }
	.agent-chat-think.is-live .agent-chat-think-label { animation: agent-chat-think-pulse 1.4s ease-in-out infinite; }
	@media (prefers-reduced-motion: reduce) {
		.agent-chat-think.is-live .agent-chat-think-label { animation: none; }
	}
	.agent-chat-status { font-size: var(--text-sm); color: var(--text-muted); margin-bottom: 10px; }
	.agent-chat-empty { color: var(--text-muted); padding: 24px 0; text-align: center; }
	.agent-chat-action { border: 1px solid var(--border-color); border-inline-start: 3px solid var(--orange-500, var(--border-color)); border-radius: 8px; padding: 8px 12px; margin: 4px 0 10px 0; background: var(--card-bg, var(--control-bg)); }
	.agent-chat-action-title { font-weight: 600; }
	.agent-chat-action-target { font-size: var(--text-sm); color: var(--text-muted); }
	.agent-chat-action-reason { margin-top: 4px; white-space: pre-wrap; word-break: break-word; }
	.agent-chat-action-link { display: inline-block; margin-top: 6px; font-size: var(--text-sm); }
	.agent-chat-action.is-extraction { border-inline-start-color: var(--blue-500, var(--border-color)); }
	.agent-chat-action.is-extraction.is-review { border-inline-start-color: var(--orange-500, var(--border-color)); }
	.agent-chat-action.is-extraction.is-failed { border-inline-start-color: var(--red-500, var(--border-color)); }
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

// How much of a conversation is kept in memory, and how many conversations.
// Oldest events go first: a run that is still going will write its own log the
// moment it ends, and that log is what a reload reads.
const BUFFER_EVENTS = 2000;
const BUFFER_CONVERSATIONS = 20;

// How far from the bottom the log may be scrolled and still follow new text.
const FOLLOW_SLACK = 120;

// Two chats can be mounted at once — the page and a form panel — so the ids the
// chips point at with aria-controls have to be unique per widget.
let mounted_chats = 0;

/**
 * Every run event this page has heard, kept per conversation.
 *
 * A run only writes its event log when it ends, so a run still going has
 * nothing on the server to redraw from: switch to another conversation and back
 * and the tool lines, the thinking and the half-written answer were gone. They
 * are kept here instead, for as long as the page is open, and replayed when the
 * conversation comes back on screen.
 *
 * One listener for the whole page rather than one per chat, because two chats
 * can be mounted at once — the Agent Chat page and a form panel — and both
 * storing the same event would replay it twice.
 */
frappe_agents.run_buffer = {
	conversations: new Map(),
	listening: false,

	listen() {
		if (this.listening) return;
		this.listening = true;
		frappe.realtime.on(frappe_agents.RUN_UPDATE_EVENT, (data) => this.keep("run", data));
		frappe.realtime.on(frappe_agents.EXTRACTION_UPDATE_EVENT, (data) => this.keep("extraction", data));
	},

	keep(channel, data) {
		const conversation = data && data.conversation;
		if (!conversation) return;
		const kept = this.conversations.get(conversation) || [];
		kept.push({ channel: channel, data: data });
		if (kept.length > BUFFER_EVENTS) kept.splice(0, kept.length - BUFFER_EVENTS);
		// Re-inserting moves the key to the end, so the map is ordered by the
		// conversation that spoke least recently and that is the one dropped.
		this.conversations.delete(conversation);
		this.conversations.set(conversation, kept);
		while (this.conversations.size > BUFFER_CONVERSATIONS) {
			this.conversations.delete(this.conversations.keys().next().value);
		}
	},

	events(conversation) {
		return this.conversations.get(conversation) || [];
	},
};

/** The content blocks an assistant message ended up with. */
function message_blocks(message) {
	if (!message || message.role !== "assistant") return [];
	return Array.isArray(message.content) ? message.content : [];
}

/** What that message said, without the thinking and the tool calls. */
function message_text(message) {
	return message_blocks(message)
		.filter((block) => block && block.type === "text")
		.map((block) => block.text || "")
		.join("");
}

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

/** The paperclip on the attach button. */
function clip_icon() {
	return $(
		`<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
			stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
			<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
		</svg>`
	);
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
		// Files uploaded for the next message. The upload itself is already on a
		// record; this list is only what the message about to be sent will name.
		this.uploads = [];
		// One card per extraction, redrawn in place as its status moves.
		this.extractions = {};
		// One card per proposal, so replaying a log cannot draw a second one.
		this.actions = {};
		// Tool lines drawn when the tool started, keyed by run and tool call id,
		// plus the queue the finished event finds them by.
		this.tool_lines = {};
		this.tool_waiting = {};
		// What each run is writing right now: the message on screen, the text
		// accumulated for it, and the thinking strips open beside it.
		this.streams = {};
		this.update_busy();
	}

	make() {
		add_styles();

		this.uid = ++mounted_chats;
		this.pop_id = `agent-chat-pop-${this.uid}`;

		// The popover is drawn after the chips that open it: it is absolutely
		// positioned, so nothing moves, and tab now leaves a chip into its own menu
		// instead of five stops back past the whole transcript.
		this.$body = $(`
			<div class="agent-chat">
				<div class="agent-chat-head">
					<span class="agent-chat-title"></span>
					<span class="agent-chat-context"></span>
				</div>
				<div class="agent-chat-log"></div>
				<div class="agent-chat-sr" role="status"></div>
				<div class="agent-chat-composer">
					<textarea class="form-control agent-chat-input" rows="2"></textarea>
					<div class="agent-chat-files"></div>
					<div class="agent-chat-bar">
						<button type="button" class="agent-chat-attach"></button>
						<span class="agent-chat-chips"></span>
						<button class="btn btn-primary btn-sm agent-chat-send"></button>
					</div>
					<div class="agent-chat-pop" id="${this.pop_id}" role="group" tabindex="-1"></div>
				</div>
			</div>
		`).appendTo(this.parent);

		if (this.compact) this.$body.addClass("agent-chat-compact");

		this.$head = this.$body.find(".agent-chat-head");
		this.$title = this.$body.find(".agent-chat-title");
		this.$context = this.$body.find(".agent-chat-context");
		this.$log = this.$body.find(".agent-chat-log");
		this.$sr = this.$body.find(".agent-chat-sr");
		this.$input = this.$body.find(".agent-chat-input");
		this.$files = this.$body.find(".agent-chat-files");
		this.$chips = this.$body.find(".agent-chat-chips");
		this.$attach = this.$body.find(".agent-chat-attach");
		this.$pop = this.$body.find(".agent-chat-pop");
		this.$send = this.$body.find(".agent-chat-send");

		this.$input.attr("placeholder", __("Ask the agent something…"));
		// An icon on its own says nothing to a screen reader, so the hint is its name.
		this.$attach.attr({ title: this.attach_hint(), "aria-label": this.attach_hint() });
		this.$attach.append(clip_icon());
		this.$send.text(__("Send"));
		this.render_head();
		this.render_chips();
		this.render_files();
	}

	bind() {
		this.$send.on("click", () => this.send());
		this.$attach.on("click", () => this.attach());
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

		// The buffer listens for the whole page, whether a chat is mounted or not:
		// a run that finishes while the panel is closed still has to be redrawable.
		frappe_agents.run_buffer.listen();

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
				this.clickable($opt, () => {
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
				this.clickable($opt, () => {
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

	/**
	 * A chip. One that opens a menu is a button, so it is reachable from the
	 * keyboard like any other control; one that only states a fact is not.
	 */
	make_chip(key, value, has_menu) {
		const $chip = has_menu
			? $("<button type='button' class='agent-chat-chip'></button>").attr({
					"aria-haspopup": "true",
					"aria-expanded": "false",
					"aria-controls": this.pop_id,
			  })
			: $("<span class='agent-chat-chip is-static'></span>");
		if (key) $("<span class='agent-chat-chip-key'></span>").text(key).appendTo($chip);
		$("<span class='agent-chat-chip-value'></span>").text(value).appendTo($chip);
		if (has_menu) $("<span class='agent-chat-chip-caret'>▾</span>").appendTo($chip);
		return $chip;
	}

	/** One popover, reused: opening another closes the one before it. */
	toggle_pop(owner, $anchor, build, event) {
		// The document listener closes the popover on every other click, and this
		// click is the one that opens it.
		if (event) event.stopPropagation();
		if (this.pop_owner === owner) {
			this.close_pop();
			$anchor.focus();
			return;
		}
		this.close_pop();
		this.pop_owner = owner;
		this.$pop_anchor = $anchor;
		$anchor.attr("aria-expanded", "true");
		this.$pop.addClass("is-open");
		build();
		this.place_pop($anchor);
		// A menu opened from the keyboard has to take the keyboard with it. The
		// tools popover has nothing to pick, so the popover itself takes focus.
		const $first = this.$pop.find(".agent-chat-pop-opt.is-clickable").first();
		($first.length ? $first : this.$pop).focus();
	}

	/**
	 * Where the popover sits, in inline terms so it mirrors with the text, and
	 * never past the trailing edge of the composer it hangs off.
	 */
	place_pop($anchor) {
		const parent_width = this.$pop.parent().width() || 0;
		const pop_width = this.$pop.outerWidth() || 0;
		const rtl = this.$body.length && window.getComputedStyle(this.$body[0]).direction === "rtl";
		const anchor_start = rtl
			? parent_width - ($anchor.position().left + ($anchor.outerWidth() || 0))
			: $anchor.position().left;
		const room = Math.max(0, parent_width - pop_width);
		this.$pop.css("inset-inline-start", `${Math.max(0, Math.min(anchor_start, room))}px`);
	}

	/**
	 * Close it, and put the keyboard back where it came from — but only when the
	 * keyboard is still in the popover. A click on some other control has already
	 * chosen where focus goes.
	 */
	close_pop() {
		if (!this.$pop) return;
		const $anchor = this.$pop_anchor;
		const inside =
			$anchor &&
			this.pop_owner &&
			(document.activeElement === document.body ||
				$(document.activeElement).closest(".agent-chat-pop").length > 0);
		this.pop_owner = null;
		this.$pop_anchor = null;
		this.$pop.removeClass("is-open").removeAttr("aria-label").empty();
		if ($anchor) $anchor.attr("aria-expanded", "false");
		if (inside) $anchor.focus();
	}

	pop_label(text) {
		// The popover's own name, so it is not an unnamed group when it takes focus.
		this.$pop.attr("aria-label", text);
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

	/** A popover row you can pick, with the keyboard as well as the mouse. */
	clickable($opt, choose) {
		return $opt
			.addClass("is-clickable")
			.attr({ role: "button", tabindex: 0 })
			.on("click", choose)
			.on("keydown", (e) => {
				if (e.key !== "Enter" && e.key !== " ") return;
				e.preventDefault();
				choose();
			});
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
		this.$attach
			.prop("disabled", !this.agent)
			.attr({ title: this.attach_hint(), "aria-label": this.attach_hint() });
		this.render_files();
	}

	/** What the paperclip promises, which depends on where the file will land. */
	attach_hint() {
		return this.on_document()
			? __("Attach a file — stored on this document")
			: __("Attach a file — stored on this conversation");
	}

	/** A chat opened from a form is about a document, and that document is the anchor. */
	on_document() {
		return Boolean(this.context_doctype && this.context_name);
	}

	/**
	 * Upload a file the agent can then be asked to read.
	 *
	 * Frappe's own uploader does the uploading; all this decides is what the File
	 * hangs off, because that is what a reviewer checks it by. The open document
	 * when there is one — it is the better anchor, and the file belongs to it. A
	 * plain chat has no document, so the conversation is the record: it says who
	 * supplied the file, to which agent, and what was being talked about.
	 */
	attach() {
		if (!this.agent) {
			frappe.show_alert({ message: __("Select an agent first"), indicator: "orange" });
			return;
		}
		this.upload_anchor()
			.then((anchor) => this.open_uploader(anchor))
			.catch((error) => console.error("frappe_agents: could not open an upload", error));
	}

	/** The record the upload attaches to, opening a conversation first if there is none. */
	upload_anchor() {
		if (this.on_document()) {
			return Promise.resolve({ doctype: this.context_doctype, name: this.context_name });
		}
		if (this.conversation) {
			return Promise.resolve({ doctype: "Agent Conversation", name: this.conversation });
		}

		const args = { agent: this.agent };
		if (this.model_profile) args.model_profile = this.model_profile;
		return frappe.xcall("frappe_agents.api.start_conversation", args).then((r) => {
			this.conversation = r.conversation;
			// Freshly created, exactly as the first message creates one: the page
			// lists it and the form panel remembers it.
			if (this.on_conversation) this.on_conversation(this.conversation, null);
			return { doctype: "Agent Conversation", name: this.conversation };
		});
	}

	open_uploader(anchor) {
		// Desk loads the uploader asynchronously, so ask for it before using it.
		// require() is idempotent and comes straight back when it is already there.
		return frappe.require("file_uploader.bundle.js", () => this.make_uploader(anchor));
	}

	make_uploader(anchor) {
		return new frappe.ui.FileUploader({
			doctype: anchor.doctype,
			docname: anchor.name,
			folder: "Home/Attachments",
			allow_multiple: true,
			// Private, and not a checkbox: a file handed to an agent is evidence a
			// reviewer opens from the record it hangs off, not a public asset.
			make_attachments_public: false,
			allow_toggle_private: false,
			// A file already in the system is named in the message by its link or
			// its filename. The paperclip is for putting a new one in.
			disable_file_browser: true,
			on_success: (file_doc) => this.add_upload(file_doc),
		});
	}

	add_upload(file_doc) {
		if (!file_doc || !file_doc.name) return;
		if (this.uploads.some((file) => file.name === file_doc.name)) return;
		this.uploads.push({ name: file_doc.name, file_name: file_doc.file_name || file_doc.name });
		this.render_files();
	}

	/** One chip per file waiting to be named in the next message. */
	render_files() {
		if (!this.$files) return;
		this.$files.empty().toggleClass("is-filled", this.uploads.length > 0);
		this.uploads.forEach((file) => {
			const $chip = $("<span class='agent-chat-file'></span>").appendTo(this.$files);
			$("<span class='agent-chat-file-name'></span>")
				.text(file.file_name)
				.attr("title", file.file_name)
				.appendTo($chip);
			$("<button type='button' class='agent-chat-file-drop'>×</button>")
				.attr("title", __("Leave this file out of the message. It stays where it was uploaded."))
				.on("click", () => this.drop_upload(file.name))
				.appendTo($chip);
		});
	}

	/** Take a file out of the next message. The upload itself is a record and stays. */
	drop_upload(name) {
		this.uploads = this.uploads.filter((file) => file.name !== name);
		this.render_files();
	}

	/**
	 * The line the message carries so the model knows what it was handed.
	 *
	 * Plain text on the message the person sent, not a channel of its own: a run
	 * stores one input_message, and that is what a reload draws the bubble from.
	 * The File name is in it because that is what a tool that reads files takes.
	 */
	files_line() {
		if (!this.uploads.length) return "";
		const list = this.uploads.map((file) => `${file.file_name} (File: ${file.name})`).join(", ");
		return __("Attached files: {0}", [list]);
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
			this.replay_buffer();
		}

		if (this.on_conversation) this.on_conversation(this.conversation, data);
		this.scroll_to_bottom();
	}

	render_past_run(run) {
		if (run.input_message) {
			this.$log.append(this.make_bubble(run.input_message, "is-user"));
		}

		// The log is the transcript: every tool call, every thinking block and
		// every message the agent wrote, in the order they happened. A run from
		// before the log was stored has only its final message to show.
		const events = Array.isArray(run.event_log) ? run.event_log : [];
		const said = events.length ? this.replay_run_events(run.name, events) : "";

		if (run.output_message) {
			// Already on screen when the log carried it, which it does for every
			// run that recorded one.
			if (run.output_message !== said) {
				this.$log.append(this.make_bubble(run.output_message, ""));
			}
		} else if (run.error) {
			this.$log.append(this.make_bubble(run.error, "is-error"));
		} else if (["Queued", "Running"].includes(run.status)) {
			// Still in flight: keep a pending line so realtime updates land in place.
			const $pending = $("<div class='agent-chat-status'></div>")
				.text(`${__(run.status)}…`)
				.attr("data-run", run.name);
			this.$log.append($pending);
			this.pending[run.name] = $pending;
			this.update_busy();
		}

		this.replay_extractions(run);
	}

	/**
	 * The cards for the documents this run had read, in the state they are in now.
	 *
	 * Extraction is its own job on its own record: it finishes after the run that
	 * asked for it, so nothing in the run's log says how it ended and the card
	 * used to live in this browser and nowhere else. The server answers that
	 * question instead, and it answers it fresh — a card redrawn here says what
	 * the extraction says today, not what it said when the run stopped.
	 *
	 * Last, so a card sits under the answer the way it does when it arrives live.
	 */
	replay_extractions(run) {
		(run.extractions || []).forEach((extraction) => {
			if (!extraction || !extraction.name) return;
			this.render_extraction_update(extraction.name, {
				run: run.name,
				status: extraction.status,
				target_doctype: extraction.target_doctype,
				created_doc: extraction.created_doc,
			});
		});
	}

	/**
	 * Redraw a run from what it recorded.
	 *
	 * A reopened conversation used to show the questions and the answers and
	 * nothing in between: the tool lines, the thinking and the proposal cards
	 * were published while the run was going and never drawn again. All of it is
	 * in the run's event log. Returns the last thing the agent said, which is how
	 * the caller knows the stored answer is already on screen.
	 */
	replay_run_events(run, events) {
		// The reason the agent gave is an argument of the call, not part of its
		// result, so it is picked up when the call starts.
		const reasons = {};
		let said = "";

		events.forEach((event) => {
			if (!event || !event.type) return;
			if (event.type === "tool_execution_start") {
				reasons[event.toolCallId] = (event.args || {}).reason;
				this.render_tool_started(run, event);
				return;
			}
			if (event.type === "message_end") {
				said = this.replay_message(run, event.message) || said;
				return;
			}
			if (event.type !== "tool_execution_end") return;

			this.finish_tool_line(this.tool_lines[tool_line_key(run, event.toolCallId)]);
			this.record_tool_result(run, event);

			const proposal = proposal_from_event(event);
			if (!proposal) return;
			this.render_action_proposed({
				run: run,
				action: proposal.action,
				action_type: proposal.action_type,
				target_doctype: proposal.target_doctype,
				target_name: proposal.target_name,
				reason: reasons[event.toolCallId] || "",
			});
		});
		return said;
	}

	/**
	 * One stored message: what the model thought, then what it said.
	 *
	 * Only the agent's own messages. The question is drawn from the run's input
	 * and a tool result is drawn as the tool line it belongs to.
	 */
	replay_message(run, message) {
		const blocks = message_blocks(message);
		if (!blocks.length) return "";

		blocks.forEach((block) => {
			if (!block || block.type !== "thinking" || !block.thinking) return;
			this.clear_empty();
			this.insert_before_pending(run, this.make_thinking_strip(block.thinking, false).$el);
		});

		const text = message_text(message);
		if (text) {
			this.clear_empty();
			this.insert_before_pending(run, this.make_bubble(text, ""));
		}
		return text;
	}

	/**
	 * Put back what arrived while this conversation was off screen.
	 *
	 * Only for the runs still going: a run that ended has been drawn already,
	 * from its log or from its own row, and drawing it again would double every
	 * line of it. Extraction progress is replayed whatever the run did — it is
	 * not part of any log, so the buffer is the only place a card that was
	 * published earlier can come back from.
	 */
	replay_buffer() {
		if (!this.conversation) return;
		const live = new Set(Object.keys(this.pending));
		frappe_agents.run_buffer.events(this.conversation).forEach((entry) => {
			if (entry.channel === "extraction") {
				this.on_extraction_update(entry.data);
				return;
			}
			if (!entry.data || !live.has(entry.data.run)) return;
			this.apply_run_update(entry.data);
		});
	}

	/**
	 * Say what just happened, once, to anyone not watching the log.
	 *
	 * Outcomes only — the answer streams in several words a second, and a live
	 * region reading every delta is unusable. "The agent replied" is the news;
	 * the words themselves are in the transcript to read at leisure.
	 */
	announce(text) {
		if (!this.$sr || !text) return;
		this.$sr.text(text);
	}

	/** The log is busy for as long as a run on it has not landed. */
	update_busy(starting) {
		if (!this.$log) return;
		const busy = Boolean(starting) || Object.keys(this.pending).length > 0;
		this.$log.attr("aria-busy", busy ? "true" : "false");
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
		const typed = (this.$input.val() || "").trim();
		// One message: what was typed, then what was attached. The bubble shows the
		// same text the run is stored with, so a reload draws exactly this again.
		const message = [typed, this.files_line()].filter(Boolean).join("\n\n");
		if (!this.agent) {
			frappe.show_alert({ message: __("Select an agent first"), indicator: "orange" });
			return;
		}
		if (!message) return;

		this.clear_empty();
		this.add_bubble(message, "is-user");
		this.$input.val("");
		this.uploads = [];
		this.render_files();
		this.set_busy(true);

		const $pending = this.add_status(__("Queued…"));
		this.update_busy(true);

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
					this.update_busy();
					return;
				}
				const is_new = this.conversation !== r.message.conversation;
				this.conversation = r.message.conversation;
				$pending.attr("data-run", r.message.run);
				this.pending[r.message.run] = $pending;
				this.update_busy();
				if (is_new && this.on_conversation) this.on_conversation(this.conversation, null);
			},
			error: () => {
				$pending.remove();
				this.update_busy();
			},
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

		this.apply_run_update(data);
		// Text arrives several times a second. Chasing it is right when the person
		// is at the bottom of the log and wrong when they are reading further up.
		if (data.type === "message_update") {
			this.follow();
		} else {
			this.scroll_to_bottom();
		}
	}

	/** Draw one run event. Replaying the buffer comes through here too. */
	apply_run_update(data) {
		switch (data.type) {
			case "harness_event":
				this.on_harness_event(data);
				break;
			case "message_update":
				this.render_stream_update(data);
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
	}

	render_status(data) {
		const status = data.status || data.value || "";
		const $pending = this.pending[data.run];
		if (["Completed", "Failed", "Cancelled"].includes(status)) {
			if ($pending) {
				if (status === "Completed") {
					$pending.remove();
				} else {
					const text = __("Run {0}", [__(status)]);
					$pending.text(text);
					this.announce(text);
				}
			}
			delete this.pending[data.run];
			this.update_busy();
			return;
		}
		if ($pending && status) {
			$pending.text(`${__(status)}…`);
			this.announce(__(status));
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
			this.record_tool_result(data.run, event);
		} else if (event.type === "message_end") {
			this.finish_stream_message(data.run, event.message);
		}
	}

	/** What a run is writing right now, and what it has written so far. */
	stream(run) {
		if (!this.streams[run]) {
			this.streams[run] = { text: "", $row: null, $bubble: null, thinking: {}, said: "" };
		}
		return this.streams[run];
	}

	/**
	 * A piece of the answer, or of the thinking, as the model writes it.
	 *
	 * Deltas are what the bubble grows from. They are never the last word: the
	 * message_end behind them carries the canonical text and replaces this.
	 */
	render_stream_update(data) {
		const state = this.stream(data.run);
		if (data.kind === "thinking") {
			this.render_thinking_update(data.run, state, data);
			return;
		}
		if (data.kind !== "text" || data.phase !== "delta" || !data.delta) return;
		state.text += data.delta;
		this.write_stream_text(data.run, state, state.text);
	}

	/** The agent's bubble for the message being written, made on the first word. */
	write_stream_text(run, state, text) {
		this.clear_empty();
		if (!state.$row) {
			state.$row = this.make_bubble("", "");
			state.$bubble = state.$row.find(".agent-chat-bubble");
			this.insert_before_pending(run, state.$row);
		}
		state.$bubble.text(text);
	}

	/**
	 * The model has finished a message.
	 *
	 * Its content is the canonical form, so it replaces whatever the deltas
	 * accumulated, and any thinking still open closes. A run can write several
	 * messages — one before each tool call — so the next one starts fresh.
	 */
	finish_stream_message(run, message) {
		if (!message || message.role !== "assistant") return;
		const state = this.stream(run);

		Object.keys(state.thinking).forEach((index) => this.close_thinking(state.thinking[index]));
		state.thinking = {};

		const text = message_text(message);
		if (text) {
			this.write_stream_text(run, state, text);
			state.said = text;
			this.announce(__("The agent replied."));
		} else if (state.$row) {
			// It ended up saying nothing — a turn that only called a tool.
			state.$row.remove();
		}
		state.text = "";
		state.$row = null;
		state.$bubble = null;
	}

	/** A thinking block opening, growing, or ending. */
	render_thinking_update(run, state, data) {
		const index = String(data.index || 0);
		if (data.phase === "end") {
			this.close_thinking(state.thinking[index]);
			return;
		}

		const strip = this.open_thinking(run, state, index);
		if (data.phase !== "delta" || !data.delta) return;
		strip.text += data.delta;
		strip.$body.text(strip.text);
		strip.$body.scrollTop(strip.$body[0].scrollHeight);
	}

	open_thinking(run, state, index) {
		if (state.thinking[index]) return state.thinking[index];
		this.clear_empty();
		const strip = this.make_thinking_strip("", true);
		this.insert_before_pending(run, strip.$el);
		state.thinking[index] = strip;
		return strip;
	}

	/**
	 * The strip that shows the model thinking.
	 *
	 * Open and streaming while it thinks; one line the moment it stops — "Thought
	 * for 4s" — and the whole of it a click away either way. A strip replayed
	 * from a log starts closed: the thinking is there to open, but the answer is
	 * what a person came back for.
	 */
	make_thinking_strip(text, running) {
		const strip = { text: text || "", running: Boolean(running), started: Date.now() };
		strip.$el = $("<div class='agent-chat-think'></div>").toggleClass("is-live", strip.running);
		strip.$head = $("<button type='button' class='agent-chat-think-head'></button>")
			.attr("aria-expanded", "false")
			.appendTo(strip.$el);
		$("<span class='agent-chat-think-caret'>▸</span>").appendTo(strip.$head);
		strip.$label = $("<span class='agent-chat-think-label'></span>")
			.text(strip.running ? __("Thinking…") : __("Thought"))
			.appendTo(strip.$head);
		strip.$body = $("<pre class='agent-chat-think-body'></pre>").text(strip.text).appendTo(strip.$el);
		strip.$head.on("click", () => this.toggle_thinking(strip));
		if (strip.running) this.toggle_thinking(strip);
		return strip;
	}

	toggle_thinking(strip) {
		const open = !strip.$el.hasClass("is-open");
		strip.$el.toggleClass("is-open", open);
		strip.$head.attr("aria-expanded", open ? "true" : "false");
	}

	/** A thinking block has ended: it folds itself away and says how long it took. */
	close_thinking(strip) {
		if (!strip || !strip.running) return;
		strip.running = false;
		const seconds = Math.max(1, Math.round((Date.now() - strip.started) / 1000));
		strip.$label.text(__("Thought for {0}s", [seconds]));
		strip.$el.removeClass("is-live");
		if (strip.$el.hasClass("is-open")) this.toggle_thinking(strip);
	}

	/** Draw a tool the agent has just started calling. It resolves in place when it returns. */
	render_tool_started(run, event) {
		const id = event.toolCallId;
		const key = tool_line_key(run, id);
		if (!id || this.tool_lines[key]) return;

		const line = this.make_tool_line(event.toolName || "tool", event.args || {}, true);
		this.insert_before_pending(run, line.$el);

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
		// A line with no started event behind it will get no result event either.
		// What this event says about the outcome is all there will ever be.
		if (!data.ok && data.error) this.write_tool_result(line, data.error, true);
		this.insert_before_pending(data.run, line.$el);
	}

	/**
	 * One tool line: the call, and behind a click the arguments it ran with and
	 * what it handed back.
	 *
	 * Both halves are the same data the run's event log already gives this
	 * person — the call ran under their own permissions — so opening a line
	 * discloses nothing new; it saves a trip to another doctype to find out what
	 * the agent actually did. The head is a button, so it opens from the keyboard
	 * like the composer chips and the thinking strip.
	 */
	make_tool_line(tool, args, running) {
		const pretty = pretty_args(args);
		const line = { tool: tool, args: args, done: !running };

		line.$el = $("<div class='agent-chat-tool'></div>");
		line.$head = $("<button type='button' class='agent-chat-tool-head'></button>")
			.attr("aria-expanded", "false")
			.appendTo(line.$el);
		$("<span class='agent-chat-tool-caret'>▸</span>").appendTo(line.$head);
		line.$text = $("<span></span>").text(tool_line_text(tool, pretty, running)).appendTo(line.$head);

		// Arguments on the left, result on the right — what went in beside what
		// came back. One column again on narrow screens.
		const $detail = $("<div class='agent-chat-tool-detail'></div>").appendTo(line.$el);
		const $args_col = $("<div class='agent-chat-tool-col'></div>").appendTo($detail);
		$("<div class='agent-chat-tool-label'></div>").text(__("Arguments")).appendTo($args_col);
		line.$args = $("<pre class='agent-chat-tool-block'></pre>").text(pretty).appendTo($args_col);
		const $result_col = $("<div class='agent-chat-tool-col'></div>").appendTo($detail);
		line.$result_label = $("<div class='agent-chat-tool-label'></div>")
			.text(__("Result"))
			.appendTo($result_col);
		line.$result = $("<pre class='agent-chat-tool-block is-waiting'></pre>")
			.text(running ? __("Still running…") : __("Nothing was recorded for this call."))
			.appendTo($result_col);

		line.$head.on("click", () => this.toggle_tool(line));
		return line;
	}

	toggle_tool(line) {
		const open = !line.$el.hasClass("is-open");
		line.$el.toggleClass("is-open", open);
		line.$head.attr("aria-expanded", open ? "true" : "false");
	}

	/**
	 * What a finished tool handed back, written onto the line that called it.
	 *
	 * Separate from finishing the line because the two arrive separately: the
	 * legacy tool_call event resolves the line and carries no result, so the
	 * result usually lands on a line that is already done. The text is exactly
	 * what was stored, truncation note and all — a result that was cut is a fact
	 * about the run, not something to hide.
	 */
	record_tool_result(run, event) {
		const line = this.tool_lines[tool_line_key(run, event.toolCallId)];
		if (!line) return;
		this.write_tool_result(line, result_text(event.result), Boolean(event.isError));
	}

	/** Fill in a line's result box. Empty text still says something happened. */
	write_tool_result(line, text, failed) {
		line.$result_label.text(failed ? __("Error") : __("Result"));
		line.$result
			.removeClass("is-waiting")
			.toggleClass("is-error", Boolean(failed))
			.text(text || __("It returned nothing."));
		this.announce(
			failed ? __("{0} failed.", [line.tool]) : __("{0} finished.", [line.tool])
		);
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
			line.$text.text(tool_line_text(line.tool, pretty, false));
			line.$args.text(pretty);
			return;
		}
		line.$text.text(tool_line_text(line.tool, pretty_args(line.args), false));
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

		if (data.created_doc) {
			// Named, not linked: the review form is the way into an extraction, and
			// it is one click away at the bottom of this card.
			$("<div class='agent-chat-action-line'></div>")
				.text(__("Draft: {0}", [data.created_doc]))
				.appendTo($card);
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
		const state = this.streams[data.run];
		// The run signs off with the answer it ended on. When that answer was
		// written into the bubble as it came, this is the same words a second
		// time and there is nothing left to draw.
		const streamed = Boolean(state && text && state.said === text);
		if (text && !streamed) {
			this.clear_empty();
			this.insert_before_pending(data.run, this.make_bubble(text, ""));
		}
		const $pending = this.pending[data.run];
		if ($pending) {
			$pending.remove();
			delete this.pending[data.run];
		}
		this.update_busy();
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
		this.announce(__("The run failed."));
		this.update_busy();
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

	/**
	 * Keep up with text as it is written, unless the person has scrolled away.
	 * Dragging the log back down under someone reading what was said earlier is
	 * the wrong answer to a message arriving.
	 */
	follow() {
		const log = this.$log[0];
		if (!log) return;
		if (log.scrollHeight - log.scrollTop - log.clientHeight <= FOLLOW_SLACK) {
			this.scroll_to_bottom();
		}
	}
};
