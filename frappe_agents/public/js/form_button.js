// "Ask Agent" on every eligible form.
//
// The wildcard doctype is the only every-form hook in v16, and custom buttons are
// wiped immediately before each refresh, so the button has to be added here and
// nowhere else. A throw would abort the rest of the form's refresh chain, hence
// the try/catch.
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

frappe.ui.form.on("*", {
	refresh(frm) {
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
	},
});
