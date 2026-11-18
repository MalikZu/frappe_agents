// Run a rehearsal from its own form. The run happens in the background; the
// button opens the result, which fills in when the run is scored.
(function () {
	const START = "frappe_agents.rehearsals.runner.start_rehearsal";
	const RESULT = "Agent Rehearsal Result";

	function start(frm, model_profile) {
		frappe
			.call({
				method: START,
				args: { rehearsal: frm.doc.name, model_profile: model_profile || null },
				freeze: true,
				freeze_message: __("Queueing the rehearsal…"),
			})
			.then((r) => {
				frappe.show_alert({ message: __("Rehearsal queued"), indicator: "blue" });
				frappe.set_route("Form", RESULT, r.message.result);
			});
	}

	function pick_model(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Run on another model"),
			fields: [
				{
					fieldname: "model_profile",
					fieldtype: "Link",
					options: "LLM Model Profile",
					label: __("Model Profile"),
					reqd: 1,
					description: __("The agent must list it as an alternate profile."),
				},
			],
			primary_action_label: __("Run"),
			primary_action(values) {
				dialog.hide();
				start(frm, values.model_profile);
			},
		});
		dialog.show();
	}

	frappe.ui.form.on("Agent Rehearsal", {
		refresh(frm) {
			if (frm.is_new()) return;
			frm.add_custom_button(__("Run rehearsal"), () => start(frm)).addClass("btn-primary");
			frm.add_custom_button(__("Run on another model"), () => pick_model(frm), __("Run"));
			frm.add_custom_button(
				__("Results"),
				() => frappe.set_route("List", RESULT, { rehearsal: frm.doc.name }),
				__("View")
			);
			if (frm.doc.last_result) {
				frm.add_custom_button(
					__("Last result"),
					() => frappe.set_route("Form", RESULT, frm.doc.last_result),
					__("View")
				);
			}
		},
	});
})();
