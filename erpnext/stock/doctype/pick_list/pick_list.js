// Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

class PickListBarcodeScanner extends erpnext.utils.BarcodeScanner {
	update_table(data) {
		this.serial_scan = data.serial_no ? data : null;
		const rows = this.frm.doc.locations || [];
		if (this.serial_scan) {
			if (rows.some((row) => (row.serial_no || "").split("\n").map((s) => s.trim()).includes(data.serial_no))) {
				this.show_alert(__("Serial No {0} is already added", [data.serial_no]), "orange");
				return Promise.reject();
			}
		}
		const row = this.get_row_to_modify_on_scan(data.item_code, data.batch_no, data.uom, data.barcode, data.default_warehouse);
		if (!row?.item_code && rows.some((r) => r.item_code === data.item_code)) {
			this.show_alert(__("No available row for item {0}; check the picked quantity and location.", [data.item_code]), "red");
			this.clean_up();
			return Promise.reject();
		}
		return super.update_table(data);
	}

	get_row_to_modify_on_scan(item_code, batch_no, ...args) {
		if (!this.serial_scan) return super.get_row_to_modify_on_scan(item_code, batch_no, ...args);
		const data = this.serial_scan;
		const rows = this.frm.doc.locations || [];
		const eligible = rows.filter((row) => {
			const demand = this.demand_ref_fields.some((f) => row[f]);
			return row.item_code === item_code && !row.serial_and_batch_bundle && !row.has_item_scanned &&
				(!demand || flt(row.picked_qty) < flt(row.qty)) &&
				(!row.batch_no || row.batch_no === batch_no) &&
				(!row.picked_qty || (row.warehouse === data.serial_warehouse &&
					(row.storage_location || null) === (data.serial_storage_location || null)));
		});
		return eligible.find((r) => r.warehouse === data.serial_warehouse &&
			(r.storage_location || null) === (data.serial_storage_location || null)) || eligible[0];
	}

	async set_serial_no(row, serial_no) {
		if (serial_no && this.serial_scan) {
			await frappe.model.set_value(row.doctype, row.name, "warehouse", this.serial_scan.serial_warehouse);
			await frappe.model.set_value(row.doctype, row.name, "storage_location", this.serial_scan.serial_storage_location);
		}
		return super.set_serial_no(row, serial_no);
	}

	is_duplicate_serial_no(row, serial_no) {
		return serial_no ? (row.serial_no || "").split("\n").map((s) => s.trim()).includes(serial_no) : false;
	}
}

frappe.ui.form.on("Pick List", {
	after_save(frm) {
		setTimeout(() => {
			// Added to fix the issue of locations table not getting updated after save
			frm.reload_doc();
		}, 500);
	},

	set_warehouse_query: function (frm, fieldname, parentfield = null) {
		const query = () => {
			let filters = { company: frm.doc.company };

			frm.doc.consider_rejected_warehouses ? null : (filters.is_rejected_warehouse = 0);

			return { filters };
		};

		if (parentfield) {
			frm.set_query(fieldname, parentfield, query);
		} else {
			frm.set_query(fieldname, query);
		}
	},

	setup: (frm) => {
		frm.ignore_doctypes_on_cancel_all = ["Serial and Batch Bundle"];

		frm.set_indicator_formatter("item_code", function (doc) {
			return doc.stock_qty === 0 ? "red" : "green";
		});

		frm.custom_make_buttons = {
			"Delivery Note": "Delivery Note",
			"Stock Entry": "Stock Entry",
		};

		frm.events.set_warehouse_query(frm, "warehouse", "locations");
		frm.events.set_warehouse_query(frm, "parent_warehouse");

		frm.set_query("work_order", () => {
			return {
				query: "erpnext.stock.doctype.pick_list.pick_list.get_pending_work_orders",
				filters: {
					company: frm.doc.company,
				},
			};
		});

		frm.set_query("material_request", () => {
			return {
				filters: {
					material_request_type: ["=", frm.doc.purpose],
				},
			};
		});

		frm.set_query("item_code", "locations", () => {
			return erpnext.queries.item({ is_stock_item: 1 });
		});

		frm.set_query("storage_location", "locations", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return erpnext.queries.storage_location(row.warehouse, row.item_code);
		});

		frm.set_query("batch_no", "locations", (frm, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				query: "erpnext.controllers.queries.get_batch_no",
				filters: {
					item_code: row.item_code,
					warehouse: row.warehouse,
					storage_location: row.storage_location,
				},
			};
		});

		frm.set_query("serial_and_batch_bundle", "locations", (doc, cdt, cdn) => {
			let row = locals[cdt][cdn];
			return {
				filters: {
					item_code: row.item_code,
					voucher_type: doc.doctype,
					voucher_no: ["in", [doc.name, ""]],
					is_cancelled: 0,
				},
			};
		});
	},
	set_item_locations: (frm, save) => {
		if (!(frm.doc.locations && frm.doc.locations.length)) {
			frappe.msgprint(__("Add items in the Item Locations table"));
		} else {
			frappe.call({
				method: "set_item_locations",
				doc: frm.doc,
				args: {
					save: save,
				},
				freeze: 1,
				freeze_message: __("Setting Item Locations..."),
				callback(r) {
					refresh_field("locations");
				},
			});
		}
	},

	pick_manually: (frm) => {
		frm.trigger("update_warehouse_property");
	},

	update_warehouse_property: (frm) => {
		frm.fields_dict.locations.grid.update_docfield_property(
			"warehouse",
			"read_only",
			!frm.doc.pick_manually
		);
	},

	get_item_locations: (frm) => {
		// The Auto Pick button: one press fills every row (Pick List.keep_manual_picking: nothing picks on save)
		frm.events.set_item_locations(frm, false);
	},
	refresh: (frm) => {
		frm.trigger("add_get_items_button");
		frm.trigger("update_warehouse_property");
		erpnext.toggle_serial_batch_fields(frm);

		if (frm.doc.docstatus === 1) {
			const status_completed = frm.doc.status === "Completed";

			if (!status_completed) {
				// "Update Current Stock" re-ran the automatic pick on a submitted list: the Auto Pick of the draft
				// is the one place it runs.

				if (frm.doc.purpose === "Delivery") {
					if (frm.doc.custody_loan) {
						frm.add_custom_button(__("Custody Loan"), () =>
							frappe.set_route("Form", "Custody Loan", frm.doc.custody_loan)
						);
					} else {
						frm.add_custom_button(
							__("Delivery Note"),
							() => frm.events.create_delivery(frm, "Delivery Note"),
							__("Create")
						);
						frm.add_custom_button(
							__("Sales Invoice"),
							() => frm.events.create_delivery(frm, "Sales Invoice"),
							__("Create")
						);
					}
				} else if (!["Customer Demo - Asset Conversion", "Asset Conversion"].includes(frm.doc.pick_list_type)) {
					frm.add_custom_button(
						__("Stock Entry"),
						() => frm.trigger("create_stock_entry"),
						__("Create")
					);
				}
			}

			if (frm.doc.purpose === "Delivery" && frm.doc.status === "Open") {
				if (frm.doc.__onload && frm.doc.__onload.has_unreserved_stock) {
					frm.add_custom_button(
						__("Reserve"),
						() => frm.events.create_stock_reservation_entries(frm),
						__("Stock Reservation"),
						"Stock Reservation Entry"
					);
				}

				if (frm.doc.__onload && frm.doc.__onload.has_reserved_stock) {
					frm.add_custom_button(
						__("Unreserve"),
						() => {
							frappe.confirm(
								__(
									"The reserved stock will be released. Are you certain you wish to proceed?"
								),
								() => frm.events.cancel_stock_reservation_entries(frm)
							);
						},
						__("Stock Reservation")
					);
					frm.add_custom_button(
						__("Reserved Stock"),
						() => frm.events.show_reserved_stock(frm),
						__("Stock Reservation")
					);
				}
			}
		}

		let sbb_field = frm.get_docfield("locations", "serial_and_batch_bundle");
		if (sbb_field) {
			sbb_field.get_route_options_for_new_doc = (row) => {
				return {
					item_code: row.doc.item_code,
					warehouse: row.doc.warehouse,
					voucher_type: frm.doc.doctype,
				};
			};
		}
	},
	work_order: (frm) => {
		frappe.db
			.get_value("Work Order", frm.doc.work_order, ["qty", "material_transferred_for_manufacturing"])
			.then((data) => {
				let qty_data = data.message;
				let max = qty_data.qty - qty_data.material_transferred_for_manufacturing;
				frappe.prompt(
					{
						fieldtype: "Float",
						label: __("Qty of Finished Goods Item"),
						fieldname: "qty",
						description: __("Max: {0}", [max]),
						default: max,
					},
					(data) => {
						frm.set_value("for_qty", data.qty);
						if (data.qty > max) {
							frappe.msgprint(__("Quantity must not be more than {0}", [max]));
							return;
						}
						frm.clear_table("locations");
						erpnext.utils.map_current_doc({
							method: "erpnext.manufacturing.doctype.work_order.work_order.create_pick_list",
							target: frm,
							source_name: frm.doc.work_order,
						});
					},
					__("Select Quantity"),
					__("Get Items")
				);
			});
	},
	material_request: (frm) => {
		erpnext.utils.map_current_doc({
			method: "erpnext.stock.doctype.material_request.material_request.create_pick_list",
			target: frm,
			source_name: frm.doc.material_request,
		});
	},
	purpose: (frm) => {
		frm.clear_table("locations");
		frm.trigger("add_get_items_button");
	},
	create_delivery(frm, doctype) {
		frappe.model.open_mapped_doc({
			method: "erpnext.stock.doctype.pick_list.pick_list.create_delivery",
			args: {
				target: doctype,
			},
			frm: frm,
		});
	},
	create_stock_entry: (frm) => {
		frappe
			.xcall("erpnext.stock.doctype.pick_list.pick_list.create_stock_entry", {
				pick_list: frm.doc,
			})
			.then((stock_entry) => {
				frappe.model.sync(stock_entry);
				frappe.set_route("Form", "Stock Entry", stock_entry.name);
			});
	},
	update_pick_list_stock: (frm) => {
		frm.events.set_item_locations(frm, true);
	},
	add_get_items_button: (frm) => {
		let purpose = frm.doc.purpose;
		if (purpose != "Delivery" || frm.doc.docstatus !== 0) return;
		let get_query_filters = {
			docstatus: 1,
			per_delivered: ["<", 100],
			status: ["!=", ""],
			customer: frm.doc.customer,
		};
		frm.get_items_btn = frm.add_custom_button(__("Get Items"), () => {
			erpnext.utils.map_current_doc({
				method: "erpnext.selling.doctype.sales_order.sales_order.create_pick_list",
				source_doctype: "Sales Order",
				target: frm,
				setters: {
					company: frm.doc.company,
					customer: frm.doc.customer,
				},
				date_field: "transaction_date",
				get_query_filters: get_query_filters,
			});
		});
	},
	scan_barcode: (frm) => {
		const opts = {
			frm,
			items_table_name: "locations",
			qty_field: "picked_qty",
			max_qty_field: "qty",
			demand_ref_fields: ["sales_order_item", "material_request_item", "product_bundle_item"],
			dont_allow_new_row: !frm.doc.pick_manually,
			prompt_qty: frm.doc.prompt_qty,
			scan_api: "erpnext.stock.doctype.pick_list.pick_list.scan_pick_list_barcode",
		};
		const barcode_scanner = new PickListBarcodeScanner(opts);
		barcode_scanner.process_scan();
	},
	create_stock_reservation_entries: (frm) => {
		frappe.call({
			doc: frm.doc,
			method: "create_stock_reservation_entries",
			args: {
				notify: true,
			},
			freeze: true,
			freeze_message: __("Reserving Stock..."),
			callback: (r) => {
				frm.doc.__onload.has_unreserved_stock = false;
				frm.reload_doc();
			},
		});
	},
	cancel_stock_reservation_entries: (frm) => {
		frappe.call({
			doc: frm.doc,
			method: "cancel_stock_reservation_entries",
			args: {
				notify: true,
			},
			freeze: true,
			freeze_message: __("Unreserving Stock..."),
			callback: (r) => {
				frm.doc.__onload.has_reserved_stock = false;
				frm.reload_doc();
			},
		});
	},
	show_reserved_stock(frm) {
		// Get the latest modified date from the locations table.
		var to_date = moment(
			new Date(Math.max(...frm.doc.locations.map((e) => new Date(e.modified))))
		).format("YYYY-MM-DD");

		frappe.route_options = {
			company: frm.doc.company,
			from_date: moment(frm.doc.creation).format("YYYY-MM-DD"),
			to_date: to_date,
			voucher_type: "Sales Order",
			from_voucher_type: "Pick List",
			from_voucher_no: frm.doc.name,
		};
		frappe.set_route("query-report", "Reserved Stock");
	},
});

frappe.ui.form.on("Pick List Item", {
	item_code: (frm, cdt, cdn) => {
		let row = frappe.get_doc(cdt, cdn);
		if (row.item_code) {
			get_item_details(row.item_code, row.uom, row.warehouse, frm.doc.company, row.storage_location).then((data) => {
				frappe.model.set_value(cdt, cdn, "uom", data.stock_uom);
				frappe.model.set_value(cdt, cdn, "stock_uom", data.stock_uom);
				frappe.model.set_value(cdt, cdn, "conversion_factor", 1);
				frappe.model.set_value(cdt, cdn, "actual_qty", data.actual_qty);
				frappe.model.set_value(cdt, cdn, "company_total_stock", data.company_total_stock);
			});
		}
	},

	uom: (frm, cdt, cdn) => {
		let row = frappe.get_doc(cdt, cdn);
		if (row.uom) {
			get_item_details(row.item_code, row.uom).then((data) => {
				frappe.model.set_value(cdt, cdn, "conversion_factor", data.conversion_factor);
			});
		}
	},

	warehouse: (frm, cdt, cdn) => {
		const row = frappe.get_doc(cdt, cdn);
		if (!row.item_code || !row.warehouse) return;
		if (row.storage_location) {
			const location = row.storage_location;
			frappe.db.get_value("Storage Location", location, "warehouse").then((r) => {
				if (row.storage_location === location && r.message?.warehouse !== row.warehouse) {
					frappe.model.set_value(cdt, cdn, "storage_location", null);
				}
			});
		}
		get_item_details(row.item_code, row.uom, row.warehouse, frm.doc.company, row.storage_location).then((data) => {
			frappe.model.set_value(cdt, cdn, "actual_qty", data.actual_qty);
			frappe.model.set_value(cdt, cdn, "company_total_stock", data.company_total_stock);
		});
	},

	storage_location: (frm, cdt, cdn) => {
		const row = frappe.get_doc(cdt, cdn);
		if (row.serial_and_batch_bundle) {
			frappe.model.set_value(cdt, cdn, "serial_and_batch_bundle", null);
		}
		if (row.serial_no) {
			frappe.model.set_value(cdt, cdn, "serial_no", null);
		}
		if (row.batch_no) {
			frappe.model.set_value(cdt, cdn, "batch_no", null);
		}
		if (!row.item_code || !row.warehouse) return;
		get_item_details(row.item_code, row.uom, row.warehouse, frm.doc.company, row.storage_location).then((data) => {
			frappe.model.set_value(cdt, cdn, "actual_qty", data.actual_qty);
		});
	},

	qty: (frm, cdt, cdn) => {
		let row = frappe.get_doc(cdt, cdn);
		frappe.model.set_value(cdt, cdn, "stock_qty", row.qty * row.conversion_factor);
	},

	conversion_factor: (frm, cdt, cdn) => {
		let row = frappe.get_doc(cdt, cdn);
		frappe.model.set_value(cdt, cdn, "stock_qty", row.qty * row.conversion_factor);
	},

	pick_serial_and_batch(frm, cdt, cdn) {
		let item = locals[cdt][cdn];
		let path = "assets/erpnext/js/utils/serial_no_batch_selector.js";

		frappe.db.get_value("Item", item.item_code, ["has_batch_no", "has_serial_no"]).then((r) => {
			if (r.message && (r.message.has_batch_no || r.message.has_serial_no)) {
				item.has_serial_no = r.message.has_serial_no;
				item.has_batch_no = r.message.has_batch_no;
				item.type_of_transaction = item.qty > 0 ? "Outward" : "Inward";

				item.title = item.has_serial_no ? __("Select Serial No") : __("Select Batch No");

				if (item.has_serial_no && item.has_batch_no) {
					item.title = __("Select Serial and Batch");
				}

				new erpnext.SerialBatchPackageSelector(frm, item, (r) => {
					if (r) {
						let qty = Math.abs(r.total_qty);
						frappe.model.set_value(item.doctype, item.name, {
							serial_and_batch_bundle: r.name,
							use_serial_batch_fields: 0,
							qty: qty / flt(item.conversion_factor || 1, precision("conversion_factor", item)),
						});
					}
				});
			}
		});
	},
});

function get_item_details(item_code, uom = null, warehouse = null, company = null, storage_location = null) {
	if (item_code) {
		return frappe.xcall("erpnext.stock.doctype.pick_list.pick_list.get_item_details", {
			item_code,
			uom,
			warehouse,
			company,
			storage_location,
		});
	}
}
