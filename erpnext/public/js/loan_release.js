(() => {
	if (frappe.__connect_diagnostics_loan_release) return;
	frappe.__connect_diagnostics_loan_release = true;
	const api = "erpnext.borrowing.loan_release.";
	const loan_types = ["Customer Demo", "Borrow", "Customer Borrow"];

	function row_fields(request, row) {
		const units = Math.max(Math.round(Number(row.qty) || 1), 1);
		const label = `${row.idx}: ${row.item_code} - ${row.item_name || ""}`;
		const fields = [
			{
				fieldname: `section_${row.row}`,
				fieldtype: "Section Break",
				label,
				depends_on: row.decided ? "eval:doc.change" : "",
			},
		];
		for (let unit = 0; unit < units; unit++) {
			fields.push({
				fieldname: `${row.row}__${unit}`,
				fieldtype: "Link",
				options: "Asset",
				label: units > 1 ? __("Asset {0} of {1}", [unit + 1, units]) : __("Asset"),
				reqd: row.decided ? 0 : 1,
				default: unit === 0 ? row.asset || "" : "",
				depends_on: row.decided ? "eval:doc.change" : "",
				get_query: () => ({
					filters: {
						company: request.company,
						item_code: row.asset_item_code || row.item_code,
						...(row.conversion_request
							? {
									material_request: row.conversion_request,
									stock_to_asset_conversion_item: [
										"in",
										row.conversion_items || [],
									],
							  }
							: {}),
						docstatus: 1,
					},
				}),
			});
		}
		return fields;
	}

	async function decide(frm) {
		const { message: release } = await frappe.call({
			method: api + "get_release",
			args: { material_request: frm.doc.name },
		});
		const rows = (release.rows || []).map((row) => ({ ...row }));
		const undecided = rows.filter((row) => !row.decided);
		const decided = rows.filter((row) => row.decided);
		const request = { company: frm.doc.company, demo: release.demo };
		const may_change = !release.loans.length && decided.length;
		const fields = [];
		if (may_change) {
			fields.push({
				fieldname: "change",
				fieldtype: "Check",
				label: __("Change an earlier decision"),
			});
		}
		for (const row of [...undecided, ...(may_change ? decided : [])]) {
			fields.push(...row_fields(request, row));
		}
		if (may_change) {
			fields.push({
				fieldname: "reason",
				fieldtype: "Small Text",
				label: __("Why does it change?"),
				depends_on: "eval:doc.change",
				mandatory_depends_on: "eval:doc.change",
			});
		}
		const dialog = new frappe.ui.Dialog({
			title: __("Decide release"),
			fields,
			primary_action_label: __("Save Selection"),
			async primary_action(values) {
				const decisions = [];
				for (const row of rows) {
					if (row.decided && !values.change) continue;
					const assets = [];
					for (
						let unit = 0;
						unit < Math.max(Math.round(Number(row.qty) || 1), 1);
						unit++
					) {
						assets.push(values[`${row.row}__${unit}`]);
					}
					decisions.push({ row: row.row, assets: assets.filter(Boolean) });
				}
				await frappe.call({
					method: api + "decide_release",
					args: {
						material_request: frm.doc.name,
						decisions: JSON.stringify(decisions),
						reason: values.reason || null,
					},
					freeze: true,
				});
				dialog.hide();
				frappe.show_alert({
					message: __(
						"Selection saved. Prepare the borrowings when the items are ready."
					),
					indicator: "green",
				});
				await frm.reload_doc();
			},
		});
		dialog.show();
	}

	function supply_choice(frm, release, action) {
		const rows = (release.stock_rows || []).filter((row) =>
			action === "use_stock"
				? row.converting || row.selecting_assets
				: action === "prepare_conversion"
				? !row.converted
				: true
		);
		if (!rows.length) return;
		const labels = rows.map((row) => `${row.idx}: ${row.item_code} (${row.qty})`);
		const pick = (label) => rows[labels.indexOf(label)];
		const converting = action === "prepare_conversion";
		// Siwat, 17 Nov 2026: a Stock row with no Asset Item yet gets one here, through Create Asset Item's code.
		const creates = (doc) => {
			const row = pick(doc.row);
			return Boolean(row && !row.asset_item_code && row.asset_item_plan?.can_create);
		};
		const needs_category = (doc) => {
			const row = pick(doc.row);
			return creates(doc) && !row.asset_item_plan.links;
		};
		const esc = (value) => frappe.utils.escape_html(String(value ?? ""));
		const note = (row) => {
			if (!row) return "";
			if (row.asset_item_code) {
				return __("Asset Item: {0}", [esc(row.asset_item_code)]);
			}
			const plan = row.asset_item_plan || {};
			if (!plan.can_create) return esc(plan.message || "");
			if (plan.links) {
				return __(
					"No Asset Item linked yet. Pressing Prepare Conversion links the existing {0} to {1}.",
					[esc(plan.suggested_code), esc(row.item_code)]
				);
			}
			return __(
				"No Asset Item yet. Pressing Prepare Conversion creates {0} from {1} and links it.",
				[esc(plan.suggested_code), esc(row.item_code)]
			);
		};
		let ready = false;
		// declared before the dialog: its depends_on functions read `choices` while it is built
		let choices = null;
		let choices_request = 0;
		const dialog = new frappe.ui.Dialog({
			title: __(
				converting
					? "Prepare Conversion"
					: action === "use_stock"
					? "Use Stock"
					: "Select Assets"
			),
			fields: [
				{
					fieldname: "row",
					fieldtype: "Select",
					label: __("Stock Item"),
					options: labels,
					default: labels[0],
					reqd: 1,
					onchange: () => {
						show_row();
						load_choices();
					},
				},
				...(converting
					? [
							{
								fieldname: "warehouse",
								fieldtype: "Link",
								options: "Warehouse",
								label: __("Source Warehouse"),
								reqd: 1,
								default: rows[0].warehouse || "",
								get_query: () => ({
									filters: { company: frm.doc.company, is_group: 0 },
								}),
								onchange: () => load_choices(),
							},
							{
								// FL05 round 2: the conversion goes to review with its Asset Location
								fieldname: "asset_location",
								fieldtype: "Link",
								options: "Location",
								label: __("Asset Location"),
								reqd: 1,
							},
							{
								// FL05 round 2: one Serial Number for every unit, from the source warehouse
								fieldname: "serial_nos",
								fieldtype: "Small Text",
								label: __("Serial Numbers"),
								description: __("One per line, one for every unit."),
								depends_on: () => Boolean(choices?.has_serial_no),
								mandatory_depends_on: () => Boolean(choices?.has_serial_no),
							},
							{
								fieldname: "serial_note",
								fieldtype: "HTML",
							},
							{
								fieldname: "asset_item_note",
								fieldtype: "HTML",
							},
							{
								fieldname: "asset_item_code",
								fieldtype: "Data",
								label: __("Asset Item Code"),
								depends_on: creates,
								mandatory_depends_on: creates,
							},
							{
								fieldname: "asset_category",
								fieldtype: "Link",
								options: "Asset Category",
								label: __("Asset Category"),
								reqd: 0,
								depends_on: needs_category,
								mandatory_depends_on: needs_category,
								get_query: () =>
									frm.doc.material_request_type === "Customer Demo"
										? { filters: { is_demo_category: 1 } }
										: {},
							},
					  ]
					: []),
				...(action === "use_stock"
					? [
							{
								fieldname: "reason",
								fieldtype: "Small Text",
								label: __("Reason"),
								reqd: 1,
							},
					  ]
					: []),
			],
			primary_action_label: __(
				converting
					? "Prepare Conversion"
					: action === "use_stock"
					? "Use Stock"
					: "Select Assets"
			),
			async primary_action(values) {
				const row = pick(values.row);
				if (!row) return;
				const create = converting && creates(values);
				if (converting && !row.asset_item_code && !create) {
					frappe.msgprint(
						esc(
							row.asset_item_plan?.message ||
								__("Open Item {0} and use Create Asset Item first.", [
									row.item_code,
								])
						)
					);
					return;
				}
				if (action === "prepare_asset_selection" && !row.asset_item_code) {
					const link = frappe.utils.get_form_link("Item", row.item_code, true);
					frappe.msgprint(
						frappe.model.can_create("Stock to Asset Conversion") &&
							row.asset_item_plan?.can_create
							? __(
									"Item {0} has no Asset Item yet: use Create > Prepare Conversion, " +
										"which creates it.",
									[link]
							  )
							: __(
									"Item {0} has no Asset Item yet. Ask an Item Manager to use " +
										"Item > Create Asset Item.",
									[link]
							  )
					);
					return;
				}
				const { message } = await frappe.call({
					method: api + action,
					args: {
						material_request: frm.doc.name,
						row: row.row,
						...(converting
							? {
									warehouse: values.warehouse,
									asset_location: values.asset_location,
									serial_nos: values.serial_nos || null,
							  }
							: {}),
						...(create
							? {
									asset_item_code: values.asset_item_code,
									asset_category: values.asset_category,
							  }
							: {}),
						...(action === "use_stock" ? { reason: values.reason } : {}),
					},
					freeze: true,
				});
				dialog.hide();
				await frm.reload_doc();
				if (converting) {
					await frappe.model.with_doctype(message.doctype);
					const [doc] = frappe.model.sync(message);
					frappe.set_route("Form", doc.doctype, doc.name);
				} else if (action === "prepare_asset_selection") {
					await decide(frm);
				}
			},
		});
		async function load_choices() {
			if (!converting || !ready) return;
			const row = pick(dialog.get_value("row"));
			const warehouse = dialog.get_value("warehouse");
			if (!row) return;
			// a new row or warehouse: the serials picked before belong to the old one
			const request = ++choices_request;
			dialog.set_value("serial_nos", "");
			const { message } = await frappe.call({
				method: api + "conversion_choices",
				args: {
					material_request: frm.doc.name,
					row: row.row,
					warehouse: warehouse || null,
				},
			});
			if (request !== choices_request) return; // a later change already asked again
			choices = message || {};
			dialog.set_value("asset_location", choices.asset_location || "");
			const free = (choices.serial_nos || []).map((entry) => entry.serial_no);
			if (choices.has_serial_no && free.length === Number(choices.qty)) {
				dialog.set_value("serial_nos", free.join("\n"));
			}
			dialog.fields_dict.serial_note?.$wrapper?.html(
				choices.has_serial_no
					? __("Free in the warehouse: {0}", [esc(free.join(", ") || __("none"))])
					: ""
			);
			dialog.refresh?.();
		}
		function show_row() {
			if (!converting || !ready) return;
			const row = pick(dialog.get_value("row"));
			dialog.fields_dict.asset_item_note?.$wrapper?.html(note(row));
			if (row && !row.asset_item_code && row.asset_item_plan?.can_create) {
				dialog.set_value("asset_item_code", row.asset_item_plan.suggested_code || "");
				dialog.set_value("asset_category", row.asset_item_plan.default_category || "");
			}
		}
		dialog.show();
		ready = true;
		show_row();
		load_choices();
	}

	frappe.ui.form.on("Material Request", {
		async refresh(frm) {
			if (
				!loan_types.includes(frm.doc.material_request_type) ||
				frm.is_new() ||
				frm.doc.docstatus !== 1
			)
				return;
			const name = frm.doc.name;
			const { message: release } = await frappe.call({
				method: api + "get_release",
				args: { material_request: name, omit_if_unavailable: 1 },
			});
			if (frm.doc.name !== name || !release) return;
			if (frm.doc.loan_issue_problem) {
				frm.set_intro(frappe.utils.escape_html(frm.doc.loan_issue_problem), "red");
			}
			if (frm.doc.loan_release_pending) {
				frm.set_intro(
					__(
						"Choose the Assets to lend. Approve a Stock conversion before selecting its Assets."
					),
					"orange"
				);
				frm.add_custom_button(__("Decide release"), () => decide(frm), __("Actions"));
			} else if (
				(frm.doc.items || []).some((row) => row.demo_acquisition_method === "Wait Return")
			) {
				// an Asset reserved until it is back can still be swapped for another one (no loan exists yet)
				frm.add_custom_button(__("Change release"), () => decide(frm), __("Actions"));
			}
			for (const loan of release.loans || []) {
				frm.add_custom_button(
					__("Open Borrowing {0}", [loan]),
					() => frappe.set_route("Form", "Borrowing", loan),
					__("View")
				);
			}
			if (!release.loans?.length && release.stock_rows?.length) {
				// A Stock Item Borrow keeps its approved supply type.
				if (!release.stock_only) {
					frm.add_custom_button(
						__("Select Assets"),
						() => supply_choice(frm, release, "prepare_asset_selection"),
						__("Actions")
					);
				}
				if (
					!release.stock_only &&
					frappe.model.can_create("Stock to Asset Conversion") &&
					release.stock_rows.some((row) => !row.converted)
				) {
					frm.add_custom_button(
						__("Prepare Conversion"),
						() => supply_choice(frm, release, "prepare_conversion"),
						__("Create"),
						"Stock to Asset Conversion"
					);
				}
				if (release.stock_rows.some((row) => row.converting || row.selecting_assets)) {
					frm.add_custom_button(
						__("Use Stock"),
						() => supply_choice(frm, release, "use_stock"),
						__("Actions")
					);
				}
				const conversions = release.stock_rows.flatMap((row) => row.conversions || []);
				if (conversions.length) {
					frm.dashboard.add_section(
						conversions
							.map((conversion) =>
								frappe.utils.get_form_link(
									"Stock to Asset Conversion",
									conversion.name,
									true
								)
							)
							.join("<br>"),
						__("Stock conversions")
					);
				}
			}
			if (
				frm.doc.loan_release_pending ||
				!release.needs_preparation ||
				!frappe.model.can_create("Borrowing")
			) return;
			for (const loan_type of release.available_loan_types || []) {
				frm.add_custom_button(
					__("Borrowing ({0})", [__(loan_type)]),
					async () => {
						const { message } = await frappe.call({
							method: api + "issue_loan",
							args: { material_request: name, loan_type },
							freeze: true,
						});
						frappe.model.sync(message);
						frappe.set_route("Form", message.doctype, message.name);
					},
					__("Create"),
					"Borrowing"
				);
			}
			if (!frm.doc.loan_issue_problem) {
				frm.set_intro(
					__("Ready to prepare. No items move until Confirm Delivery."),
					"blue"
				);
			}
		},
	});
})();
