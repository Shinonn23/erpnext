// New Customer / Supplier from a Tax ID: "Fetch from Revenue Department" fills the legal name, type Company, Tax ID
// and branch code from RD (regional/thailand/utils.get_party_by_tax_id) and, on save, the server makes the RD
// address the primary Billing Address (regional/thailand/custom/rd_party.py). A party that already has the Tax ID
// (any branch, the same branch first) is offered instead. RD not knowing the Tax ID (an individual, a non-VAT party) or being down never
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

erpnext.thailand.apply_rd_party = function (frm, data) {
	const fields = erpnext.thailand.RD_PARTY[frm.doctype];
	if (data.existing && data.existing.length) {
		const first = data.existing[0];
		frappe.confirm(
			__(
				"{0} {1} ({2}) already has Tax ID {3} (branch {4}). Open it instead of creating a new one? Another branch is added there as an Address.",
				[__(frm.doctype), first.title, first.name, data.tax_id, first.branch_code || "-"]
			),
			() => frappe.set_route("Form", frm.doctype, first.name)
		);
		return "existing";
	}
	if (!data.found) {
		frappe.show_alert({
			message: __(
				"Tax ID {0} is not found at the Revenue Department (an individual or not VAT registered). Type the details by hand.",
				[data.tax_id]
			),
			indicator: "orange",
		}, 10);
		frm.doc.rd_fill_address = 0;
		return "not_found";
	}
	frm.set_value(fields.name, data.legal_name);
	frm.set_value(fields.type, "Company");
	frm.set_value("tax_id", data.tax_id);
	if (frm.fields_dict.branch_code) frm.set_value("branch_code", data.branch_code);
	// Not a stored field: tells the server to make the RD address after save (it asks RD again itself).
	frm.doc.rd_fill_address = 1;
	const a = data.address || {};
	frappe.show_alert({
		message: __("Filled from the Revenue Department. The address {0} becomes the primary address on save.", [
			[a.address_line1, a.city, a.county, a.state, a.pincode].filter(Boolean).join(" "),
		]),
		indicator: "green",
	}, 10);
	return "filled";
};

erpnext.thailand.fetch_rd_party = function (frm) {
	const tax_id = erpnext.thailand.rd_tax_id_digits(frm.doc.tax_id);
	if (!/^[0-9]{13}$/.test(tax_id)) {
		frappe.show_alert({ message: __("Type the 13-digit Tax ID first."), indicator: "orange" });
		return;
	}
	frm.__rd_last_lookup = `${tax_id}:${frm.doc.branch_code || ""}`;
	return frappe.call({
		method: "erpnext.regional.thailand.utils.get_party_by_tax_id",
		args: { doctype: frm.doctype, tax_id, branch: frm.doc.branch_code || "00000" },
		freeze: true,
		callback: (r) => {
			if (r.message) erpnext.thailand.apply_rd_party(frm, r.message);
		},
	});
};

for (const doctype of Object.keys(erpnext.thailand.RD_PARTY)) {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			if (!erpnext.thailand.show_rd_button(frm)) return;
			frm.add_custom_button(__(erpnext.thailand.RD_BUTTON), () =>
				erpnext.thailand.fetch_rd_party(frm)
			);
		},
		tax_id(frm) {
			// Once per Tax ID and branch when the Tax ID is complete; the button asks again.
			if (!erpnext.thailand.show_rd_button(frm)) return;
			const tax_id = erpnext.thailand.rd_tax_id_digits(frm.doc.tax_id);
			if (!/^[0-9]{13}$/.test(tax_id)) return;
			if (frm.__rd_last_lookup === `${tax_id}:${frm.doc.branch_code || ""}`) return;
			erpnext.thailand.fetch_rd_party(frm);
		},
	});
}
