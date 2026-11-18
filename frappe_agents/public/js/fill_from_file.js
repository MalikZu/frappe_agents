// "Fill from file" on the forms an agent may extract into.
//
// The server reads the file through the extraction pipeline and stops before any
// insert; this script puts the values into the open form as unsaved changes and
// the person saves. Three rules hold everything below.
//
// 1. Every value came out of a document nobody here trusts. It reaches the DOM
//    through .text() and never as HTML.
// 2. A sensitive value is never set by this script on its own. It is shown next
//    to the empty field, with the master comparison, and the person types it or
//    clicks to use the printed value. A mismatch, or a link changed since the
//    reading, takes that click away.
// 3. A value a person already put in the form is not overwritten. It is listed
//    as a conflict with a button to take the file's value instead.
(function () {
	frappe.provide("frappe_agents");

	const START_METHOD = "frappe_agents.api.fill_from_file";
	const VALUES_METHOD = "frappe_agents.api.get_fill_values";
	const RECORD_METHOD = "frappe_agents.api.record_filled_document";
	const DISCARD_METHOD = "frappe_agents.api.discard_extraction";
	const UPDATE_EVENT = "frappe_agents:extraction_update";

	const STYLE_ID = "frappe-agents-fill-styles";
	const POLL_MS = 4000;
	const MAX_POLL_ERRORS = 3;
	// The extraction job's own timeout, plus a margin.
	const GIVE_UP_MS = 16 * 60 * 1000;
	const LOW_CONFIDENCE = 0.6;
	const VALUE_CHARS = 200;
	const READABLE_FILE = /\.(pdf|png|jpe?g|gif|webp)$/i;

	const STYLES = `
		.fa-fill-panel { border: 1px solid var(--border-color); border-radius: var(--border-radius, 6px); padding: 10px 12px; margin: 0 0 12px; background: var(--card-bg, var(--control-bg)); }
		.fa-fill-panel.is-error { border-color: var(--red-500, var(--border-color)); }
		.fa-fill-head { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; justify-content: space-between; }
		.fa-fill-title { font-weight: 600; }
		.fa-fill-actions { display: flex; gap: 6px; flex-wrap: wrap; }
		.fa-fill-note { font-size: var(--text-sm); color: var(--text-muted); margin-top: 4px; }
		.fa-fill-section { margin-top: 10px; }
		.fa-fill-section-title { font-weight: 600; font-size: var(--text-sm); margin-bottom: 4px; }
		.fa-fill-item { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; padding: 4px 0; border-top: 1px solid var(--border-color); }
		.fa-fill-item:first-of-type { border-top: none; }
		.fa-fill-label { font-weight: 600; }
		.fa-fill-value { font-family: var(--font-stack-mono, monospace); word-break: break-all; }
		.fa-fill-warn { color: var(--text-on-red, var(--text-color)); background: var(--bg-red, var(--control-bg)); border-radius: var(--border-radius, 6px); padding: 2px 6px; font-weight: 600; }
		.fa-fill-ok { color: var(--text-on-green, var(--text-color)); background: var(--bg-green, var(--control-bg)); border-radius: var(--border-radius, 6px); padding: 2px 6px; }
		.fa-fill-filled .control-input, .fa-fill-filled .like-disabled-input { box-shadow: inset 3px 0 0 var(--blue-500, #2490ef); }
		.fa-fill-filled.is-low .control-input { box-shadow: inset 3px 0 0 var(--orange-500, #f8814f); }
		.fa-fill-sensitive .control-input { outline: 2px solid var(--red-500, #e24c4c); border-radius: var(--border-radius, 6px); }
	`;

	function add_styles() {
		if (document.getElementById(STYLE_ID)) return;
		$(`<style id="${STYLE_ID}">${STYLES}</style>`).appendTo(document.head);
	}

	/** Agents on the boot payload whose Extract rules cover this doctype. */
	frappe_agents.get_fill_agents = function (doctype) {
		const boot = (frappe.boot && frappe.boot.frappe_agents) || {};
		return (boot.form_agents || []).filter((agent) =>
			(agent.extract_doctypes || []).includes(doctype),
		);
	};

	function can_fill(frm) {
		if (!frappe.user.has_role("Agent User")) return false;
		if (frm.meta.istable || frm.meta.issingle) return false;
		if (frm.is_new()) return frappe.model.can_create(frm.doctype);
		return cint(frm.doc.docstatus) === 0 && !!(frm.perm && frm.perm[0] && frm.perm[0].write);
	}

	// -- small helpers ---------------------------------------------------------

	function is_blank(value) {
		return value === null || value === undefined || value === "";
	}

	function short(value) {
		const text = is_blank(value) ? "" : String(value);
		return text.length > VALUE_CHARS ? text.slice(0, VALUE_CHARS) + "…" : text;
	}

	function same(left, right) {
		const norm = (value) =>
			is_blank(value) ? "" : String(value).replace(/\s+/g, "").toUpperCase();
		return norm(left) === norm(right);
	}

	function label_of(doctype, fieldname) {
		const df = frappe.meta.get_docfield(doctype, fieldname);
		return (df && __(df.label)) || fieldname;
	}

	/** Blank, or still the value the doctype would have put there by itself. */
	function is_untouched(df, doc, value) {
		if (is_blank(value)) return true;
		if (["Int", "Float", "Currency", "Percent", "Check"].includes(df.fieldtype) && !flt(value))
			return true;
		try {
			const fallback = frappe.model.get_default_value(df, doc);
			return !is_blank(fallback) && String(fallback) === String(value);
		} catch (error) {
			return false;
		}
	}

	function el(tag, cls, text) {
		const $node = $(`<${tag}>`);
		if (cls) $node.addClass(cls);
		if (!is_blank(text)) $node.text(text);
		return $node;
	}

	function button(text, action, opts = {}) {
		const $btn = $('<button type="button" class="btn btn-xs btn-default">').text(text);
		if (opts.disabled) $btn.prop("disabled", true).attr("title", opts.reason || "");
		$btn.on("click", (event) => {
			event.preventDefault();
			action();
		});
		return $btn;
	}

	// -- state -------------------------------------------------------------------

	function state_of(frm) {
		return frm.__fa_fill || null;
	}

	function teardown(frm) {
		const state = state_of(frm);
		if (!state) return;
		state.alive = false;
		if (state.timer) clearInterval(state.timer);
		if (state.handler && frappe.realtime) frappe.realtime.off(UPDATE_EVENT, state.handler);
		if (state.$panel) state.$panel.remove();
		clear_marks(frm);
		frm.__fa_fill = null;
	}

	function clear_marks(frm) {
		$(frm.wrapper)
			.find(".fa-fill-filled, .fa-fill-sensitive")
			.removeClass("fa-fill-filled fa-fill-sensitive is-low");
	}

	// -- starting ---------------------------------------------------------------

	frappe_agents.open_fill_dialog = function (frm, agents) {
		const attachments = frm.is_new()
			? []
			: ((frm.get_docinfo && frm.get_docinfo().attachments) || []).filter((file) =>
					READABLE_FILE.test(file.file_name || file.file_url || ""),
				);

		const fields = [];
		if (agents.length > 1) {
			fields.push({
				fieldtype: "Select",
				fieldname: "agent",
				label: __("Agent"),
				options: agents.map((agent) => ({
					value: agent.name,
					// Select labels are rendered as HTML by the framework.
					label: frappe.utils.escape_html(agent.agent_name || agent.name),
				})),
				default: agents[0].name,
				reqd: 1,
			});
		}
		if (attachments.length) {
			fields.push({
				fieldtype: "Select",
				fieldname: "file",
				label: __("Attached file"),
				options: attachments.map((file) => ({
					value: file.name,
					// A file name is whatever the uploader called it, and Select labels are
					// rendered as HTML by the framework.
					label: frappe.utils.escape_html(file.file_name || file.name),
				})),
				default: attachments[0].name,
			});
		}
		fields.push({
			fieldtype: "HTML",
			fieldname: "help",
			options: `<p class="text-muted small">${frappe.utils.escape_html(
				__("A PDF or an image. The values go into this form; nothing is saved until you save it."),
			)}</p>`,
		});

		const dialog = new frappe.ui.Dialog({
			title: __("Fill from file"),
			fields: fields,
			primary_action_label: attachments.length ? __("Read this file") : __("Upload a file"),
			primary_action(values) {
				const agent = values.agent || agents[0].name;
				dialog.hide();
				if (attachments.length && values.file) return start(frm, agent, values.file);
				upload(frm, agent);
			},
		});
		if (attachments.length) {
			dialog.set_secondary_action_label(__("Upload a new file"));
			dialog.set_secondary_action(() => {
				const agent = dialog.get_value("agent") || agents[0].name;
				dialog.hide();
				upload(frm, agent);
			});
		}
		dialog.show();
		return dialog;
	};

	function upload(frm, agent) {
		new frappe.ui.FileUploader({
			// A new form has no name to attach to yet. The upload stays private to the
			// person, and the reading attaches a copy of it to itself.
			doctype: frm.is_new() ? null : frm.doctype,
			docname: frm.is_new() ? null : frm.docname,
			make_attachments_public: false,
			allow_multiple: false,
			// Only files stored in this site can be read.
			allow_web_link: false,
			restrictions: { allowed_file_types: ["application/pdf", "image/*"] },
			on_success: (file_doc) => {
				if (file_doc && file_doc.name) start(frm, agent, file_doc.name);
			},
		});
	}

	function start(frm, agent, file) {
		teardown(frm);
		frappe
			.call({
				method: START_METHOD,
				args: {
					agent: agent,
					file: file,
					doctype: frm.doctype,
					docname: frm.is_new() ? null : frm.docname,
				},
				freeze: true,
				freeze_message: __("Sending the file…"),
			})
			.then((r) => {
				const started = r && r.message;
				if (!started || !started.extraction) return;
				track(frm, started);
			});
	}

	// -- waiting ------------------------------------------------------------------

	function track(frm, started) {
		add_styles();
		const state = {
			alive: true,
			phase: "reading",
			extraction: started.extraction,
			file_name: started.file_name || "",
			docname: frm.docname,
			started_at: Date.now(),
			$panel: el("div", "fa-fill-panel"),
			watched: {},
		};
		frm.__fa_fill = state;
		$(frm.layout.wrapper).prepend(state.$panel);
		draw_progress(frm, __("Reading {0}…", [state.file_name || __("the file")]));

		// Realtime makes it quick; polling makes it certain. The event is per user,
		// not per tab, so it only ever prompts a check of this reading.
		state.handler = (data) => {
			if (data && data.extraction === state.extraction) check(frm);
		};
		if (frappe.realtime && frappe.realtime.socket) frappe.realtime.on(UPDATE_EVENT, state.handler);
		state.timer = setInterval(() => check(frm), POLL_MS);
	}

	function check(frm) {
		const state = state_of(frm);
		if (!state || !state.alive || state.phase !== "reading" || state.checking) return;
		if (Date.now() - state.started_at > GIVE_UP_MS) {
			stop_waiting(state);
			return draw_error(frm, __("The reading took too long. Try again."));
		}
		state.checking = true;
		frappe
			.call({ method: VALUES_METHOD, args: { extraction: state.extraction }, type: "GET" })
			.then((r) => {
				state.checking = false;
				if (!state.alive || state_of(frm) !== state) return;
				const data = (r && r.message) || {};
				if (data.status === "Failed") {
					stop_waiting(state);
					return draw_error(frm, data.error || __("The reading failed."));
				}
				if (data.status !== "Ready") return;
				stop_waiting(state);
				state.phase = "applying";
				draw_progress(frm, __("Filling the form…"));
				return apply(frm, data);
			})
			.catch(() => {
				state.checking = false;
				// One dropped request is not a failed reading. Give up after a few.
				state.errors = (state.errors || 0) + 1;
				if (state.errors < MAX_POLL_ERRORS) return;
				stop_waiting(state);
				draw_error(frm, __("The values could not be loaded. Try again."));
			});
	}

	function stop_waiting(state) {
		if (state.timer) clearInterval(state.timer);
		state.timer = null;
		if (state.handler && frappe.realtime) frappe.realtime.off(UPDATE_EVENT, state.handler);
		state.handler = null;
	}

	// -- filling ------------------------------------------------------------------

	async function apply(frm, data) {
		const state = state_of(frm);
		const values = data.values || {};
		const notes = data.notes || {};
		const report = { filled: [], conflicts: [], tables: [], skipped: [] };

		const scalars = Object.keys(values).filter((key) => !Array.isArray(values[key]));
		const tables = Object.keys(values).filter((key) => Array.isArray(values[key]));
		// Links first: setting one runs its fetches, and the values the file states
		// are set after them so a fetch does not overwrite what the document says.
		scalars.sort((a, b) => link_first(frm.doctype, a) - link_first(frm.doctype, b));

		// Each set_value can wait on the server. If the person moves to another
		// record meanwhile, the rest of these values are not for it.
		const still_here = () =>
			state.alive && state_of(frm) === state && frm.docname === state.docname;
		try {
			for (const fieldname of scalars) {
				if (!still_here()) return;
				await fill_scalar(frm, fieldname, values[fieldname], report);
			}
			for (const table of tables) {
				if (!still_here()) return;
				await fill_table(frm, table, values[table], report);
			}
		} catch (error) {
			console.error("frappe_agents: fill stopped part way", error);
			report.error = true;
		}

		if (!still_here()) return;
		state.phase = "applied";
		state.data = data;
		state.report = report;
		mark_filled(frm, report.filled, notes);
		draw_result(frm);
	}

	function link_first(doctype, fieldname) {
		const df = frappe.meta.get_docfield(doctype, fieldname);
		return df && df.fieldtype === "Link" ? 0 : 1;
	}

	async function fill_scalar(frm, fieldname, value, report) {
		const df = frappe.meta.get_docfield(frm.doctype, fieldname, frm.docname);
		if (!df || df.read_only || df.hidden) {
			report.skipped.push(fieldname);
			return;
		}
		const current = frm.doc[fieldname];
		if (!is_untouched(df, frm.doc, current)) {
			if (!same(current, value)) report.conflicts.push({ fieldname, value, current });
			return;
		}
		await frm.set_value(fieldname, value);
		report.filled.push(fieldname);
	}

	async function fill_table(frm, table, rows, report) {
		const df = frappe.meta.get_docfield(frm.doctype, table, frm.docname);
		if (!df || df.read_only || df.hidden || !rows.length) return;
		const child_doctype = df.options;

		// A new form often starts with one empty row. A row is the person's work if
		// any field in it holds something other than what the row starts with; only
		// rows with nothing of theirs make way, and only on a form never saved.
		const existing = frm.doc[table] || [];
		const value_fields = (frappe.get_meta(child_doctype).fields || []).filter(
			(cdf) => !frappe.model.no_value_type.includes(cdf.fieldtype),
		);
		const blank = (row) => value_fields.every((cdf) => is_untouched(cdf, row, row[cdf.fieldname]));
		if (frm.is_new() && existing.length && existing.every(blank)) {
			frm.clear_table(table);
		}
		if ((frm.doc[table] || []).length) {
			report.tables.push({ table, rows, added: false });
			return;
		}
		await add_rows(frm, table, child_doctype, rows);
		report.tables.push({ table, rows, added: true });
	}

	async function add_rows(frm, table, child_doctype, rows) {
		for (const row of rows) {
			const child = frm.add_child(table);
			const keys = Object.keys(row).sort(
				(a, b) => link_first(child_doctype, a) - link_first(child_doctype, b),
			);
			for (const key of keys) {
				const cdf = frappe.meta.get_docfield(child_doctype, key);
				if (!cdf || cdf.read_only) continue;
				await frappe.model.set_value(child.doctype, child.name, key, row[key]);
			}
		}
		frm.refresh_field(table);
	}

	function mark_filled(frm, filled, notes) {
		for (const fieldname of filled) {
			const field = frm.get_field(fieldname);
			if (!field || !field.$wrapper) continue;
			const note = notes[fieldname] || {};
			const parts = [__("Read from the file")];
			if (note.page) parts.push(__("page {0}", [note.page]));
			if (note.confidence !== undefined)
				parts.push(__("model's confidence {0}%", [Math.round(note.confidence * 100)]));
			field.$wrapper.addClass("fa-fill-filled").attr("title", parts.join(", "));
			if (note.confidence !== undefined && note.confidence < LOW_CONFIDENCE)
				field.$wrapper.addClass("is-low");
		}
		for (const key of Object.keys((state_of(frm).data || {}).sensitive || {})) {
			const field = key.includes("[") ? null : frm.get_field(key);
			if (field && field.$wrapper) field.$wrapper.addClass("fa-fill-sensitive");
		}
	}

	// -- drawing ------------------------------------------------------------------

	function draw_progress(frm, message) {
		const state = state_of(frm);
		state.$panel.removeClass("is-error").empty();
		const $head = el("div", "fa-fill-head").appendTo(state.$panel);
		el("span", "fa-fill-title", message).appendTo($head);
		el("div", "fa-fill-actions")
			.append(button(__("Cancel"), () => cancel(frm)))
			.appendTo($head);
		el(
			"div",
			"fa-fill-note",
			__("You can keep working. The form fills in when the reading is done."),
		).appendTo(state.$panel);
	}

	function draw_error(frm, message) {
		const state = state_of(frm);
		if (!state) return;
		state.phase = "failed";
		state.$panel.addClass("is-error").empty();
		const $head = el("div", "fa-fill-head").appendTo(state.$panel);
		el("span", "fa-fill-title", __("Fill from file did not work")).appendTo($head);
		el("div", "fa-fill-actions")
			.append(button(__("Close"), () => teardown(frm)))
			.appendTo($head);
		el("div", "fa-fill-note", message).appendTo(state.$panel);
	}

	function draw_result(frm) {
		const state = state_of(frm);
		const data = state.data;
		const report = state.report;
		state.$panel.removeClass("is-error").empty();

		const $head = el("div", "fa-fill-head").appendTo(state.$panel);
		el(
			"span",
			"fa-fill-title",
			__("Filled {0} field(s) from {1}. Check them, then save.", [
				report.filled.length,
				state.file_name || __("the file"),
			]),
		).appendTo($head);
		const $actions = el("div", "fa-fill-actions").appendTo($head);
		const url = String((data.source && data.source.file_url) || "");
		if (url.startsWith("/files/") || url.startsWith("/private/files/")) {
			$actions.append(button(__("Open the file"), () => window.open(url, "_blank", "noopener")));
		}
		// Hides the panel only. The reading stays open so that saving the form still
		// records it and attaches the file; a discarded reading would lose that.
		$actions.append(button(__("Hide"), () => state.$panel.hide()));

		el(
			"div",
			"fa-fill-note",
			__(
				"Highlighted fields came from the file. Orange means the model was unsure. Nothing is saved until you save.",
			),
		).appendTo(state.$panel);
		if (report.error) {
			el(
				"div",
				"fa-fill-note",
				__("Some values could not be set. Check the form before saving."),
			).appendTo(state.$panel);
		}

		draw_duplicates(frm, data.duplicates || {});
		draw_sensitive(frm, data.sensitive || {});
		draw_candidates(frm, data.candidates || {});
		draw_conflicts(frm, report.conflicts);
		draw_tables(frm, report.tables);
	}

	function section(frm, title) {
		const $section = el("div", "fa-fill-section").appendTo(state_of(frm).$panel);
		el("div", "fa-fill-section-title", title).appendTo($section);
		return $section;
	}

	function draw_duplicates(frm, duplicates) {
		const same_file = duplicates.same_file || [];
		const collisions = duplicates.unique_collisions || [];
		const hidden =
			cint(duplicates.same_file_hidden_count) + cint(duplicates.unique_collisions_hidden_count);
		if (!same_file.length && !collisions.length && !hidden) return;

		const $section = section(frm, __("This may be a duplicate"));
		for (const row of same_file) {
			const $item = el("div", "fa-fill-item").appendTo($section);
			el("span", "fa-fill-warn", __("This file was read before")).appendTo($item);
			if (row.created_doc) {
				$item.append(
					button(row.created_doc, () => frappe.set_route("Form", frm.doctype, row.created_doc)),
				);
			}
		}
		for (const row of collisions) {
			const $item = el("div", "fa-fill-item").appendTo($section);
			el("span", "fa-fill-label", label_of(frm.doctype, row.fieldname)).appendTo($item);
			el("span", "fa-fill-value", short(row.value)).appendTo($item);
			$item.append(button(row.name, () => frappe.set_route("Form", frm.doctype, row.name)));
		}
		if (hidden) {
			el("div", "fa-fill-note", __("{0} more you cannot open.", [hidden])).appendTo($section);
		}
	}

	function draw_sensitive(frm, sensitive) {
		const keys = Object.keys(sensitive);
		if (!keys.length) return;
		const $section = section(frm, __("Check these by hand. They were left empty on purpose."));

		for (const key of keys) {
			const flag = sensitive[key] || {};
			const $item = el("div", "fa-fill-item").appendTo($section);
			el("span", "fa-fill-label", sensitive_label(frm, key)).appendTo($item);
			el("span", "fa-fill-note", __("The file says")).appendTo($item);
			el("span", "fa-fill-value", short(flag.value) || __("nothing")).appendTo($item);

			const stale = master_changed(frm, flag);
			if (stale) {
				el(
					"span",
					"fa-fill-warn",
					__("Compared with {0}, but the form now says {1}. Check it against the right record.", [
						flag.master_name,
						frm.doc[flag.master_field] || __("nothing"),
					]),
				).appendTo($item);
			} else if (flag.master_name && cint(flag.mismatch)) {
				el(
					"span",
					"fa-fill-warn",
					__("Different from what {0} {1} holds", [__(flag.master_doctype), flag.master_name]),
				).appendTo($item);
				if (!is_blank(flag.master_value))
					el("span", "fa-fill-value", short(flag.master_value)).appendTo($item);
			} else if (flag.master_name && cint(flag.compared) && !is_blank(flag.value)) {
				el(
					"span",
					"fa-fill-ok",
					__("Same as {0} {1}", [__(flag.master_doctype), flag.master_name]),
				).appendTo($item);
			}

			watch_master(frm, flag);
			if (key.includes("[") || is_blank(flag.value)) continue;
			$item.append(
				button(__("Use the printed value"), () => use_printed(frm, key, flag), {
					disabled: is_blocked(frm, flag),
					reason: __(
						"Only offered when the linked record holds the same value. Type it yourself after checking.",
					),
				}),
			);
		}
	}

	function sensitive_label(frm, key) {
		const match = /^([a-z0-9_]+)\[(\d+)\]\.([a-z0-9_]+)$/.exec(key);
		if (!match) return label_of(frm.doctype, key);
		const df = frappe.meta.get_docfield(frm.doctype, match[1]);
		const child = df && df.options;
		return __("{0} row {1}: {2}", [
			label_of(frm.doctype, match[1]),
			cint(match[2]) + 1,
			child ? label_of(child, match[3]) : match[3],
		]);
	}

	function master_changed(frm, flag) {
		if (!flag.master_field || !flag.master_name) return false;
		return frm.doc[flag.master_field] !== flag.master_name;
	}

	/** Redraw when the link a comparison went through changes in the form. */
	function watch_master(frm, flag) {
		const state = state_of(frm);
		if (!flag.master_field || state.watched[flag.master_field]) return;
		state.watched[flag.master_field] = true;
		const handlers = (frappe_agents.__fill_watched = frappe_agents.__fill_watched || {});
		const key = `${frm.doctype}::${flag.master_field}`;
		if (handlers[key]) return;
		handlers[key] = true;
		frappe.ui.form.on(frm.doctype, flag.master_field, (form) => {
			const current = state_of(form);
			if (current && current.phase === "applied") draw_result(form);
		});
	}

	/** One click only when the record the form links to now holds the same value. */
	function is_blocked(frm, flag) {
		return cint(flag.blocked) || cint(flag.mismatch) || master_changed(frm, flag);
	}

	function use_printed(frm, key, flag) {
		// Asked again at the click: the link may have moved since the panel drew.
		if (is_blocked(frm, flag)) return draw_result(frm);
		frm.set_value(key, flag.value);
	}

	function draw_candidates(frm, candidates) {
		const keys = Object.keys(candidates);
		if (!keys.length) return;
		const $section = section(frm, __("Pick these yourself"));
		for (const key of keys) {
			const entry = candidates[key] || {};
			const in_row = key.includes("[");
			const $item = el("div", "fa-fill-item").appendTo($section);
			el("span", "fa-fill-label", sensitive_label(frm, key)).appendTo($item);
			if (entry.text) el("span", "fa-fill-value", short(entry.text)).appendTo($item);
			if (entry.note) el("span", "fa-fill-note", entry.note).appendTo($item);
			// A row's link is picked in the row itself: its number may have moved.
			if (in_row) continue;
			for (const option of entry.options || []) {
				$item.append(button(option.title || option.name, () => frm.set_value(key, option.name)));
			}
		}
	}

	function draw_conflicts(frm, conflicts) {
		if (!conflicts.length) return;
		const $section = section(frm, __("Already filled in, so left alone"));
		for (const conflict of conflicts) {
			const $item = el("div", "fa-fill-item").appendTo($section);
			el("span", "fa-fill-label", label_of(frm.doctype, conflict.fieldname)).appendTo($item);
			el("span", "fa-fill-note", __("The form has")).appendTo($item);
			el("span", "fa-fill-value", short(conflict.current)).appendTo($item);
			el("span", "fa-fill-note", __("the file says")).appendTo($item);
			el("span", "fa-fill-value", short(conflict.value)).appendTo($item);
			$item.append(
				button(__("Use the file's value"), () => {
					frm.set_value(conflict.fieldname, conflict.value);
					state_of(frm).report.conflicts = state_of(frm).report.conflicts.filter(
						(c) => c !== conflict,
					);
					draw_result(frm);
				}),
			);
		}
	}

	function draw_tables(frm, tables) {
		const kept = tables.filter((entry) => !entry.added);
		if (!kept.length) return;
		const $section = section(frm, __("Tables that already had rows"));
		for (const entry of kept) {
			const $item = el("div", "fa-fill-item").appendTo($section);
			el("span", "fa-fill-label", label_of(frm.doctype, entry.table)).appendTo($item);
			el("span", "fa-fill-note", __("The file has {0} row(s).", [entry.rows.length])).appendTo(
				$item,
			);
			$item.append(
				button(__("Add them"), async () => {
					const df = frappe.meta.get_docfield(frm.doctype, entry.table, frm.docname);
					entry.added = true;
					await add_rows(frm, entry.table, df.options, entry.rows);
					draw_result(frm);
				}),
			);
		}
	}

	function cancel(frm) {
		const state = state_of(frm);
		if (state && ["reading", "applied"].includes(state.phase)) {
			// Closes the reading for the audit trail. Values already in the form stay
			// there, unsaved; reloading the form throws them away.
			frappe.call({ method: DISCARD_METHOD, args: { name: state.extraction } }).catch(() => {});
		}
		teardown(frm);
	}

	// -- after the save -----------------------------------------------------------

	function record(frm) {
		const state = state_of(frm);
		if (!state || state.phase !== "applied" || frm.is_new()) return;
		state.phase = "recording";
		frappe
			.call({
				method: RECORD_METHOD,
				args: { extraction: state.extraction, docname: frm.doc.name },
			})
			.then((r) => {
				const done = (r && r.message) || {};
				teardown(frm);
				frappe.show_alert({
					message: done.source_attached
						? __("Saved. The file it was filled from is attached.")
						: __("Saved. The file could not be attached; attach it by hand if you need it."),
					indicator: done.source_attached ? "green" : "orange",
				});
				if (done.source_attached) frm.reload_docinfo && frm.reload_docinfo();
			})
			.catch(() => teardown(frm));
	}

	/** The same document, allowing for a new form that was just saved and renamed. */
	function same_document(state, frm) {
		if (state.docname === frm.docname) return true;
		const renamed = frappe.model.new_names && frappe.model.new_names[state.docname];
		return renamed === frm.docname;
	}

	const handlers = {
		refresh(frm) {
			try {
				const state = state_of(frm);
				// Another document in the same form object: this reading is not for it.
				if (state && !same_document(state, frm)) teardown(frm);

				if (!can_fill(frm)) return;
				const agents = frappe_agents.get_fill_agents(frm.doctype);
				if (!agents.length) return;
				frm.add_custom_button(__("Fill from file"), () =>
					frappe_agents.open_fill_dialog(frm, agents),
				);
			} catch (error) {
				// Never take the form's refresh chain down with us.
				console.error("frappe_agents: could not add the Fill from file button", error);
			}
		},
		after_save(frm) {
			try {
				const state = state_of(frm);
				if (state && same_document(state, frm)) state.docname = frm.docname;
				record(frm);
			} catch (error) {
				console.error("frappe_agents: could not record the filled document", error);
			}
		},
	};

	// Registered per doctype rather than on "*": version-15 has no wildcard form
	// handlers, and only the doctypes an agent may extract into need the button.
	const registered = new Set();
	function register() {
		const boot = (frappe.boot && frappe.boot.frappe_agents) || {};
		for (const agent of boot.form_agents || []) {
			for (const doctype of agent.extract_doctypes || []) {
				if (registered.has(doctype)) continue;
				registered.add(doctype);
				frappe.ui.form.on(doctype, handlers);
			}
		}
	}
	register();
	$(document).on("startup", register);
})();
