// Copyright (c) 2026, Malik AlZubaidi and contributors
// For license information, please see LICENSE

frappe.query_reports["Agent Action Review Quality"] = {
	filters: [
		{
			fieldname: "agent",
			label: __("Agent"),
			fieldtype: "Link",
			options: "Agent",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
	],
};
