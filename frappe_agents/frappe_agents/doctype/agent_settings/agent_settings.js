frappe.ui.form.on("Agent Settings", {
	refresh(frm) {
		const warning = frm.doc.__onload && frm.doc.__onload.queue_warning;
		if (warning) {
			frm.set_intro(warning, "orange");
		}
	},
});
