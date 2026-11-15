# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Service Equipment: a machine the service team looks after for its owner, kept apart from the company's Assets.

The Asset support DocTypes are shared: Asset Movement (its rows), Asset Maintenance (+ Log), Asset Repair and Asset
Activity name either an Asset or a Service Equipment. A document about equipment never has a financial side: no
value, depreciation, GL Entry, capitalization, stock consumption or cost center. This module holds what they share.
"""

import frappe
from frappe import _
from frappe.utils import get_link_to_form, now_datetime

SERVICE_ROLES = frozenset({"Maintenance User", "Maintenance Manager"})

# The handover type of a movement decides the status the equipment takes.
STATUS_BY_HANDOVER = {
	"Receive from Supplier": "Received",
	"Install": "Installed",
	"Transfer": "Installed",
	"Send for Repair": "Under Repair",
	"Return from Repair": "Installed",
	"Return to Supplier": "Returned to Supplier",
	"Retire": "Retired",
}
CLOSED_STATUSES = ("Returned to Supplier", "Retired")
PLACE_FIELDS = ("location", "custodian", "customer", "address", "placed_on", "last_movement")


def add_activity(equipment, subject, reference=None):
	"""One line of the equipment's history (Asset Activity with `service_equipment`)."""
	if reference:
		subject = f"{subject} ({get_link_to_form(reference.doctype, reference.name)})"
	frappe.get_doc(
		{
			"doctype": "Asset Activity",
			"service_equipment": equipment,
			"subject": subject,
			"user": frappe.session.user,
			"date": now_datetime(),
		}
	).insert(ignore_permissions=True, ignore_links=True)


def validate_placement(company, location=None, employee=None, customer=None, address=None):
	"""A place is a company Location, or a customer with one of the customer's addresses."""
	if location and frappe.db.get_value("Location", location, "is_group"):
		frappe.throw(_("Select an individual Location, not a group."))
	if employee:
		person = frappe.db.get_value("Employee", employee, ["company", "status"], as_dict=True)
		if not person or person.company != company or person.status != "Active":
			frappe.throw(_("The custodian must be an active Employee of the responsible Company."))
	if address and (
		not customer
		or not frappe.db.exists(
			"Dynamic Link",
			{"parent": address, "parenttype": "Address", "link_doctype": "Customer", "link_name": customer},
		)
	):
		frappe.throw(_("The Address must belong to the selected Customer."))
	if not location and not customer:
		frappe.throw(_("Choose a Location, or a Customer, as the destination of the equipment."))


def check_private_file(doc, fieldname, url=None):
	"""A file the user chose to attach must be a private File of this document; no file is fine."""
	url = url or doc.get(fieldname)
	if not url:
		return
	name = (
		frappe.db.get_value(
			"File",
			{
				"file_url": url,
				"is_private": 1,
				"attached_to_doctype": doc.doctype,
				"attached_to_name": doc.name,
			},
			"name",
		)
		if url.startswith("/private/files/")
		else None
	)
	if not name or not frappe.get_doc("File", name).exists_on_disk():
		frappe.throw(_("The attachment must be a private File attached to this document."))


def refuse_financial(doc, fieldnames):
	"""An equipment document carries none of the financial fields an Asset document may have."""
	for fieldname in fieldnames:
		if doc.get(fieldname):
			label = doc.meta.get_label(fieldname)
			frappe.throw(_("{0} is not used for Service Equipment.").format(_(label)))


def equipment_of(doc):
	"""Every Service Equipment a Support or Maintenance document is about, once each and in order."""
	names = [doc.get("service_equipment")] if doc.meta.has_field("service_equipment") else []
	for table in ("purposes", "items"):
		names += [row.get("service_equipment") for row in doc.get(table) or []]
	return list(dict.fromkeys(name for name in names if name))


def log_document_activity(doc, method=None):
	"""The history of each equipment shows the visits, Issues, Warranty Claims and Installation Notes about it."""
	if method == "after_insert":
		subject = _("{0} opened").format(_(doc.doctype))
	elif method == "on_cancel":
		subject = _("{0} cancelled").format(_(doc.doctype))
	else:
		subject = _("{0} submitted").format(_(doc.doctype))
	for name in equipment_of(doc):
		add_activity(name, subject, doc)


def install_from_note(note, method=None):
	"""A submitted Installation Note installs its equipment rows at its customer: one Install Asset Movement."""
	names = [row.service_equipment for row in note.get("items") or [] if row.get("service_equipment")]
	if not names:
		return
	movement = frappe.get_doc(
		{
			"doctype": "Asset Movement",
			"company": note.company,
			"purpose": "Transfer",
			"transaction_date": f"{note.inst_date} {note.get('inst_time') or '00:00:00'}"
			if note.get("inst_date")
			else now_datetime(),
			"reference_doctype": "Installation Note",
			"reference_name": note.name,
			"service_movement_type": "Install",
			"service_target_customer": note.customer,
			"service_target_address": note.get("customer_address"),
			"service_reason": _("Installed by {0}").format(note.name),
			"assets": [{"service_equipment": name} for name in dict.fromkeys(names)],
		}
	)
	movement.flags.ignore_permissions = True
	movement.insert()
	movement.submit()


def note_movements(note):
	return frappe.get_all(
		"Asset Movement",
		filters={"reference_doctype": "Installation Note", "reference_name": note.name, "docstatus": 1},
		pluck="name",
	)


def check_note_cancel(note, method=None):
	"""The Install movement of a note can be undone only while it is the equipment's latest movement."""
	for movement in note_movements(note):
		for name in frappe.get_all(
			"Asset Movement Item", filters={"parent": movement}, pluck="service_equipment"
		):
			if name and frappe.db.get_value("Service Equipment", name, "last_movement") != movement:
				frappe.throw(
					_(
						"Service Equipment {0} has moved since this note installed it. Cancel its later Asset Movements "
						"first, then cancel the note."
					).format(name)
				)


def uninstall_from_note(note, method=None):
	for movement in note_movements(note):
		doc = frappe.get_doc("Asset Movement", movement)
		doc.flags.ignore_permissions = True
		doc.cancel()


# Who sees what: a holder of Maintenance User / Maintenance Manager who has no other right on a shared Asset DocType
# works on the equipment records only, never on the company's Assets.
EQUIPMENT_CONDITIONS = {
	"Asset Movement": (
		"exists (select 1 from `tabAsset Movement Item` ami where ami.parent = `tabAsset Movement`.name"
		" and ami.parenttype = 'Asset Movement' and ifnull(ami.service_equipment, '') != '')"
	),
	"Asset Maintenance": "ifnull(`tabAsset Maintenance`.service_equipment, '') != ''",
	"Asset Maintenance Log": "ifnull(`tabAsset Maintenance Log`.service_equipment, '') != ''",
	"Asset Repair": "ifnull(`tabAsset Repair`.service_equipment, '') != ''",
	"Asset Activity": "ifnull(`tabAsset Activity`.service_equipment, '') != ''",
}


def equipment_only(doctype, user=None, ptype="read"):
	"""True when the user reaches the DocType only through the service roles."""
	user = user or frappe.session.user
	if user == "Administrator":
		return False
	roles = set(frappe.get_roles(user))
	if not roles & SERVICE_ROLES:
		return False
	from frappe.permissions import get_doctype_roles

	other = set(get_doctype_roles(doctype, ptype)) - SERVICE_ROLES
	return not roles & other


def is_equipment_document(doc):
	if doc.doctype == "Asset Movement":
		return bool(doc.get("assets")) and all(row.get("service_equipment") for row in doc.assets)
	return bool(doc.get("service_equipment"))


# Who acts on an equipment repair: the service team (and System Manager). Accounting keeps the company's repairs
# (it submits and cancels them) but has nothing to do on a repair that has no financial side.
SERVICE_ONLY = {"Asset Repair": "ifnull(`tabAsset Repair`.service_equipment, '') = ''"}


def serves_equipment(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & (SERVICE_ROLES | {"System Manager"}))


def permission_query(user=None, doctype=None):
	if doctype in SERVICE_ONLY and not serves_equipment(user):
		return SERVICE_ONLY[doctype]
	if doctype not in EQUIPMENT_CONDITIONS or not equipment_only(doctype, user):
		return ""
	if doctype == "Asset Repair":
		conditions = [EQUIPMENT_CONDITIONS[doctype]]
		assets = (frappe.get_list("Asset", user=user, pluck="name", limit_page_length=0)
			if frappe.has_permission("Asset", "read", user=user) else [])
		if assets:
			conditions.append("`tabAsset Repair`.asset in (" + ", ".join(
				frappe.db.escape(asset) for asset in assets) + ")")
		conditions.append(failed_asset_return_condition(user))
		return "(" + " or ".join(conditions) + ")"
	return EQUIPMENT_CONDITIONS[doctype]


def failed_asset_return_condition(user=None):
	"""List the same exact rejected returns allowed by readable_failed_asset_return."""
	if not frappe.has_permission("Custody Loan Return", "read", user=user):
		return "1=0"
	returns = frappe.get_list("Custody Loan Return", user=user, filters={"docstatus": 1},
		pluck="name", limit_page_length=0)
	if not returns:
		return "1=0"
	names = ", ".join(frappe.db.escape(name) for name in returns)
	return f"""exists (select 1 from `tabCustody Loan Return` loan_return
		join `tabCustody Loan Return Item` returned on returned.parent = loan_return.name
		join `tabQuality Inspection` qi on qi.name = returned.quality_inspection
		join `tabAsset Movement` movement on movement.name = loan_return.asset_movement
		where loan_return.name in ({names}) and loan_return.docstatus = 1 and movement.docstatus = 1
		and loan_return.name = `tabAsset Repair`.custody_loan_return
		and loan_return.company = `tabAsset Repair`.company
		and returned.name = `tabAsset Repair`.custody_loan_return_item
		and returned.asset = `tabAsset Repair`.asset
		and qi.docstatus = 1 and qi.status = 'Rejected' and qi.reference_type = 'Custody Loan Return'
		and qi.reference_name = loan_return.name and qi.child_row_reference = returned.name)"""


def readable_failed_asset_return(doc, user=None):
	"""A returned asset is eligible only through the exact submitted rejected return row."""
	return_name, row_name, asset = doc.get("custody_loan_return"), doc.get("custody_loan_return_item"), doc.get("asset")
	if not return_name or not row_name or not asset or not frappe.has_permission("Custody Loan Return", "read", doc=return_name, user=user):
		return False
	parent = frappe.db.get_value("Custody Loan Return", return_name, ["docstatus", "company", "asset_movement"], as_dict=True)
	row = frappe.db.get_value("Custody Loan Return Item", row_name, ["parent", "asset", "quality_inspection"], as_dict=True)
	if not parent or parent.docstatus != 1 or parent.company != doc.get("company") or not row or row.parent != return_name or row.asset != asset:
		return False
	if not parent.asset_movement or frappe.db.get_value("Asset Movement", parent.asset_movement, "docstatus") != 1:
		return False
	qi = frappe.db.get_value("Quality Inspection", row.quality_inspection,
		["docstatus", "status", "reference_type", "reference_name", "child_row_reference"], as_dict=True) if row.quality_inspection else None
	return bool(qi and qi.docstatus == 1 and qi.status == "Rejected" and qi.reference_type == "Custody Loan Return"
		and qi.reference_name == return_name and qi.child_row_reference == row_name)


def has_permission(doc, ptype="read", user=None, **kwargs):
	if doc.doctype in SERVICE_ONLY and doc.get("service_equipment") and not serves_equipment(user):
		return False
	if doc.doctype not in EQUIPMENT_CONDITIONS or not equipment_only(doc.doctype, user, ptype):
		return True
	if doc.doctype == "Asset Repair" and doc.get("asset") and ptype in ("read", "select", "write", "create", "print", "report"):
		return frappe.has_permission("Asset", "read", doc=doc.asset, user=user) or readable_failed_asset_return(doc, user)
	if (
		doc.is_new()
		and ptype in ("read", "write", "create")
		and not doc.get("assets" if doc.doctype == "Asset Movement" else "service_equipment")
	):
		return True  # a new form starts empty; validate() refuses a company Asset
	return is_equipment_document(doc)


def refuse_company_asset(doc, method=None):
	"""A user who works on equipment only cannot put a company Asset into a shared document."""
	if doc.doctype not in EQUIPMENT_CONDITIONS or not equipment_only(doc.doctype):
		return
	if doc.doctype == "Asset Repair" and doc.get("asset") and (frappe.has_permission("Asset", "read", doc=doc.asset)
		or readable_failed_asset_return(doc)):
		return
	if not is_equipment_document(doc):
		frappe.throw(_("You may only record Service Equipment here."), frappe.PermissionError)


def _query(doctype):
	def query(user=None):
		return permission_query(user, doctype)

	return query


asset_movement_query = _query("Asset Movement")
asset_maintenance_query = _query("Asset Maintenance")
asset_maintenance_log_query = _query("Asset Maintenance Log")
asset_repair_query = _query("Asset Repair")
asset_activity_query = _query("Asset Activity")
