frappe.listview_settings["Agent Rehearsal"] = {
	add_fields: ["last_verdict"],
	get_indicator(doc) {
		const colours = { Pass: "green", Fail: "red", Inconclusive: "orange" };
		if (!doc.last_verdict) return [__("Not run"), "gray", "last_verdict,is,not set"];
		return [__(doc.last_verdict), colours[doc.last_verdict], "last_verdict,=," + doc.last_verdict];
	},
};
