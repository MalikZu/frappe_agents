// List settings for Agent Action.
//
// They live in the desk bundle rather than in an agent_action_list.js beside the
// doctype because the list only has to know two things — what each status looks
// like and that Pending is the view worth opening on — and the bundle is loaded
// on every desk page already.
frappe.provide("frappe.listview_settings");

const INDICATORS = {
	Pending: "orange",
	Approved: "blue",
	Applied: "green",
	Rejected: "gray",
	Failed: "red",
	Expired: "gray",
};

frappe.listview_settings["Agent Action"] = {
	add_fields: ["status", "action_type", "target_doctype", "target_name"],

	// The queue is the reason to open this list; everything else is history.
	filters: [["status", "=", "Pending"]],

	get_indicator(doc) {
		const colour = INDICATORS[doc.status] || "gray";
		return [__(doc.status), colour, `status,=,${doc.status}`];
	},
};
