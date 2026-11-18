// "Ask Agent" on every eligible form.
//
// Custom buttons are wiped immediately before each refresh, so the button has to
// be added from a refresh handler (registration is at the bottom of this file).
// A throw would abort the rest of the form's refresh chain, hence the try/catch.
frappe.provide("frappe_agents");

/** Agents the boot payload says may be opened from this doctype's form. */
frappe_agents.get_form_agents = function (doctype) {
	const boot = (frappe.boot && frappe.boot.frappe_agents) || {};
	const agents = boot.form_agents || [];
	return agents.filter((agent) => {
		const doctypes = agent.doctypes || [];
		return !doctypes.length || doctypes.includes(doctype);
	});
};

// Conversations are kept per form, per document, per agent, so reopening the
// panel on the same record resumes the thread instead of starting over.
function conversation_cache(frm) {
	if (!frm.frappe_agents_conversations) {
		frm.frappe_agents_conversations = {};
	}
	return frm.frappe_agents_conversations;
}

function cache_key(frm, agent) {
	return `${frm.doctype}::${frm.docname}::${agent}`;
}

frappe_agents.open_form_panel = function (frm, agents) {
	const cache = conversation_cache(frm);
	const first = agents[0].name;
	const fields = [];

	if (agents.length > 1) {
		fields.push({
			fieldtype: "Select",
			fieldname: "agent",
			label: __("Agent"),
			options: agents.map((agent) => ({
				value: agent.name,
				label: agent.agent_name || agent.name,
			})),
			default: first,
			change: () => switch_agent(),
		});
	}
	fields.push({ fieldtype: "HTML", fieldname: "chat" });

	const dialog = new frappe.ui.Dialog({
		title: __("Ask Agent"),
		size: "large",
		minimizable: true,
		fields: fields,
	});

	const chat = new frappe_agents.ChatUI({
		parent: dialog.fields_dict.chat.$wrapper,
		agent: first,
		conversation: cache[cache_key(frm, first)] || null,
		context_doctype: frm.doctype,
		context_name: frm.docname,
		compact: true,
		on_conversation: (conversation) => {
			if (chat.agent) cache[cache_key(frm, chat.agent)] = conversation;
		},
	});

	function switch_agent() {
		const chosen = dialog.get_value("agent");
		if (!chosen || chosen === chat.agent) return;
		chat.set_agent(chosen);
		const previous = cache[cache_key(frm, chosen)];
		if (previous) chat.load_conversation(previous);
	}

	// Without this the panel's realtime listener outlives the panel and every
	// reopen renders the same run one more time.
	dialog.on_hide = () => chat.destroy();

	dialog.show();
	if (agents.length === 1) chat.focus();
	return dialog;
};

function add_ask_agent_button(frm) {
	try {
		if (frm.is_new()) return;
		if (!frappe.user.has_role("Agent User")) return;

		const agents = frappe_agents.get_form_agents(frm.doctype);
		if (!agents.length) return;

		frm.add_custom_button(__("Ask Agent"), () => frappe_agents.open_form_panel(frm, agents));
	} catch (error) {
		// Never take the form's refresh chain down with us.
		console.error("frappe_agents: could not add the Ask Agent button", error);
	}
}

// Version-15 has no wildcard form handlers, and an agent with no doctypes listed
// shows on every form, so the doctypes cannot all be known up front. Instead,
// "form-refresh" (fired on both v15 and v16 just before the form's own refresh
// handlers run) adds this doctype's handler the first time one of its forms
// opens, and the same refresh then runs it after the doctype's own scripts.
//
// The handler goes straight onto the list. frappe.ui.form.on would also replace
// cur_frm.events.refresh, which doctype scripts call to rerun their own refresh.
$(document).on("form-refresh", (event, frm) => {
	try {
		if (!frm || !frm.doctype) return;
		if (!frappe_agents.get_form_agents(frm.doctype).length) return;

		const handlers = frappe.ui.form.get_event_handler_list(frm.doctype, "refresh");
		if (!handlers.includes(add_ask_agent_button)) handlers.push(add_ask_agent_button);
	} catch (error) {
		console.error("frappe_agents: could not register the Ask Agent button", error);
	}
});
