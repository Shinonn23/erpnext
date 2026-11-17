// New Customer / Supplier from a Tax ID: "Fetch from Revenue Department" fills the legal name, type Company, Tax ID
// and branch code from RD (regional/thailand/utils.get_party_by_tax_id) and puts RD's address into the form's
// address fields at once (address line, ตำบล/แขวง = City, อำเภอ/เขต = County, จังหวัด = State, ZIP, Thailand); on
// save ERPNext's create_primary_address makes it the linked primary Address. A party that already has the Tax ID
// (a Customer at any branch, a Supplier at the same branch) is offered instead. RD not knowing the Tax ID (an individual, a non-VAT party) or being down never
// blocks saving: the user types by hand. Nothing here is mandatory.
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

erpnext.thailand.form_target = function (frm) {
	return {
		doctype: frm.doctype,
		doc: frm.doc,
		state: (frm.__rd_state = frm.__rd_state || {}),
		set_value: (field, value) => frm.set_value(field, value),
		has_field: (field) => Boolean(frm.fields_dict[field]),
	};
};

erpnext.thailand.apply_rd_party = function (frm, data) {
	return erpnext.thailand.apply_rd_result(erpnext.thailand.form_target(frm), data);
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

erpnext.thailand.fetch_rd_party = function (frm) {
	return erpnext.thailand.lookup_rd(erpnext.thailand.form_target(frm));
};

// Tax ID or branch changed: a complete new pair is looked up once (its RD record replaces the filled values).
erpnext.thailand.on_rd_key_change = function (target) {
	const key = erpnext.thailand.rd_key(target.doc);
	const tax_id = erpnext.thailand.rd_tax_id_digits(target.doc.tax_id);
	if (!/^[0-9]{13}$/.test(tax_id)) return;
	if (target.state.rd_last_lookup === key) return;
	erpnext.thailand.lookup_rd(target);
};

for (const doctype of Object.keys(erpnext.thailand.RD_PARTY)) {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (!erpnext.thailand.show_rd_button(frm)) return;
			frm.add_custom_button(__(erpnext.thailand.RD_BUTTON), () => erpnext.thailand.fetch_rd_party(frm));
		},
		tax_id(frm) {
			if (!erpnext.thailand.show_rd_button(frm)) return;
			erpnext.thailand.on_rd_key_change(erpnext.thailand.form_target(frm));
		},
		branch_code(frm) {
			if (!erpnext.thailand.show_rd_button(frm)) return;
			erpnext.thailand.on_rd_key_change(erpnext.thailand.form_target(frm));
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
