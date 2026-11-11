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
			[
				"Warranty Claim",
				{
					service_equipment: frm.doc.name,
					customer: frm.doc.customer,
					company: frm.doc.company,
					// the claim's Serial No links a Stock Serial No: the equipment's own, when it has one
					serial_no: frm.doc.serial_no_link,
				},
			],
		];
		for (const [doctype, values] of plain) {
			if (frappe.model.can_create(doctype)) {
				frm.add_custom_button(__(doctype), () => make(doctype, values), __("Create"));
			}
		}
		// A schedule lists its equipment as item rows: the first row is this equipment.
		if (frappe.model.can_create("Maintenance Schedule")) {
			frm.add_custom_button(
				__("Maintenance Schedule"),
				() =>
					frappe.new_doc(
						"Maintenance Schedule",
						{
							customer: frm.doc.customer,
							company: frm.doc.company,
							service_employee: frm.doc.technician,
						},
						(schedule) => {
							const row = frappe.model.add_child(
								schedule,
								"Maintenance Schedule Item",
								"items"
							);
							row.service_equipment = frm.doc.name;
						}
					),
				__("Create")
			);
		}
		// A visit lists its equipment as Purposes rows: the first row is this equipment (with its Item, if any).
		if (frappe.model.can_create("Maintenance Visit")) {
			frm.add_custom_button(
				__("Maintenance Visit"),
				() =>
					frappe.new_doc(
						"Maintenance Visit",
						{ customer: frm.doc.customer, company: frm.doc.company },
						(visit) => {
							const row = frappe.model.add_child(
								visit,
								"Maintenance Visit Purpose",
								"purposes"
							);
							row.service_equipment = frm.doc.name;
							if (frm.doc.item) row.item_code = frm.doc.item;
						}
					),
				__("Create")
			);
		}
	},
});
