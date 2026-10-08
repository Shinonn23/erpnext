// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

function make_shipment_from_stock_asset_transaction(frm) {
	frappe.call({
		method: "erpnext.stock.doctype.shipment.shipment.make_shipment_from_document",
		args: { source_doctype: frm.doctype, source_name: frm.doc.name },
		callback: (r) => {
			if (!r.message) return;
			const doclist = frappe.model.sync(r.message);
			frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
		},
	});
}

frappe.ui.form.on("Asset Movement", {
	setup: (frm) => {
		frm.set_query("to_employee", "assets", (doc) => {
			return {
				filters: {
					company: doc.company,
				},
			};
		});
		frm.set_query("from_employee", "assets", (doc) => {
			return {
				filters: {
					company: doc.company,
				},
			};
		});
		frm.set_query("reference_name", (doc) => {
			return {
				filters: {
					company: doc.company,
					docstatus: 1,
				},
			};
		});
		frm.set_query("reference_doctype", () => {
			return {
				filters: {
					name: ["in", ["Purchase Receipt", "Purchase Invoice", "Material Request"]],
				},
			};
		});
		frm.set_query("request_item", "assets", (doc) => ({
			filters: {
				parent: doc.reference_doctype === "Material Request" ? doc.reference_name || "" : "",
			},
		}));
		frm.set_query("asset", "assets", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				filters: {
					company: doc.company,
					item_code: row.asset_item_code || ["!=", ""],
					...(row.demo_asset_required ? { is_demo_asset: 1 } : {}),
					status: ["not in", ["Draft"]],
				},
			};
		});
	},

	refresh: (frm) => {
		frm.trigger("set_required_fields");
		if (
			(frm.doc.docstatus === 0 && !frm.is_new() && frm.doc.requires_shipment) ||
			(frm.doc.docstatus === 1 && !frm.doc.shipment)
		) {
			if (!frappe.model.can_create("Shipment")) return;
			frm.add_custom_button(
				__("Shipment"),
				() => make_shipment_from_stock_asset_transaction(frm),
				__("Create")
			);
		}
	},

	purpose: (frm) => {
		frm.trigger("set_required_fields");
	},

	reference_doctype: async (frm) => {
		await frm.set_value("reference_name", null);
		if (frm.doc.reference_doctype === "Material Request") {
			await frm.set_value("purpose", frm.doc.purpose === "Receipt" ? "Receipt" : "Transfer");
		}
		frm.trigger("set_required_fields");
	},

	reference_name: async (frm) => {
		if (frm.doc.reference_doctype === "Material Request" && frm.doc.reference_name) {
			const reference_name = frm.doc.reference_name;
			const request = await frappe.db.get_doc("Material Request", reference_name);
			if (
				frm.doc.reference_doctype !== "Material Request" ||
				frm.doc.reference_name !== reference_name
			)
				return;
			await frm.set_value({
				purpose: frm.doc.purpose === "Receipt" ? "Receipt" : "Transfer",
				customer: request.customer,
				customer_contact: request.contact_person,
				customer_address: request.customer_address,
				customer_address_display: request.customer_address_display,
				customer_contact_display: request.customer_contact_display,
				customer_contact_email: request.customer_contact_email,
				expected_return_date: request.expected_return_date,
			});
		} else {
			await frm.set_value({
				customer: null,
				customer_contact: null,
				customer_address: null,
				customer_address_display: null,
				customer_contact_display: null,
				customer_contact_email: null,
				expected_return_date: null,
			});
		}
		frm.trigger("set_required_fields");
	},

	customer: (frm) => {
		frm.trigger("set_required_fields");
	},

	set_required_fields: (frm, cdt, cdn) => {
		const shipment = Boolean(frm.doc.requires_shipment && ["Transfer", "Receipt"].includes(frm.doc.purpose));
		frm.toggle_display("shipment", Boolean(frm.doc.requires_shipment));
		frm.fields_dict.assets.grid.toggle_display("shipment_destination_location", shipment);
		frm.fields_dict.assets.grid.toggle_display("target_location", !shipment);
		frm.fields_dict.assets.grid.update_docfield_property("shipment_destination_location", "reqd", shipment ? 1 : 0);
		let fieldnames_to_be_altered;
		if (frm.doc.purpose === "Transfer") {
			fieldnames_to_be_altered = {
				target_location: { read_only: 0, reqd: 1 },
				source_location: { read_only: 1, reqd: 1 },
				from_employee: { read_only: 1, reqd: 0 },
				to_employee: { read_only: 1, reqd: 0 },
			};
		} else if (frm.doc.purpose === "Receipt") {
			fieldnames_to_be_altered = {
				target_location: { read_only: 0, reqd: 1 },
				source_location: { read_only: 1, reqd: 0 },
				from_employee: { read_only: 1, reqd: 0 },
				to_employee: { read_only: 0, reqd: 0 },
			};
		} else if (frm.doc.purpose === "Issue") {
			fieldnames_to_be_altered = {
				target_location: { read_only: 1, reqd: 0 },
				source_location: { read_only: 1, reqd: 0 },
				from_employee: { read_only: 1, reqd: 0 },
				to_employee: { read_only: 0, reqd: 1 },
			};
		} else if (frm.doc.purpose === "Transfer and Issue") {
			fieldnames_to_be_altered = {
				target_location: { read_only: 0, reqd: 1 },
				source_location: { read_only: 0, reqd: 1 },
				from_employee: { read_only: 0, reqd: 1 },
				to_employee: { read_only: 0, reqd: 1 },
			};
		}
		if (fieldnames_to_be_altered) {
			Object.keys(fieldnames_to_be_altered).forEach((fieldname) => {
				let property_to_be_altered = fieldnames_to_be_altered[fieldname];
				Object.keys(property_to_be_altered).forEach((property) => {
					let value = property_to_be_altered[property];
					frm.fields_dict["assets"].grid.update_docfield_property(fieldname, property, value);
				});
			});
			frm.refresh_field("assets");
		}
		if (shipment) {
			frm.fields_dict.assets.grid.update_docfield_property("target_location", "read_only", 1);
			frm.fields_dict.assets.grid.update_docfield_property("target_location", "reqd", 0);
		}
	},

	requires_shipment: (frm) => frm.trigger("set_required_fields"),
});

frappe.ui.form.on("Asset Movement Item", {
	request_item(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.request_item) return;
		frappe.db
			.get_value("Material Request Item", row.request_item, ["item_code", "item_type", "parent"])
			.then(({ message }) => {
				if (!message) return;
				return frappe.db.get_doc("Material Request", message.parent).then((request) => {
					return frappe.db
						.get_value("Item", message.item_code, "demo_asset_item_code")
						.then(({ message: mapping }) => {
							const asset_item_code =
								message.item_type === "Stock Item"
									? mapping?.demo_asset_item_code
									: message.item_code;
							frappe.model.set_value(cdt, cdn, {
								asset_item_code,
								demo_asset_required: request.material_request_type === "Customer Demo" ? 1 : 0,
							});
						});
				});
			});
	},
	asset: function (frm, cdt, cdn) {
		// on manual entry of an asset auto sets their source location / employee
		const asset_name = locals[cdt][cdn].asset;
		if (asset_name) {
			frappe.db
				.get_doc("Asset", asset_name)
				.then((asset_doc) => {
					if (asset_doc.location)
						frappe.model.set_value(cdt, cdn, "source_location", asset_doc.location);
					if (asset_doc.custodian)
						frappe.model.set_value(cdt, cdn, "from_employee", asset_doc.custodian);
				})
				.catch((err) => {
					console.log(err); // eslint-disable-line
				});
		}
	},
});
