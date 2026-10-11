"""Copy legacy Material Request addresses into the shared recipient address field."""

import frappe
from frappe import _
from frappe.database.schema import add_column


LEGACY_ADDRESS_FIELDS = ("customer_address", "loan_recipient_address")
LEGACY_DISPLAY_FIELDS = ("customer_address_display", "loan_recipient_address_display")


def execute():
	if not frappe.db.table_exists("Material Request"):
		return

	columns = set(frappe.db.get_table_columns("Material Request"))
	address_fields = [field for field in LEGACY_ADDRESS_FIELDS if field in columns]
	if not address_fields:
		return

	if len(address_fields) == 2:
		conflicts = frappe.db.sql(
			"""select name from `tabMaterial Request`
			where customer_address is not null and customer_address != ''
			and loan_recipient_address is not null and loan_recipient_address != ''
			and customer_address != loan_recipient_address
			limit 10"""
		)
		if conflicts:
			frappe.throw(
				_("Resolve conflicting Customer Address and Recipient Address values before migrating Material Requests: {0}").format(
					", ".join(row[0] for row in conflicts)
				)
			)

	if "recipient_address" not in columns:
		add_column("Material Request", "recipient_address", "Link")
	if "recipient_address_display" not in columns:
		add_column("Material Request", "recipient_address_display", "Text Editor")

	address_source = _legacy_value_expression(address_fields, "customer_address", "loan_recipient_address")
	frappe.db.sql(
		f"""update `tabMaterial Request`
		set recipient_address = coalesce(nullif(recipient_address, ''), {address_source})"""
	)

	display_columns = set(frappe.db.get_table_columns("Material Request"))
	display_fields = [field for field in LEGACY_DISPLAY_FIELDS if field in display_columns]
	if display_fields:
		display_source = _legacy_value_expression(
			display_fields, "customer_address_display", "loan_recipient_address_display"
		)
		frappe.db.sql(
			f"""update `tabMaterial Request`
			set recipient_address_display = coalesce(nullif(recipient_address_display, ''), {display_source})"""
		)


def _legacy_value_expression(fields, customer_field, loan_field):
	if len(fields) == 1:
		return f"nullif(`{fields[0]}`, '')"

	return (
		f"case when material_request_type = 'Customer Borrow / Loan' "
		f"then coalesce(nullif(`{loan_field}`, ''), nullif(`{customer_field}`, '')) "
		f"else coalesce(nullif(`{customer_field}`, ''), nullif(`{loan_field}`, '')) end"
	)
