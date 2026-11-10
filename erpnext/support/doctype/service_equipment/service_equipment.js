// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Service Equipment", {
	refresh(frm) {
		if (frm.is_new()) return;
		const make = (doctype, values) => frappe.new_doc(doctype, values);
		if (frappe.model.can_create("Asset Movement")) {
			frm.add_custom_button(
				__("Asset Movement"),
				() =>
					frappe.model.with_doctype("Asset Movement", () => {
						const doc = frappe.model.get_new_doc("Asset Movement");
						doc.company = frm.doc.company;
						doc.purpose = "Transfer";
						const row = frappe.model.add_child(doc, "assets");
						row.service_equipment = frm.doc.name;
						row.equipment_name = frm.doc.equipment_name;
						row.source_location = frm.doc.location;
						row.from_employee = frm.doc.custodian;
						frappe.set_route("Form", "Asset Movement", doc.name);
					}),
				__("Create")
			);
		}
		const plain = [
			["Asset Maintenance", { service_equipment: frm.doc.name, company: frm.doc.company }],
			["Asset Repair", { service_equipment: frm.doc.name, company: frm.doc.company }],
			["Issue", { service_equipment: frm.doc.name, customer: frm.doc.customer }],
		];
		for (const [doctype, values] of plain) {
			if (frappe.model.can_create(doctype)) {
				frm.add_custom_button(__(doctype), () => make(doctype, values), __("Create"));
			}
		}
	},
});
