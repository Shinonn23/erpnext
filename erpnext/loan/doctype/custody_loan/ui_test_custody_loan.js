import {
	saveIfDirty,
	waitForm,
	api,
	current,
	open,
	field,
	row,
	action,
	absent,
	present,
	submit,
	rejectSubmit,
	cancel,
	checkpoint,
	requestLoan,
	issueStock,
	inspectReceive,
	receiveReturn,
	childConditions,
	queryConditions,
	noEffects,
	confirmShipment,
	movementConditions,
	shipmentQueries,
	pickQueries,
} from "../../../../cypress/support/custody";

// Tests own isolated fixtures; transaction documents are created only by visible UI actions.
describe("Custody Loan browser lifecycle", () => {
	let run;
	beforeEach(() => {
		run = null;
		Cypress.env("custodyRunId", null);
		expect(
			Cypress.env("adminPassword"),
			"Supply CYPRESS_adminPassword; never commit credentials"
		).to.be.a("string").and.not.empty;
		cy.login("Administrator");
		cy.visit("/desk");
		cy.intercept("POST", "**/api/method/frappe.desk.search.search_link", (request) => {
			const args =
				typeof request.body === "string"
					? Object.fromEntries(new URLSearchParams(request.body))
					: request.body;
			const filters = typeof args.filters === "string" ? JSON.parse(args.filters) : args.filters;
			if (!run || !filters) return;
			if (args.doctype === "Asset") {
				expect(filters.company, "Asset choices stay in fixture company").to.eq(run.company);
				expect(filters.material_request_type).to.eq("Customer Borrow / Loan");
			}
			if (args.doctype === "Employee") expect(filters.company).to.eq(run.company);
			if (args.doctype === "Warehouse" && args.reference_doctype === "Custody Loan") {
				expect(filters.company).to.eq(run.company);
				expect(filters.is_group).to.eq(0);
			}
			if (
				args.doctype === "Custody Loan" &&
				["Custody Loan Return", "Custody Loan Adjustment"].includes(args.reference_doctype)
			) {
				expect(filters.docstatus).to.eq(1);
				expect(filters.status).to.deep.eq(["in", ["Active", "Partially Returned"]]);
			}
		});
	});
	afterEach(function () {
		if (!run) return;
		cy.login("Administrator");
		cy.visit("/desk");
		api("snapshot", { run_id: run.run_id }, true).then((response) => {
			cy.task("custodyEvidence", {
				name: `${run.run_id}-final`,
				evidence: {
					fixture: run,
					snapshot: response.body,
					snapshotStatus: response.status,
					state: this.currentTest.state,
				},
			});
		});
		api("cleanup", { run_id: run.run_id }, true).then((response) => {
			cy.task("custodyEvidence", {
				name: `${run.run_id}-cleanup`,
				evidence: { status: response.status, result: response.body },
			});
			cy.then(() => {
				expect(response.status, "Cleanup request succeeds; retained manifest records failures").to.eq(
					200
				);
				expect(response.body.message.errors, "Cleanup errors").to.deep.eq([]);
			});
		});
	});
	const prepare = (scenario = "stock", qty = 2, restricted = false) =>
		api("prepare", {
			scenario,
			qty,
			limited_password: restricted ? Cypress.env("custodyLimitedPassword") : undefined,
		}).then((fixture) => {
			run = fixture;
			Cypress.env("custodyRunId", fixture.run_id);
			return fixture;
		});
	const balance = (status, returned_qty, source_qty, loan_qty, outstanding_qty, extra = {}) => ({
		status,
		returned_qty,
		source_qty,
		loan_qty,
		outstanding_qty,
		...extra,
	});

	it("UI-MR-01: purpose, item type and recipient change visibility and mandatory fields", () => {
		const wrapper = (name) =>
			cy.get(`.page-container:visible .frappe-control[data-fieldname="${name}"]`).first();
		const visible = (names, shown) =>
			names.forEach((name) => wrapper(name).should(shown ? "be.visible" : "not.be.visible"));
		const required = (name, yes) =>
			wrapper(name)
				.find(".control-label")
				.should(yes ? "have.class" : "not.have.class", "reqd");
		cy.new_form("Material Request");
		field("material_request_type", "Customer Borrow / Loan", "Select");
		field("material_request_type", "Purchase", "Select");
		visible(
			["loan_recipient_type", "borrow_item_type", "recipient_address", "expected_return_date"],
			false
		);
		field("material_request_type", "Customer Demo", "Select");
		visible(["customer", "recipient_address", "contact_person", "expected_return_date"], true);
		required("customer", true);
		required("recipient_address", true);
		required("contact_person", true);
		visible(["loan_recipient_type", "borrow_item_type"], false);
		field("material_request_type", "Customer Borrow / Loan", "Select");
		visible(["borrow_item_type", "loan_recipient_type", "loan_recipient", "recipient_address"], true);
		required("loan_recipient_type", true);
		required("loan_recipient", true);
		required("recipient_address", false);
		field("borrow_item_type", "Stock Item", "Select");
		visible(["customer_loan_warehouse"], true);
		visible(["loan_recipient_location"], false);
		field("borrow_item_type", "Asset", "Select");
		visible(["customer_loan_warehouse"], false);
		visible(["loan_recipient_location"], true);
		field("loan_recipient_type", "Customer", "Select");
		required("loan_recipient_location", true);
		visible(["contact_person"], true);
		visible(["loan_recipient_contact"], false);
		for (const recipient of ["Employee", "Supplier", "Contact"]) {
			field("loan_recipient_type", recipient, "Select");
			required("loan_recipient_location", recipient !== "Employee");
			visible(["contact_person"], false);
			visible(["loan_recipient_contact"], true);
		}
		cy.screenshot("UI-MR-01-recipient-fields");
		cy.reload();
		absent("Loan Order");
	});

	it("UI-MR-CHILD-01: asset and stock rows change mandatory, visible and editable fields", () => {
		for (const [purpose, borrowType] of [
			["Purchase", null],
			["Material Transfer", null],
			["Asset Transfer", null],
			["Customer Demo", null],
			["Customer Borrow / Loan", "Stock Item"],
			["Customer Borrow / Loan", "Asset"],
		]) {
			cy.new_form("Material Request");
			field("material_request_type", purpose, "Select");
			if (borrowType) field("borrow_item_type", borrowType, "Select");
			cy.get('[data-fieldname="items"] .grid-add-row').click();
			cy.get('[data-fieldname="items"] .grid-row[data-idx="1"] .btn-open-row').first().click();
			cy.get(".grid-row-open:visible").within(() => {
				const asset =
					purpose === "Asset Transfer" || purpose === "Customer Demo" || borrowType === "Asset";
				const wrapper = (name) => cy.get(`.frappe-control[data-fieldname="${name}"]`).first();
				for (const name of ["asset", "asset_name", "asset_serial_number", "target_location"])
					wrapper(name).should(asset ? "be.visible" : "not.be.visible");
				wrapper("asset")
					.find(".control-label")
					.should(asset ? "have.class" : "not.have.class", "reqd");
				wrapper("source_location").should(
					["Asset Transfer", "Customer Demo"].includes(purpose) ? "be.visible" : "not.be.visible"
				);
				wrapper("target_location")
					.find(".control-label")
					.should(purpose === "Asset Transfer" ? "have.class" : "not.have.class", "reqd");
				if (asset)
					wrapper("target_location")
						.find("input:visible")
						.should(purpose === "Asset Transfer" ? "exist" : "not.exist");
				for (const name of ["uom", "stock_uom", "conversion_factor"])
					wrapper(name)
						.find(".control-label")
						.should(asset ? "not.have.class" : "have.class", "reqd");
				for (const name of ["ordered_qty", "received_qty"]) wrapper(name).should("not.be.visible");
				cy.get(".grid-collapse-row").click();
			});
			cy.screenshot(`UI-MR-CHILD-01-${purpose}-${borrowType || "native"}`);
		}
	});

	it("ASYNC-01: delayed Pick List state hides, enables, and never leaks action onto another form", () => {
		prepare()
			.then(() => requestLoan(run))
			.then((loan) => {
				cy.intercept("GET", "**/api/method/frappe.client.get_value*", (request) => {
					if (request.query.doctype !== "Pick List" || request.query.fieldname !== "docstatus")
						return;
					request.alias = "delayedPickStatus";
					request.on("response", (response) => response.setDelay(2500));
				});
				open("Custody Loan", loan, false);
				absent("Create Loan Delivery Note");
				cy.wait("@delayedPickStatus");
				absent("Create Loan Delivery Note");
				action("Open Pick List");
				pickQueries();
				cy.intercept("POST", "**/api/method/run_doc_method", (request) => {
					const args =
						typeof request.body === "string"
							? Object.fromEntries(new URLSearchParams(request.body))
							: request.body;
					if (args.method === "set_item_locations") request.alias = "asyncLocations";
				});
				cy.get('[data-fieldname="get_item_locations"] button').click();
				cy.wait("@asyncLocations");
				row("locations", { picked_qty: 2 });
				submit();
				open("Custody Loan", loan, false);
				cy.get(".navbar-breadcrumbs a").contains("Custody Loan").click();
				cy.get(".page-container:visible .primary-action").contains("Add Custody Loan").click();
				cy.wait("@delayedPickStatus");
				current().its("name").should("not.eq", loan);
				absent("Create Loan Delivery Note");
				cy.screenshot(`${run.run_id}-async-stale-form`);
				open("Custody Loan", loan, false);
				absent("Create Loan Delivery Note");
				cy.wait("@delayedPickStatus");
				present("Create Loan Delivery Note");
				checkpoint(run, loan, "async-pick-ready", balance("Pending Delivery Note", 0, 2, 0, 0));
			});
	});

	it("STOCK-01: borrow 2, return 1, return remaining 1, persist across reload", () => {
		prepare()
			.then(() => requestLoan(run))
			.then((loan) => {
				checkpoint(run, loan, "pending-pick", balance("Pending Pick", 0, 2, 0, 0));
				queryConditions(run, loan);
				issueStock(loan);
				submit();
				open("Custody Loan", loan);
				present("Return");
				present("Open Loan Delivery Note");
				action("Adjustment", "Create");
				queryConditions(run, loan);
				childConditions("Custody Loan Adjustment");
				absent("Open Stock Entry");
				open("Custody Loan", loan);
				checkpoint(run, loan, "issued", balance("Active", 0, 0, 2, 2));
				receiveReturn(run, loan, 1);
				open("Custody Loan", loan);
				present("Return");
				checkpoint(run, loan, "returned-one", balance("Partially Returned", 1, 1, 1, 1));
				receiveReturn(run, loan, 1);
				open("Custody Loan", loan);
				absent("Return");
				absent("Adjustment");
				checkpoint(run, loan, "returned-all", balance("Returned", 2, 2, 0, 0));
			});
	});

	it("STOCK-02: full return stays pending until Accepted QI and receiving note submit", () => {
		prepare()
			.then(() => requestLoan(run))
			.then((loan) => {
				issueStock(loan);
				submit();
				open("Custody Loan", loan);
				action("Return", "Create");
				submit();
				current().then((ret) => {
					checkpoint(
						run,
						loan,
						"pending-return",
						balance("Pending Quality Inspection", 0, 0, 2, 2)
					);
					action("Loan Receive");
					current().then((dn) => {
						noEffects(run, loan, () => rejectSubmit("Quality Inspection"));
						inspectReceive(run, dn.name, "Rejected");
						noEffects(run, loan, () => rejectSubmit("accepted Quality Inspection"));
						action("Inspect Loan Return Items");
						cy.get(".modal:visible a").first().click();
						cy.get(".modal:visible .btn-modal-close").click();
						waitForm("Quality Inspection");
						field("status", "Accepted", "Select");
						field("remarks", "Repeat inspection accepted", "Text");
						submit();
						open("Delivery Note", dn.name);
						submit(false);
						open("Custody Loan Return", ret.name);
						current().its("status").should("eq", "Received");
						checkpoint(run, loan, "full-return", balance("Returned", 2, 2, 0, 0));
					});
				});
			});
	});

	it("SHIP-01: Shipment delivery gates issue and return inspection", () => {
		const shipment = (returning) => {
			action("Create Loan Shipment");
			current().its("doctype").should("eq", "Shipment");
			shipmentQueries();
			field(returning ? "delivery_address_name" : "pickup_address_name", run.company_address);
			field("value_of_goods", 200);
			field("description_of_content", run.run_id, "Text");
			field("pickup_from", "09:00:00");
			field("pickup_to", "17:00:00");
			cy.get('[data-fieldname="shipment_parcel"] .grid-add-row').click();
			row("shipment_parcel", { length: 10, width: 10, height: 10, weight: 1, count: 1 });
			submit();
			return current().then((doc) => doc.name);
		};
		prepare()
			.then(() => requestLoan(run, false, true))
			.then((loan) => {
				issueStock(loan);
				saveIfDirty();
				noEffects(run, loan, () => rejectSubmit("Shipment"));
				current().then((dn) => {
					shipment(false).then((shipmentName) => {
						open("Delivery Note", dn.name);
						current().its("docstatus").should("eq", 1);
						open("Custody Loan", loan);
						absent("Return");
						checkpoint(run, loan, "shipment-pending", balance("Pending Shipment", 0, 0, 2, 2));
						open("Shipment", shipmentName);
						confirmShipment(run);
						open("Custody Loan", loan);
						present("Return");
						action("Return", "Create");
						field("requires_shipment", true, "Check");
						submit();
						action("Loan Receive");
						current().then((receive) => {
							action("Inspect Loan Return Items");
							cy.get(".modal:visible").last().should("contain.text", "Delivered");
							cy.get(".modal:visible").last().find(".btn-modal-close").click();
							shipment(true).then((returnShipment) => {
								open("Delivery Note", receive.name);
								noEffects(run, loan, () => rejectSubmit("Delivered"));
								open("Shipment", returnShipment);
								confirmShipment(run);
								open("Delivery Note", receive.name);
								inspectReceive(run, receive.name);
								submit(false);
								checkpoint(run, loan, "shipment-returned", balance("Returned", 2, 2, 0, 0));
							});
						});
					});
				});
			});
	});

	for (const scenario of ["serial", "batch"]) {
		it(`${scenario.toUpperCase()}-01: identity, over-return, partial return, adjustment reversal`, () => {
			prepare(scenario)
				.then(() => requestLoan(run))
				.then((loan) => {
					issueStock(loan);
					submit();
					open("Custody Loan", loan);
					action("Return", "Create");
					row("items", { returned_qty: 3 });
					noEffects(run, loan, () => {
						cy.save();
						cy.get(".modal:visible")
							.last()
							.should("contain.text", scenario === "serial" ? "Serial" : "quantity");
						cy.get(".modal:visible").last().find(".btn-modal-close").click();
					});
					row("items", {
						returned_qty: 1,
						...(scenario === "serial" ? { serial_no: run.wrong_serial } : {}),
					});
					if (scenario === "batch") {
						cy.get('[data-fieldname="items"] .grid-row[data-idx="1"] .btn-open-row')
							.first()
							.click();
						cy.get(".grid-row-open:visible").within(() => {
							field("batch_no", run.wrong_batch, "Raw");
							cy.get(".grid-collapse-row").click();
						});
					}
					noEffects(run, loan, () => {
						cy.save();
						cy.get(".modal:visible")
							.last()
							.should("contain.text", scenario === "serial" ? "Serial" : "Batch");
						cy.get(".modal:visible").last().find(".btn-modal-close").click();
					});
					row(
						"items",
						scenario === "serial" ? { serial_no: run.serials[0] } : { batch_no: run.batch }
					);
					submit();
					action("Loan Receive");
					current().then((dn) => {
						inspectReceive(run, dn.name);
						submit(false);
					});
					if (scenario === "serial") {
						open("Custody Loan", loan);
						action("Return", "Create");
						row("items", { serial_no: run.serials[0] });
						noEffects(run, loan, () => {
							cy.save();
							cy.get(".modal:visible")
								.last()
								.should("contain.text", "cannot be received again");
							cy.get(".modal:visible").last().find(".btn-modal-close").click();
						});
					}
					open("Custody Loan", loan);
					action("Adjustment", "Create");
					queryConditions(run, loan);
					childConditions("Custody Loan Adjustment", false, scenario);
					row("items", { qty: 2, reason: `E2E loss ${run.run_id}` });
					noEffects(run, loan, () => {
						cy.save();
						cy.get(".modal:visible")
							.last()
							.should("contain.text", scenario === "serial" ? "Serial" : "outstanding Qty");
						cy.get(".modal:visible").last().find(".btn-modal-close").click();
					});
					row("items", { qty: 1 });
					submit();
					current().then((adjustment) => {
						present("Open Stock Entry");
						checkpoint(run, loan, "adjusted", balance("Closed", 1, 1, 0, 0, { adjusted_qty: 1 }));
						open("Custody Loan", loan);
						noEffects(run, loan, () => cancel("Adjustments"));
						open("Custody Loan Adjustment", adjustment.name);
						cancel();
						checkpoint(
							run,
							loan,
							"adjustment-reversed",
							balance("Partially Returned", 1, 1, 1, 1, { adjusted_qty: 0 })
						);
					});
				});
		});
	}

	for (const scenario of ["asset", "asset_employee"]) {
		it(`${scenario.toUpperCase()}-01: custody, location, return and cancellation`, () => {
			prepare(scenario, 1)
				.then(() => requestLoan(run, true))
				.then((loan) => {
					absent("Adjustment");
					absent("Open Pick List");
					if (scenario === "asset_employee") absent("Asset Sale Invoice");
					else present("Asset Sale Invoice");
					movementConditions(run);
					open("Custody Loan", loan);
					queryConditions(run, loan);
					checkpoint(run, loan, "asset-issued", {
						status: "Active",
						returned_qty: 0,
						outstanding_qty: 1,
					});
					action("Return", "Create");
					childConditions("Custody Loan Return", true);
					cy.get('[data-fieldname="requires_shipment"] input:visible').should("not.exist");
					submit();
					current().then((ret) => {
						movementConditions(run, true);
						open("Custody Loan Return", ret.name);
						checkpoint(run, loan, "asset-returned", {
							status: "Returned",
							returned_qty: 1,
							outstanding_qty: 0,
						});
						open("Custody Loan", loan);
						noEffects(run, loan, () => cancel("Returns"));
						open("Custody Loan Return", ret.name);
						cancel();
						checkpoint(run, loan, "asset-return-reversed", {
							status: "Active",
							returned_qty: 0,
							outstanding_qty: 1,
						});
						open("Custody Loan", loan);
						cancel();
						absent("Return");
						absent("Asset Sale Invoice");
						checkpoint(run, loan, "asset-loan-cancelled", { status: "Cancelled" });
					});
				});
		});
	}

	it("ASSET-SALE-01: customer asset sale and invoice cancellation restore custody and GL", () => {
		prepare("asset", 1)
			.then(() => requestLoan(run, true))
			.then((loan) => {
				action("Asset Sale Invoice", "Create");
				row("items", { rate: 200 });
				submit();
				noEffects(run, loan, () => cancel("generated transaction"));
				open("Custody Loan", loan);
				checkpoint(run, loan, "asset-sold", { status: "Closed", outstanding_qty: 0, sold_qty: 1 });
				noEffects(run, loan, () => cancel("Asset Sale Invoice"));
				action("Cancel Asset Sale Invoice", "Sale");
				current().its("status").should("eq", "Active");
				checkpoint(run, loan, "asset-sale-reversed", {
					status: "Active",
					outstanding_qty: 1,
					sold_qty: 0,
				});
			});
	});

	it("ASSET-SALE-DRAFT-01: linked draft sale can be opened and deleted without accounting effects", () => {
		prepare("asset", 1)
			.then(() => requestLoan(run, true))
			.then((loan) => {
				action("Asset Sale Invoice", "Create");
				current().then((invoice) => {
					expect(invoice.docstatus).to.eq(0);
					open("Custody Loan", loan);
					present("Open Asset Sale Invoice");
					present("Delete Draft Asset Sale Invoice");
					absent("Cancel Asset Sale Invoice");
					checkpoint(run, loan, "draft-sale", {
						status: "Active",
						outstanding_qty: 1,
						sold_qty: 0,
					});
					action("Open Asset Sale Invoice", "Sale");
					current().its("name").should("eq", invoice.name);
					open("Custody Loan", loan);
					action("Delete Draft Asset Sale Invoice", "Sale");
					current().its("sales_invoice").should("not.be.ok");
					absent("Open Asset Sale Invoice");
					checkpoint(run, loan, "draft-sale-deleted", {
						status: "Active",
						outstanding_qty: 1,
						sold_qty: 0,
					});
				});
			});
	});

	it("CANCEL-01: return dependency blocks loan cancellation; reversal restores stock", () => {
		prepare()
			.then(() => requestLoan(run))
			.then((loan) => {
				issueStock(loan);
				submit();
				receiveReturn(run, loan, 2).then((ret) => {
					open("Custody Loan", loan);
					noEffects(run, loan, () => cancel("Returns"));
					open("Custody Loan Return", ret);
					cancel();
					checkpoint(run, loan, "stock-return-reversed", balance("Active", 0, 0, 2, 2));
					open("Custody Loan", loan);
					cancel();
					absent("Return");
					absent("Adjustment");
					absent("Create Loan Delivery Note");
					checkpoint(run, loan, "stock-loan-reversed", {
						status: "Cancelled",
						source_qty: 2,
						loan_qty: 0,
					});
				});
			});
	});

	for (const scenario of ["stock", "asset"]) {
		it(`PERM-01-${scenario}: restricted user cannot see actions or bypass them by API`, () => {
			expect(
				Cypress.env("custodyLimitedPassword"),
				"Supply CYPRESS_custodyLimitedPassword for restricted account"
			)
				.to.be.a("string")
				.and.have.length.at.least(12);
			prepare(scenario, scenario === "asset" ? 1 : 2, true)
				.then(() => requestLoan(run, scenario === "asset"))
				.then((loan) => {
					if (scenario === "stock") {
						issueStock(loan);
						submit();
					}
					api("share_loan", { run_id: run.run_id, loan_name: loan });
					api("snapshot", { run_id: run.run_id, loan_name: loan }).then((before) => {
						cy.login(run.limited_user, Cypress.env("custodyLimitedPassword"));
						open("Custody Loan", loan);
						for (const label of [
							"Return",
							"Adjustment",
							"Create Loan Delivery Note",
							"Open Pick List",
							"Open Loan Delivery Note",
							"Asset Sale Invoice",
							"Open Asset Movement",
						])
							absent(label);
						cy.window()
							.its("frappe.csrf_token")
							.then((token) => {
								for (const method of scenario === "stock"
									? ["make_return", "make_adjustment", "make_delivery_note", "cancel"]
									: [
											"make_return",
											"make_asset_sale_invoice",
											"cancel_asset_sale_invoice",
											"cancel",
									  ]) {
									cy.request({
										url: "/api/method/run_doc_method",
										method: "POST",
										failOnStatusCode: false,
										headers: { "X-Frappe-CSRF-Token": token },
										body: { dt: "Custody Loan", dn: loan, method },
									}).then((response) => {
										expect(response.status).to.be.oneOf([403, 417]);
										expect(response.body.exc_type).to.eq("PermissionError");
									});
								}
							});
						cy.login("Administrator");
						open("Custody Loan", loan);
						api("snapshot", { run_id: run.run_id, loan_name: loan }).then((after) => {
							for (const key of ["documents", "ledgers", "bins", "asset", "serials", "bundles"])
								expect(after[key], `Denied API preserves ${key}`).to.deep.equal(before[key]);
							cy.task("custodyEvidence", {
								name: `${run.run_id}-permission`,
								evidence: { before, after },
							});
						});
						checkpoint(
							run,
							loan,
							"permission-denied",
							scenario === "stock"
								? balance("Active", 0, 0, 2, 2)
								: { status: "Active", returned_qty: 0, outstanding_qty: 1 }
						);
					});
				});
		});
	}
});
