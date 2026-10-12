"""Test-only prerequisites and independent persisted assertions for browser flows.

HTTP requests commit independently. A private, server-held manifest bounds reads
and lifecycle cleanup; it survives browser failures and is never supplied by clients.
"""

import json
import re
from pathlib import Path
from uuid import uuid4

import frappe
from frappe.tests.utils import whitelist_for_tests
from frappe.utils import add_days, flt, today

from erpnext.loan.doctype.custody_loan.custody_loan_ui_preflight import inventory

RUN_ID = re.compile(r"^[a-f0-9]{32}$")
SCENARIOS = {"stock", "serial", "batch", "asset", "asset_employee"}
FLOW_TYPES = ("Custody Loan", "Custody Loan Return", "Custody Loan Adjustment", "Material Request")


def _authorize():
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		raise frappe.PermissionError("Custody browser helpers require System Manager")


def _path(run_id):
	if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
		raise frappe.PermissionError("Invalid browser fixture identifier")
	return Path(frappe.get_site_path("private", "custody-e2e", run_id + ".json"))


def _store(manifest):
	path = _path(manifest["run_id"])
	path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
	temporary = path.with_suffix(".tmp")
	temporary.write_text(json.dumps(manifest, default=str, indent=2))
	temporary.chmod(0o600)
	temporary.replace(path)


def _manifest(run_id):
	_authorize()
	path = _path(run_id)
	if not path.exists():
		raise frappe.PermissionError("Unknown browser fixture")
	manifest = json.loads(path.read_text())
	if manifest["owner"] != frappe.session.user:
		raise frappe.PermissionError("Browser fixture belongs to another user")
	return manifest


def _owned(manifest, doctype, name):
	"""Require actual ownership, never trust a requested name or prefix alone."""
	doc = frappe.get_doc(doctype, name)
	if doctype in FLOW_TYPES:
		if doc.company != manifest["company"]:
			raise frappe.PermissionError("Document is outside this browser fixture")
		if doctype == "Material Request":
			if not doc.items or any(row.item_code != manifest["item"] for row in doc.items):
				raise frappe.PermissionError("Request does not contain the fixture item")
		elif doctype == "Custody Loan":
			_owned(manifest, "Material Request", doc.material_request)
			if any(row.item_code != manifest["item"] for row in doc.items):
				raise frappe.PermissionError("Loan contains unrelated items")
		else:
			_owned(manifest, "Custody Loan", doc.custody_loan)
	else:
		if name not in manifest.get("prerequisites", {}).get(doctype, []):
			raise frappe.PermissionError("Prerequisite is outside this browser fixture")
	return doc


def _account(company, account_type, root_type=None):
	filters = {"company": company, "account_type": account_type, "is_group": 0}
	if root_type:
		filters["root_type"] = root_type
	name = frappe.db.get_value("Account", filters, "name")
	if not name:
		frappe.throw(f"Fixture chart is missing {account_type} account")
	return name


@whitelist_for_tests()
def prepare(scenario="stock", qty=2, limited_password=None):
	_authorize()
	if scenario not in SCENARIOS or str(qty) not in ("1", "2"):
		frappe.throw("Unsupported browser fixture scenario or quantity")
	qty = int(qty)
	if scenario.startswith("asset") and qty != 1:
		frappe.throw("Asset browser fixture requires quantity 1")
	preflight = inventory()
	if preflight["blockers"]:
		frappe.throw("Browser preflight blocked: " + "; ".join(preflight["blockers"]))
	from erpnext.assets.doctype.asset.test_asset import create_asset
	from erpnext.stock.doctype.item.test_item import make_item
	from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

	run_id = uuid4().hex
	prefix = f"Custody E2E {run_id[:12]}"
	manifest = {
		"run_id": run_id,
		"owner": frappe.session.user,
		"scenario": scenario,
		"qty": qty,
		"expected_return_date": add_days(today(), 30),
		"prerequisites": {},
		"rate": 100,
	}

	def insert(doctype, **values):
		doc = frappe.get_doc({"doctype": doctype, **values}).insert()
		manifest["prerequisites"].setdefault(doctype, []).append(doc.name)
		return doc

	company = insert(
		"Company",
		company_name=prefix,
		abbr=run_id[:8].upper(),
		country="United States",
		default_currency="USD",
		chart_of_accounts="Standard",
		create_chart_of_accounts_based_on="Standard Template",
	)
	manifest["company"] = company.name
	stock_account = _account(company.name, "Stock")
	adjustment_account = _account(company.name, "Stock Adjustment")
	company.stock_adjustment_account = adjustment_account
	company.default_inventory_account = stock_account
	company.enable_perpetual_inventory = 1
	company.save()
	manifest.update(stock_account=stock_account, adjustment_account=adjustment_account)
	item_group = insert("Item Group", item_group_name=prefix, parent_item_group="All Item Groups", is_group=0)
	customer_group = insert(
		"Customer Group", customer_group_name=prefix, parent_customer_group="All Customer Groups", is_group=0
	)
	territory = insert("Territory", territory_name=prefix, parent_territory="All Territories", is_group=0)
	# The checkout requires one company-specific row; existing tax rows stay intact.
	from erpnext.regional.thailand.tests.utils import ensure_company_tax_settings

	ensure_company_tax_settings(company.name)
	for key in ("source_location", "target_location"):
		manifest[key] = insert("Location", location_name=f"{prefix} {key}").name
	for key, location in (
		("source_warehouse", manifest["source_location"]),
		("loan_warehouse", manifest["target_location"]),
	):
		warehouse = insert(
			"Warehouse",
			warehouse_name=f"{prefix} {key}",
			company=company.name,
			account=stock_account,
			asset_location=location,
		)
		storage = insert(
			"Storage Location", warehouse=warehouse.name, location_code=f"E2E-{run_id[:12]}-{key}"
		)
		warehouse.default_storage_location = storage.name
		warehouse.save()
		manifest[key] = warehouse.name
	company.customer_loan_warehouse = manifest["loan_warehouse"]
	company.save()
	customer = insert(
		"Customer",
		customer_name=f"{prefix} Customer",
		customer_type="Company",
		default_currency="USD",
		customer_group=customer_group.name,
		territory=territory.name,
	)
	internal = insert(
		"Customer",
		customer_name=f"{prefix} Internal",
		customer_type="Company",
		is_internal_customer=1,
		customer_group=customer_group.name,
		territory=territory.name,
		represents_company=company.name,
		companies=[{"company": company.name}],
	)
	manifest.update(
		customer=customer.name,
		internal_customer=internal.name,
		recipient_type="Customer",
		recipient=customer.name,
	)
	for key, dt, party in (
		("customer_address", "Customer", customer.name),
		("company_address", "Company", company.name),
	):
		manifest[key] = insert(
			"Address",
			address_title=f"{prefix} {key}",
			address_type="Shipping",
			address_line1="1 Test Road",
			city="Test City",
			country="United States",
			is_your_company_address=int(dt == "Company"),
			links=[{"link_doctype": dt, "link_name": party}],
		).name
	manifest["customer_contact"] = insert(
		"Contact", first_name=prefix, links=[{"link_doctype": "Customer", "link_name": customer.name}]
	).name
	if scenario == "asset_employee":
		employee = insert(
			"Employee",
			naming_series="EMP-",
			first_name=prefix,
			company=company.name,
			date_of_birth="1990-05-08",
			date_of_joining=today(),
			gender="Female",
			status="Active",
		)
		manifest.update(recipient_type="Employee", recipient=employee.name)
	properties = {
		"item_group": item_group.name,
		"stock_uom": "Nos",
		"is_stock_item": int(not scenario.startswith("asset")),
		"has_serial_no": int(scenario == "serial"),
		"has_batch_no": int(scenario == "batch"),
	}
	if scenario.startswith("asset"):
		category = insert(
			"Asset Category",
			asset_category_name=prefix,
			accounts=[
				{
					"company_name": company.name,
					"fixed_asset_account": _account(company.name, "Fixed Asset"),
					"accumulated_depreciation_account": _account(company.name, "Accumulated Depreciation"),
					"depreciation_expense_account": _account(company.name, "Depreciation"),
				}
			],
		)
		properties.update(is_fixed_asset=1, asset_category=category.name, auto_create_assets=0)
	item = make_item(item_code=f"{prefix} Item", properties=properties)
	manifest["item"] = item.name
	manifest["prerequisites"].setdefault("Item", []).append(item.name)
	if scenario.startswith("asset"):
		asset = create_asset(
			item_code=item.name,
			asset_name=prefix,
			asset_category=properties["asset_category"],
			company=company.name,
			location=manifest["source_location"],
			warehouse=manifest["source_warehouse"],
			purchase_date=today(),
			available_for_use_date=today(),
			net_purchase_amount=100,
			purchase_amount=100,
			submit=True,
		)
		manifest["asset"] = asset.name
		manifest["prerequisites"].setdefault("Asset", []).append(asset.name)
	else:
		manifest["serials"] = [f"E2E-{run_id}-{i}" for i in range(qty)] if scenario == "serial" else []
		manifest["wrong_serial"] = f"E2E-{run_id}-unissued"
		if scenario == "serial":
			insert("Serial No", serial_no=manifest["wrong_serial"], item_code=item.name, company=company.name)
		if scenario == "batch":
			manifest["batch"] = insert("Batch", batch_id=f"E2E-{run_id}", item=item.name).name
			manifest["wrong_batch"] = insert("Batch", batch_id=f"E2E-{run_id}-unissued", item=item.name).name
		opening = make_stock_entry(
			company=company.name,
			item_code=item.name,
			qty=qty,
			to_warehouse=manifest["source_warehouse"],
			rate=100,
			serial_no="\n".join(manifest["serials"]) or None,
			batch_no=manifest.get("batch"),
			use_serial_batch_fields=1,
			posting_date=today(),
		)
		manifest["opening_stock_entry"] = opening.name
		manifest["prerequisites"].setdefault("Stock Entry", []).append(opening.name)
	if limited_password:
		if not isinstance(limited_password, str) or len(limited_password) < 12:
			frappe.throw("Use a test password with at least 12 characters")
		role = insert("Role", role_name=f"{prefix} Reader", desk_access=1)
		user = insert(
			"User",
			email=f"custody-{run_id}@example.com",
			first_name=prefix,
			send_welcome_email=0,
			user_type="System User",
			new_password=limited_password,
			roles=[{"role": role.name}],
		)
		manifest["limited_user"] = user.name
		frappe.clear_cache(doctype="Custody Loan")
	_store(manifest)
	return {key: value for key, value in manifest.items() if key not in ("owner", "prerequisites")}


@whitelist_for_tests()
def share_loan(run_id, loan_name):
	"""Give the fixture reader one document, without replacing global DocPerm."""
	from frappe.share import add_docshare

	manifest = _manifest(run_id)
	_owned(manifest, "Custody Loan", loan_name)
	user = manifest.get("limited_user")
	if not user:
		frappe.throw("This fixture has no restricted reader")
	_owned(manifest, "User", user)
	share = add_docshare("Custody Loan", loan_name, user=user, read=1, notify=0)
	manifest["prerequisites"].setdefault("DocShare", []).append(share.name)
	_store(manifest)
	frappe.clear_cache(user=user)
	return share.name


def _flow_documents(manifest):
	result = {}
	for doctype in FLOW_TYPES:
		result[doctype] = []
		for name in frappe.get_all(
			doctype, filters={"company": manifest["company"]}, pluck="name", order_by="name"
		):
			result[doctype].append(_owned(manifest, doctype, name))
	return result


def _transactions(manifest, flows):
	result = {}
	for dt, link in (
		("Delivery Note", "custody_loan"),
		("Stock Entry", "custody_loan"),
		("Asset Movement", "custody_loan"),
		("Sales Invoice", "custody_loan"),
		("Shipment", "custody_loan"),
	):
		loans = {loan.name for loan in flows["Custody Loan"]}
		result[dt] = (
			[
				frappe.get_doc(dt, name)
				for name in frappe.get_all(
					dt,
					filters={link: ["in", sorted(loans)], "company": manifest["company"]},
					pluck="name",
					order_by="name",
				)
			]
			if loans
			else []
		)
	for loan in flows["Custody Loan"]:
		if loan.pick_list:
			pick = frappe.get_doc("Pick List", loan.pick_list)
			if pick.custody_loan != loan.name or pick.company != manifest["company"]:
				raise frappe.PermissionError("Pick List belongs to another fixture")
			result.setdefault("Pick List", []).append(pick)
	for dn in result["Delivery Note"]:
		for name in frappe.get_all(
			"Quality Inspection",
			filters={"reference_type": "Delivery Note", "reference_name": dn.name},
			pluck="name",
			order_by="name",
		):
			qi = frappe.get_doc("Quality Inspection", name)
			if qi.item_code != manifest["item"] or qi.child_row_reference not in {
				row.name for row in dn.items
			}:
				raise frappe.PermissionError("Inspection belongs to another fixture")
			result.setdefault("Quality Inspection", []).append(qi)
		if dn.custody_loan_shipment:
			if dn.custody_loan_shipment not in {shipment.name for shipment in result["Shipment"]}:
				raise frappe.PermissionError("Shipment belongs to another fixture")
	return result


def _stable(doc):
	data = doc.as_dict()
	for key in (
		"modified",
		"modified_by",
		"creation",
		"owner",
		"__onload",
		"_comments",
		"_user_tags",
		"_assign",
		"_liked_by",
		"_seen",
	):
		data.pop(key, None)
	for key, value in list(data.items()):
		if isinstance(value, list):
			data[key] = [
				_stable(row)
				if hasattr(row, "as_dict")
				else {
					k: v for k, v in row.items() if k not in ("modified", "modified_by", "creation", "owner")
				}
				for row in value
			]
	return data


@whitelist_for_tests(methods=["GET", "POST"])
def snapshot(run_id, loan_name=None, request_name=None):
	manifest = _manifest(run_id)
	if loan_name:
		_owned(manifest, "Custody Loan", loan_name)
	if request_name:
		_owned(manifest, "Material Request", request_name)
	flows = _flow_documents(manifest)
	transactions = _transactions(manifest, flows)
	documents = {dt: [_stable(doc) for doc in docs] for dt, docs in {**flows, **transactions}.items()}
	vouchers = [(doc.doctype, doc.name) for docs in transactions.values() for doc in docs]
	ledgers = {}
	for dt in ("Stock Ledger Entry", "GL Entry"):
		rows = []
		for voucher_type, voucher_no in vouchers:
			fields = ["name", "voucher_type", "voucher_no", "company", "is_cancelled"] + (
				[
					"item_code",
					"warehouse",
					"actual_qty",
					"stock_value_difference",
					"voucher_detail_no",
					"serial_and_batch_bundle",
				]
				if dt == "Stock Ledger Entry"
				else ["account", "debit", "credit"]
			)
			rows.extend(
				frappe.get_all(
					dt,
					filters={
						"voucher_type": voucher_type,
						"voucher_no": voucher_no,
						"company": manifest["company"],
					},
					fields=fields,
					order_by="name",
				)
			)
		ledgers[dt] = rows
	bins = {
		warehouse: flt(
			frappe.db.get_value("Bin", {"item_code": manifest["item"], "warehouse": warehouse}, "actual_qty")
		)
		for warehouse in (manifest["source_warehouse"], manifest["loan_warehouse"])
	}
	asset = _stable(_owned(manifest, "Asset", manifest["asset"])) if manifest.get("asset") else None
	serials = (
		frappe.get_all(
			"Serial No",
			filters={"name": ["in", manifest.get("serials", [])]},
			fields=["name", "item_code", "warehouse", "status"],
		)
		if manifest.get("serials")
		else []
	)
	bundles = []
	for name in sorted(
		{row.serial_and_batch_bundle for row in ledgers["Stock Ledger Entry"] if row.serial_and_batch_bundle}
	):
		bundle = frappe.get_doc("Serial and Batch Bundle", name)
		if bundle.item_code != manifest["item"] or bundle.company != manifest["company"]:
			raise frappe.PermissionError("Bundle belongs to another fixture")
		bundles.append(_stable(bundle))
	reports = {}
	for report in (
		"custody_loan_register",
		"custody_loan_stock_balance",
		"custody_loan_asset_balance",
		"custody_loan_movement_history",
	):
		execute = frappe.get_attr(f"erpnext.loan.report.{report}.{report}.execute")
		reports[report] = execute({"company": manifest["company"]})[1]
	return {
		"run_id": run_id,
		"documents": documents,
		"bins": bins,
		"asset": asset,
		"serials": serials,
		"bundles": bundles,
		"ledgers": ledgers,
		"reports": reports,
	}


def _equal(actual, expected, label):
	if actual != expected:
		raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


@whitelist_for_tests(methods=["GET", "POST"])
def verify(run_id, loan_name, expected):
	manifest = _manifest(run_id)
	loan = _owned(manifest, "Custody Loan", loan_name)
	expected = frappe.parse_json(expected)
	allowed = {
		"status",
		"returned_qty",
		"outstanding_qty",
		"adjusted_qty",
		"sold_qty",
		"source_qty",
		"loan_qty",
	}
	if not isinstance(expected, dict) or set(expected) - allowed:
		frappe.throw("Unknown browser expectation")
	result = snapshot(run_id, loan_name)
	for key, value in expected.items():
		if key == "status":
			actual = loan.status
		elif key in ("source_qty", "loan_qty"):
			actual = result["bins"][manifest["source_warehouse" if key == "source_qty" else "loan_warehouse"]]
		else:
			actual = sum(flt(row.get(key)) for row in loan.items)
		_equal(actual, value, key)
	_assert_effects(manifest, loan, result)
	return result


def _gl_net(rows, voucher_type, voucher_no):
	accounts = {}
	for row in rows:
		if row.voucher_type == voucher_type and row.voucher_no == voucher_no:
			accounts[row.account] = flt(accounts.get(row.account, 0) + row.debit - row.credit, 2)
	return {key: value for key, value in accounts.items() if value}


def _assert_effects(manifest, loan, result):
	"""Expected quantities/value come from opening fixtures and transaction inputs."""
	documents = result["documents"]
	sle = result["ledgers"]["Stock Ledger Entry"]
	gl = result["ledgers"]["GL Entry"]
	for dn in documents.get("Delivery Note", []):
		rows = [
			row
			for row in sle
			if row.voucher_type == "Delivery Note" and row.voucher_no == dn["name"] and not row.is_cancelled
		]
		if dn["docstatus"] != 1:
			_equal(rows, [], "Draft/cancelled Delivery Note has no active SLE")
		else:
			if dn["is_return"]:
				inspections = {doc["name"]: doc for doc in documents.get("Quality Inspection", [])}
				seen = set()
				for item in dn["items"]:
					inspection = inspections.get(item.get("quality_inspection"))
					if not inspection or inspection["name"] in seen:
						raise AssertionError("Every received row requires its own inspection")
					seen.add(inspection["name"])
					_equal(
						(
							inspection["docstatus"],
							inspection["status"],
							inspection["reference_name"],
							inspection["child_row_reference"],
						),
						(1, "Accepted", dn["name"], item["name"]),
						"Accepted inspection linkage",
					)
				if dn.get("requires_shipment"):
					shipment = next(
						(
							doc
							for doc in documents.get("Shipment", [])
							if doc["name"] == dn.get("custody_loan_shipment")
						),
						None,
					)
					if not shipment:
						raise AssertionError("Received Shipment return has no Shipment")
					_equal(
						(shipment["docstatus"], shipment["tracking_status"]),
						(1, "Delivered"),
						"Return shipment arrival",
					)
			quantity = sum(abs(flt(item["qty"])) for item in dn["items"])
			direction = 1 if dn["is_return"] else -1
			expected = sorted(
				[
					(
						manifest["source_warehouse"],
						direction * quantity,
						direction * quantity * manifest["rate"],
					),
					(
						manifest["loan_warehouse"],
						-direction * quantity,
						-direction * quantity * manifest["rate"],
					),
				]
			)
			_equal(
				sorted(
					(row.warehouse, flt(row.actual_qty), flt(row.stock_value_difference, 2)) for row in rows
				),
				expected,
				"Delivery Note stock quantity/value",
			)
			if not all(row.item_code == manifest["item"] and row.voucher_detail_no for row in rows):
				raise AssertionError("SLE must identify the fixture item and document row")
		_equal(_gl_net(gl, "Delivery Note", dn["name"]), {}, "Same-account custody transfer GL net")
	for adjustment in documents.get("Custody Loan Adjustment", []):
		stock_entry = adjustment.get("stock_entry")
		if not stock_entry:
			continue
		quantity = (
			sum(flt(item["qty"]) for item in adjustment["items"]) if adjustment["docstatus"] == 1 else 0
		)
		rows = [
			row
			for row in sle
			if row.voucher_type == "Stock Entry" and row.voucher_no == stock_entry and not row.is_cancelled
		]
		_equal(
			sorted((row.warehouse, flt(row.actual_qty), flt(row.stock_value_difference, 2)) for row in rows),
			[(manifest["loan_warehouse"], -quantity, -quantity * manifest["rate"])] if quantity else [],
			"Adjustment SLE",
		)
		expected_gl = (
			{
				manifest["stock_account"]: -quantity * manifest["rate"],
				manifest["adjustment_account"]: quantity * manifest["rate"],
			}
			if quantity
			else {}
		)
		_equal(_gl_net(gl, "Stock Entry", stock_entry), expected_gl, "Adjustment GL accounts/amounts")
	if manifest.get("asset"):
		asset = result["asset"]
		sold = sum(flt(row.sold_qty) for row in loan.items)
		outstanding = loan.docstatus == 1 and sum(flt(row.outstanding_qty) for row in loan.items) > 0
		_equal(
			asset["location"],
			manifest["target_location"] if outstanding or sold else manifest["source_location"],
			"Asset location",
		)
		_equal(
			asset.get("custodian") or None,
			manifest["recipient"] if outstanding and manifest["recipient_type"] == "Employee" else None,
			"Asset custodian",
		)
		if sold:
			_equal(asset["status"], "Sold", "Asset sale status")
		elif asset["status"] == "Sold":
			raise AssertionError("Cancelled sale left Asset Sold")
		else:
			_equal(flt(asset["value_after_depreciation"]), 100, "Loan preserves asset book value")
		for movement in documents.get("Asset Movement", []):
			if movement["docstatus"] != 1:
				continue
			returning = movement["custody_loan_source_doctype"] == "Custody Loan Return"
			_equal(
				movement["purpose"],
				"Receipt"
				if returning
				else "Issue"
				if manifest["recipient_type"] == "Employee"
				else "Transfer",
				"Movement purpose",
			)
			for row in movement["assets"]:
				_equal(row["asset"], manifest["asset"], "Movement asset identity")
				_equal(
					row["target_location"],
					manifest["source_location"] if returning else manifest["target_location"],
					"Movement target location",
				)
			_equal(
				_gl_net(gl, "Asset Movement", movement["name"]),
				{},
				"Physical asset custody has no net GL change",
			)
		for invoice in documents.get("Sales Invoice", []):
			if invoice["docstatus"] == 1:
				amount = sum(flt(row["qty"]) * flt(row["rate"]) for row in invoice["items"])
				asset_doc = frappe.get_doc("Asset", manifest["asset"])
				expected_gl = {
					invoice["debit_to"]: amount,
					asset_doc.get_fixed_asset_account(): -100,
					invoice["items"][0]["income_account"]: 100 - amount,
				}
				expected_gl = {key: value for key, value in expected_gl.items() if value}
			else:
				expected_gl = {}
			_equal(_gl_net(gl, "Sales Invoice", invoice["name"]), expected_gl, "Asset sale GL/reversal")
	for bundle in result["bundles"]:
		if manifest["scenario"] == "serial":
			if any(entry.get("serial_no") not in manifest["serials"] for entry in bundle["entries"]):
				raise AssertionError("Ledger bundle used an unissued serial")
		if manifest["scenario"] == "batch":
			if any(entry.get("batch_no") != manifest["batch"] for entry in bundle["entries"]):
				raise AssertionError("Ledger bundle used a different batch")
		active_rows = [
			row for row in sle if row.serial_and_batch_bundle == bundle["name"] and not row.is_cancelled
		]
		for ledger_row in active_rows:
			_equal(
				abs(sum(flt(entry["qty"]) for entry in bundle["entries"])),
				abs(flt(ledger_row.actual_qty)),
				"Serial/Batch bundle ledger quantity",
			)
	if manifest["scenario"] == "serial":
		expected_serials = {serial: manifest["source_warehouse"] for serial in manifest["serials"]}
		for dn in documents.get("Delivery Note", []):
			if dn["docstatus"] == 1 and not dn["is_return"]:
				for serial in manifest["serials"]:
					expected_serials[serial] = manifest["loan_warehouse"]
		for dn in documents.get("Delivery Note", []):
			if dn["docstatus"] == 1 and dn["is_return"]:
				for item in dn["items"]:
					for serial in (item.get("serial_no") or "").splitlines():
						expected_serials[serial] = manifest["source_warehouse"]
		for adjustment in documents.get("Custody Loan Adjustment", []):
			if adjustment["docstatus"] == 1:
				for item in adjustment["items"]:
					for serial in (item.get("serial_no") or "").splitlines():
						expected_serials[serial] = None
		_equal(
			{row.name: row.warehouse or None for row in result["serials"]},
			expected_serials,
			"Each original Serial No custody",
		)
	balance_rows = [
		row
		for row in result["reports"][
			"custody_loan_stock_balance" if loan.loan_type == "Stock" else "custody_loan_asset_balance"
		]
		if row.get("loan") == loan.name
	]
	active = loan.docstatus == 1 and loan.status in ("Active", "Partially Returned")
	_equal(
		len(balance_rows),
		len([row for row in loan.items if flt(row.outstanding_qty) > 0]) if active else 0,
		"Balance report row presence",
	)
	_equal(
		sum(flt(row["outstanding_qty"]) for row in balance_rows),
		sum(flt(row.outstanding_qty) for row in loan.items) if active else 0,
		"Balance report outstanding",
	)
	register = [row for row in result["reports"]["custody_loan_register"] if row.get("loan") == loan.name]
	_equal(len(register), int(loan.docstatus == 1), "Register row presence")
	if register:
		_equal(register[0]["status"], loan.status, "Register status")
		_equal(
			flt(register[0]["issued_qty"]),
			sum(flt(row.issued_qty) for row in loan.items),
			"Register physically issued quantity",
		)
		_equal(
			flt(register[0]["returned_qty"]),
			sum(flt(row.returned_qty) for row in loan.items),
			"Register returned quantity",
		)
	movements = [
		row for row in result["reports"]["custody_loan_movement_history"] if row.get("loan") == loan.name
	]
	expected_movements = sorted(
		(dt, doc["name"], len(doc.get("assets", doc.get("items", []))))
		for dt in ("Delivery Note", "Stock Entry", "Asset Movement")
		for doc in documents.get(dt, [])
		if doc["docstatus"] == 1 and loan.docstatus == 1
	)
	actual_movements = {}
	for row in movements:
		key = (row.source_doctype, row.source_document)
		actual_movements[key] = actual_movements.get(key, 0) + 1
	_equal(
		sorted((*key, count) for key, count in actual_movements.items()),
		expected_movements,
		"Movement history rows",
	)
	_assert_request(manifest, loan, documents)


def _assert_request(manifest, loan, documents):
	request = next(doc for doc in documents["Material Request"] if doc["name"] == loan.material_request)
	shipments = {doc["name"]: doc for doc in documents.get("Shipment", [])}
	issued = False
	returned = adjusted = sold = 0
	if loan.docstatus == 1:
		for doc in documents.get("Delivery Note", []):
			if doc["docstatus"] != 1:
				continue
			if doc["is_return"]:
				returned += sum(abs(flt(row["qty"])) for row in doc["items"])
			else:
				shipment = shipments.get(doc.get("custody_loan_shipment"))
				issued = not doc.get("requires_shipment") or bool(
					shipment and shipment["docstatus"] == 1 and shipment["tracking_status"] == "Delivered"
				)
		for doc in documents.get("Asset Movement", []):
			if doc["docstatus"] == 1:
				if doc["custody_loan_source_doctype"] == "Custody Loan":
					issued = True
				else:
					returned += len(doc["assets"])
		adjusted = sum(
			flt(row["qty"])
			for doc in documents.get("Custody Loan Adjustment", [])
			if doc["docstatus"] == 1
			for row in doc["items"]
		)
		sold = sum(
			flt(row["qty"])
			for doc in documents.get("Sales Invoice", [])
			if doc["docstatus"] == 1
			for row in doc["items"]
		)
	fulfilled = manifest["qty"] if issued else 0
	outstanding = max(fulfilled - returned - adjusted - sold, 0)
	_equal(
		sum(flt(row["qty"]) for row in request["items"]), manifest["qty"], "MR requested quantity unchanged"
	)
	for field, value in (
		("fulfilled_qty", fulfilled),
		("returned_qty", returned),
		("outstanding_qty", outstanding),
	):
		_equal(sum(flt(row[field]) for row in request["items"]), value, "MR " + field)
	status = (
		"Pending"
		if not fulfilled
		else "Closed"
		if not outstanding and (adjusted or sold)
		else "Returned"
		if returned >= fulfilled
		else "Partially Returned"
		if returned or adjusted or sold
		else "Fulfilled"
	)
	_equal(request["request_progress_status"], status, "MR progress status including reversal")


@whitelist_for_tests()
def cleanup(run_id):
	manifest = _manifest(run_id)
	result = {"run_id": run_id, "cancelled": [], "deleted_drafts": [], "retained": [], "errors": []}
	savepoint = "custody_cleanup_" + uuid4().hex
	frappe.db.savepoint(savepoint)
	try:
		_cleanup_business(manifest, result)
	except Exception as exc:
		frappe.db.rollback(save_point=savepoint)
		result["cancelled"] = []
		result["deleted_drafts"] = []
		result["errors"].append({"error": str(exc)})
	finally:
		try:
			_remove_permissions(manifest, result)
		except Exception as exc:
			result["errors"].append({"error": "Permission cleanup failed: " + str(exc)})
		manifest["cleanup"] = result
		_store(manifest)
	return result


def _cleanup_business(manifest, result):
	flows = _flow_documents(manifest)
	transactions = _transactions(manifest, flows)
	posted_shipments = [doc for doc in transactions.get("Shipment", []) if doc.docstatus == 1]
	if posted_shipments:
		# Native Shipment and posted DN cancellation prohibit one another. A completed
		# return is the supported terminal state; retain this isolated company for audit.
		loans = flows["Custody Loan"]
		if loans and all(
			loan.status == "Returned" and not sum(flt(row.outstanding_qty) for row in loan.items)
			for loan in loans
		):
			result["retained"] = [
				[doc.doctype, doc.name] for docs in {**flows, **transactions}.values() for doc in docs
			]
			result["reason"] = (
				"Completed Shipment flow retained: native posted Shipment/DN cancellation is unsupported"
			)
			return result
		result["errors"].append(
			{"error": "Incomplete posted Shipment flow requires UI return; manifest retained"}
		)
		return result
	# Never bypass dependencies. Native source cancellation owns generated documents.
	for dt in ("Custody Loan Return", "Custody Loan Adjustment", "Custody Loan", "Material Request"):
		for doc in reversed(flows[dt]):
			savepoint = "custody_cleanup_" + uuid4().hex
			frappe.db.savepoint(savepoint)
			try:
				if (
					dt == "Custody Loan"
					and doc.sales_invoice
					and frappe.db.get_value("Sales Invoice", doc.sales_invoice, "docstatus") != 2
				):
					doc.cancel_asset_sale_invoice()
				doc.reload()
				if doc.docstatus == 1:
					doc.cancel()
					result["cancelled"].append([dt, doc.name])
				elif doc.docstatus == 0:
					frappe.delete_doc(dt, doc.name)
					result["deleted_drafts"].append([dt, doc.name])
			except Exception as exc:
				frappe.db.rollback(save_point=savepoint)
				result["errors"].append({"doctype": dt, "name": doc.name, "error": str(exc)})
	if not result["errors"]:
		for dt in ("Stock Entry", "Asset"):
			for name in manifest["prerequisites"].get(dt, []):
				savepoint = "custody_cleanup_" + uuid4().hex
				frappe.db.savepoint(savepoint)
				try:
					doc = _owned(manifest, dt, name)
					if doc.docstatus == 1:
						doc.cancel()
						result["cancelled"].append([dt, name])
				except Exception as exc:
					frappe.db.rollback(save_point=savepoint)
					result["errors"].append({"doctype": dt, "name": name, "error": str(exc)})


def _remove_permissions(manifest, result):
	# New fixture master records remain for audit; only temporary permission grants go away.
	for dt in ("DocShare", "User", "Role"):
		for name in manifest["prerequisites"].get(dt, []):
			savepoint = "custody_cleanup_" + uuid4().hex
			frappe.db.savepoint(savepoint)
			try:
				if frappe.db.exists(dt, name):
					_owned(manifest, dt, name)
					frappe.delete_doc(dt, name)
			except Exception as exc:
				frappe.db.rollback(save_point=savepoint)
				result["errors"].append({"doctype": dt, "name": name, "error": str(exc)})
	frappe.clear_cache(doctype="Custody Loan")
