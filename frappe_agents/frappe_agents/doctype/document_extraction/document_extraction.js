// The review surface for an extracted document: the source file on the left,
// what the model claims to have read on the right. A reviewer who cannot see
// both is not reviewing, they are rubber-stamping.
//
// Two rules govern everything below.
//
// 1. Every extracted value came out of a document nobody here trusts. It is
//    written into the DOM with .text() or .val() and never as HTML — a supplier
//    name of "<img onerror=…>" is a string, not markup.
// 2. Sensitive fields are not in the draft and do not get there by being drawn
//    nicely. Each one needs its own tick, and the server applies only the ones
//    named in `confirmed`. The mismatch diff is drawn loud because a changed
//    payment detail on an otherwise genuine invoice is the whole attack.
//
// Wrapped in an IIFE: doctype scripts are evaluated in the global scope, so a
// bare top-level `const STYLE_ID` or `function add_styles()` here would collide
// with — or silently overwrite — the same name in another doctype's script.
(function () {
	frappe.provide("frappe_agents");

	const STYLE_ID = "frappe-agents-extraction-styles";
	const APPLY_METHOD = "frappe_agents.api.apply_extraction";
	const DISCARD_METHOD = "frappe_agents.api.discard_extraction";
	// The one role the server lets review somebody else's extraction.
	const SYSTEM_MANAGER = "System Manager";
	const NEEDS_REVIEW = "Needs Review";

	// The desk bundle defines it; the literal keeps this form working if the
	// bundle ever stops being loaded on every page.
	const UPDATE_EVENT = frappe_agents.EXTRACTION_UPDATE_EVENT || "frappe_agents:extraction_update";

	// Below this the model itself is unsure. Its claim, not our measurement.
	const LOW_CONFIDENCE = 0.6;

	const MOUNT_FIELDS = ["review_ui", "extraction_review", "source_preview", "review_html"];
	const LONG_TEXT_TYPES = ["Text", "Small Text", "Long Text", "Text Editor", "Code", "Markdown Editor", "JSON"];
	const CHILD_ROWS = 10;
	const CHILD_COLUMNS = 5;
	const VALUE_CHARS = 500;

	const STYLES = `
		.fa-x-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: start; }
		@media (max-width: 1100px) { .fa-x-grid { grid-template-columns: minmax(0, 1fr); } }
		.fa-x-column { display: flex; flex-direction: column; gap: 16px; }
		.fa-x-pane { border: 1px solid var(--border-color); border-radius: var(--border-radius, 6px); background: var(--card-bg, var(--control-bg)); }
		.fa-x-pane-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--border-color); }
		.fa-x-pane-title { font-weight: 600; }
		.fa-x-pane-body { padding: 12px; }
		.fa-x-source { position: sticky; top: 12px; }
		.fa-x-source iframe, .fa-x-source img { display: block; width: 100%; border: none; border-radius: var(--border-radius, 6px); background: var(--control-bg); }
		.fa-x-source iframe { height: 76vh; min-height: 420px; }
		.fa-x-source img { max-height: 76vh; object-fit: contain; }
		.fa-x-note { font-size: var(--text-sm); color: var(--text-muted); }
		.fa-x-note.is-spaced { margin-top: 8px; }
		.fa-x-banner { border-radius: var(--border-radius, 6px); padding: 8px 12px; margin-bottom: 12px; }
		.fa-x-banner.is-warning { background: var(--bg-yellow, var(--control-bg)); color: var(--text-on-yellow, var(--text-color)); }
		.fa-x-banner.is-danger { background: var(--bg-red, var(--control-bg)); color: var(--text-on-red, var(--text-color)); }
		.fa-x-banner-title { font-weight: 600; }
		.fa-x-banner ul { margin: 4px 0 0 18px; }
		.fa-x-field { padding: 8px 0; border-bottom: 1px solid var(--border-color); }
		.fa-x-field:last-child { border-bottom: none; }
		.fa-x-field-head { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; margin-bottom: 4px; }
		.fa-x-label { font-weight: 600; }
		.fa-x-fieldname { font-size: var(--text-sm); color: var(--text-muted); font-family: var(--font-stack-mono, monospace); }
		.fa-x-badge { font-size: var(--text-sm); color: var(--text-muted); border: 1px solid var(--border-color); border-radius: 10px; padding: 0 8px; cursor: help; }
		.fa-x-badge.is-low { background: var(--bg-orange, var(--control-bg)); color: var(--text-on-orange, var(--text-color)); border-color: transparent; }
		.fa-x-field.is-low { background: var(--bg-orange, transparent); border-radius: var(--border-radius, 6px); padding-left: 8px; padding-right: 8px; }
		.fa-x-sensitive { border: 2px solid var(--red-500, var(--border-color)); border-radius: var(--border-radius, 6px); }
		.fa-x-sensitive .fa-x-pane-head { border-bottom-color: var(--red-500, var(--border-color)); }
		.fa-x-diff { margin: 6px 0; border-radius: var(--border-radius, 6px); overflow: hidden; border: 1px solid var(--border-color); }
		.fa-x-diff-row { display: flex; gap: 8px; padding: 4px 8px; font-family: var(--font-stack-mono, monospace); word-break: break-all; }
		.fa-x-diff-row + .fa-x-diff-row { border-top: 1px solid var(--border-color); }
		.fa-x-diff-label { flex: 0 0 40%; font-family: var(--font-stack); color: var(--text-muted); }
		.fa-x-diff.is-mismatch { border-color: var(--red-500, var(--border-color)); }
		.fa-x-diff.is-mismatch .fa-x-diff-row.is-document { background: var(--bg-red, var(--control-bg)); color: var(--text-on-red, var(--text-color)); }
		.fa-x-mismatch-warning { color: var(--text-on-red, var(--text-color)); background: var(--bg-red, var(--control-bg)); border-radius: var(--border-radius, 6px); padding: 6px 8px; margin-bottom: 6px; font-weight: 600; }
		.fa-x-confirm { display: flex; gap: 8px; align-items: flex-start; margin-top: 6px; }
		.fa-x-confirm input { margin-top: 4px; }
		.fa-x-child { width: 100%; font-size: var(--text-sm); }
		.fa-x-child th, .fa-x-child td { padding: 2px 6px; border-bottom: 1px solid var(--border-color); text-align: left; vertical-align: top; word-break: break-word; }
		.fa-x-child th { color: var(--text-muted); font-weight: normal; }
		.fa-x-empty { color: var(--text-muted); padding: 12px 0; }
	`;

	function add_styles() {
		if (document.getElementById(STYLE_ID)) return;
		$(`<style id="${STYLE_ID}">${STYLES}</style>`).appendTo(document.head);
	}

	// -- data ------------------------------------------------------------------

	/** A Code (JSON) field as an object. Anything unparseable is treated as absent. */
	function parse_json(value) {
		if (!value) return {};
		if (typeof value === "object") return value;
		try {
			const parsed = JSON.parse(value);
			return parsed && typeof parsed === "object" ? parsed : {};
		} catch (error) {
			return {};
		}
	}

	function is_blank(value) {
		return value === null || value === undefined || value === "";
	}

	/** A scalar as display text. Objects are stringified rather than dropped. */
	function as_text(value) {
		if (is_blank(value)) return "";
		if (typeof value === "boolean") return value ? __("Yes") : __("No");
		if (typeof value === "object") {
			try {
				return JSON.stringify(value);
			} catch (error) {
				return String(value);
			}
		}
		const text = String(value);
		return text.length > VALUE_CHARS ? text.slice(0, VALUE_CHARS) + "…" : text;
	}

	function docfield(state, fieldname) {
		return state.fields[fieldname] || null;
	}

	function label_for(state, fieldname) {
		const df = docfield(state, fieldname);
		return df ? __(df.label || df.fieldname) : fieldname;
	}

	/** Model self-reported confidence for a field, as {confidence, page} or null. */
	function confidence_of(state, fieldname) {
		const entry = state.confidence[fieldname];
		if (entry === undefined || entry === null) return null;
		if (typeof entry === "number") return { confidence: entry, page: null };
		if (typeof entry !== "object") return null;
		const value = entry.confidence;
		return {
			confidence: typeof value === "number" ? value : parseFloat(value),
			page: entry.page === undefined ? null : entry.page,
		};
	}

	/** Link candidates arrive as names or as {value,label} rows; accept both. */
	function candidates_of(state, fieldname) {
		const raw = state.candidates[fieldname];
		const rows = Array.isArray(raw) ? raw : raw && Array.isArray(raw.candidates) ? raw.candidates : [];
		return rows
			.map((row) => {
				if (row === null || row === undefined) return null;
				if (typeof row !== "object") return { value: String(row), label: "" };
				const value = row.value !== undefined ? row.value : row.name;
				if (is_blank(value)) return null;
				const label = row.label !== undefined ? row.label : row.title || row.description || "";
				return { value: String(value), label: label ? String(label) : "" };
			})
			.filter((row) => row);
	}

	/** duplicate_flags has no fixed shape; flatten whatever is there into lines. */
	function duplicate_lines(flags) {
		const lines = [];

		const describe = (item) => {
			if (item === null || item === undefined || item === false) return "";
			if (typeof item !== "object") return String(item);
			if (item.message) return String(item.message);
			return Object.keys(item)
				.map((key) => `${key}: ${as_text(item[key])}`)
				.join(", ");
		};

		if (Array.isArray(flags)) {
			flags.forEach((item) => {
				const line = describe(item);
				if (line) lines.push(line);
			});
			return lines;
		}

		Object.keys(flags || {}).forEach((key) => {
			const value = flags[key];
			if (!value) return;
			if (value === true) {
				lines.push(key);
				return;
			}
			if (Array.isArray(value)) {
				value.forEach((item) => {
					const line = describe(item);
					lines.push(line ? `${key}: ${line}` : key);
				});
				return;
			}
			const line = describe(value);
			lines.push(line ? `${key}: ${line}` : key);
		});

		return lines;
	}

	/**
	 * Whether this user may review this extraction — the same rule the server
	 * applies: the person who started it, or a System Manager.
	 *
	 * An auditor can read every extraction on the site and may review none of
	 * them. Drawing Accept and Discard for them offers a decision the server will
	 * refuse, which reads as a broken app rather than as a boundary.
	 */
	function may_review(frm) {
		if (frm.doc.owner && frm.doc.owner === frappe.session.user) return true;
		return (frappe.user_roles || []).includes(SYSTEM_MANAGER);
	}

	function read_state(frm) {
		const state = frm.__fa_extract;
		state.values = parse_json(frm.doc.extracted_json);
		state.confidence = parse_json(frm.doc.field_confidence);
		state.sensitive = parse_json(frm.doc.sensitive_flags);
		state.duplicates = parse_json(frm.doc.duplicate_flags);
		state.candidates = parse_json(frm.doc.link_candidates);
		state.editable = frm.doc.status === NEEDS_REVIEW && may_review(frm);
		state.controls = {};
	}

	function with_doctype(doctype) {
		// with_doctype fires the callback straight away when the meta is cached.
		return new Promise((resolve) =>
			frappe.model.with_doctype(doctype, () => resolve(frappe.get_meta(doctype) || locals.DocType[doctype]))
		);
	}

	function index_fields(meta) {
		const fields = {};
		((meta && meta.fields) || []).forEach((df) => (fields[df.fieldname] = df));
		return fields;
	}

	/** Extracted fields in the target doctype's own order, strays last. */
	function ordered_fieldnames(state, skip) {
		const present = Object.keys(state.values).filter((fieldname) => !skip.has(fieldname));
		const ordered = [];
		Object.keys(state.fields).forEach((fieldname) => {
			if (present.includes(fieldname)) ordered.push(fieldname);
		});
		present.forEach((fieldname) => {
			if (!ordered.includes(fieldname)) ordered.push(fieldname);
		});
		return ordered;
	}

	// -- source pane -----------------------------------------------------------

	/**
	 * Only a same-origin path is ever put in an iframe or an img. A File row can
	 * carry a remote or crafted file_url, and "javascript:" in a src is script
	 * execution inside the desk.
	 */
	function safe_source_url(frm) {
		const candidates = [frm.doc.source_url, frm.doc.__source_unique_url, frm.doc.source_unique_url, frm.__fa_extract.file_url];
		const url = candidates.find((value) => typeof value === "string" && value);
		if (!url) return null;
		if (!/^\/[^/]/.test(url)) return null;
		return url;
	}

	function $source_pane(frm) {
		const $pane = $("<div class='fa-x-pane fa-x-source'></div>");
		const $head = $("<div class='fa-x-pane-head'></div>").appendTo($pane);
		$("<span class='fa-x-pane-title'></span>").text(__("Source document")).appendTo($head);

		const url = safe_source_url(frm);
		if (url) {
			$("<a class='fa-x-note'></a>")
				.attr("href", url)
				.attr("target", "_blank")
				.attr("rel", "noopener")
				.text(__("Open in a new tab"))
				.appendTo($head);
		}

		const $body = $("<div class='fa-x-pane-body'></div>").appendTo($pane);

		if (!url) {
			$("<div class='fa-x-empty'></div>")
				.text(__("The source file is not available to you, so it cannot be shown here."))
				.appendTo($body);
			return $pane;
		}

		if (frappe.utils.is_image_file(url)) {
			$("<img>").attr("src", url).attr("alt", __("Source document")).appendTo($body);
		} else {
			// The browser's own viewer. Frappe ships no PDF renderer, so a browser
			// without one shows an empty frame — hence the note and the tab link.
			$("<iframe></iframe>").attr("src", url).attr("title", __("Source document")).appendTo($body);
			$("<div class='fa-x-note is-spaced'></div>")
				.text(__("Shown by your browser. If the frame stays blank, open the file in a new tab."))
				.appendTo($body);
		}

		return $pane;
	}

	// -- field controls --------------------------------------------------------

	function $confidence_badge(state, fieldname) {
		const entry = confidence_of(state, fieldname);
		if (!entry || isNaN(entry.confidence)) return null;

		const percent = Math.round(Math.max(0, Math.min(1, entry.confidence)) * 100);
		const parts = [__("Model says {0}% sure", [percent])];
		if (!is_blank(entry.page)) parts.push(__("page {0}", [as_text(entry.page)]));

		const $badge = $("<span class='fa-x-badge'></span>")
			.text(parts.join(" · "))
			.attr(
				"title",
				__("The model's own claim about its answer, not a measurement and not evidence. Check it against the document.")
			);
		if (entry.confidence < LOW_CONFIDENCE) $badge.addClass("is-low");
		return $badge;
	}

	function $child_table(rows, state, fieldname) {
		const df = docfield(state, fieldname);
		const child_meta = df && df.options ? frappe.get_meta(df.options) : null;
		const columns = [];

		((child_meta && child_meta.fields) || []).forEach((child_df) => {
			if (columns.length >= CHILD_COLUMNS) return;
			if (!child_df.in_list_view) return;
			columns.push({ fieldname: child_df.fieldname, label: __(child_df.label || child_df.fieldname) });
		});

		if (!columns.length) {
			const keys = [];
			rows.forEach((row) => {
				if (!row || typeof row !== "object") return;
				Object.keys(row).forEach((key) => {
					if (keys.length < CHILD_COLUMNS && !keys.includes(key)) keys.push(key);
				});
			});
			keys.forEach((key) => columns.push({ fieldname: key, label: key }));
		}

		const $wrapper = $("<div></div>");
		const $table = $("<table class='fa-x-child'></table>").appendTo($wrapper);
		const $head = $("<tr></tr>").appendTo($("<thead></thead>").appendTo($table));
		columns.forEach((column) => $("<th></th>").text(column.label).appendTo($head));

		const $body = $("<tbody></tbody>").appendTo($table);
		rows.slice(0, CHILD_ROWS).forEach((row) => {
			const $row = $("<tr></tr>").appendTo($body);
			columns.forEach((column) => {
				const value = row && typeof row === "object" ? row[column.fieldname] : row;
				$("<td></td>").text(as_text(value)).appendTo($row);
			});
		});

		if (rows.length > CHILD_ROWS) {
			$("<div class='fa-x-note'></div>")
				.text(__("and {0} more rows", [rows.length - CHILD_ROWS]))
				.appendTo($wrapper);
		}
		$("<div class='fa-x-note'></div>")
			.text(__("Table rows are applied as extracted. Edit them on the draft afterwards."))
			.appendTo($wrapper);

		return $wrapper;
	}

	/**
	 * One editable control per field, registered in state.controls so Accept can
	 * read it back. Values go in through .val(), never through markup.
	 */
	function build_control(state, fieldname, value, $body) {
		const df = docfield(state, fieldname);
		const candidates = candidates_of(state, fieldname);

		if (Array.isArray(value)) {
			$body.append($child_table(value, state, fieldname));
			state.controls[fieldname] = { get: () => value };
			return;
		}

		if (candidates.length) {
			const $select = $("<select class='form-control'></select>").appendTo($body);
			$("<option></option>").attr("value", "").text(__("— leave empty —")).appendTo($select);

			const values = candidates.map((row) => row.value);
			if (!is_blank(value) && !values.includes(String(value))) {
				$("<option></option>")
					.attr("value", String(value))
					.text(__("{0} (as extracted)", [as_text(value)]))
					.appendTo($select);
			}
			candidates.forEach((row) => {
				$("<option></option>")
					.attr("value", row.value)
					.text(row.label ? `${row.value} — ${row.label}` : row.value)
					.appendTo($select);
			});

			$select.val(is_blank(value) ? "" : String(value));
			$select.prop("disabled", !state.editable);
			$("<div class='fa-x-note is-spaced'></div>")
				.text(__("More than one record matched. Pick the right one — nothing new is created."))
				.appendTo($body);
			state.controls[fieldname] = { get: () => $select.val() };
			return;
		}

		if (df && df.fieldtype === "Check") {
			const $label = $("<label class='fa-x-confirm'></label>").appendTo($body);
			const $box = $("<input type='checkbox'>").prependTo($label);
			$box.prop("checked", Boolean(cint(value)));
			$box.prop("disabled", !state.editable);
			$("<span></span>").text(__("Ticked")).appendTo($label);
			state.controls[fieldname] = { get: () => ($box.prop("checked") ? 1 : 0) };
			return;
		}

		if (df && LONG_TEXT_TYPES.includes(df.fieldtype)) {
			const $area = $("<textarea class='form-control' rows='3'></textarea>").appendTo($body);
			$area.val(is_blank(value) ? "" : String(value));
			$area.prop("disabled", !state.editable);
			state.controls[fieldname] = { get: () => $area.val() };
			return;
		}

		const $input = $("<input type='text' class='form-control'>").appendTo($body);
		$input.val(is_blank(value) ? "" : String(value));
		$input.prop("disabled", !state.editable);
		state.controls[fieldname] = { get: () => $input.val() };
	}

	function $field_row(state, fieldname, value) {
		const $row = $("<div class='fa-x-field'></div>");
		const $head = $("<div class='fa-x-field-head'></div>").appendTo($row);
		$("<span class='fa-x-label'></span>").text(label_for(state, fieldname)).appendTo($head);
		$("<span class='fa-x-fieldname'></span>").text(fieldname).appendTo($head);

		const $badge = $confidence_badge(state, fieldname);
		if ($badge) {
			$badge.appendTo($head);
			const entry = confidence_of(state, fieldname);
			if (entry && entry.confidence < LOW_CONFIDENCE) $row.addClass("is-low");
		}

		build_control(state, fieldname, value, $("<div></div>").appendTo($row));
		return $row;
	}

	// -- sensitive block -------------------------------------------------------

	function $diff(flag) {
		const mismatch = Boolean(flag.mismatch);
		const $diff_box = $("<div class='fa-x-diff'></div>");
		if (mismatch) $diff_box.addClass("is-mismatch");

		const $master = $("<div class='fa-x-diff-row'></div>").appendTo($diff_box);
		$("<span class='fa-x-diff-label'></span>").text(__("On the linked record")).appendTo($master);
		$("<span></span>")
			.text(is_blank(flag.master_value) ? __("nothing to compare against") : as_text(flag.master_value))
			.appendTo($master);

		const $document = $("<div class='fa-x-diff-row is-document'></div>").appendTo($diff_box);
		$("<span class='fa-x-diff-label'></span>").text(__("In this document")).appendTo($document);
		$("<span></span>").text(as_text(flag.value)).appendTo($document);

		return $diff_box;
	}

	function $sensitive_row(state, fieldname, flag) {
		const $row = $("<div class='fa-x-field'></div>");
		const $head = $("<div class='fa-x-field-head'></div>").appendTo($row);
		$("<span class='fa-x-label'></span>").text(label_for(state, fieldname)).appendTo($head);
		$("<span class='fa-x-fieldname'></span>").text(fieldname).appendTo($head);
		const $badge = $confidence_badge(state, fieldname);
		if ($badge) $badge.appendTo($head);

		if (flag.mismatch) {
			$("<div class='fa-x-mismatch-warning'></div>")
				.text(
					__(
						"This does not match the record on file. A changed payment detail on a genuine-looking document is exactly what an invoice-redirection attack looks like — check it with the supplier through a channel you already use, never one printed on this document."
					)
				)
				.appendTo($row);
		}

		if (!is_blank(flag.master_value) || flag.mismatch) {
			$diff(flag).appendTo($row);
		}

		const $control = $("<div></div>").appendTo($row);
		const $input = $("<input type='text' class='form-control'>").appendTo($control);
		$input.val(is_blank(flag.value) ? "" : String(flag.value));
		$input.prop("disabled", !state.editable);

		const $label = $("<label class='fa-x-confirm'></label>").appendTo($row);
		const $box = $("<input type='checkbox'>").appendTo($label);
		$box.prop("checked", Boolean(flag.confirmed) && !state.editable);
		$box.prop("disabled", !state.editable);
		$("<span></span>")
			.text(__("I checked this value against a source I trust — apply it to the draft"))
			.appendTo($label);

		state.controls[fieldname] = {
			sensitive: true,
			mismatch: Boolean(flag.mismatch),
			label: label_for(state, fieldname),
			confirmed: () => Boolean($box.prop("checked")),
			get: () => $input.val(),
		};

		return $row;
	}

	function $sensitive_pane(state) {
		const fieldnames = Object.keys(state.sensitive);
		if (!fieldnames.length) return null;

		const $pane = $("<div class='fa-x-pane fa-x-sensitive'></div>");
		const $head = $("<div class='fa-x-pane-head'></div>").appendTo($pane);
		$("<span class='fa-x-pane-title'></span>").text(__("Held back — confirm each one")).appendTo($head);

		const $body = $("<div class='fa-x-pane-body'></div>").appendTo($pane);
		$("<div class='fa-x-note'></div>")
			.text(
				__(
					"These values were never written to the draft. Each one is applied only if you tick it, whatever the model claims about its own confidence."
				)
			)
			.appendTo($body);

		fieldnames.forEach((fieldname) => {
			const flag = state.sensitive[fieldname];
			if (!flag || typeof flag !== "object") return;
			$sensitive_row(state, fieldname, flag).appendTo($body);
		});

		return $pane;
	}

	// -- panes and banners -----------------------------------------------------

	function $fields_column(frm) {
		const state = frm.__fa_extract;
		const $column = $("<div class='fa-x-column'></div>");
		const $pane = $("<div class='fa-x-pane'></div>").appendTo($column);
		const $head = $("<div class='fa-x-pane-head'></div>").appendTo($pane);
		$("<span class='fa-x-pane-title'></span>").text(__("What the model read")).appendTo($head);
		$("<span class='fa-x-note'></span>").text(__(frm.doc.target_doctype || "")).appendTo($head);

		const $body = $("<div class='fa-x-pane-body'></div>").appendTo($pane);
		$("<div class='fa-x-note'></div>")
			.text(
				__(
					"Confidence and page numbers are the model's own claim about its answer, not a measurement. Read every value off the document on the left before accepting."
				)
			)
			.appendTo($body);

		const skip = new Set(Object.keys(state.sensitive));
		const fieldnames = ordered_fieldnames(state, skip);

		if (!fieldnames.length) {
			$("<div class='fa-x-empty'></div>").text(__("The model returned no values for this document.")).appendTo($body);
		} else {
			fieldnames.forEach((fieldname) => $field_row(state, fieldname, state.values[fieldname]).appendTo($body));
		}

		const $sensitive = $sensitive_pane(state);
		if ($sensitive) $sensitive.appendTo($column);

		return $column;
	}

	function render_banners(frm, $mount) {
		const state = frm.__fa_extract;

		const lines = duplicate_lines(state.duplicates);
		if (lines.length) {
			const $banner = $("<div class='fa-x-banner is-warning'></div>").appendTo($mount);
			$("<div class='fa-x-banner-title'></div>")
				.text(__("This document looks like one that was already extracted"))
				.appendTo($banner);
			const $list = $("<ul></ul>").appendTo($banner);
			lines.forEach((line) => $("<li></li>").text(line).appendTo($list));
		}

		const mismatched = Object.keys(state.sensitive).filter((fieldname) => {
			const flag = state.sensitive[fieldname];
			return flag && flag.mismatch;
		});
		if (mismatched.length) {
			const $banner = $("<div class='fa-x-banner is-danger'></div>").appendTo($mount);
			$("<div class='fa-x-banner-title'></div>")
				.text(__("{0} of these values disagree with the record on file", [mismatched.length]))
				.appendTo($banner);
			$("<div></div>")
				.text(mismatched.map((fieldname) => label_for(state, fieldname)).join(", "))
				.appendTo($banner);
		}

		if (cint(frm.doc.pages_capped)) {
			$("<div class='fa-x-banner is-warning'></div>")
				.text(__("The document was longer than the page cap, so only its first pages were read."))
				.appendTo($mount);
		}
	}

	/** Where the review draws. Lane A may hand over an HTML field; otherwise the
	 *  dashboard, and both are wiped by the form's own refresh. */
	function mount(frm) {
		const state = frm.__fa_extract;
		if (state.$mount && state.$mount.parent().length) return state.$mount;

		const $el = $("<div class='fa-x-review'></div>");
		const fieldname = MOUNT_FIELDS.find((name) => frm.fields_dict && frm.fields_dict[name]);
		if (fieldname) {
			frm.fields_dict[fieldname].$wrapper.empty().append($el);
		} else if (frm.dashboard && frm.dashboard.add_section) {
			frm.dashboard.add_section($el, __("Review"));
		} else {
			frm.layout.wrapper.prepend($el);
		}
		state.$mount = $el;
		return $el;
	}

	function render(frm) {
		const $mount = mount(frm).empty();
		render_banners(frm, $mount);
		const $grid = $("<div class='fa-x-grid'></div>").appendTo($mount);
		$grid.append($source_pane(frm));
		$grid.append($fields_column(frm));
	}

	// -- accept and discard ----------------------------------------------------

	/**
	 * Reviewed values plus the sensitive fields the reviewer actually ticked.
	 * An unticked sensitive value is not sent at all — the server refuses it
	 * anyway, and sending it would only invite a future shortcut.
	 */
	function collect(frm) {
		const state = frm.__fa_extract;
		const values = {};
		const confirmed = [];
		const withheld = [];

		Object.keys(state.controls).forEach((fieldname) => {
			const control = state.controls[fieldname];
			if (!control.sensitive) {
				values[fieldname] = control.get();
				return;
			}
			if (control.confirmed()) {
				values[fieldname] = control.get();
				confirmed.push(fieldname);
			} else {
				withheld.push(control.label);
			}
		});

		return { values: values, confirmed: confirmed, withheld: withheld };
	}

	function accept(frm) {
		const state = frm.__fa_extract;
		const collected = collect(frm);

		const dialog = new frappe.ui.Dialog({
			title: __("Accept this extraction"),
			fields: [{ fieldtype: "HTML", fieldname: "summary" }],
			primary_action_label: __("Accept"),
			primary_action: () => {
				dialog.hide();
				send_accept(frm, collected);
			},
		});

		const $summary = dialog.fields_dict.summary.$wrapper;
		$("<p></p>")
			.text(
				__("{0} values will be written to the {1} draft.", [
					Object.keys(collected.values).length,
					__(frm.doc.target_doctype || ""),
				])
			)
			.appendTo($summary);

		if (collected.confirmed.length) {
			const risky = collected.confirmed.filter((fieldname) => state.controls[fieldname].mismatch);
			$("<p></p>")
				.text(
					__("You are confirming: {0}.", [
						collected.confirmed.map((fieldname) => state.controls[fieldname].label).join(", "),
					])
				)
				.appendTo($summary);
			if (risky.length) {
				$("<p class='fa-x-mismatch-warning'></p>")
					.text(
						__("{0} disagrees with the record on file and you are applying it anyway.", [
							risky.map((fieldname) => state.controls[fieldname].label).join(", "),
						])
					)
					.appendTo($summary);
			}
		}

		if (collected.withheld.length) {
			$("<p></p>")
				.text(__("Left empty on the draft: {0}.", [collected.withheld.join(", ")]))
				.appendTo($summary);
		}

		dialog.show();
	}

	function send_accept(frm, collected) {
		frappe.call({
			method: APPLY_METHOD,
			args: {
				name: frm.doc.name,
				values: JSON.stringify(collected.values),
				confirmed: JSON.stringify(collected.confirmed),
			},
			freeze: true,
			freeze_message: __("Applying the reviewed values…"),
			callback: (r) => {
				// created_doc only — a bare `name` in the reply is the extraction's
				// own, and routing to it under the target doctype opens nothing.
				const created = (r.message && r.message.created_doc) || frm.doc.created_doc;
				frappe.show_alert({ message: __("Extraction accepted"), indicator: "green" });
				if (created && frm.doc.target_doctype) {
					frappe.set_route("Form", frm.doc.target_doctype, created);
					return;
				}
				frm.reload_doc();
			},
		});
	}

	function discard(frm) {
		frappe.confirm(
			__("Discard this extraction? Nothing is written to the draft and the record stays for the audit trail."),
			() =>
				frappe.call({
					method: DISCARD_METHOD,
					args: { name: frm.doc.name },
					freeze: true,
					freeze_message: __("Discarding…"),
					callback: () => {
						frappe.show_alert({ message: __("Extraction discarded"), indicator: "orange" });
						frm.reload_doc();
					},
				})
		);
	}

	// -- form ------------------------------------------------------------------

	function set_intro(frm) {
		frm.set_intro("");

		if (frm.doc.status === "Failed" && frm.doc.error) {
			frm.set_intro(frm.doc.error, "red");
			return;
		}
		if (["Pending", "Running"].includes(frm.doc.status)) {
			frm.set_intro(__("Reading the document. This page updates when it is done."), "blue");
			return;
		}
		if (frm.doc.status === NEEDS_REVIEW && !may_review(frm)) {
			// Read-only, and said in words rather than by a button that fails.
			frm.set_intro(
				__(
					"Waiting for review by {0}. You can read what was extracted here; accepting or discarding it is theirs to do, or a System Manager's.",
					[frm.doc.owner || ""]
				),
				"blue"
			);
			return;
		}
		if (frm.doc.status === "Accepted") {
			frm.set_intro(
				__("Accepted by {0}. The draft still has to be submitted by a person.", [frm.doc.reviewed_by || ""]),
				"green"
			);
			return;
		}
		if (frm.doc.status === "Discarded") {
			frm.set_intro(__("Discarded. Nothing was written to the draft."), "orange");
		}
	}

	/** While the job runs, the row changes underneath the form; reload on its event. */
	function watch(frm) {
		const state = frm.__fa_extract;
		if (state.handler) {
			frappe.realtime.off(UPDATE_EVENT, state.handler);
			state.handler = null;
		}
		if (!["Pending", "Running"].includes(frm.doc.status)) return;

		state.handler = (data) => {
			const name = data && (data.extraction || data.name);
			if (name !== frm.doc.name) return;
			if (data.status && data.status === frm.doc.status) return;
			frm.reload_doc();
		};
		frappe.realtime.on(UPDATE_EVENT, state.handler);
	}

	/** The source file's own url, for the pane, when the server did not ship one. */
	function load_file_url(frm) {
		const state = frm.__fa_extract;
		const token = state.token;
		if (!frm.doc.source_file || safe_source_url(frm)) return Promise.resolve();

		return frappe.db
			.get_value("File", frm.doc.source_file, "file_url")
			.then((r) => {
				if (state.token !== token) return;
				// No ?fid= — that narrows the permission check to one File row, and
				// the reviewer is often not the one who uploaded it. Without it the
				// server tries every row with this url and serves the readable one.
				state.file_url = (r && r.message && r.message.file_url) || null;
			})
			.catch(() => {});
	}

	frappe.ui.form.on("Document Extraction", {
		onload(frm) {
			add_styles();
		},

		refresh(frm) {
			// The row moves through whitelisted methods only, so the form never saves.
			frm.disable_save();

			// A refresh has just wiped the dashboard and the buttons; the token makes
			// an in-flight lookup from the previous refresh drop its result rather
			// than draw into a detached node.
			const previous = frm.__fa_extract || {};
			if (previous.handler) frappe.realtime.off(UPDATE_EVENT, previous.handler);
			frm.__fa_extract = {
				token: (previous.token || 0) + 1,
				$mount: null,
				fields: {},
				controls: {},
				file_url: previous.file_url || null,
				handler: null,
			};
			const state = frm.__fa_extract;
			const token = state.token;

			set_intro(frm);
			watch(frm);
			read_state(frm);

			const meta = frm.doc.target_doctype ? with_doctype(frm.doc.target_doctype) : Promise.resolve(null);
			Promise.all([meta.catch(() => null), load_file_url(frm)]).then((loaded) => {
				if (state.token !== token) return;
				state.fields = index_fields(loaded[0]);
				render(frm);
			});

			if (state.editable) {
				frm.page.set_primary_action(__("Accept"), () => accept(frm));
				frm.add_custom_button(__("Discard"), () => discard(frm));
			}
			if (frm.doc.created_doc && frm.doc.target_doctype) {
				frm.add_custom_button(__("Open Draft"), () =>
					frappe.set_route("Form", frm.doc.target_doctype, frm.doc.created_doc)
				);
			}
		},
	});
})();
