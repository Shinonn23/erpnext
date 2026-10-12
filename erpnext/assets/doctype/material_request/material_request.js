frappe.ui.form.on("Material Request", {
	setup(frm) {
		frm.__last_material_request_type = frm.doc.material_request_type;
		configure_material_request_item_columns(frm);
		frm.set_query("recipient_address", () => {
			const customer =
				frm.doc.material_request_type === "Customer Demo"
					? frm.doc.customer
					: frm.doc.loan_recipient_type === "Customer"
						? frm.doc.loan_recipient || frm.doc.customer
						: null;
			const link_doctype = customer ? "Customer" : frm.doc.loan_recipient_type;
			const link_name = customer || frm.doc.loan_recipient;
			if (!link_doctype || !link_name) return { filters: { name: ["=", ""] } };
			return {
				query: "frappe.contacts.doctype.address.address.address_query",
				filters: { link_doctype, link_name },
			};
		});
		set_stock_item_query(frm);
		frm.set_query("asset_item_filter", () => ({
			filters: { is_fixed_asset: 1, disabled: 0 },
		}));
		frm.set_query("asset", "items", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			const fixed_asset_purpose =
				["Asset Transfer", "Customer Demo"].includes(doc.material_request_type) ||
				(doc.material_request_type === "Customer Borrow / Loan" && doc.borrow_item_type === "Asset");
			if (!fixed_asset_purpose) return { filters: { name: ["=", ""] } };
			return {
				query: "erpnext.assets.doctype.material_request.material_request.asset_query",
				filters: {
					company: doc.company,
					material_request_type: doc.material_request_type,
					location: doc.material_request_type === "Asset Transfer" ? doc.asset_source_location : null,
					item_code: row.item_code || doc.asset_item_filter,
				},
			};
		});
		frm.set_query("warehouse", "items", (doc) => ({
			filters: { company: doc.company, is_group: 0 },
		}));
		frm.set_query("contact_person", () => ({
			query: "frappe.contacts.doctype.contact.contact.contact_query",
			filters: { link_doctype: "Customer", link_name: frm.doc.customer },
		}));
		frm.set_query("loan_recipient", () => {
			const filters = {};
			if (frm.doc.loan_recipient_type === "Employee") filters.company = frm.doc.company;
			return { filters };
		});
		frm.set_query("loan_recipient_contact", () => ({
			query: "frappe.contacts.doctype.contact.contact.contact_query",
			filters: {
				link_doctype: frm.doc.loan_recipient_type,
				link_name: frm.doc.loan_recipient,
			},
		}));
	},
	onload(frm) {
		set_stock_item_query(frm);
		configure_material_request_item_columns(frm);
	},
	material_request_type(frm) {
		const purpose = frm.doc.material_request_type;
		const previous = frm.__last_material_request_type;
		frm.__last_material_request_type = purpose;
		const values = {};
		if (previous && previous !== purpose) {
			values.items = [];
			values.recipient_address = null;
			values.recipient_address_display = null;
		}
		if (purpose !== "Customer Demo") {
			Object.assign(values, {
				customer: null,
				contact_person: null,
				customer_contact_display: null,
				customer_contact_email: null,
				customer_location: null,
				expected_return_date: null,
			});
		}
		if (purpose !== "Customer Borrow / Loan") {
			Object.assign(values, {
				borrow_item_type: null,
				loan_recipient_type: null,
				loan_recipient: null,
				loan_recipient_location: null,
				loan_recipient_contact: null,
				loan_recipient_contact_display: null,
				loan_recipient_contact_email: null,
			});
		} else if (previous !== purpose) {
			values.borrow_item_type = "Stock Item";
		}
		if (purpose !== "Material Transfer") values.set_from_warehouse = null;
		if (purpose !== "Asset Transfer") values.asset_source_location = null;
		if (purpose !== "Asset Transfer") values.asset_transfer_status = null;
		if (purpose !== "Customer Demo" && purpose !== "Customer Borrow / Loan") {
			values.request_progress_status = null;
			values.request_remarks = null;
		}
		if (!["Asset Transfer", "Customer Demo"].includes(purpose) &&
			!(purpose === "Customer Borrow / Loan" && frm.doc.borrow_item_type === "Asset")) {
			values.asset_item_filter = null;
		}
		return frm.set_value(values).then(() => {
			set_stock_item_query(frm);
			configure_material_request_item_columns(frm);
			frm.refresh_fields();
		});
	},
	borrow_item_type(frm) {
		if (frm.doc.material_request_type !== "Customer Borrow / Loan") return;
		return frm.set_value({ items: [], asset_item_filter: null }).then(() => {
			configure_material_request_item_columns(frm);
		});
	},
	schedule_date(frm) {
		configure_material_request_item_columns(frm);
	},
	get_assets_from_source_location(frm) {
		let asset_dialog;
		asset_dialog = new frappe.ui.form.MultiSelectDialog({
			doctype: "Asset",
			target: frm,
			size: "large",
			setters: {},
			get_query: () => {
				const filters = {
					company: frm.doc.company,
					material_request_type: frm.doc.material_request_type,
				};
				if (frm.doc.asset_source_location) filters.location = frm.doc.asset_source_location;
				if (frm.doc.asset_item_filter) filters.item_code = frm.doc.asset_item_filter;
				return {
					query: "erpnext.assets.doctype.material_request.material_request.asset_query",
					filters,
				};
			},
			columns: ["name", "asset_serial_number", "asset_name"],
			primary_action_label: __("Add Selected Assets"),
			action: () => {
				const assets = [...asset_dialog.selected_fields]
					.map((name) => asset_dialog.selected_items[name])
					.filter(Boolean);
				if (!assets.length) {
					frappe.msgprint(__("Select at least one Asset."));
					return;
				}

				asset_dialog.dialog.hide();
				const existing_assets = new Set((frm.doc.items || []).map((row) => row.asset).filter(Boolean));
				for (const asset of assets) {
					if (existing_assets.has(asset.name)) continue;
					frm.add_child("items", {
						item_code: asset.item_code,
						asset: asset.name,
						asset_name: asset.asset_name,
						asset_serial_number: asset.asset_serial_number,
						source_location: asset.location,
						target_location:
							frm.doc.material_request_type === "Customer Demo" ? frm.doc.customer_location : null,
						uom: asset.stock_uom,
						stock_uom: asset.stock_uom,
						conversion_factor: 1,
						qty: 1,
						stock_qty: 1,
					});
					existing_assets.add(asset.name);
				}
				frm.refresh_field("items");
			},
		});
	},
	async customer(frm) {
		await frm.set_value({
			recipient_address: null,
			recipient_address_display: null,
			contact_person: null,
			customer_contact_display: null,
			customer_contact_email: null,
		});
		if (!frm.doc.customer) return;
		const customer = frm.doc.customer;
		frappe.call({
			method: "erpnext.assets.doctype.material_request.material_request.get_default_request_address",
			args: { customer },
			callback: (r) => {
				if (r.message && frm.doc.customer === customer) frm.set_value("recipient_address", r.message);
			},
		});
	},
	loan_recipient_type(frm) {
		frm.set_value({
			loan_recipient: null,
			loan_recipient_location: null,
			recipient_address: null,
			recipient_address_display: null,
			loan_recipient_contact: null,
			loan_recipient_contact_display: null,
			loan_recipient_contact_email: null,
			customer: null,
		});
	},
	loan_recipient(frm) {
		if (frm.doc.loan_recipient_type === "Customer" && frm.doc.customer !== frm.doc.loan_recipient) {
			frm.set_value("customer", frm.doc.loan_recipient);
		}
	},
	loan_recipient_contact(frm) {
		if (!frm.doc.loan_recipient_contact) {
			frm.set_value({ loan_recipient_contact_display: null, loan_recipient_contact_email: null });
			return;
		}
		const contact = frm.doc.loan_recipient_contact;
		frappe.call({
			method: "frappe.contacts.doctype.contact.contact.get_contact_details",
			args: { contact },
			callback: (r) => {
				if (!r.message || frm.doc.loan_recipient_contact !== contact) return;
				const details = r.message;
				frm.set_value({
					loan_recipient_contact_display: [details.contact_display, details.contact_email, details.contact_phone || details.contact_mobile]
						.filter(Boolean).join("<br>"),
					loan_recipient_contact_email: details.contact_email,
				});
			},
		});
	},
	recipient_address(frm) {
		if (!frm.doc.recipient_address) {
			frm.set_value("recipient_address_display", null);
			return;
		}
		const address = frm.doc.recipient_address;
		frappe.call({
			method: "frappe.contacts.doctype.address.address.get_address_display",
			args: { address_dict: address },
			callback: (r) => {
				if (frm.doc.recipient_address === address)
					frm.set_value("recipient_address_display", r.message || "");
			},
		});
	},
	contact_person(frm) {
		if (!frm.doc.contact_person) {
			frm.set_value({ customer_contact_display: null, customer_contact_email: null });
			return;
		}
		const contact_person = frm.doc.contact_person;
		frappe.call({
			method: "frappe.contacts.doctype.contact.contact.get_contact_details",
			args: { contact: contact_person },
			callback: (r) => {
				if (!r.message || frm.doc.contact_person !== contact_person) return;
				const details = r.message;
				const display = [
					details.contact_display,
					details.contact_email,
					details.contact_phone || details.contact_mobile,
				].filter(Boolean).join("<br>");
				frm.set_value({
					customer_contact_display: display,
					customer_contact_email: details.contact_email,
				});
			},
		});
	},
	refresh(frm) {
		set_stock_item_query(frm);
		configure_material_request_item_columns(frm);
		if (frm.doc.docstatus === 0 && frm.doc.workflow_state === "Draft") {
			frm.page.set_indicator(__("Draft"), "orange");
		}
		const request_name = frm.doc.name;
		const is_special_request_type = ["Customer Demo", "Customer Borrow / Loan", "Asset Transfer"].includes(
			frm.doc.material_request_type
		);
		const remove_standard_status_actions = () => {
			if (frm.doc.name !== request_name || !is_special_request_type) return;
			frm.remove_custom_button(__("Stop"));
			frm.remove_custom_button(__("Re-open"));
		};
		remove_standard_status_actions();
		if (frm.doc.docstatus !== 1) return;
		const action_refresh_id = (frm.__material_request_action_refresh_id || 0) + 1;
		frm.__material_request_action_refresh_id = action_refresh_id;
		return Promise.resolve(frm.call("get_post_submit_actions")).then((r) => {
			if (
				frm.doc.name !== request_name ||
				frm.doc.docstatus !== 1 ||
				frm.__material_request_action_refresh_id !== action_refresh_id
			) return;
			remove_standard_status_actions();
			const actions = r.message || {};
			if (actions.stock_custody_loan || actions.asset_custody_loan) {
				const loan_type = actions.stock_custody_loan ? "Stock" : "Asset";
				frm.add_custom_button(__("Loan Order"), () =>
					frm.call("make_custody_loan", { loan_type }).then((r) => open_generated_doc(r.message)),
					__("Create")
				);
			}
			if (actions.asset_movement) {
				frm.add_custom_button(__("Asset Movement"), () =>
					frm.call("make_asset_movement").then((response) => open_generated_doc(response.message)), __("Create")
				);
			}
		}).finally(remove_standard_status_actions);
	},
});

frappe.ui.form.on("Material Request Item", {
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) return;
		const item_code = row.item_code;
		frappe.db.get_value("Item", item_code, ["item_code", "is_stock_item", "is_fixed_asset", "stock_uom"]).then(({ message }) => {
			if (!message || row.item_code !== item_code) return;
			if (row.asset && row.item_code !== message.item_code) {
				frappe.model.set_value(cdt, cdn, "asset", null);
			}
			const values = {
				item_type: message.is_stock_item ? "Stock Item" : message.is_fixed_asset ? "Fixed Asset" : "",
				uom: message.stock_uom,
			};
			if (row.asset) {
				values.stock_uom = message.stock_uom;
				values.conversion_factor = 1;
				values.stock_qty = row.qty || 1;
			}
			frappe.model.set_value(cdt, cdn, values);
		});
	},
	asset(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.asset) return;
		const asset_name = row.asset;
		return frappe.db.get_value(
			"Asset", asset_name, ["asset_name", "asset_serial_number", "item_code", "location"]
		).then(({ message }) => {
			if (!message || row.asset !== asset_name) return;
			return frappe.db.get_value("Item", message.item_code, "stock_uom").then(({ message: item }) => {
				if (!item || row.asset !== asset_name) return;
				return frappe.model.set_value(cdt, cdn, {
					asset_name: message.asset_name,
					asset_serial_number: message.asset_serial_number,
					item_code: message.item_code,
					source_location: message.location,
					target_location:
						frm.doc.material_request_type === "Customer Demo"
							? frm.doc.customer_location
							: frm.doc.material_request_type === "Customer Borrow / Loan"
								? frm.doc.loan_recipient_location
								: row.target_location,
					qty: 1,
					stock_qty: 1,
					item_type: "Fixed Asset",
					uom: item.stock_uom,
					stock_uom: item.stock_uom,
					conversion_factor: 1,
				});
			});
		});
	},
});

function open_generated_doc(doc) {
	if (!doc) return;
	frappe.model.with_doctype(doc.doctype, () => {
		const new_doc = frappe.model.sync(doc)[0];
		frappe.set_route("Form", new_doc.doctype, new_doc.name);
	});
}

function set_stock_item_query(frm) {
	frm.set_query("item_code", "items", (doc) => ({
		query: "erpnext.controllers.queries.item_query",
		filters:
			["Asset Transfer", "Customer Demo"].includes(doc.material_request_type) ||
			(doc.material_request_type === "Customer Borrow / Loan" && doc.borrow_item_type === "Asset")
				? { is_fixed_asset: 1, disabled: 0 }
				: doc.material_request_type === "Purchase"
					? { is_purchase_item: 1, disabled: 0 }
					: { is_stock_item: 1, disabled: 0 },
	}));
}

function configure_material_request_item_columns(frm) {
	const grid = frm.fields_dict.items?.grid;
	if (!grid) return;

	const purpose = frm.doc.material_request_type;
	const is_asset_request =
		["Asset Transfer", "Customer Demo"].includes(purpose) ||
		(purpose === "Customer Borrow / Loan" && frm.doc.borrow_item_type === "Asset");
	const visible_columns = new Set();

	if (is_asset_request) {
		visible_columns.add("asset");
		visible_columns.add("target_location");
		if (["Asset Transfer", "Customer Demo"].includes(purpose)) visible_columns.add("source_location");
	} else {
		visible_columns.add("qty");
		if (
			["Purchase", "Material Transfer"].includes(purpose) ||
			(purpose === "Customer Borrow / Loan" && frm.doc.borrow_item_type === "Stock Item")
		) {
			visible_columns.add("warehouse");
		}
		if (purpose === "Material Transfer") visible_columns.add("from_warehouse");
	}

	// The header date is copied to every row by ERPNext's standard handler.
	// Keep the row date visible only when each row may need its own value.
	if (!frm.doc.schedule_date) visible_columns.add("schedule_date");

	const managed_columns = [
		"asset",
		"source_location",
		"target_location",
		"qty",
		"warehouse",
		"from_warehouse",
		"schedule_date",
	];
	grid.set_column_disp_in_list_view(
		managed_columns.filter((fieldname) => visible_columns.has(fieldname)),
		true
	);
	grid.set_column_disp_in_list_view(
		managed_columns.filter((fieldname) => !visible_columns.has(fieldname)),
		false
	);
}
