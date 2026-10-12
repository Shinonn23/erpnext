const helper = "erpnext.loan.doctype.custody_loan.custody_loan_ui_test_helpers";
export const api = (method, args, raw = false) =>
	cy
		.window({ log: false })
		.its("frappe.csrf_token", { log: false })
		.then((token) =>
			cy.request({
				url: `/api/method/${helper}.${method}`,
				method: "POST",
				body: args,
				log: false,
				failOnStatusCode: !raw,
				headers: { "X-Frappe-CSRF-Token": token },
			})
		)
		.then((response) => (raw ? response : response.body.message));
export const current = () => cy.window().its("cur_frm.doc");
export const waitForm = (doctype, name, waitForRequests = true) => {
	cy.window().should((win) => {
		expect(win.cur_frm?.doc?.doctype).to.eq(doctype);
		if (name) expect(win.cur_frm.doc.name).to.eq(name);
	});
	if (waitForRequests) cy.get("body").should("have.attr", "data-ajax-state", "complete");
};
export const saveIfDirty = () =>
	current().then((doc) => {
		if (doc.__islocal || doc.__unsaved) cy.save();
	});
export const open = (doctype, name, waitForRequests = true) => {
	cy.visit(`/desk/${doctype.toLowerCase().replaceAll(" ", "-")}/${encodeURIComponent(name)}`);
	waitForm(doctype, name, waitForRequests);
};
export const field = (name, value, type = "Data") => {
	const control = `[data-fieldname="${name}"] ${
		type === "Select" ? "select" : type === "Text" ? "textarea" : "input"
	}:visible`;
	cy.get(control).first().should("be.enabled");
	const links = [
		"company",
		"item_code",
		"warehouse",
		"from_warehouse",
		"source_warehouse",
		"asset",
		"loan_recipient",
		"loan_recipient_location",
		"recipient_address",
		"batch_no",
		"pickup_address_name",
		"delivery_address_name",
		"custody_loan",
		"custody_loan_item",
		"pickup_contact_name",
		"delivery_contact_name",
		"contact_person",
	];
	if (type === "Data" && links.includes(name)) cy.fill_field(name, String(value), "Link");
	else if (type === "Select") cy.get(control).first().select(value);
	else if (type === "Check") cy.get(control).first()[value ? "check" : "uncheck"]();
	else {
		cy.get(control).first().clear().type(String(value), { parseSpecialCharSequences: false }).blur();
	}
};
export const row = (table, values, rawLink) => {
	cy.get(`[data-fieldname="${table}"] .grid-row[data-idx="1"] .btn-open-row`).first().click();
	cy.get(".grid-row-open:visible").within(() => {
		for (const [name, value] of Object.entries(values))
			field(
				name,
				value,
				name === rawLink ? "Raw Link" : ["serial_no", "reason"].includes(name) ? "Text" : "Data"
			);
		cy.get(".grid-collapse-row").click();
	});
};
export const action = (name, group) => {
	if (group) cy.get(".page-container:visible .custom-actions .dropdown-toggle").contains(group).click();
	cy.get(`.page-container:visible .custom-actions [data-label="${encodeURIComponent(name)}"]`)
		.should("be.visible")
		.click();
	const target = {
		"Loan Order": "Custody Loan",
		"Open Pick List": "Pick List",
		"Create Loan Delivery Note": "Delivery Note",
		"Open Loan Delivery Note": "Delivery Note",
		Return: "Custody Loan Return",
		Adjustment: "Custody Loan Adjustment",
		"Asset Sale Invoice": "Sales Invoice",
		"Open Asset Sale Invoice": "Sales Invoice",
		"Loan Receive": "Delivery Note",
		"Open Asset Movement": "Asset Movement",
		"Open Stock Entry": "Stock Entry",
		"Create Loan Shipment": "Shipment",
		"Create Loan Return Shipment": "Shipment",
		"Open Loan Shipment": "Shipment",
		"Open Loan Return Shipment": "Shipment",
	}[name];
	if (target) waitForm(target);
};
export const absent = (name) =>
	cy
		.get(`.page-container:visible .custom-actions [data-label="${encodeURIComponent(name)}"]`)
		.should("not.exist");
export const present = (name) =>
	cy
		.get(`.page-container:visible .custom-actions [data-label="${encodeURIComponent(name)}"]`)
		.should("exist");
let stepNumber = 0;
const recordStep = () => {
	const runId = Cypress.env("custodyRunId");
	if (!runId) return;
	cy.get("body").should("have.attr", "data-ajax-state", "complete");
	current().then((doc) => {
		const label = `${runId}-step-${++stepNumber}-${doc.doctype}-${doc.name}-${doc.docstatus}`;
		api("snapshot", { run_id: runId }).then((before) => {
			cy.task("custodyEvidence", { name: `${label}-before-reload`, evidence: before });
			cy.screenshot(`${label}-before-reload`);
			cy.reload();
			waitForm(doc.doctype, doc.name);
			api("snapshot", { run_id: runId }).then((after) => {
				cy.task("custodyEvidence", { name: `${label}-after-reload`, evidence: after });
				cy.screenshot(`${label}-after-reload`);
				cy.then(() => {
					for (const key of [
						"documents",
						"ledgers",
						"bins",
						"asset",
						"serials",
						"bundles",
						"reports",
					])
						expect(after[key], `Reload preserves ${key}`).to.deep.eq(before[key]);
				});
				for (const loan of after.documents["Custody Loan"] || [])
					api("verify", { run_id: runId, loan_name: loan.name, expected: "{}" });
			});
		});
	});
};
export const submit = (save = true) => {
	if (save) saveIfDirty();
	cy.get(".page-container:visible .primary-action").contains("Submit").click();
	cy.get(".modal:visible").last().find(".btn-primary").contains("Yes").click();
	current().its("docstatus").should("eq", 1);
	recordStep();
};
export const rejectSubmit = (message, save = false) => {
	if (save) saveIfDirty();
	cy.get(".page-container:visible .primary-action").contains("Submit").click();
	cy.get(".modal:visible").last().find(".btn-primary").contains("Yes").click();
	cy.get(".modal:visible").last().should("contain.text", message);
	cy.get(".modal:visible").last().find(".btn-modal-close").click();
	current().its("docstatus").should("eq", 0);
};
export const cancel = (message) => {
	cy.click_menu_button("Cancel");
	cy.get(".modal:visible").last().find(".btn-primary").contains("Yes").click();
	if (message) {
		cy.get(".modal:visible").last().should("contain.text", message);
		cy.get(".modal:visible").last().find(".btn-modal-close").click();
		current().its("docstatus").should("eq", 1);
	} else {
		current().its("docstatus").should("eq", 2);
		recordStep();
	}
};
export const checkpoint = (run, loan, label, expected) => {
	const visibleState = () =>
		current().then((doc) => {
			if (doc.doctype === "Custody Loan" && doc.name === loan && expected?.status)
				expect(doc.status).to.eq(expected.status);
			if (["Custody Loan", "Custody Loan Return", "Custody Loan Adjustment"].includes(doc.doctype)) {
				cy.get(
					'.page-container:visible .frappe-control[data-fieldname="status"] .control-value:visible'
				).should("contain.text", doc.status);
			}
		});
	visibleState();
	cy.screenshot(`${run.run_id}-${label}-before-reload`);
	if (loan) api("verify", { run_id: run.run_id, loan_name: loan, expected: JSON.stringify(expected) });
	cy.reload();
	cy.get("body").should("have.attr", "data-ajax-state", "complete");
	if (loan) api("verify", { run_id: run.run_id, loan_name: loan, expected: JSON.stringify(expected) });
	api("snapshot", { run_id: run.run_id, loan_name: loan }).then((snapshot) => {
		cy.task("custodyEvidence", {
			name: `${run.run_id}-${label}`,
			evidence: { run_id: run.run_id, loan, snapshot, expected },
		});
	});
	visibleState();
	cy.screenshot(`${run.run_id}-${label}-after-reload`);
};
export const requestLoan = (run, asset = false, shipment = false) => {
	cy.new_form("Material Request");
	field("company", run.company);
	field("material_request_type", "Customer Borrow / Loan", "Select");
	field("borrow_item_type", asset ? "Asset" : "Stock Item", "Select");
	field("loan_recipient_type", run.recipient_type || "Customer", "Select");
	field("loan_recipient", run.recipient || run.customer);
	if (asset) field("loan_recipient_location", run.target_location);
	if (run.recipient_type !== "Employee") {
		field("recipient_address", run.customer_address);
		field("contact_person", run.customer_contact);
	}
	cy.window().then((win) => {
		const date = win.frappe.datetime.str_to_user(run.expected_return_date);
		field("expected_return_date", date);
		field("schedule_date", date);
	});
	cy.get('[data-fieldname="items"] .grid-add-row').click();
	row("items", asset ? { asset: run.asset } : { item_code: run.item, qty: run.qty });
	absent("Loan Order");
	submit();
	present("Loan Order");
	action("Loan Order", "Create");
	current().its("doctype").should("eq", "Custody Loan");
	cy.get('[data-fieldname="material_request"] input:visible').should("not.exist");
	if (!asset) row("items", { source_warehouse: run.source_warehouse });
	if (!asset && shipment) field("requires_shipment", true, "Check");
	submit();
	childConditions("Custody Loan", asset);
	return current().then((doc) => doc.name);
};
export const issueStock = (loan) => {
	absent("Return");
	absent("Create Loan Delivery Note");
	action("Open Pick List");
	pickQueries();
	cy.intercept("POST", "**/api/method/run_doc_method", (request) => {
		const args =
			typeof request.body === "string"
				? Object.fromEntries(new URLSearchParams(request.body))
				: request.body;
		if (args.method === "set_item_locations") request.alias = "custodyLocations";
	});
	cy.get('[data-fieldname="get_item_locations"] button').click();
	cy.wait("@custodyLocations");
	current().its("locations").should("have.length", 1);
	row("locations", { picked_qty: 2 });
	submit();
	open("Custody Loan", loan);
	present("Create Loan Delivery Note");
	action("Create Loan Delivery Note", "Create");
	current().its("doctype").should("eq", "Delivery Note");
};
export const inspectReceive = (run, receive, result = "Accepted") => {
	action("Inspect Loan Return Items");
	cy.get(".modal:visible a").first().click();
	cy.get(".modal:visible .btn-modal-close").click();
	waitForm("Quality Inspection");
	field("status", result, "Select");
	field("remarks", `Custody E2E ${run.run_id}: physical inspection ${result}`, "Text");
	submit();
	open("Delivery Note", receive);
};
export const receiveReturn = (run, loan, qty, options = {}) => {
	open("Custody Loan", loan);
	action("Return", "Create");
	field("custody_loan", loan);
	queryConditions(run, loan);
	childConditions("Custody Loan Return");
	row("items", { returned_qty: qty, ...(options.serial ? { serial_no: options.serial } : {}) });
	if (options.shipment) field("requires_shipment", true, "Check");
	submit();
	return current().then((ret) => {
		const name = ret.name;
		action("Loan Receive");
		current().then((dn) => {
			noEffects(run, loan, () => rejectSubmit("Quality Inspection"));
			inspectReceive(run, dn.name);
			submit(false);
		});
		return cy.then(() => name);
	});
};

export const childConditions = (doctype, asset = false, scenario = "stock") => {
	cy.get('[data-fieldname="items"] .grid-row[data-idx="1"] .btn-open-row').first().click();
	cy.get(".grid-row-open:visible").within(() => {
		const wrapper = (name) => cy.get(`.frappe-control[data-fieldname="${name}"]`).first();
		if (doctype === "Custody Loan") {
			for (const name of [
				"item_code",
				"source_warehouse",
				"asset",
				"source_location",
				"target_location",
			]) {
				wrapper(name).find("input:visible").should("not.exist");
			}
			wrapper("source_warehouse").should(asset ? "not.be.visible" : "be.visible");
			wrapper("asset").should(asset ? "be.visible" : "not.be.visible");
			for (const name of ["batch_no", "serial_no"]) wrapper(name).should("not.be.visible");
		} else if (doctype === "Custody Loan Return") {
			wrapper("item_code").find("input:visible").should("not.exist");
			for (const name of ["batch_no", "serial_no"])
				wrapper(name).should(asset ? "not.be.visible" : "be.visible");
			for (const name of ["asset", "target_location"])
				wrapper(name).should(asset ? "be.visible" : "not.be.visible");
		} else if (doctype === "Custody Loan Adjustment") {
			wrapper("batch_no").should(scenario === "batch" ? "be.visible" : "not.be.visible");
			wrapper("serial_no").should(scenario === "serial" ? "be.visible" : "not.be.visible");
			wrapper("reason").find(".control-label").should("have.class", "reqd");
		}
		cy.get(".grid-collapse-row").click();
	});
};

let rejection = 0;
export const noEffects = (run, loan, attempt) =>
	api("snapshot", { run_id: run.run_id, loan_name: loan }).then((before) => {
		attempt();
		api("snapshot", { run_id: run.run_id, loan_name: loan }).then((after) => {
			cy.screenshot(`${run.run_id}-rejected-transition-${rejection++}`);
			cy.task("custodyEvidence", {
				name: `${run.run_id}-rejection-${rejection}`,
				evidence: { before, after },
			});
			cy.then(() => {
				for (const key of ["documents", "ledgers", "bins", "asset", "serials", "bundles"])
					expect(after[key], `Rejected transition preserves ${key}`).to.deep.eq(before[key]);
			});
		});
	});

export const queryConditions = (run, loan) =>
	cy.window().then((win) => {
		const frm = win.cur_frm;
		const doc = frm.doc;
		const item = doc.items[0];
		const query = (name) =>
			frm.fields_dict.items.grid.get_field(name).get_query(doc, item.doctype, item.name);
		// These links are read-only on generated forms; inspect the handlers installed by UI setup.
		if (doc.doctype === "Custody Loan") {
			expect(frm.fields_dict.material_request.get_query().filters).to.deep.eq({
				docstatus: 1,
				material_request_type: ["in", ["Customer Demo", "Customer Borrow / Loan"]],
			});
			expect(query("source_warehouse").filters).to.deep.eq({ company: run.company, is_group: 0 });
			expect(query("asset").filters).to.deep.eq({
				company: run.company,
				item_code: run.item,
				docstatus: 1,
				status: ["not in", ["Draft", "Scrapped", "Sold", "Capitalized"]],
				demo_loan_status: "Available",
				custodian: ["is", "not set"],
			});
			const batch = query("batch_no");
			expect(batch.query).to.eq("erpnext.controllers.queries.get_batch_no");
			expect(batch.filters).to.deep.eq({
				item_code: run.item,
				warehouse: run.source_warehouse,
				include_expired_batches: 1,
			});
		} else {
			expect(query("custody_loan_item").filters).to.deep.eq({ parent: loan });
			const filters = frm.fields_dict.custody_loan.get_query().filters;
			expect(filters.docstatus).to.eq(1);
			expect(filters.status).to.deep.eq(["in", ["Active", "Partially Returned"]]);
			if (doc.doctype === "Custody Loan Adjustment") expect(filters.loan_type).to.eq("Stock");
		}
	});

export const confirmShipment = (run) => {
	cy.get('[data-fieldname="proof_of_delivery_section"]').then(($section) => {
		if (!$section.find(".section-body").is(":visible")) cy.wrap($section).find(".section-head").click();
	});
	for (const name of ["received_by", "received_on", "delivery_signature", "delivery_notes"])
		cy.get(`.page-container:visible [data-fieldname="${name}"]`).first().should("not.be.visible");
	action("Confirm Receipt");
	for (const name of ["received_by", "received_on", "delivery_signature"]) {
		cy.get(`.page-container:visible [data-fieldname="${name}"]`)
			.first()
			.should("be.visible")
			.find(".control-label")
			.should("have.class", "reqd");
	}
	cy.get('.page-container:visible [data-fieldname="delivery_notes"]').first().should("be.visible");
	field("received_by", `Test receiver ${run.run_id}`);
	cy.get('[data-fieldname="delivery_signature"] canvas:visible')
		.first()
		.realMouseMove(20, 40, { position: "topLeft" })
		.realMouseDown()
		.realMouseMove(50, 70, { position: "topLeft" })
		.realMouseMove(90, 30, { position: "topLeft" })
		.realMouseUp();
	current()
		.its("delivery_signature")
		.should("match", /^data:image\//);
	cy.save();
	recordStep();
};

export const movementConditions = (run, returning = false) => {
	action("Open Asset Movement");
	current().its("doctype").should("eq", "Asset Movement");
	current().then((doc) => {
		expect(doc.reference_doctype).to.eq("Material Request");
		expect(doc.purpose).to.eq(
			returning ? "Receipt" : run.recipient_type === "Employee" ? "Issue" : "Transfer"
		);
		expect(doc.assets[0].asset).to.eq(run.asset);
		if (run.recipient_type === "Employee")
			expect(doc.assets[0][returning ? "from_employee" : "to_employee"]).to.eq(run.recipient);
	});
	cy.get('[data-fieldname="assets"] .grid-row[data-idx="1"] .btn-open-row').first().click();
	cy.get(".grid-row-open:visible").within(() => {
		const shown = returning
			? ["target_location", "to_employee"]
			: run.recipient_type === "Employee"
			? ["to_employee"]
			: ["source_location", "target_location"];
		for (const name of ["source_location", "target_location", "from_employee", "to_employee"]) {
			cy.get(`[data-fieldname="${name}"]`)
				.first()
				.should(shown.includes(name) ? "be.visible" : "not.be.visible");
		}
		cy.get(".grid-collapse-row").click();
	});
};

export const shipmentQueries = () =>
	cy.window().then((win) => {
		const frm = win.cur_frm;
		for (const side of ["pickup", "delivery"]) {
			const partyType = frm.doc[side === "pickup" ? "pickup_from_type" : "delivery_to_type"];
			const party = frm.doc[`${side}_${win.frappe.model.scrub(partyType)}`];
			expect(frm.fields_dict[`${side}_address_name`].get_query().filters).to.deep.eq({
				link_doctype: partyType,
				link_name: party,
				is_your_company_address: partyType === "Company" ? 1 : 0,
			});
			expect(frm.fields_dict[`${side}_contact_name`].get_query().filters).to.deep.eq({
				link_doctype: partyType,
				link_name: party,
			});
		}
	});

export const pickQueries = () =>
	cy.window().then((win) => {
		const frm = win.cur_frm;
		const item = frm.doc.locations[0];
		const query = (name) =>
			frm.fields_dict.locations.grid.get_field(name).get_query(frm.doc, item.doctype, item.name);
		expect(frm.doc.purpose).to.eq("Delivery");
		expect(query("warehouse").filters).to.deep.eq({ company: frm.doc.company, is_rejected_warehouse: 0 });
		expect(query("storage_location").filters).to.deep.eq({ warehouse: item.warehouse });
		expect(query("batch_no").filters).to.deep.eq({
			item_code: item.item_code,
			warehouse: item.warehouse,
			storage_location: item.storage_location,
		});
		expect(query("serial_and_batch_bundle").filters).to.deep.eq({
			item_code: item.item_code,
			voucher_type: "Pick List",
			voucher_no: ["in", [frm.doc.name, ""]],
			is_cancelled: 0,
		});
	});
