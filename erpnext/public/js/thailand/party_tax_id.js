// Customer / Supplier from a Tax ID (Revenue Department, regional/thailand/utils).
// The list button "New from Tax ID" and the new form's "Fetch from Revenue Department" open ONE preview dialog:
// Tax ID + branch -> Fetch shows RD's legal name, Tax ID, branch and address (international layout: Address Line 2 =
// ตำบล/แขวง, City = อำเภอ/เขต, State = จังหวัด). A party that already has the Tax ID (a Customer at any branch, a
// Supplier at the same branch) is offered with "Open Existing"; RD not knowing it gives "Enter Manually". "Save" asks
// the server (create_party_from_tax_id), which reads RD again and creates the party with its Billing Address;
// "Edit Before Saving" opens the unsaved form with the name, type, Tax ID and branch, and only the non-stored key
// rd_address_lookup ("<tax id>:<branch>"): the server reads RD again on insert and makes the Address
// (fill_rd_address_before_insert). The browser never sends the stored address. Nothing here is mandatory.
// The quick entry dialog keeps its own Tax ID section, which fills its native address fields.
erpnext.thailand = erpnext.thailand || {};

erpnext.thailand.RD_PARTY = {
	Customer: { name: "customer_name", type: "customer_type" },
	Supplier: { name: "supplier_name", type: "supplier_type" },
};
erpnext.thailand.RD_BUTTON = "Fetch from Revenue Department";

erpnext.thailand.rd_tax_id_digits = function (value) {
	return String(value || "").replace(/[\s-]/g, "");
};

erpnext.thailand.show_rd_button = function (frm) {
	return Boolean(frm.is_new() && frappe.model.can_create(frm.doctype));
};

erpnext.thailand.rd_branch = function (value) {
	const digits = String(value || "").replace(/\s/g, "");
	return /^[0-9]{1,5}$/.test(digits) ? digits.padStart(5, "0") : "00000";
};

erpnext.thailand.rd_key = function (doc) {
	return `${erpnext.thailand.rd_tax_id_digits(doc.tax_id)}:${erpnext.thailand.rd_branch(doc.branch_code)}`;
};

// The party to open instead: a Customer with the Tax ID at any branch (one Customer per Tax ID; another branch is
// an Address of it), a Supplier only at the same branch (one Supplier per Tax ID and branch).
erpnext.thailand.rd_blocking_party = function (doctype, data) {
	const existing = data.existing || [];
	if (doctype === "Customer") return existing[0];
	return existing.find((party) => party.same_branch);
};

// Shared by the form and the quick entry dialog. `target` is {doctype, doc, state, set_value(field, value), has_field(field)}.
erpnext.thailand.apply_rd_result = function (target, data) {
	const fields = erpnext.thailand.RD_PARTY[target.doctype];
	const blocking = erpnext.thailand.rd_blocking_party(target.doctype, data);
	if (blocking) {
		const extra =
			target.doctype === "Customer" ? " " + __("Another branch is added there as an Address.") : "";
		frappe.confirm(
			__("{0} {1} ({2}) already has Tax ID {3} (branch {4}). Open it instead of creating a new one?", [
				__(target.doctype),
				blocking.title,
				blocking.name,
				data.tax_id,
				blocking.branch_code || "-",
			]) + extra,
			() => frappe.set_route("Form", target.doctype, blocking.name),
		);
		return "existing";
	}
	if (!data.found) {
		frappe.show_alert(
			{
				message: __(
					"Tax ID {0} is not found at the Revenue Department (an individual or not VAT registered). Type the details by hand.",
					[data.tax_id],
				),
				indicator: "orange",
			},
			10,
		);
		return "not_found";
	}
	const key = `${data.tax_id}:${data.branch_code}`;
	target.state.rd_last_lookup = key; // the set_value calls below must not ask RD again
	target.set_value(fields.name, data.legal_name);
	target.set_value(fields.type, "Company");
	target.set_value("tax_id", data.tax_id);
	if (target.has_field("branch_code")) target.set_value("branch_code", data.branch_code);
	const a = data.address || {};
	erpnext.thailand.fill_rd_address(target, a);
	const others = (data.existing || []).filter((party) => !party.same_branch);
	frappe.show_alert(
		{
			message:
				__(
					"Filled from the Revenue Department, address {0}. It becomes the primary Address on save.",
					[[a.address_line1, a.address_line2, a.city, a.state, a.pincode].filter(Boolean).join(" ")],
				) +
				(others.length
					? " " +
						__("This Tax ID is already {0} at another branch.", [
							others.map((p) => p.name).join(", "),
						])
					: ""),
			indicator: "green",
		},
		10,
	);
	return "filled";
};

// RD's address into the form's own address fields (only the ones the form or dialog has), international
// layout (Siwat, 17 Nov 2026): address_line2 = ตำบล/แขวง, city = อำเภอ/เขต, state = จังหวัด, county unused.
// City is required by ERPNext's make_address, so a record without อำเภอ falls back to จังหวัด.
erpnext.thailand.RD_ADDRESS_FIELDS = ["address_line1", "address_line2", "city", "state", "pincode"];
erpnext.thailand.fill_rd_address = function (target, a) {
	if (!a.address_line1 && !a.address_line2 && !a.city && !a.state) return;
	const values = {
		address_line1: a.address_line1 || a.name || "",
		address_line2: a.address_line2 || "",
		city: a.city || a.state || "",
		state: a.state || "",
		pincode: a.pincode || "",
	};
	for (const field of erpnext.thailand.RD_ADDRESS_FIELDS) {
		if (target.has_field(field)) target.set_value(field, values[field]);
	}
	for (const field of ["country", "country_address"]) {
		if (target.has_field(field)) target.set_value(field, "Thailand");
	}
};

erpnext.thailand.lookup_rd = function (target) {
	const tax_id = erpnext.thailand.rd_tax_id_digits(target.doc.tax_id);
	if (!/^[0-9]{13}$/.test(tax_id)) {
		frappe.show_alert({ message: __("Type the 13-digit Tax ID first."), indicator: "orange" });
		return;
	}
	const branch = erpnext.thailand.rd_branch(target.doc.branch_code);
	target.state.rd_last_lookup = `${tax_id}:${branch}`;
	return frappe.call({
		method: "erpnext.regional.thailand.utils.get_party_by_tax_id",
		args: { doctype: target.doctype, tax_id, branch },
		freeze: true,
		callback: (r) => {
			if (r.message) erpnext.thailand.apply_rd_result(target, r.message);
		},
	});
};

// Tax ID or branch changed: a complete new pair is looked up once (its RD record replaces the filled values).
erpnext.thailand.on_rd_key_change = function (target) {
	const key = erpnext.thailand.rd_key(target.doc);
	const tax_id = erpnext.thailand.rd_tax_id_digits(target.doc.tax_id);
	if (!/^[0-9]{13}$/.test(tax_id)) return;
	if (target.state.rd_last_lookup === key) return;
	erpnext.thailand.lookup_rd(target);
};

// ---- Preview dialog -------------------------------------------------------------------------------------

erpnext.thailand.RD_LOOKUP_KEY = "rd_address_lookup";
erpnext.thailand.LIST_BUTTON = "New from Tax ID";

// What the preview offers for an RD answer: "existing" (Open Existing), "not_found" (Enter Manually) or "found".
erpnext.thailand.preview_mode = function (doctype, data) {
	if (erpnext.thailand.rd_blocking_party(doctype, data)) return "existing";
	return data && data.found ? "found" : "not_found";
};

erpnext.thailand.preview_address = function (a) {
	a = a || {};
	return [a.address_line1, a.address_line2, a.city, a.state, a.pincode].filter(Boolean).join(" ");
};

erpnext.thailand.preview_html = function (doctype, data) {
	const esc = (v) => frappe.utils.escape_html(String(v || ""));
	const mode = erpnext.thailand.preview_mode(doctype, data);
	if (mode === "existing") {
		const p = erpnext.thailand.rd_blocking_party(doctype, data);
		return `<p>${esc(
			__("{0} {1} ({2}) already has Tax ID {3} (branch {4}).", [
				__(doctype),
				p.title,
				p.name,
				data.tax_id,
				p.branch_code || "-",
			]),
		)}</p>`;
	}
	if (mode === "not_found") {
		return `<p>${esc(
			__(
				"Tax ID {0} is not found at the Revenue Department (an individual or not VAT registered). Enter the details by hand.",
				[data.tax_id],
			),
		)}</p>`;
	}
	const a = data.address || {};
	const rows = [
		[__("Legal Name"), data.legal_name],
		[__("Tax ID"), data.tax_id],
		[__("Branch Code"), data.branch_code],
		[__("Address Line 1"), a.address_line1],
		[__("Address Line 2"), a.address_line2],
		[__("City/Town"), a.city || a.state],
		[__("State/Province"), a.state],
		[__("Postal Code"), a.pincode],
		[__("Country"), "Thailand"],
	];
	const others = (data.existing || []).filter((p) => !p.same_branch);
	return (
		`<table class="table table-bordered table-condensed">${rows
			.map(([label, value]) => `<tr><th>${esc(label)}</th><td>${esc(value)}</td></tr>`)
			.join("")}</table>` +
		(others.length
			? `<p class="text-muted">${esc(
					__("This Tax ID is already {0} at another branch.", [
						others.map((p) => p.name).join(", "),
					]),
				)}</p>`
			: "")
	);
};

// The values an unsaved form starts with. With rd = true it carries the lookup key, so the server adds RD's address.
erpnext.thailand.form_values = function (doctype, data, rd) {
	const fields = erpnext.thailand.RD_PARTY[doctype];
	const values = { tax_id: data.tax_id, branch_code: data.branch_code };
	if (rd) {
		values[fields.name] = data.legal_name;
		values[fields.type] = "Company";
		values[erpnext.thailand.RD_LOOKUP_KEY] = `${data.tax_id}:${data.branch_code}`;
	}
	return values;
};

// Open the values in a form: the one the preview came from, else a new unsaved one.
erpnext.thailand.open_form = function (doctype, values, frm) {
	if (frm) {
		for (const [field, value] of Object.entries(values)) {
			if (frm.fields_dict[field]) frm.set_value(field, value);
			else frm.doc[field] = value;
		}
		if (!values[erpnext.thailand.RD_LOOKUP_KEY]) delete frm.doc[erpnext.thailand.RD_LOOKUP_KEY];
		return;
	}
	const doc = frappe.model.get_new_doc(doctype);
	Object.assign(doc, values);
	frappe.set_route("Form", doctype, doc.name);
};

erpnext.thailand.save_from_preview = function (doctype, data, dialog, frm) {
	return frappe.call({
		method: "erpnext.regional.thailand.utils.create_party_from_tax_id",
		args: { doctype, tax_id: data.tax_id, branch: data.branch_code },
		freeze: true,
		callback: (r) => {
			const out = r.message || {};
			if (out.name) {
				dialog && dialog.hide();
				frappe.set_route("Form", doctype, out.name);
			} else if (out.missing) {
				frappe.show_alert(
					{
						message: __("{0} has no default set. Check the details before saving.", [
							out.missing.join(", "),
						]),
						indicator: "orange",
					},
					10,
				);
				dialog && dialog.hide();
				erpnext.thailand.open_form(doctype, erpnext.thailand.form_values(doctype, data, true), frm);
			} else if (out.existing) {
				erpnext.thailand.show_preview(dialog, doctype, { ...data, existing: out.existing });
			} else {
				erpnext.thailand.show_preview(dialog, doctype, { ...data, found: false });
			}
		},
	});
};

// Show an RD answer in the dialog and set its buttons.
erpnext.thailand.show_preview = function (dialog, doctype, data) {
	const mode = erpnext.thailand.preview_mode(doctype, data);
	dialog.__rd_data = data;
	dialog.__rd_mode = mode;
	dialog.fields_dict.preview.$wrapper.html(erpnext.thailand.preview_html(doctype, data));
	const label = { existing: __("Open Existing"), not_found: __("Enter Manually"), found: __("Save") }[mode];
	dialog.set_primary_action(label, () => erpnext.thailand.preview_primary(dialog, doctype));
	if (dialog.__rd_edit_button) dialog.__rd_edit_button.toggle(mode === "found");
	return mode;
};

erpnext.thailand.preview_primary = function (dialog, doctype) {
	const data = dialog.__rd_data;
	const frm = dialog.__rd_frm;
	if (!data) return erpnext.thailand.preview_fetch(dialog, doctype);
	if (dialog.__rd_mode === "existing") {
		dialog.hide();
		frappe.set_route("Form", doctype, erpnext.thailand.rd_blocking_party(doctype, data).name);
	} else if (dialog.__rd_mode === "not_found") {
		dialog.hide();
		erpnext.thailand.open_form(doctype, erpnext.thailand.form_values(doctype, data, false), frm);
	} else {
		erpnext.thailand.save_from_preview(doctype, data, dialog, frm);
	}
};

erpnext.thailand.preview_fetch = function (dialog, doctype) {
	const tax_id = erpnext.thailand.rd_tax_id_digits(dialog.get_value("tax_id"));
	if (!/^[0-9]{13}$/.test(tax_id)) {
		frappe.show_alert({ message: __("Type the 13-digit Tax ID first."), indicator: "orange" });
		return;
	}
	const branch = erpnext.thailand.rd_branch(dialog.get_value("branch_code"));
	return frappe.call({
		method: "erpnext.regional.thailand.utils.get_party_by_tax_id",
		args: { doctype, tax_id, branch },
		freeze: true,
		callback: (r) => {
			if (r.message) erpnext.thailand.show_preview(dialog, doctype, r.message);
		},
	});
};

erpnext.thailand.open_tax_id_preview = function (doctype, { tax_id, branch_code, frm } = {}) {
	const dialog = new frappe.ui.Dialog({
		title: __("New {0} from Tax ID", [__(doctype)]),
		fields: [
			{ label: __("Tax ID"), fieldname: "tax_id", fieldtype: "Data", default: tax_id || "" },
			{ fieldtype: "Column Break" },
			{
				label: __("Branch Code"),
				fieldname: "branch_code",
				fieldtype: "Data",
				default: branch_code || "00000",
			},
			{ fieldtype: "Column Break" },
			{ label: __("Fetch"), fieldname: "fetch", fieldtype: "Button" },
			{ fieldtype: "Section Break" },
			{ fieldname: "preview", fieldtype: "HTML" },
		],
		secondary_action_label: __("Cancel"),
		secondary_action: () => dialog.hide(),
	});
	dialog.__rd_frm = frm;
	dialog.fields_dict.fetch.df.click = () => erpnext.thailand.preview_fetch(dialog, doctype);
	for (const field of ["tax_id", "branch_code"]) {
		dialog.fields_dict[field].df.onchange = () => {
			dialog.__rd_data = null;
			dialog.fields_dict.preview.$wrapper.html("");
			dialog.set_primary_action(__("Fetch"), () => erpnext.thailand.preview_primary(dialog, doctype));
			if (dialog.__rd_edit_button) dialog.__rd_edit_button.toggle(false);
		};
	}
	dialog.set_primary_action(__("Fetch"), () => erpnext.thailand.preview_primary(dialog, doctype));
	dialog.add_custom_action(__("Edit Before Saving"), () => {
		dialog.hide();
		erpnext.thailand.open_form(
			doctype,
			erpnext.thailand.form_values(doctype, dialog.__rd_data, true),
			dialog.__rd_frm,
		);
	});
	// add_custom_action returns nothing: the button is the last one it appended
	dialog.__rd_edit_button = dialog.custom_actions ? dialog.custom_actions.find("button").last() : null;
	if (dialog.__rd_edit_button) dialog.__rd_edit_button.toggle(false);
	dialog.show();
	if (tax_id) erpnext.thailand.preview_fetch(dialog, doctype);
	return dialog;
};

// List view (customer_list.js, supplier_list.js): "New from Tax ID" for a user who may create the party.
erpnext.thailand.add_list_button = function (listview) {
	const doctype = listview.doctype;
	if (!erpnext.thailand.RD_PARTY[doctype] || !frappe.model.can_create(doctype)) return;
	listview.page.add_inner_button(__(erpnext.thailand.LIST_BUTTON), () =>
		erpnext.thailand.open_tax_id_preview(doctype),
	);
};

for (const doctype of Object.keys(erpnext.thailand.RD_PARTY)) {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (!erpnext.thailand.show_rd_button(frm)) return;
			frm.add_custom_button(__(erpnext.thailand.RD_BUTTON), () =>
				erpnext.thailand.open_tax_id_preview(frm.doctype, {
					tax_id: frm.doc.tax_id,
					branch_code: frm.doc.branch_code,
					frm,
				}),
			);
		},
	});
}

// Quick entry (Customer and Supplier, also "Create new" from a Link field): a Tax ID section with the same button.
// The RD address goes into the dialog's own Primary Address fields.
erpnext.thailand.quick_entry_fields = function (doctype) {
	if (!erpnext.thailand.RD_PARTY[doctype]) return [];
	return [
		{ fieldtype: "Section Break", label: __("Tax ID"), collapsible: 0 },
		{ label: __("Tax ID"), fieldname: "tax_id", fieldtype: "Data" },
		{ fieldtype: "Column Break" },
		{ label: __("Branch Code"), fieldname: "branch_code", fieldtype: "Data", default: "00000" },
		{ fieldtype: "Column Break" },
		{ label: __(erpnext.thailand.RD_BUTTON), fieldname: "fetch_from_rd", fieldtype: "Button" },
	];
};

erpnext.thailand.dialog_target = function (doctype, dialog) {
	return {
		doctype,
		doc: dialog.doc,
		state: (dialog.__rd_state = dialog.__rd_state || {}),
		set_value: (field, value) => {
			if (dialog.fields_dict[field]) dialog.set_value(field, value);
			else dialog.doc[field] = value;
		},
		has_field: (field) => Boolean(dialog.fields_dict[field]),
	};
};

if (frappe.ui.form.ContactAddressQuickEntryForm) {
	const proto = frappe.ui.form.ContactAddressQuickEntryForm.prototype;
	const base_variant_fields = proto.get_variant_fields;
	proto.get_variant_fields = function () {
		const fields = base_variant_fields.call(this);
		const have = new Set((this.mandatory || []).map((df) => df.fieldname));
		const extra = erpnext.thailand
			.quick_entry_fields(this.doctype)
			.filter((df) => !df.fieldname || !have.has(df.fieldname));
		return extra.length > 1 && frappe.model.can_create(this.doctype) ? extra.concat(fields) : fields;
	};
	const base_render = proto.render_dialog;
	proto.render_dialog = function () {
		base_render.call(this);
		if (!erpnext.thailand.RD_PARTY[this.doctype] || !this.dialog) return;
		const dialog = this.dialog;
		const target = erpnext.thailand.dialog_target(this.doctype, dialog);
		if (dialog.fields_dict.fetch_from_rd) {
			dialog.fields_dict.fetch_from_rd.df.click = () => erpnext.thailand.lookup_rd(target);
		}
		for (const field of ["tax_id", "branch_code"]) {
			if (dialog.fields_dict[field]) {
				dialog.fields_dict[field].df.onchange = () => erpnext.thailand.on_rd_key_change(target);
			}
		}
	};
}
