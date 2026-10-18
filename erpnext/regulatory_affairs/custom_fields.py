"""Custom Fields the RA module adds to Item and to the stock/sales/purchase documents it guards.

Layout rules (see the DKSH notes on `insert_after`): every chain starts from a native field that is the
LAST field of its tab or row form, so no native section is swallowed by a new Section/Tab Break.
The two Item assessment fields are the one exception to the "Regulatory tab chain": they are plain fields
anchored on `item_group` (native, in the Item header) and do not start a Tab/Section Break.
"""

from erpnext.regulatory_affairs.constants import (
	ASSESSMENT_EXEMPT,
	ASSESSMENT_FIELD,
	ASSESSMENT_NOT_ASSESSED,
	ASSESSMENT_NOTE_FIELD,
	ASSESSMENT_REQUIRED,
	ASSESSMENT_STATUSES,
	MEDICAL_DEVICE,
	MODULE,
	OTHER_CATEGORY,
	PRODUCT_CATEGORIES,
	PRODUCT_REGISTRATION,
	REGULATORY_OVERRIDE,
	TRANSACTION_DOCTYPES,
	TRANSACTION_ITEM_DOCTYPES,
)

# Last field of each item row's form; the snapshot section is appended after it.
ITEM_ROW_ANCHORS = {
	"Purchase Order Item": "job_card",
	"Purchase Receipt Item": "subcontracting_receipt_item",
	"Stock Entry Detail": "bom_secondary_item",
	"Sales Order Item": "project",
	"Delivery Note Item": "page_break",
	"Sales Invoice Item": "page_break",
}

SNAPSHOT_FIELDNAMES = (
	"regulatory_registration",
	"regulatory_newcode",
	"regulatory_identifier",
	"regulatory_manufacturer",
	"regulatory_manufacturer_country",
	"regulatory_registration_status",
	"regulatory_registration_expiry",
	"regulatory_coverage_date",
	"regulatory_override",
)


def _field(fieldname, fieldtype, label, insert_after, **kwargs):
	return {
		"fieldname": fieldname,
		"fieldtype": fieldtype,
		"label": label,
		"insert_after": insert_after,
		"module": MODULE,
		"is_system_generated": 1,
		**kwargs,
	}


def item_fields():
	return [
		# The classification pair is not part of the Regulatory tab chain: it sits in the Item header
		# next to the Item Group, exactly where the original Thai FDA assessment fields were placed.
		_field(
			ASSESSMENT_FIELD,
			"Select",
			"Thai FDA Assessment Status",
			"item_group",
			options="\n".join(ASSESSMENT_STATUSES),
			default=ASSESSMENT_NOT_ASSESSED,
			in_standard_filter=1,
			in_list_view=1,
			description="Registration Required: transactions are validated against FDA registrations.",
		),
		_field(
			ASSESSMENT_NOTE_FIELD,
			"Small Text",
			"Thai FDA Assessment Reason / Notes",
			ASSESSMENT_FIELD,
			depends_on=f"eval:doc.{ASSESSMENT_FIELD} == '{ASSESSMENT_EXEMPT}'",
			mandatory_depends_on=f"eval:doc.{ASSESSMENT_FIELD} == '{ASSESSMENT_EXEMPT}'",
		),
		_field("regulatory_tab", "Tab Break", "Regulatory", "quality_inspection_template"),
		_field(
			"regulatory_product_category",
			"Select",
			"Product Category",
			"regulatory_tab",
			options="\n" + "\n".join(PRODUCT_CATEGORIES),
			in_standard_filter=1,
			description="The Thai FDA product group of this Item.",
		),
		_field(
			"regulatory_other_category",
			"Data",
			"Other Category",
			"regulatory_product_category",
			depends_on=f"eval:doc.regulatory_product_category == '{OTHER_CATEGORY}'",
		),
		_field(
			"medical_device_category",
			"Select",
			"Medical Device Category",
			"regulatory_other_category",
			options="\nIVD\nNon-IVD\nOther",
			depends_on=f"eval:doc.regulatory_product_category == '{MEDICAL_DEVICE}'",
		),
		_field(
			"regulatory_owner",
			"Link",
			"Regulatory Owner",
			"medical_device_category",
			options="User",
		),
		_field(
			"regulatory_current_section",
			"Section Break",
			"Current Regulatory Coverage",
			"regulatory_owner",
			depends_on=f"eval:doc.{ASSESSMENT_FIELD} == '{ASSESSMENT_REQUIRED}'",
		),
		_field(
			"current_regulatory_registration",
			"Link",
			"Current Registration",
			"regulatory_current_section",
			options=PRODUCT_REGISTRATION,
			read_only=1,
			no_copy=1,
		),
		_field(
			"current_newcode",
			"Data",
			"Current NEWCODE",
			"current_regulatory_registration",
			read_only=1,
			no_copy=1,
		),
		_field("regulatory_current_column", "Column Break", None, "current_newcode"),
		_field(
			"current_registration_expiry",
			"Date",
			"Current Registration Expiry",
			"regulatory_current_column",
			read_only=1,
			no_copy=1,
		),
		_field(
			"current_registration_status",
			"Data",
			"Current Registration Status",
			"current_registration_expiry",
			read_only=1,
			no_copy=1,
		),
	]


def snapshot_fields(anchor):
	"""Read-only regulatory facts stamped on an item row at submission."""
	collapsible = {"collapsible": 1, "depends_on": "eval:doc.regulatory_coverage_date"}
	read_only = {"read_only": 1, "no_copy": 1}
	return [
		_field(
			"regulatory_snapshot_section",
			"Section Break",
			"Regulatory Snapshot",
			anchor,
			**collapsible,
		),
		_field(
			"regulatory_registration",
			"Link",
			"Registration",
			"regulatory_snapshot_section",
			options=PRODUCT_REGISTRATION,
			**read_only,
		),
		_field("regulatory_newcode", "Data", "FDA NEWCODE", "regulatory_registration", **read_only),
		_field("regulatory_identifier", "Data", "Identifier", "regulatory_newcode", **read_only),
		_field(
			"regulatory_manufacturer",
			"Link",
			"Regulatory Manufacturer",
			"regulatory_identifier",
			options="Manufacturer",
			**read_only,
		),
		_field("regulatory_snapshot_column", "Column Break", None, "regulatory_manufacturer"),
		_field(
			"regulatory_manufacturer_country",
			"Link",
			"Regulatory Manufacturer Country",
			"regulatory_snapshot_column",
			options="Country",
			**read_only,
		),
		_field(
			"regulatory_registration_status",
			"Data",
			"Registration Status at Coverage Date",
			"regulatory_manufacturer_country",
			**read_only,
		),
		_field(
			"regulatory_registration_expiry",
			"Date",
			"Registration Expiry",
			"regulatory_registration_status",
			**read_only,
		),
		_field(
			"regulatory_coverage_date", "Date", "Coverage Date", "regulatory_registration_expiry", **read_only
		),
		_field(
			"regulatory_override",
			"Link",
			"Regulatory Override",
			"regulatory_coverage_date",
			options=REGULATORY_OVERRIDE,
			**read_only,
		),
	]


def transaction_fields():
	return [
		_field(
			"regulatory_override_ref",
			"Link",
			"Regulatory Override",
			"items",
			options=REGULATORY_OVERRIDE,
			no_copy=1,
			description="Approved override for this document; it must be bound to this document's name.",
		)
	]


def get_custom_fields():
	fields = {"Item": item_fields()}
	for item_doctype in TRANSACTION_ITEM_DOCTYPES.values():
		fields[item_doctype] = snapshot_fields(ITEM_ROW_ANCHORS[item_doctype])
	for doctype in TRANSACTION_DOCTYPES:
		fields[doctype] = transaction_fields()
	return fields


CUSTOM_FIELDS = get_custom_fields()
