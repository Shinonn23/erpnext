frappe.ui.form.on("Material Request", {
	setup(frm) {
		frm.set_query("customer_address", () => ({
			query: "frappe.contacts.doctype.address.address.address_query",
			filters: { link_doctype: "Customer", link_name: frm.doc.customer },
		}));
		frm.set_query("asset_transfer_asset", "items", () => ({
			filters: {
				company: frm.doc.company,
				docstatus: 1,
				status: ["not in", ["Draft", "Scrapped", "Sold", "Capitalized"]],
				demo_loan_status: ["not in", ["With Customer", "In Transit"]],
			},
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
		frm.set_query("loan_recipient_address", () => ({
			query: "frappe.contacts.doctype.address.address.address_query",
			filters: {
				link_doctype: frm.doc.loan_recipient_type,
				link_name: frm.doc.loan_recipient,
			},
		}));
	},
	async customer(frm) {
		await frm.set_value({
			customer_address: null,
			customer_address_display: null,
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
				if (r.message && frm.doc.customer === customer) frm.set_value("customer_address", r.message);
			},
		});
	},
	loan_recipient_type(frm) {
		frm.set_value({
			loan_recipient: null,
			loan_recipient_location: null,
			loan_recipient_address: null,
			loan_recipient_address_display: null,
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
	loan_recipient_address(frm) {
		if (!frm.doc.loan_recipient_address) {
			frm.set_value("loan_recipient_address_display", null);
			return;
		}
		const address = frm.doc.loan_recipient_address;
		frappe.call({
			method: "frappe.contacts.doctype.address.address.get_address_display",
			args: { address_dict: address },
			callback: (r) => {
				if (frm.doc.loan_recipient_address === address)
					frm.set_value("loan_recipient_address_display", r.message || "");
			},
		});
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
	customer_address(frm) {
		if (!frm.doc.customer_address) {
			frm.set_value("customer_address_display", null);
			return;
		}
		const address = frm.doc.customer_address;
		frappe.call({
			method: "frappe.contacts.doctype.address.address.get_address_display",
			args: { address_dict: address },
			callback: (r) => {
				if (frm.doc.customer_address === address)
					frm.set_value("customer_address_display", r.message || "");
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
		frm.call("get_post_submit_actions").then((r) => {
			if (
				frm.doc.name !== request_name ||
				frm.doc.docstatus !== 1 ||
				frm.__material_request_action_refresh_id !== action_refresh_id
			) return;
			remove_standard_status_actions();
			const actions = r.message || {};
			if (actions.release_hold) {
				frm.add_custom_button(__("Release Hold"), () => frm.call("release_hold").then(() => frm.reload_doc()));
				return;
			}
			if (actions.stock_custody_loan) {
				frm.add_custom_button(__("Stock Custody Loan"), () =>
					frm.call("make_custody_loan", { loan_type: "Stock" }).then((r) => open_generated_doc(r.message)), __("Create")
				);
			}
			if (actions.asset_custody_loan) {
				frm.add_custom_button(__("Asset Custody Loan"), () =>
					frm.call("make_custody_loan", { loan_type: "Asset" }).then((r) => open_generated_doc(r.message)), __("Create")
				);
			}
			if (actions.check_asset_availability) {
				frm.add_custom_button(__("Check Asset Availability"), () =>
					frm.call("check_asset_availability").then((response) => {
						frappe.msgprint({
							title: __("Asset Availability"),
							message: `<pre>${frappe.utils.escape_html(JSON.stringify(response.message || [], null, 2))}</pre>`,
						});
					})
				);
			}
			if (actions.asset_movement) {
				frm.add_custom_button(__("Asset Movement"), () =>
					frm.call("make_asset_movement").then((response) => open_generated_doc(response.message)), __("Create")
				);
			}
			if (actions.stock_entry_issue || actions.stock_entry_return) {
				frm.add_custom_button(__("Stock Entry"), () =>
					show_stock_entry_direction_dialog(frm, actions.stock_entry_issue, actions.stock_entry_return), __("Create")
				);
			}
			if (actions.pick_list) {
				frm.add_custom_button(__("Pick List"), () =>
					frm.call("make_pick_list").then((response) => open_generated_doc(response.message)), __("Create")
				);
			}
			if (actions.return_assets) {
				frm.add_custom_button(__("Return Assets"), () => {
					const dialog = new frappe.ui.Dialog({
						title: __("Return Assets"),
						fields: [{ fieldname: "requires_shipment", fieldtype: "Check", label: __("Ship via Shipment"), default: 1 }],
						primary_action_label: __("Create"),
						primary_action(values) {
							dialog.hide();
							frm.call("make_asset_receipt", { requires_shipment: values.requires_shipment }).then((response) => open_generated_doc(response.message));
						},
					});
					dialog.show();
				}, __("Return"));
			}
			if (actions.hold_for_asset_return) {
				frm.add_custom_button(__("Hold for Asset Return"), () =>
					frm.call("hold_for_asset_return").then(() => frm.reload_doc())
				);
			}
		}).finally(remove_standard_status_actions);
	},
});

function show_stock_entry_direction_dialog(frm, can_issue, can_return) {
	const issue_label = __("Issue to Customer Loan Warehouse");
	const return_label = __("Return to Source Warehouse");
	const options = [can_issue ? issue_label : null, can_return ? return_label : null].filter(Boolean);
	const dialog = new frappe.ui.Dialog({
		title: __("Stock Entry"),
		fields: [{
			fieldname: "transfer_direction",
			fieldtype: "Select",
			label: __("Transfer Direction"),
			options: options.join("\n"),
			reqd: 1,
			default: options.length === 1 ? options[0] : null,
		}],
		primary_action_label: __("Create"),
		primary_action(values) {
			dialog.hide();
			frm.call("make_stock_entry", {
				return_stock: values.transfer_direction === return_label ? 1 : 0,
			}).then((r) => open_generated_doc(r.message));
		},
	});
	dialog.show();
}

frappe.ui.form.on("Material Request Item", {
	asset_transfer_asset(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.asset_transfer_asset) return;
		frappe.db.get_value("Asset", row.asset_transfer_asset, "item_code").then(({ message }) => {
			if (message?.item_code) frappe.model.set_value(cdt, cdn, "item_code", message.item_code);
		});
	},
	item_code(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.item_code) return;
		frappe.db.get_value("Item", row.item_code, ["is_stock_item", "is_fixed_asset", "stock_uom"]).then(({ message }) => {
			if (!message) return;
			frappe.model.set_value(cdt, cdn, {
				item_type: message.is_stock_item ? "Stock Item" : message.is_fixed_asset ? "Fixed Asset" : "",
				uom: message.stock_uom,
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
