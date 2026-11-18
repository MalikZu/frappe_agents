frappe.listview_settings["Agent Rehearsal Result"] = {
	add_fields: ["verdict", "status"],
	get_indicator(doc) {
		const colours = { Pass: "green", Fail: "red", Inconclusive: "orange" };
		if (doc.verdict) return [__(doc.verdict), colours[doc.verdict], "verdict,=," + doc.verdict];
		return [__(doc.status), "blue", "status,=," + doc.status];
	},
};
