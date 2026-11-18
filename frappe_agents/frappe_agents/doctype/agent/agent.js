// The effective-access preview: what this agent's rules actually come to.
//
// The rules narrow, they never widen — effective access is the invoking user's
// own permissions ∩ the matrix — so a form that only lists the rules tells half
// the story. The server answers the other half per row: a rule the person
// reading the form could not use themselves is drawn struck through, because
// that is the honest answer to "why can't my agent see X".
//
// Everything is drawn with .text(). Doctype names, profile names and report
// names are all user-supplied strings, and a preview is no place to start
// trusting them.
//
// Wrapped in an IIFE: doctype scripts are evaluated in the global scope, so a
// bare top-level `const STYLE_ID` here would collide with the same name in
// another doctype's script.
(function () {
	const STYLE_ID = "frappe-agents-access-styles";
	const METHOD = "frappe_agents.api.effective_access";
	const FIELD = "effective_access_preview";
	// The manager roles the server answers for. Asking anyone else only earns a
	// permission error, so the panel does not ask.
	const MANAGER_ROLES = ["Agent Manager", "System Manager"];

	const STYLES = `
		.fa-a-panel { border: 1px solid var(--border-color); border-radius: var(--border-radius, 6px); background: var(--card-bg, var(--control-bg)); }
		.fa-a-head { display: flex; flex-wrap: wrap; gap: 4px 12px; justify-content: space-between; align-items: baseline; padding: 8px 12px; border-block-end: 1px solid var(--border-color); }
		.fa-a-title { font-weight: 600; }
		.fa-a-note { font-size: var(--text-sm); color: var(--text-muted); }
		.fa-a-body { padding-block: 4px; padding-inline: 12px; }
		.fa-a-banner { margin-block: 8px; padding-block: 6px; padding-inline: 10px; border-radius: var(--border-radius, 6px); background: var(--bg-yellow, var(--control-bg)); color: var(--text-on-yellow, var(--text-color)); font-size: var(--text-sm); }
		.fa-a-table { width: 100%; font-size: var(--text-sm); }
		.fa-a-table th, .fa-a-table td { padding-block: 6px; padding-inline-end: 12px; border-block-end: 1px solid var(--border-color); text-align: start; vertical-align: top; word-break: break-word; }
		.fa-a-table tr:last-child td { border-block-end: none; }
		.fa-a-table th { color: var(--text-muted); font-weight: normal; }
		.fa-a-target { font-weight: 600; }
		.fa-a-row.is-void .fa-a-target { text-decoration: line-through; }
		.fa-a-row.is-void { color: var(--text-muted); }
		.fa-a-verb { display: inline-block; border: 1px solid var(--border-color); border-radius: 10px; padding-inline: 8px; margin-inline-end: 4px; margin-block-end: 2px; }
		.fa-a-verb.is-void { text-decoration: line-through; border-style: dashed; }
		.fa-a-limit { color: var(--text-muted); }
		.fa-a-foot { padding-block: 8px; padding-inline: 12px; border-block-start: 1px solid var(--border-color); font-size: var(--text-sm); color: var(--text-muted); }
		.fa-a-tool { font-family: var(--font-stack-mono, monospace); margin-inline-end: 6px; }
		.fa-a-empty { color: var(--text-muted); padding-block: 12px; }
	`;

	function add_styles() {
		if (document.getElementById(STYLE_ID)) return;
		$(`<style id="${STYLE_ID}">${STYLES}</style>`).appendTo(document.head);
	}

	function wrapper(frm) {
		const field = frm.fields_dict && frm.fields_dict[FIELD];
		return field ? field.$wrapper : null;
	}

	function is_manager() {
		return MANAGER_ROLES.some((role) => frappe.user.has_role(role));
	}

	/** Redraw the panel from the saved record. */
	function render(frm) {
		const $wrapper = wrapper(frm);
		if (!$wrapper) return;

		add_styles();
		$wrapper.empty();

		if (frm.is_new() || !is_manager()) return;

		frappe
			.call({ method: METHOD, args: { agent: frm.doc.name } })
			.then((response) => {
				const access = response && response.message;
				if (!access) return;
				// The form may have moved on while the call was in flight.
				const $current = wrapper(frm);
				if ($current) draw($current, access, frm.is_dirty());
			})
			.catch(() => {
				// A refused or failed preview is not worth an error dialog on a form
				// the user came to edit. The panel simply stays empty.
			});
	}

	/** Say the panel is out of date rather than quietly showing yesterday's rules. */
	function mark_stale(frm) {
		const $wrapper = wrapper(frm);
		if (!$wrapper || !$wrapper.find(".fa-a-panel").length) return;
		$wrapper.find(".fa-a-stale").remove();
		$("<div>")
			.addClass("fa-a-banner fa-a-stale")
			.text(__("Not saved yet. Save the agent to see what these rules come to."))
			.insertAfter($wrapper.find(".fa-a-head"));
	}

	function draw($wrapper, access, dirty) {
		const $panel = $("<div>").addClass("fa-a-panel").appendTo($wrapper);

		const $head = $("<div>").addClass("fa-a-head").appendTo($panel);
		$("<div>").addClass("fa-a-title").text(__("Effective access")).appendTo($head);
		$("<div>")
			.addClass("fa-a-note")
			.text(__("As {0} would see it. An agent never exceeds the person using it.", [access.user]))
			.appendTo($head);

		if (dirty) {
			$("<div>")
				.addClass("fa-a-banner fa-a-stale")
				.text(__("Not saved yet. Save the agent to see what these rules come to."))
				.appendTo($panel);
		}

		if (access.legacy) {
			$("<div>")
				.addClass("fa-a-banner")
				.text(
					__(
						"This agent still runs on the tools selected below. Add an access rule or a profile to move it onto the matrix."
					)
				)
				.appendTo($panel);
		}

		const $body = $("<div>").addClass("fa-a-body").appendTo($panel);
		if (access.rows.length) {
			draw_table($body, access);
		} else if (!access.legacy) {
			$("<div>")
				.addClass("fa-a-empty")
				.text(__("No rules. This agent is offered none of the generic tools."))
				.appendTo($body);
		}

		draw_footer($panel, access);
	}

	function draw_table($body, access) {
		const $table = $("<table>").addClass("fa-a-table").appendTo($body);
		const $head = $("<tr>").appendTo($("<thead>").appendTo($table));
		[__("Target"), __("May"), __("Limits"), __("From")].forEach((label) =>
			$("<th>").text(label).appendTo($head)
		);

		const $rows = $("<tbody>").appendTo($table);
		access.rows.forEach((row) => draw_row($rows, row));
	}

	function draw_row($rows, row) {
		const $row = $("<tr>").addClass("fa-a-row").appendTo($rows);
		if (row.nullified_for_user || !row.exists) $row.addClass("is-void");

		const $target = $("<td>").appendTo($row);
		$("<div>").addClass("fa-a-target").text(row.target).appendTo($target);
		$("<div>")
			.addClass("fa-a-note")
			.text(row.target_type === "Report" ? __("Report") : __("DocType"))
			.appendTo($target);
		if (!row.exists) {
			$("<div>").addClass("fa-a-note").text(__("No such record on this site.")).appendTo($target);
		} else if (row.nullified_for_user) {
			$("<div>")
				.addClass("fa-a-note")
				.text(__("Your own permissions do not reach this, so it grants you nothing."))
				.appendTo($target);
		}

		const $verbs = $("<td>").appendTo($row);
		row.verbs.forEach((verb) => {
			const $verb = $("<span>").addClass("fa-a-verb").text(__(verb.label)).appendTo($verbs);
			if (verb.nullified_for_user) {
				$verb.addClass("is-void").attr("title", __("Not permitted to you."));
			}
		});

		const $limits = $("<td>").addClass("fa-a-limit").appendTo($row);
		const limits = [];
		if (row.target_type !== "Report") {
			limits.push(row.update_any_draft ? __("Any user's drafts") : __("Own drafts only"));
		}
		if (row.max_rows_per_call) {
			limits.push(__("At most {0} rows a call", [row.max_rows_per_call]));
		}
		limits.forEach((text) => $("<div>").text(text).appendTo($limits));

		const $sources = $("<td>").addClass("fa-a-limit").appendTo($row);
		(row.sources || []).forEach((source) => $("<div>").text(source).appendTo($sources));
	}

	function draw_footer($panel, access) {
		const $foot = $("<div>").addClass("fa-a-foot").appendTo($panel);

		const $tools = $("<div>").appendTo($foot);
		if (access.tools.length) {
			$tools.append($("<span>").text(__("Tools offered:") + " "));
			access.tools.forEach((tool) => $("<span>").addClass("fa-a-tool").text(tool).appendTo($tools));
		} else {
			$tools.text(__("No tools are offered to this agent."));
		}

		if (access.may_read_files) {
			$("<div>").text(__("May read attached files.")).appendTo($foot);
		}
		if (!access.proposals_allowed) {
			$("<div>")
				.text(
					__("Autonomy {0}: the proposal tools are not offered whatever the rules say.", [
						__(access.autonomy || ""),
					])
				)
				.appendTo($foot);
		}
	}

	frappe.ui.form.on("Agent", {
		refresh: render,
		access_profiles: mark_stale,
		access_rules_add: mark_stale,
		access_rules_remove: mark_stale,
		may_read_files: mark_stale,
		autonomy: mark_stale,
	});

	// Ticking a check on a rule row changes the answer as much as adding the row.
	frappe.ui.form.on("Agent Access Rule", {
		target: (frm) => mark_stale(frm),
		target_type: (frm) => mark_stale(frm),
		can_read: (frm) => mark_stale(frm),
		can_create_draft: (frm) => mark_stale(frm),
		can_update_draft: (frm) => mark_stale(frm),
		can_propose: (frm) => mark_stale(frm),
		can_extract: (frm) => mark_stale(frm),
		update_any_draft: (frm) => mark_stale(frm),
		max_rows_per_call: (frm) => mark_stale(frm),
	});
})();

// Rehearsals: saved test cases run against this agent. The buttons only queue
// work or read results; the rehearsal records and their results hold the rest.
// Everything a person typed is drawn with .text().
(function () {
	const RUNNER = "frappe_agents.rehearsals.runner";
	const PACK = "frappe_agents.rehearsals.pack.load_injection_pack";
	const MANAGER_ROLES = ["Agent Manager", "System Manager"];
	const GROUP = __("Rehearsals");
	const COLOURS = { Pass: "green", Fail: "red", Inconclusive: "orange" };

	function run_all(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Run all rehearsals"),
			fields: [
				{
					fieldname: "model_profile",
					fieldtype: "Link",
					options: "LLM Model Profile",
					label: __("Model Profile"),
					description: __(
						"Leave empty for the agent's own model. Any other profile must be one of its alternates."
					),
				},
			],
			primary_action_label: __("Run"),
			primary_action(values) {
				dialog.hide();
				frappe
					.call({
						method: `${RUNNER}.run_all`,
						args: { agent: frm.doc.name, model_profile: values.model_profile || null },
						freeze: true,
					})
					.then((r) => {
						const { batch, results, refused } = r.message;
						frappe.show_alert({
							message: __("{0} rehearsals queued", [results.length]),
							indicator: "blue",
						});
						if (refused.length) {
							const $list = $("<ul>");
							refused.forEach((row) => $("<li>").text(`${row.rehearsal}: ${row.error}`).appendTo($list));
							frappe.msgprint({
								title: __("Not queued"),
								message: $("<div>").append($list).html(),
								indicator: "orange",
							});
						}
						frappe.set_route("List", "Agent Rehearsal Result", { batch });
					});
			},
		});
		dialog.show();
	}

	function load_pack(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Load the injection pack"),
			fields: [
				{
					fieldname: "bait_vector",
					fieldtype: "Select",
					label: __("Bait Vector"),
					options: ["Attached file", "Comment on the document"],
					default: "Attached file",
					description: __(
						"A file touches no business data. A comment stays on the document after the rehearsal."
					),
				},
				{
					fieldname: "run_as_user",
					fieldtype: "Link",
					options: "User",
					label: __("Run As"),
					default: frappe.session.user,
				},
				{
					fieldname: "context_doctype",
					fieldtype: "Link",
					options: "DocType",
					label: __("Document Type"),
					description: __("The document the bait talks about. Use a test record."),
				},
				{
					fieldname: "context_name",
					fieldtype: "Dynamic Link",
					options: "context_doctype",
					label: __("Document"),
				},
			],
			primary_action_label: __("Load"),
			primary_action(values) {
				dialog.hide();
				frappe
					.call({ method: PACK, args: Object.assign({ agent: frm.doc.name }, values), freeze: true })
					.then((r) => {
						frappe.show_alert({
							message: __("{0} rehearsals added, {1} already there", [
								r.message.created.length,
								r.message.skipped.length,
							]),
							indicator: "green",
						});
						frappe.set_route("List", "Agent Rehearsal", { agent: frm.doc.name });
					});
			},
		});
		dialog.show();
	}

	function compare(frm) {
		frappe.call({ method: `${RUNNER}.comparison`, args: { agent: frm.doc.name } }).then((r) => {
			const { profiles, rows } = r.message;
			const $table = $('<table class="table table-bordered table-sm">');
			const $head = $("<tr>").appendTo($("<thead>").appendTo($table));
			$("<th>").text(__("Rehearsal")).appendTo($head);
			profiles.forEach((profile) => $("<th>").text(profile || __("Unknown")).appendTo($head));
			const $body = $("<tbody>").appendTo($table);
			rows.forEach((row) => {
				const $row = $("<tr>").appendTo($body);
				$("<td>").text(row.title || row.rehearsal).appendTo($row);
				profiles.forEach((profile) => {
					const cell = row.cells[profile];
					const $cell = $("<td>").appendTo($row);
					if (!cell) {
						$cell.text("—");
						return;
					}
					$("<a>")
						.attr("href", `/app/agent-rehearsal-result/${encodeURIComponent(cell.result)}`)
						.addClass(`indicator-pill ${COLOURS[cell.verdict] || "gray"}`)
						.text(__(cell.verdict || ""))
						.appendTo($cell);
					const cost = cell.cost ? ` · ${format_number(cell.cost, null, 4)}` : "";
					$("<div>")
						.addClass("text-muted small")
						.text(__("{0} tokens", [cell.tokens]) + cost)
						.appendTo($cell);
				});
			});
			const dialog = new frappe.ui.Dialog({ title: __("Rehearsals by model"), size: "extra-large" });
			$(dialog.body).append(
				rows.length && profiles.length
					? $table
					: $("<p class='text-muted'>").text(__("No rehearsal of this agent has been scored yet."))
			);
			dialog.show();
		});
	}

	frappe.ui.form.on("Agent", {
		refresh(frm) {
			if (frm.is_new() || !frappe.user.has_role(MANAGER_ROLES)) return;
			frm.add_custom_button(
				__("Open"),
				() => frappe.set_route("List", "Agent Rehearsal", { agent: frm.doc.name }),
				GROUP
			);
			frm.add_custom_button(__("Run all"), () => run_all(frm), GROUP);
			frm.add_custom_button(__("Compare models"), () => compare(frm), GROUP);
			frm.add_custom_button(__("Load injection pack"), () => load_pack(frm), GROUP);
		},
	});
})();
