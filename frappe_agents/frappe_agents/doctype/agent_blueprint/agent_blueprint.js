// The button that turns a proposal into an agent. It is the only thing on this
// form that creates anything, and a person presses it — the drafter of a
// blueprint never does.
frappe.ui.form.on("Agent Blueprint", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frm.doc.status === "Applied") {
			frm.set_intro(__("This blueprint was applied. The agent it created is switched off until someone enables it."), "green");
			if (frm.doc.created_agent) {
				frm.add_custom_button(__("Open Agent"), () =>
					frappe.set_route("Form", "Agent", frm.doc.created_agent)
				);
			}
			return;
		}

		frm.set_intro(__("Nothing here grants anybody access. Create Agent makes a disabled agent you then review, give a model, and enable."), "blue");

		if (!frappe.user.has_role("Agent Manager")) {
			return;
		}

		frm.page.set_primary_action(__("Create Agent"), () => create_agent(frm));
	},
});

function create_agent(frm) {
	frappe.confirm(
		__("Create the agent {0} from this blueprint? It will be created switched off.", [frm.doc.title]),
		() =>
			frm
				.call({
					doc: frm.doc,
					method: "create_agent",
					freeze: true,
					freeze_message: __("Creating the agent…"),
				})
				.then((response) => {
					const created = response && response.message;
					if (!created) return;
					frappe.show_alert({ message: __("Agent {0} created", [created.agent]), indicator: "green" });
					frm.reload_doc();
				})
	);
}
