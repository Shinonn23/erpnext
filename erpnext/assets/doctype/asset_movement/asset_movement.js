// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

function get_material_request_recipient_details(request) {
	const is_customer_demo = request.material_request_type === "Customer Demo";
	const is_borrow_request = request.material_request_type === "Customer Borrow / Loan";
	const recipient_type = is_customer_demo
		? "Customer"
		: is_borrow_request
			? request.loan_recipient_type || (request.customer ? "Customer" : null)
			: null;
	const recipient = is_customer_demo
		? request.customer
		: is_borrow_request
			? request.loan_recipient || (recipient_type === "Customer" ? request.customer : null)
			: null;
	const has_recipient_details = is_customer_demo || is_borrow_request;
	const contact = has_recipient_details
		? request.contact_person || request.loan_recipient_contact || null
		: null;
	const contact_display =
		has_recipient_details
			? request.customer_contact_display || request.loan_recipient_contact_display || null
			: null;
	const contact_email =
		has_recipient_details
			? request.customer_contact_email || request.loan_recipient_contact_email || null
			: null;
	const has_return_date = ["Customer Demo", "Customer Borrow / Loan"].includes(
		request.material_request_type
	);

	return {
		customer: recipient_type === "Customer" ? recipient : null,
		customer_contact: contact,
		customer_address: has_recipient_details ? request.recipient_address || null : null,
		customer_address_display: has_recipient_details
			? request.recipient_address_display || null
			: null,
		customer_contact_display: contact_display,
		customer_contact_email: contact_email,
		recipient_type,
		recipient,
		expected_return_date: has_return_date ? request.expected_return_date || null : null,
	};
}

async function set_material_request_purpose_lock(frm, material_request = null) {
	const reference_name = frm.doc.reference_name;
	if (frm.doc.reference_doctype !== "Material Request" || !reference_name) {
		frm.asset_transfer_purpose_locked = false;
		frm.set_df_property("purpose", "read_only", 0);
		return;
	}

	material_request = material_request || (await frappe.db.get_doc("Material Request", reference_name));
	if (frm.doc.reference_doctype !== "Material Request" || frm.doc.reference_name !== reference_name) {
		return;
	}

	const lock_purpose =
		material_request.docstatus === 1 && material_request.material_request_type === "Asset Transfer";
	frm.asset_transfer_purpose_locked = lock_purpose;
	frm.set_df_property("purpose", "read_only", lock_purpose ? 1 : 0);

	if (lock_purpose && frm.doc.purpose !== "Transfer") {
		await frm.set_value("purpose", "Transfer");
	}
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
		frm.set_query("target_department", "assets", (doc) => ({
			filters: { company: doc.company },
		}));
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

	refresh: async (frm) => {
		await set_material_request_purpose_lock(frm);
		frm.trigger("set_required_fields");
	},

	purpose: async (frm) => {
		if (frm.asset_transfer_purpose_locked && frm.doc.purpose !== "Transfer") {
			await frm.set_value("purpose", "Transfer");
			return;
		}
		for (const row of frm.doc.assets || []) {
			const values = {
				change_custodian: 0,
				change_department: 0,
				// a Transfer keeps the custodian unless the user names a new one (see the asset event)
				to_employee: frm.doc.purpose === "Transfer" ? row.from_employee || null : null,
				target_department: null,
			};
			frappe.model.set_value(row.doctype, row.name, values);
		}
		frm.trigger("set_required_fields");
	},

	reference_doctype: async (frm) => {
		await frm.set_value("reference_name", null);
		if (frm.doc.reference_doctype === "Material Request") {
			await frm.set_value("purpose", frm.doc.purpose === "Receipt" ? "Receipt" : "Transfer");
		}
		await set_material_request_purpose_lock(frm);
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
			await set_material_request_purpose_lock(frm, request);
			await frm.set_value({
				purpose:
					request.material_request_type === "Asset Transfer"
						? "Transfer"
						: frm.doc.purpose === "Receipt"
							? "Receipt"
							: "Transfer",
				...get_material_request_recipient_details(request),
			});
		} else {
			await set_material_request_purpose_lock(frm);
			await frm.set_value({
				customer: null,
				customer_contact: null,
				customer_address: null,
				customer_address_display: null,
				customer_contact_display: null,
				customer_contact_email: null,
				recipient_type: null,
				recipient: null,
				expected_return_date: null,
			});
		}
		frm.trigger("set_required_fields");
	},

	customer: (frm) => {
		frm.trigger("set_required_fields");
	},

	set_required_fields: (frm, cdt, cdn) => {
		const grid = frm.fields_dict.assets.grid;
		const has_material_request_recipient = Boolean(
			frm.doc.reference_doctype === "Material Request" &&
				frm.doc.reference_name &&
				frm.doc.recipient
		);
		frm.toggle_display(
			"address_and_contact_tab",
			has_material_request_recipient
		);
		const fields_by_purpose = {
			Transfer: ["source_location", "target_location", "to_employee"],
			Receipt: ["target_location", "to_employee"],
			Issue: ["to_employee"],
			"Transfer and Issue": [
				"source_location",
				"target_location",
				"from_employee",
				"to_employee",
			],
			Reassignment: [
				"change_custodian",
				"from_employee",
				"to_employee",
				"change_department",
				"source_department",
				"target_department",
			],
		};
		const visible_fields = new Set(fields_by_purpose[frm.doc.purpose] || []);
		const movement_fields = [
			"source_location",
			"target_location",
			"from_employee",
			"to_employee",
			"change_custodian",
			"change_department",
			"source_department",
			"target_department",
		];
		for (const fieldname of movement_fields) {
			grid.toggle_display(fieldname, visible_fields.has(fieldname));
		}
		grid.set_column_disp_in_list_view(["is_demo_asset", "demo_loan_status"], false);
		const hidden_columns = movement_fields.filter((fieldname) => !visible_fields.has(fieldname));
		const shown_columns = movement_fields.filter((fieldname) => visible_fields.has(fieldname));
		grid.set_column_disp_in_list_view(hidden_columns, false);
		grid.set_column_disp_in_list_view(shown_columns, true);
		for (const fieldname of ["request_item", "branch", "department", "location"]) {
			if (grid.get_docfield(fieldname)) {
				grid.toggle_display(fieldname, false);
			}
		}
		grid.update_docfield_property("request_item", "read_only", 1);
		let fieldnames_to_be_altered;
		if (frm.doc.purpose === "Transfer") {
			fieldnames_to_be_altered = {
				target_location: { read_only: 0, reqd: 1 },
				source_location: { read_only: 1, reqd: 1 },
				from_employee: { read_only: 1, reqd: 0 },
				to_employee: { read_only: 0, reqd: 0 },
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
		} else if (frm.doc.purpose === "Reassignment") {
			fieldnames_to_be_altered = {
				source_location: { read_only: 1, reqd: 0 },
				target_location: { read_only: 1, reqd: 0 },
				from_employee: { read_only: 1, reqd: 0 },
				to_employee: { read_only: 0, reqd: 0 },
				source_department: { read_only: 1, reqd: 0 },
				target_department: { read_only: 0, reqd: 0 },
				change_custodian: { read_only: 0, reqd: 0 },
				change_department: { read_only: 0, reqd: 0 },
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
		}
		frm.refresh_field("assets");
	},
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
					const values = {
						asset_name: asset_doc.asset_name,
						asset_serial_number: asset_doc.asset_serial_number,
						is_demo_asset: asset_doc.is_demo_asset,
						demo_loan_status: asset_doc.demo_loan_status,
						source_location: asset_doc.location || null,
						from_employee: asset_doc.custodian || null,
						source_department: asset_doc.department || null,
					};
					// The asset's custodian becomes the To Employee of its latest movement, so an empty one on a
					// Transfer would clear it on submit; the user may name a new custodian (required when none).
					if (frm.doc.purpose === "Transfer" && !locals[cdt][cdn].to_employee) {
						values.to_employee = asset_doc.custodian || null;
					}
					if (locals[cdt][cdn].asset !== asset_name) return;
					frappe.model.set_value(cdt, cdn, values);
				})
				.catch((err) => {
					console.log(err); // eslint-disable-line
				});
		} else {
			frappe.model.set_value(cdt, cdn, {
				asset_name: null,
				asset_serial_number: null,
				is_demo_asset: 0,
				demo_loan_status: null,
				source_location: null,
				from_employee: null,
				source_department: null,
			});
		}
	},
	change_custodian(_frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.change_custodian) {
			frappe.model.set_value(cdt, cdn, "to_employee", null);
		}
	},
	change_department(_frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.change_department) {
			frappe.model.set_value(cdt, cdn, "target_department", null);
		}
	},
});
