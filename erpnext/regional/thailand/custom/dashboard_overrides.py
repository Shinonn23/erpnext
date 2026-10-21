from frappe import _


def get_dashboard_data_for_purchase_invoice(data):
	data["non_standard_fieldnames"].update({"Purchase Tax Invoice": "voucher_no"})
	data["transactions"].append({"label": _("Tax Invoice"), "items": ["Purchase Tax Invoice"]})
	for transaction in data["transactions"]:
		if transaction["label"] == _("Reference"):
			transaction["items"].append("Purchase Billing")
	return data


def get_dashboard_data_for_sales_invoice(data):
	data["non_standard_fieldnames"].update({"Sales Tax Invoice": "voucher_no"})
	data["transactions"].append(
		{"label": _("Tax Invoice"), "items": ["Sales Tax Invoice"]},
	)
	for transaction in data["transactions"]:
		if transaction["label"] == _("Reference"):
			transaction["items"].append("Sales Billing")
	return data


def get_dashboard_data_for_expense_claim(data):
	data["non_standard_fieldnames"].update({"Purchase Tax Invoice": "voucher_no"})
	data["transactions"].append({"label": _("Tax Invoice"), "items": ["Purchase Tax Invoice"]})
	return data


# Thai documents made from a payment or a journal; each names it through Voucher Type / Voucher No
VOUCHER_LINKED_DOCTYPES = ("Withholding Tax Cert", "Sales Tax Invoice", "Purchase Tax Invoice")


def add_thai_voucher_links(data, doctype):
	"""Connections of a Payment Entry / Journal Entry: its Withholding Tax Certs and its tax invoices."""
	data.setdefault("transactions", [])
	data.setdefault("non_standard_fieldnames", {})
	data.setdefault("dynamic_links", {})
	for linked in VOUCHER_LINKED_DOCTYPES:
		data["non_standard_fieldnames"][linked] = "voucher_no"
	# voucher_no is a Dynamic Link: count / list only the rows whose Voucher Type is this doctype
	data["dynamic_links"]["voucher_no"] = [doctype, "voucher_type"]
	data["transactions"].append({"label": _("Withholding Tax"), "items": ["Withholding Tax Cert"]})
	data["transactions"].append(
		{"label": _("Tax Invoice"), "items": ["Sales Tax Invoice", "Purchase Tax Invoice"]}
	)
	return data


def get_dashboard_data_for_payment_entry(data):
	# Payment Receipt is in payment_entry_dashboard.py already
	return add_thai_voucher_links(data, "Payment Entry")


def get_dashboard_data_for_journal_entry(data):
	return add_thai_voucher_links(data, "Journal Entry")
