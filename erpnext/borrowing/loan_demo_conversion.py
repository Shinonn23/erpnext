"""Convert a unit an employee holds on a Borrow stock loan to a Demo Asset (Siwat, 16 Nov 2026).

A Sales employee borrows stock (Material Request "Borrow" with an Employee borrower, a Stock Custody
Loan) and keeps it. When she places a unit with a customer for a demo, the unit becomes a Demo Asset without coming
back first: "Convert to Demo Asset" on the loan drafts a Stock to Asset Conversion that names the loan and its row
(ERPNext fork: `borrowing`, `borrowing_item`, never a Material Request). Nothing moves and no approval is skipped:
the Stock team requests review (My Tasks hand-off "Review Demo Asset Conversion", step ``ho_loan_conversion_review``,
also when accounting sends it back), accounting approves and submits it as for every conversion. The draft reserves the
units on the loan; the submitted conversion settles them, and its Assets are with the borrower (custodian).

Who may press: the borrower herself (checked with the same employee criteria as demo consumption) or the
Stock team (Stock Manager, Stock User, Asset Custodian) for any loan. The server checks everything first and only then
inserts the draft with ``ignore_permissions`` (as ERPNext's `demo_consumption.confirm` does). The borrower may read her own
conversion (a read-only share), never write it. No tick and no attachment is asked for anywhere.

Afterwards the Stock team records the Asset Borrowing to the same borrower from the conversion (Create > Asset
Borrowing, or the My Tasks hand-off "Record Asset Borrowing", step ``ho_loan_asset_loan``): the maker gives an UNSAVED
loan that names the conversion (`source_conversion`), so the fork asks no outgoing Asset Movement and leaves the
request's fulfilment alone. The system never makes that loan by itself (CLAUDE.md item 8).
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate, nowtime

from .policy import actor_allowed

OPEN_STATUSES = ("Active", "Partially Returned", "Pending Quality Inspection", "Pending Receipt")
BORROW = "Borrow"


def is_stock_team(user=None, *, borrowing=None):
	return actor_allowed("convert_borrowing_stock", borrowing, user)


def _is_borrower(loan, user=None):
	employee = frappe.db.get_value("Employee", loan.borrower, ["user_id", "status", "company"], as_dict=True)
	return bool(
		employee
		and employee.user_id == (user or frappe.session.user)
		and employee.status == "Active"
		and employee.company == loan.company
	)


def _refusal(loan):
	"""Why a unit may not be converted from this loan, as (message, exception), else None. It never throws, so a
	probe such as `can_convert` queues no message for the browser."""
	if (
		loan.docstatus != 1
		or loan.loan_type != "Stock"
		or loan.borrower_type != "Employee"
		or loan.status not in OPEN_STATUSES
	):
		return _("Convert to Demo Asset needs an active employee Stock Borrowing."), None
	if frappe.db.get_value("Material Request", loan.material_request, "material_request_type") != BORROW:
		return _("Convert to Demo Asset needs an employee Borrow request."), None
	if not (_is_borrower(loan) or is_stock_team(borrowing=loan)):
		return (
			_("Only the employee who holds this borrowing or the Stock team can convert it."),
			frappe.PermissionError,
		)
	return None


def _check_loan(loan):
	"""The loan a unit may be converted from, else a plain refusal."""
	if refusal := _refusal(loan):
		message, exc = refusal
		frappe.throw(message, *([exc] if exc else []))


def load_loan(name):
	loan = frappe.get_doc("Borrowing", name)
	loan.check_permission("read")
	_check_loan(loan)
	return loan


@frappe.whitelist()
def can_convert(name):
	"""Whether the button shows: a readable, submitted Stock Borrow loan of an Employee with outstanding goods, for its
	borrower or the Stock team."""
	if not name or not frappe.has_permission("Borrowing", "read", doc=name):
		return False
	loan = frappe.get_doc("Borrowing", name)
	if _refusal(loan):
		return False
	return any(flt(row.outstanding_qty) > 0 for row in loan.items)


def asset_item_of(item_code):
	target = frappe.get_cached_value("Item", item_code, "default_asset_item_for_conversion")
	if not target:
		frappe.throw(_("Item {0} has no Asset Item. Use Item > Create Asset Item first.").format(item_code))
	return target


def _open_serials(loan, row):
	from erpnext.borrowing.doctype.borrowing.borrowing_core import (
		get_open_issued_serials,
		get_reserved_serials,
	)

	reserved = set(get_reserved_serials(loan.name, row.name))
	return [serial for serial in get_open_issued_serials(loan.name, row) if serial not in reserved]


def _row_options(loan, row):
	from erpnext.borrowing.doctype.borrowing.borrowing_core import loan_item_available_to_adjust

	available = loan_item_available_to_adjust(loan.name, row)
	if flt(row.outstanding_qty) <= 0 or available <= 0:
		return None
	has_serial = cint(frappe.get_cached_value("Item", row.item_code, "has_serial_no"))
	return {
		"loan_item": row.name,
		"item_code": row.item_code,
		"item_name": frappe.get_cached_value("Item", row.item_code, "item_name"),
		"asset_item": frappe.get_cached_value("Item", row.item_code, "default_asset_item_for_conversion"),
		"available": available,
		"has_serial_no": has_serial,
		"serial_nos": _open_serials(loan, row) if has_serial else [],
	}


@frappe.whitelist(methods=["POST"])
def options(name):
	"""The rows of the loan with units the borrower still holds and nothing reserves."""
	loan = load_loan(name)
	loan.refresh_balances()
	return [option for row in loan.items if (option := _row_options(loan, row))]


def _whole(qty):
	qty = flt(qty)
	if qty <= 0 or qty % 1:
		frappe.throw(_("Quantity must be a positive whole number."))
	return int(qty)


def parse_serials(value):
	"""Serial numbers from a JSON list, a list or text with one per line."""
	if isinstance(value, str):
		value = frappe.parse_json(value) if value.strip().startswith("[") else value.split("\n")
	return [str(serial).strip() for serial in value or () if serial and str(serial).strip()]


def _make_bundle(conversion, row, serials):
	from erpnext.stock.serial_batch_bundle import SerialBatchCreation

	bundle = SerialBatchCreation(
		{
			"item_code": row.item_code,
			"warehouse": row.warehouse,
			"posting_date": conversion.posting_date,
			"posting_time": conversion.posting_time,
			"voucher_type": conversion.doctype,
			"voucher_no": conversion.name,
			"voucher_detail_no": row.name,
			"qty": len(serials),
			"type_of_transaction": "Outward",
			"company": conversion.company,
			"serial_nos": serials,
			"do_not_submit": True,
		}
	).make_serial_and_batch_bundle(ignore_permissions=True)
	return bundle.name


@frappe.whitelist(methods=["POST"])
def convert(name, loan_item, qty, serial_nos=None, customer=None, reason=None):
	"""Draft the Stock to Asset Conversion of units the borrower holds. Returns ``{"conversion", "open"}``: ``open``
	tells the browser to route there (the Stock team), else the borrower is told its name."""
	from erpnext.borrowing.doctype.borrowing.borrowing_core import (
		loan_issue_factor,
		loan_item_available_to_adjust,
		lock_borrowing,
	)

	loan = load_loan(name)
	lock_borrowing(loan.name)
	loan.reload()
	_check_loan(loan)
	loan.refresh_balances()
	row = next((item for item in loan.items if item.name == loan_item), None)
	if not row:
		frappe.throw(_("Select a row of this borrowing."))
	qty = _whole(qty)
	target = asset_item_of(row.item_code)
	if not cint(frappe.get_cached_value("Item", row.item_code, "has_serial_no")):
		frappe.throw(_("Only an Item with Serial Numbers can become a Demo Asset."))
	# the conversion counts stock units and the loan loan units: only a row issued one to one converts
	if flt(loan_issue_factor(row.name)) != 1:
		frappe.throw(
			_("Only a borrowing row issued in its stock unit (conversion factor 1) can be converted.")
		)
	available = loan_item_available_to_adjust(loan.name, row)
	if qty > available:
		frappe.throw(_("Only {0} of {1} can be converted.").format(available, row.item_code))
	serials = parse_serials(serial_nos)
	if len(serials) != qty or len(set(serials)) != qty:
		frappe.throw(_("Select one Serial Number for each unit."))
	if not set(_open_serials(loan, row)).issuperset(serials):
		frappe.throw(_("Select Serial Numbers the borrower still holds."))
	if customer and not frappe.db.exists("Customer", customer):
		frappe.throw(_("Customer {0} does not exist.").format(customer))
	if not loan.customer_loan_warehouse:
		frappe.throw(_("The borrowing has no Borrowed warehouse."))

	conversion = frappe.new_doc("Stock to Asset Conversion")
	conversion.company = loan.company
	conversion.posting_date = nowdate()
	conversion.posting_time = nowtime()
	conversion.borrowing = loan.name
	conversion.borrowing_item = row.name
	conversion.demo_customer = customer or None
	conversion.append(
		"stock_items",
		{
			"item_code": row.item_code,
			"asset_item_code": target,
			"qty": qty,
			"stock_qty": qty,
			"stock_uom": frappe.get_cached_value("Item", row.item_code, "stock_uom"),
			"warehouse": loan.customer_loan_warehouse,
		},
	)
	# Every check above ran first; the draft is the system's record of the borrower's request.
	conversion.flags.ignore_permissions = True
	conversion.insert()
	stock_row = conversion.stock_items[0]
	stock_row.serial_and_batch_bundle = _make_bundle(conversion, stock_row, serials)
	conversion.save()
	note = _("Convert to Demo Asset: {0} x {1} ({2}) drafted as {3}.").format(
		qty, row.item_code, ", ".join(serials), conversion.name
	)
	if customer:
		note += " " + _("Customer: {0}.").format(customer)
	if reason:
		note += " " + _("Reason: {0}").format(frappe.utils.escape_html(reason))
	loan.add_comment("Comment", note)
	conversion.add_comment("Comment", note)
	stock_team = is_stock_team(borrowing=loan)
	if not stock_team:
		frappe.share.add_docshare(
			conversion.doctype,
			conversion.name,
			frappe.session.user,
			read=1,
			flags={"ignore_share_permission": True},
		)
	return {"conversion": conversion.name, "open": stock_team}


# -- the Asset Borrowing afterwards -------------------------------------------------------------------------------


def active_asset_loan(conversion_name):
	return frappe.db.get_value("Borrowing", {"source_conversion": conversion_name, "docstatus": 1}, "name")


@frappe.whitelist()
def can_make_asset_loan(name):
	if not name or not frappe.has_permission("Stock to Asset Conversion", "read", doc=name):
		return False
	values = frappe.db.get_value("Stock to Asset Conversion", name, ["docstatus", "borrowing"], as_dict=True)
	return bool(
		values
		and values.docstatus == 1
		and values.borrowing
		and not active_asset_loan(name)
		and frappe.has_permission("Borrowing", "create")
	)


def _customer_party(customer, doctype, primary_field):
	"""The primary Address / Contact of a Customer, else the first one linked to it (None when it has none)."""
	primary = frappe.db.get_value("Customer", customer, primary_field)
	if primary:
		return primary
	linked = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": doctype, "link_doctype": "Customer", "link_name": customer},
		pluck="parent",
		order_by="creation asc",
		limit=1,
	)
	return linked[0] if linked else None


def set_recipient_customer(loan, customer):
	"""The demo customer receives the Assets (Siwat, 16 Nov 2026): Recipient Customer, with the address and contact
	of that customer when it has them. The borrower stays the Sales Employee."""
	if not customer:
		return
	loan.recipient_customer = customer
	if address := _customer_party(customer, "Address", "customer_primary_address"):
		from frappe.contacts.doctype.address.address import get_address_display

		loan.recipient_address = address
		loan.recipient_address_display = get_address_display(address)
	if contact := _customer_party(customer, "Contact", "customer_primary_contact"):
		values = (
			frappe.db.get_value("Contact", contact, ["first_name", "last_name", "email_id"], as_dict=True)
			or frappe._dict()
		)
		loan.recipient_contact = contact
		loan.recipient_contact_display = " ".join(
			part for part in (values.get("first_name"), values.get("last_name")) if part
		)
		loan.recipient_contact_email = values.get("email_id")


def make_asset_loan(source_name):
	"""The UNSAVED Asset Borrowing of the converted Assets: same borrower and request as the stock loan, the
	conversion named, the demo customer as recipient, nothing inserted here."""
	conversion = frappe.get_doc("Stock to Asset Conversion", source_name)
	conversion.check_permission("read")
	if conversion.docstatus != 1 or not conversion.borrowing:
		frappe.throw(_("Record the Asset Borrowing from a submitted conversion of a Borrowing."))
	if loan_name := active_asset_loan(conversion.name):
		frappe.throw(_("Borrowing {0} already records these Assets.").format(loan_name))
	stock_loan = frappe.get_doc("Borrowing", conversion.borrowing)
	request_item = frappe.db.get_value("Borrowing Item", conversion.borrowing_item, "material_request_item")
	loan = frappe.new_doc("Borrowing")
	loan.company = stock_loan.company
	loan.material_request = stock_loan.material_request
	loan.loan_type = "Asset"
	loan.source_conversion = conversion.name
	loan.borrower_type = stock_loan.borrower_type
	loan.borrower = stock_loan.borrower
	loan.expected_return_date = frappe.db.get_value(
		"Material Request", stock_loan.material_request, "expected_return_date"
	)
	set_recipient_customer(loan, conversion.get("demo_customer"))
	for output in conversion.assets:
		if not output.asset:
			continue
		loan.append(
			"items",
			{
				"item_code": frappe.db.get_value("Asset", output.asset, "item_code"),
				"asset": output.asset,
				"qty": 1,
				"material_request_item": request_item,
			},
		)
	return loan


@frappe.whitelist()
def make_asset_loan_doc(source_name):
	"""Create > Asset Borrowing on the conversion form: the unsaved loan for the browser to open."""
	return make_asset_loan(source_name).as_dict()
