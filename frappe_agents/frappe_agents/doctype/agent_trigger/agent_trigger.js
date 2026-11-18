// Copyright (c) 2026, Malik AlZubaidi and contributors
// For license information, please see LICENSE

frappe.ui.form.on("Agent Trigger", {
	refresh(frm) {
		render_activity(frm);
		if (frm.is_new() || !frm.doc.enabled) {
			return;
		}
		frm.add_custom_button(__("Run on a Document"), () => run_on_document(frm));
	},
});

function render_activity(frm) {
	const skips = (frm.doc.__onload && frm.doc.__onload.skips_today) || {};
	const reasons = Object.keys(skips);
	const field = frm.get_field("activity");
	if (!field) {
		return;
	}
	if (!reasons.length) {
		field.$wrapper.html(
			`<p class="text-muted small">${__("No events were skipped today.")}</p>`
		);
		return;
	}
	const rows = reasons
		.map((reason) => `<tr><td>${frappe.utils.escape_html(__(reason))}</td><td>${cint(skips[reason])}</td></tr>`)
		.join("");
	field.$wrapper.html(
		`<p class="small">${__("Events skipped today")}</p>
		<table class="table table-sm table-bordered small"><tbody>${rows}</tbody></table>`
	);
}

function run_on_document(frm) {
	frappe.prompt(
		[
			{
				fieldname: "docname",
				fieldtype: "Link",
				label: __(frm.doc.document_type),
				options: frm.doc.document_type,
				reqd: 1,
			},
		],
		(values) => {
			frappe
				.call("frappe_agents.triggers.dispatch.run_now", {
					trigger: frm.doc.name,
					docname: values.docname,
				})
				.then(() => {
					frappe.show_alert({ message: __("Queued. The run appears under Agent Runs."), indicator: "green" });
				});
		},
		__("Run on a Document"),
		__("Run")
	);
}
