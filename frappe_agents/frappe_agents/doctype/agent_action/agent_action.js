// The approval surface. An Agent Action is a proposal about another document,
// so the form is worth nothing without that document drawn next to it: the
// reviewer approves what they can see, field by field.
//
// Approve sends the `modified` timestamp of exactly what was drawn. The server
// refuses if the target moved on in the meantime, and the reviewer gets the
// document back with the changed fields called out instead of a silent apply.
frappe.provide("frappe_agents");

const APPROVE_METHOD = "frappe_agents.actions.approve_action";
const REJECT_METHOD = "frappe_agents.actions.reject_action";

const STYLE_ID = "frappe-agents-action-styles";
const VALUE_CHARS = 500;
const CHILD_ROWS = 10;

// Layout and no-value fieldtypes. Table is handled separately, everything else
// here has nothing to show a reviewer.
const SKIP_TYPES = [
	"Section Break",
	"Column Break",
	"Tab Break",
	"HTML",
	"Button",
	"Image",
	"Fold",
	"Heading",
	"Password",
	"Signature",
	"Barcode",
	"Geolocation",
];

const FORMATTED_TYPES = [
	"Currency",
	"Float",
	"Int",
	"Percent",
	"Date",
	"Datetime",
	"Time",
	"Duration",
	"Rating",
];

const RICH_TEXT_TYPES = ["Text Editor", "HTML Editor", "Markdown Editor", "Code"];

const STYLES = `
	.agent-action-target { font-size: var(--text-md); }
	.agent-action-head { display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: baseline; margin-bottom: 8px; }
	.agent-action-head .agent-action-title { font-weight: 600; }
	.agent-action-note { font-size: var(--text-sm); color: var(--text-muted); margin-bottom: 8px; }
	.agent-action-note.is-warning { color: var(--text-on-orange, var(--text-color)); background: var(--bg-orange, transparent); border-radius: 6px; padding: 6px 8px; }
	.agent-action-note.is-danger { color: var(--text-on-red, var(--text-color)); background: var(--bg-red, transparent); border-radius: 6px; padding: 6px 8px; }
	.agent-action-fields { width: 100%; table-layout: fixed; }
	.agent-action-fields td { padding: 4px 8px; border-bottom: 1px solid var(--border-color); vertical-align: top; word-break: break-word; }
	.agent-action-fields td.agent-action-label { width: 34%; color: var(--text-muted); }
	.agent-action-fields tr.is-changed td { background: var(--bg-yellow, var(--control-bg)); }
	.agent-action-child { width: 100%; margin-top: 4px; font-size: var(--text-sm); }
	.agent-action-child th, .agent-action-child td { padding: 2px 6px; border-bottom: 1px solid var(--border-color); text-align: left; }
	.agent-action-child th { color: var(--text-muted); font-weight: normal; }
	.agent-action-more { color: var(--text-muted); font-size: var(--text-sm); padding-top: 4px; }
`;

function add_styles() {
	if (document.getElementById(STYLE_ID)) return;
	$(`<style id="${STYLE_ID}">${STYLES}</style>`).appendTo(document.head);
}

function with_doctype(doctype) {
	// with_doctype fires the callback straight away when the meta is cached.
	return new Promise((resolve) =>
		frappe.model.with_doctype(doctype, () => resolve(frappe.get_meta(doctype) || locals.DocType[doctype]))
	);
}

function doc_url(doctype, name) {
	return `/app/${frappe.router.slug(doctype)}/${encodeURIComponent(name)}`;
}

function docstatus_label(docstatus) {
	if (cint(docstatus) === 1) return __("Submitted");
	if (cint(docstatus) === 2) return __("Cancelled");
	return __("Draft");
}

/** Empty for display purposes. A zero is a value; an empty string is not. */
function is_blank(value) {
	return value === null || value === undefined || value === "";
}

function truncate(text) {
	text = String(text);
	return text.length > VALUE_CHARS ? text.slice(0, VALUE_CHARS) + "…" : text;
}

/**
 * A field's value as plain text. Everything is rendered through .text() rather
 * than frappe.format's HTML, because these values come from a document an agent
 * just wrote into.
 */
function value_text(doc, df) {
	const value = doc[df.fieldname];
	if (is_blank(value)) return "";
	if (df.fieldtype === "Check") return cint(value) ? __("Yes") : __("No");
	if (RICH_TEXT_TYPES.includes(df.fieldtype)) return truncate(frappe.utils.strip_html(String(value)));
	if (FORMATTED_TYPES.includes(df.fieldtype)) {
		try {
			return truncate(frappe.format(value, df, { only_value: true }, doc));
		} catch (error) {
			return truncate(value);
		}
	}
	return truncate(value);
}

function link_target(doc, df) {
	if (df.fieldtype === "Link") return df.options;
	if (df.fieldtype === "Dynamic Link") return doc[df.options];
	return null;
}

function $value_cell(doc, df) {
	const $cell = $("<td></td>");
	const value = doc[df.fieldname];

	if (df.fieldtype === "Table") return $cell;

	const target = link_target(doc, df);
	if (target && !is_blank(value)) {
		$("<a></a>")
			.attr("href", doc_url(target, value))
			.text(String(value))
			.appendTo($cell);
		return $cell;
	}

	return $cell.text(value_text(doc, df));
}

function child_columns(child_meta) {
	const fields = visible_fields(child_meta).filter((df) => df.fieldtype !== "Table");
	const listed = fields.filter((df) => df.in_list_view);
	return listed.length ? listed.slice(0, 5) : fields.slice(0, 4);
}

function $child_table(rows, child_meta) {
	const columns = child_columns(child_meta);
	const $table = $("<table class='agent-action-child'></table>");
	const $head = $("<tr></tr>").appendTo($("<thead></thead>").appendTo($table));
	columns.forEach((df) => $("<th></th>").text(__(df.label || df.fieldname)).appendTo($head));

	const $body = $("<tbody></tbody>").appendTo($table);
	rows.slice(0, CHILD_ROWS).forEach((row) => {
		const $row = $("<tr></tr>").appendTo($body);
		columns.forEach((df) => $row.append($value_cell(row, df)));
	});

	const $wrapper = $("<div></div>").append($table);
	if (rows.length > CHILD_ROWS) {
		$("<div class='agent-action-more'></div>")
			.text(__("and {0} more rows", [rows.length - CHILD_ROWS]))
			.appendTo($wrapper);
	}
	return $wrapper;
}

/** Fields worth showing: has a value, is not layout, is not hidden from the user. */
function visible_fields(meta) {
	return ((meta && meta.fields) || []).filter((df) => {
		if (SKIP_TYPES.includes(df.fieldtype)) return false;
		if (df.hidden) return false;
		return true;
	});
}

function $field_table(doc, meta, child_metas, changed) {
	const $table = $("<table class='agent-action-fields'></table>");
	const $body = $("<tbody></tbody>").appendTo($table);
	let drawn = 0;

	visible_fields(meta).forEach((df) => {
		const value = doc[df.fieldname];

		if (df.fieldtype === "Table") {
			const rows = value || [];
			if (!rows.length) return;
			const child_meta = child_metas[df.options];
			if (!child_meta) return;
			const $row = $("<tr></tr>").appendTo($body);
			$("<td class='agent-action-label'></td>").text(__(df.label || df.fieldname)).appendTo($row);
			$("<td></td>").append($child_table(rows, child_meta)).appendTo($row);
			if (changed.has(df.fieldname)) $row.addClass("is-changed");
			drawn += 1;
			return;
		}

		if (is_blank(value)) return;

		const $row = $("<tr></tr>").appendTo($body);
		$("<td class='agent-action-label'></td>").text(__(df.label || df.fieldname)).appendTo($row);
		$row.append($value_cell(doc, df));
		if (changed.has(df.fieldname)) $row.addClass("is-changed");
		drawn += 1;
	});

	if (!drawn) {
		const $row = $("<tr></tr>").appendTo($body);
		$("<td colspan='2'></td>").text(__("This document has no values to show.")).appendTo($row);
	}
	return $table;
}

/** Datetime strings compared at second resolution — good enough to say "edited". */
function same_moment(a, b) {
	if (!a || !b) return !a && !b;
	return String(a).slice(0, 19) === String(b).slice(0, 19);
}

function changed_fields(before, after) {
	const changed = new Set();
	if (!before || !after) return changed;
	Object.keys(after).forEach((key) => {
		if (["modified", "modified_by", "docstatus"].includes(key)) return;
		const was = before[key];
		const now = after[key];
		if (typeof now === "object" || typeof was === "object") {
			if (JSON.stringify(was || null) !== JSON.stringify(now || null)) changed.add(key);
			return;
		}
		if ((was === undefined ? "" : was) !== (now === undefined ? "" : now)) changed.add(key);
	});
	return changed;
}

function changed_labels(meta, changed) {
	const labels = [];
	changed.forEach((fieldname) => {
		const df = (meta.fields || []).find((row) => row.fieldname === fieldname);
		labels.push(df ? __(df.label || df.fieldname) : fieldname);
	});
	return labels;
}

// -- rendering -------------------------------------------------------------

/** Where the target preview lives. Lane A may add an HTML field; the dashboard
 *  is the fallback, and both are wiped by the form's own refresh. */
function mount(frm) {
	const state = frm.__fa;
	if (state.$mount) return state.$mount;

	const $el = $("<div class='agent-action-target'></div>");
	const field = frm.fields_dict && frm.fields_dict.target_preview;
	if (field && field.$wrapper) {
		field.$wrapper.empty().append($el);
	} else if (frm.dashboard && frm.dashboard.add_section) {
		frm.dashboard.add_section($el, __("Proposed change"));
	} else {
		frm.layout.wrapper.prepend($el);
	}
	state.$mount = $el;
	return $el;
}

function render_message(frm, text, level) {
	mount(frm)
		.empty()
		.append($(`<div class='agent-action-note is-${level || "muted"}'></div>`).text(text));
}

function render_target(frm) {
	const state = frm.__fa;
	const doc = state.target;
	const meta = state.meta;
	if (!doc || !meta) return;

	const $body = mount(frm).empty();
	const $head = $("<div class='agent-action-head'></div>").appendTo($body);

	$("<a class='agent-action-title'></a>")
		.attr("href", doc_url(frm.doc.target_doctype, frm.doc.target_name))
		.text(`${__(frm.doc.target_doctype)}: ${frm.doc.target_name}`)
		.appendTo($head);
	$("<span class='text-muted'></span>").text(docstatus_label(doc.docstatus)).appendTo($head);
	if (doc.modified) {
		$("<span class='text-muted'></span>")
			.text(__("Last edited {0} by {1}", [frappe.datetime.str_to_user(doc.modified), doc.modified_by || ""]))
			.appendTo($head);
	}

	if (state.stale) {
		const labels = changed_labels(meta, state.changed);
		const detail = labels.length ? labels.join(", ") : __("some fields");
		$("<div class='agent-action-note is-danger'></div>")
			.text(__("This document changed since you looked. Changed: {0}. Read it again before approving.", [detail]))
			.appendTo($body);
	} else if (!same_moment(doc.modified, frm.doc.proposal_modified)) {
		$("<div class='agent-action-note is-warning'></div>")
			.text(__("This document was edited after the agent proposed the action."))
			.appendTo($body);
	}

	if (frm.doc.action_type === "Cancel") {
		$("<div class='agent-action-note'></div>")
			.text(__("Approving cancels this submitted document. Its current values are below."))
			.appendTo($body);
	} else {
		$("<div class='agent-action-note'></div>")
			.text(__("Approving submits this draft under your own permissions."))
			.appendTo($body);
	}

	$body.append($field_table(doc, meta, state.child_metas || {}, state.changed || new Set()));
}

/** Fetch the target through the permission-checked endpoint, then its metas. */
function fetch_target(frm) {
	return frappe
		.xcall("frappe.client.get", { doctype: frm.doc.target_doctype, name: frm.doc.target_name })
		.then((doc) =>
			with_doctype(frm.doc.target_doctype).then((meta) => {
				const child_doctypes = visible_fields(meta)
					.filter((df) => df.fieldtype === "Table" && (doc[df.fieldname] || []).length)
					.map((df) => df.options);
				const unique = Array.from(new Set(child_doctypes));
				return Promise.all(unique.map((dt) => with_doctype(dt))).then((metas) => {
					const child_metas = {};
					unique.forEach((dt, index) => (child_metas[dt] = metas[index]));
					return { doc: doc, meta: meta, child_metas: child_metas };
				});
			})
		);
}

function load_target(frm) {
	const state = frm.__fa;
	const token = state.token;

	if (!frm.doc.target_doctype || !frm.doc.target_name) {
		render_message(frm, __("This action names no target document."), "danger");
		return;
	}

	render_message(frm, __("Loading {0} {1}…", [__(frm.doc.target_doctype), frm.doc.target_name]));

	fetch_target(frm)
		.then((loaded) => {
			if (state.token !== token) return;
			state.target = loaded.doc;
			state.meta = loaded.meta;
			state.child_metas = loaded.child_metas;
			render_target(frm);
			set_actions(frm);
		})
		.catch(() => {
			if (state.token !== token) return;
			state.target = null;
			render_message(
				frm,
				__("You cannot read {0} {1}, so there is nothing to approve here.", [
					__(frm.doc.target_doctype),
					frm.doc.target_name,
				]),
				"danger"
			);
		});
}

// -- decisions -------------------------------------------------------------

function may_decide(frm) {
	if (frm.doc.status !== "Pending") return false;
	if (!frappe.user.has_role("Agent Approver")) return false;
	// Separation of duties is enforced on the server; the buttons just stop the
	// obvious cases from ever being clicked.
	if (frm.doc.requested_by === frappe.session.user) return false;
	return true;
}

function decision_summary(frm) {
	const verb = frm.doc.action_type === "Cancel" ? __("Cancel") : __("Submit");
	return __("{0} {1} {2} as {3}.", [
		verb,
		__(frm.doc.target_doctype),
		frm.doc.target_name,
		frappe.session.user,
	]);
}

function approve(frm) {
	const state = frm.__fa;
	if (!state.target) {
		frappe.msgprint({
			title: __("Nothing to approve"),
			message: __("The target document could not be read, so there is no version to approve."),
			indicator: "red",
		});
		return;
	}

	const expected_modified = state.target.modified;
	const dialog = new frappe.ui.Dialog({
		title: __("Approve this action"),
		fields: [
			{ fieldtype: "HTML", fieldname: "summary" },
			{ fieldtype: "Small Text", fieldname: "note", label: __("Note (optional)") },
		],
		primary_action_label: __("Approve"),
		primary_action: (values) => {
			dialog.hide();
			send_approval(frm, expected_modified, values.note);
		},
	});
	dialog.fields_dict.summary.$wrapper.append($("<p></p>").text(decision_summary(frm)));
	dialog.show();
}

function send_approval(frm, expected_modified, note) {
	// The note is optional, and an omitted argument is the only way to say so:
	// a null in args reaches the server as an empty string.
	const args = { action: frm.doc.name, expected_modified: expected_modified };
	if (note) args.note = note;

	frappe.call({
		method: APPROVE_METHOD,
		args: args,
		freeze: true,
		freeze_message: __("Applying the action…"),
		callback: () => {
			frappe.show_alert({ message: __("Action applied"), indicator: "green" });
			frm.reload_doc();
		},
		error: () => reload_after_refusal(frm, expected_modified),
	});
}

/**
 * The server refused. If the target moved on, that is the "changed since you
 * looked" case: pull it again and redraw with the differences marked, so the
 * next click is a decision about the document as it is now.
 */
function reload_after_refusal(frm, expected_modified) {
	const state = frm.__fa;
	const before = state.target;

	fetch_target(frm)
		.then((loaded) => {
			state.target = loaded.doc;
			state.meta = loaded.meta;
			state.child_metas = loaded.child_metas;
			state.stale = loaded.doc.modified !== expected_modified;
			state.changed = state.stale ? changed_fields(before, loaded.doc) : new Set();
			render_target(frm);
			if (state.stale) {
				frappe.show_alert({
					message: __("The document changed since you looked — it has been reloaded."),
					indicator: "orange",
				});
			}
		})
		.catch(() => frm.reload_doc());
}

function reject(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Reject this action"),
		fields: [
			{ fieldtype: "HTML", fieldname: "summary" },
			{
				fieldtype: "Small Text",
				fieldname: "note",
				label: __("Why are you rejecting it?"),
				reqd: 1,
			},
		],
		primary_action_label: __("Reject"),
		primary_action: (values) => {
			dialog.hide();
			frappe.call({
				method: REJECT_METHOD,
				args: { action: frm.doc.name, note: values.note },
				freeze: true,
				freeze_message: __("Recording the rejection…"),
				callback: () => {
					frappe.show_alert({ message: __("Action rejected"), indicator: "orange" });
					frm.reload_doc();
				},
			});
		},
	});
	dialog.fields_dict.summary.$wrapper.append($("<p></p>").text(decision_summary(frm)));
	dialog.show();
}

function set_actions(frm) {
	if (!may_decide(frm)) return;
	frm.page.set_primary_action(__("Approve"), () => approve(frm));
	frm.add_custom_button(__("Reject"), () => reject(frm));
}

function set_intro(frm) {
	frm.set_intro("");

	if (frm.doc.status === "Pending") {
		if (frm.doc.requested_by === frappe.session.user) {
			frm.set_intro(__("You asked for this action. Someone else has to approve it."), "orange");
		} else if (!frappe.user.has_role("Agent Approver")) {
			frm.set_intro(__("Only an Agent Approver can decide this action."), "blue");
		}
		return;
	}

	if (frm.doc.status === "Failed" && frm.doc.failure) {
		frm.set_intro(frm.doc.failure, "red");
		return;
	}
	if (frm.doc.decided_by) {
		frm.set_intro(
			__("{0} by {1} on {2}.", [
				__(frm.doc.status),
				frm.doc.decided_by,
				frappe.datetime.str_to_user(frm.doc.decided_at),
			]),
			frm.doc.status === "Rejected" ? "orange" : "green"
		);
	}
}

frappe.ui.form.on("Agent Action", {
	onload(frm) {
		add_styles();
	},

	refresh(frm) {
		// Rows move only through the whitelisted methods, so the form never saves.
		frm.disable_save();

		// The form's own refresh has just wiped the dashboard and the custom
		// buttons; the token makes an in-flight load from the previous refresh
		// drop its result instead of drawing into the old node.
		const previous = frm.__fa || {};
		frm.__fa = {
			token: (previous.token || 0) + 1,
			$mount: null,
			target: null,
			meta: null,
			child_metas: {},
			stale: false,
			changed: new Set(),
		};

		set_intro(frm);
		load_target(frm);

		if (frm.doc.applied_doc && frm.doc.target_doctype) {
			frm.add_custom_button(__("Open Document"), () =>
				frappe.set_route("Form", frm.doc.target_doctype, frm.doc.applied_doc)
			);
		}
		if (frm.doc.run) {
			frm.add_custom_button(__("View Run"), () => frappe.set_route("Form", "Agent Run", frm.doc.run));
		}
	},
});
