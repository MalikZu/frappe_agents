// A result fills in when its run is scored. While it waits, the form looks
// again every few seconds rather than asking the reader to reload.
(function () {
	const POLL_MS = 4000;
	const WAITING = ["Queued", "Running"];
	const COLOURS = { Pass: "green", Fail: "red", Inconclusive: "orange" };

	frappe.ui.form.on("Agent Rehearsal Result", {
		refresh(frm) {
			if (frm.doc.verdict) {
				frm.page.set_indicator(__(frm.doc.verdict), COLOURS[frm.doc.verdict] || "gray");
			} else {
				frm.page.set_indicator(__(frm.doc.status), "blue");
			}
			if (frm.doc.rehearsal) {
				frm.add_custom_button(__("Rehearsal"), () =>
					frappe.set_route("Form", "Agent Rehearsal", frm.doc.rehearsal)
				);
			}
			clearTimeout(frm.__fa_poll);
			if (WAITING.includes(frm.doc.status)) {
				const name = frm.doc.name;
				frm.__fa_poll = setTimeout(() => {
					// Only while this result is still the page on screen.
					const [view, doctype, docname] = frappe.get_route();
					if (view === "Form" && doctype === frm.doctype && docname === name) {
						frm.reload_doc();
					}
				}, POLL_MS);
			}
		},
	});
})();
