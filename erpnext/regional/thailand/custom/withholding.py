"""Thai withholding tax (ภาษีหัก ณ ที่จ่าย) on Payment Entry and Journal Entry: the "Withhold Tax" buttons.

A type of withholding is a Tax Withholding Category: its rate on a date, its Thai type of income (the PND3 / PND53
line) and, per company in its Accounts table, two accounts: the Pay Account for the tax we withhold from suppliers
and the Receive Account for the tax our customers withhold from us. Applies To says which directions the type is set
up for; each of them needs its account on every company row.

- Pay (a payment to a supplier, a journal crediting a supplier): ERPNext's own Tax Withholding does the work.
  Consider for Tax Withholding with the category gives the Tax Withholding Entries, the liability row, the PND3 /
  PND53 reports and the Withholding Tax Cert (withholding_cert.py); Withhold Tax only drives that flow.
- Receive (a receipt from a customer, a journal crediting a customer): ERPNext has nothing for it. Withhold Tax adds
  one row per type on the type's Receive Account (a Payment Entry deduction or a Journal Entry debit line) marked
  with its category, so that a second run replaces it, and balances the entry. A receipt whose Paid Amount equals
  what it allocates was entered gross: the money received is reduced by the tax. A journal that balances and has one
  bank / cash debit line was entered gross too: that line is reduced. Otherwise the entry already holds the money
  actually received and the tax fills the gap. The amount taken off is kept on the row, so a second run puts it back
  before it works out the new tax.
"""

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, today

PAY = "Pay"
RECEIVE = "Receive"
PAY_AND_RECEIVE = "Pay and Receive"
MANUAL = "Manual"
LINE_ITEMS = "Line Items"

# Tax Withholding Category / Tax Withholding Account fields (constants.ERP_CUSTOM_FIELDS)
APPLIES_TO = "thai_applies_to"
RECEIVE_ACCOUNT = "customer_withholding_account"
# a row Withhold Tax made: Payment Entry Deduction / Journal Entry Account
ROW_CATEGORY = "thai_withholding_category"
ROW_BASE = "thai_withholding_base"
ROW_OFFSET = "thai_withholding_offset"

# what a receipt settles that carries the amount before VAT
SOURCE_DOCTYPES = ("Sales Invoice", "Sales Order")
BANK_ACCOUNT_TYPES = ("Bank", "Cash")
TOLERANCE = 0.005


# ----------------------------------------------------------------------------- settings


def directions(applies_to):
	"""The directions a category is set up for; an empty Applies To is the field's default."""
	applies_to = applies_to or PAY_AND_RECEIVE
	return (PAY, RECEIVE) if applies_to == PAY_AND_RECEIVE else (applies_to,)


def has_field(doctype, fieldname):
	return frappe.get_meta(doctype).has_field(fieldname)


def get_rate(category, date, tax_withholding_group=None):
	"""Rate (%) of the category on the date, or None when no rate row covers the date."""
	rows = frappe.get_all(
		"Tax Withholding Rate",
		filters={
			"parent": category,
			"parenttype": "Tax Withholding Category",
			"from_date": ("<=", date),
			"to_date": (">=", date),
		},
		fields=["tax_withholding_rate", "tax_withholding_group"],
		order_by="idx",
	)
	for row in rows:
		if cstr(row.tax_withholding_group) == cstr(tax_withholding_group):
			return flt(row.tax_withholding_rate)
	return None


def get_accounts(category, company):
	"""(Pay Account, Receive Account) of the category for the company."""
	fields = ["account"]
	if has_field("Tax Withholding Account", RECEIVE_ACCOUNT):
		fields.append(RECEIVE_ACCOUNT)
	row = frappe.db.get_value(
		"Tax Withholding Account",
		{"parent": category, "parenttype": "Tax Withholding Category", "company": company},
		fields,
		as_dict=True,
	)
	if not row:
		return None, None
	return row.get("account"), row.get(RECEIVE_ACCOUNT)


def amount_for(base, rate, round_off=False):
	"""Tax on the base at the rate, rounded like ERPNext's own withholding (whole baht when the type says so)."""
	amount = flt(base) * flt(rate) / 100
	return flt(amount, 0) if round_off else flt(amount, 2)


def get_choices(company, date=None, direction=PAY):
	"""The types of withholding set up for the direction in the company, with their rate on the date."""
	date = getdate(date or today())
	use_applies_to = has_field("Tax Withholding Category", APPLIES_TO)
	fields = ["name", "category_name", "thai_type_of_income", "thai_income_tax_form", "round_off_tax_amount"]
	if use_applies_to:
		fields.append(APPLIES_TO)
	income_types = dict(
		frappe.get_all("Withholding Tax Type Of Income", fields=["name", "description"], as_list=True)
	)
	choices = []
	for category in frappe.get_all("Tax Withholding Category", fields=fields, order_by="name"):
		if use_applies_to and direction not in directions(category.get(APPLIES_TO)):
			continue
		pay_account, receive_account = get_accounts(category.name, company)
		account = pay_account if direction == PAY else receive_account
		if not account:
			continue
		rate = get_rate(category.name, date)
		if rate is None:
			continue
		title = category.category_name or category.name
		choices.append(
			{
				"name": category.name,
				"label": f"{title} {flt(rate):g}%",
				"title": title,
				"rate": rate,
				"account": account,
				"round_off": cint(category.round_off_tax_amount),
				"type_of_income": category.thai_type_of_income,
				"type_of_income_description": income_types.get(category.thai_type_of_income),
				"income_tax_form": category.thai_income_tax_form,
			}
		)
	return choices


@frappe.whitelist()
def get_withholding_choices(company, date=None, direction=PAY):
	"""For the Withhold Tax popups: the types set up for the direction in the company (settings only, no document
	is read)."""
	if direction not in (PAY, RECEIVE):
		frappe.throw(_("Direction must be Pay or Receive."))
	return get_choices(company, date, direction)


def receive_category(category, company, date=None):
	"""The category as Withhold Tax on a receipt uses it (its Receive Account, and its rate when a date is given);
	refuses one that is not set up for receipts in the company."""
	fields = ["name", "category_name", "round_off_tax_amount"]
	if has_field("Tax Withholding Category", APPLIES_TO):
		fields.append(APPLIES_TO)
	details = frappe.db.get_value("Tax Withholding Category", category, fields, as_dict=True)
	if not details:
		frappe.throw(_("Tax Withholding Category {0} does not exist.").format(frappe.bold(category)))
	title = details.category_name or category
	if RECEIVE not in directions(details.get(APPLIES_TO)):
		frappe.throw(
			_("{0} is not set up for receipts: its Applies To is {1}.").format(
				frappe.bold(title), _(details.get(APPLIES_TO))
			)
		)
	_pay_account, account = get_accounts(category, company)
	if not account:
		frappe.throw(
			_(
				"Set the Receive Account (tax withheld by customers) of {0} for {1} in Tax Withholding Category."
			).format(frappe.bold(title), frappe.bold(company))
		)
	rate = None
	if date:
		rate = get_rate(category, getdate(date))
		if rate is None:
			frappe.throw(
				_("{0} has no rate on {1} in Tax Withholding Category.").format(
					frappe.bold(title), frappe.format(getdate(date), "Date")
				)
			)
	return frappe._dict(
		name=category,
		title=title,
		account=account,
		rate=rate,
		round_off=cint(details.round_off_tax_amount),
	)


def validate_category_accounts(category):
	"""Each company row of a Tax Withholding Category: the Pay Account when Applies To includes Pay, the Receive
	Account when it includes Receive, both of the row's company."""
	from erpnext.controllers.accounts_controller import validate_account_head

	wanted = directions(category.get(APPLIES_TO)) if has_field(category.doctype, APPLIES_TO) else (PAY,)
	companies, pay_accounts = set(), set()
	for row in category.get("accounts") or []:
		if row.company in companies:
			frappe.throw(_("Company {0} added multiple times").format(frappe.bold(row.company)))
		companies.add(row.company)
		receive_account = row.get(RECEIVE_ACCOUNT)
		if PAY in wanted and not row.account:
			frappe.throw(
				_("Row #{0}: set the Pay Account (tax we withhold) for {1}: Applies To includes Pay.").format(
					row.idx, frappe.bold(row.company)
				)
			)
		if RECEIVE in wanted and not receive_account:
			frappe.throw(
				_(
					"Row #{0}: set the Receive Account (tax withheld by customers) for {1}: Applies To includes "
					"Receive."
				).format(row.idx, frappe.bold(row.company))
			)
		if row.account:
			if row.account in pay_accounts:
				frappe.throw(_("Account {0} added multiple times").format(frappe.bold(row.account)))
			pay_accounts.add(row.account)
			validate_account_head(row.idx, row.account, row.company, _("Pay"))
		if receive_account:
			validate_receive_account(row.idx, receive_account, row.company, row.account)


def validate_receive_account(idx, account, company, pay_account=None):
	"""Tax a customer withheld is a claim on the Revenue Department: an asset of the company, in its currency, that
	needs no party."""
	from erpnext.controllers.accounts_controller import validate_account_head

	validate_account_head(idx, account, company, _("Receive"))
	if account == pay_account:
		frappe.throw(
			_("Row #{0}: use different accounts for Pay and Receive ({1}).").format(idx, frappe.bold(account))
		)
	details = frappe.get_cached_value(
		"Account", account, ["root_type", "account_type", "account_currency"], as_dict=True
	)
	if details.root_type != "Asset":
		frappe.throw(
			_(
				"Row #{0}: the Receive Account {1} must be an Asset account (tax withheld by customers)."
			).format(idx, frappe.bold(account))
		)
	if details.account_type in ("Receivable", "Payable"):
		frappe.throw(
			_("Row #{0}: the Receive Account {1} cannot be a {2} account: its rows have no party.").format(
				idx, frappe.bold(account), _(details.account_type)
			)
		)
	currency = frappe.get_cached_value("Company", company, "default_currency")
	if details.account_currency and currency and details.account_currency != currency:
		frappe.throw(
			_("Row #{0}: the Receive Account {1} must be in {2}, the currency of {3}.").format(
				idx, frappe.bold(account), currency, frappe.bold(company)
			)
		)


class TaxWithholdingCategory:
	"""Thai additions to Tax Withholding Category (hooks.extend_doctype_class): a Pay Account and a Receive Account
	per company, each required for the directions Applies To names."""

	def validate_companies_and_accounts(self):
		validate_category_accounts(self)

	def get_company_account(self, company):
		# ERPNext's withholding on payments, journals and purchase invoices uses the Pay Account
		if any(row.company == company and row.account for row in self.get("accounts") or []):
			return super().get_company_account(company)
		frappe.throw(
			_(
				"Tax Withholding Category {0} has no Pay Account for {1}: it does not withhold from suppliers there."
			).format(frappe.bold(self.name), frappe.bold(company))
		)


def set_withholding_directions():
	"""Categories made before Applies To existed withheld from suppliers only: one without any Receive Account is
	Pay (the new field's default, Pay and Receive, would ask for Receive Accounts at its next save)."""
	if not (
		frappe.db.has_column("Tax Withholding Category", APPLIES_TO)
		and frappe.db.has_column("Tax Withholding Account", RECEIVE_ACCOUNT)
	):
		return
	set_up = set(
		frappe.get_all(
			"Tax Withholding Account",
			filters={"parenttype": "Tax Withholding Category", RECEIVE_ACCOUNT: ("is", "set")},
			pluck="parent",
		)
	)
	for name, applies_to in frappe.get_all(
		"Tax Withholding Category", fields=["name", APPLIES_TO], as_list=True
	):
		if applies_to in (None, "", PAY_AND_RECEIVE) and name not in set_up:
			frappe.db.set_value("Tax Withholding Category", name, APPLIES_TO, PAY, update_modified=False)


# ----------------------------------------------------------------------------- what a receipt withholds on


def _source(doctype, name, company):
	"""A document the receipt settles, as the user may read it."""
	source = frappe.get_doc(doctype, name)
	source.check_permission("read")
	if source.get("company") != company:
		frappe.throw(_("{0} {1} belongs to another company.").format(_(doctype), frappe.bold(name)))
	return source


def _source_total(source, account_currency):
	"""The document's total in the receivable account's currency (the way Payment Entry references carry it)."""
	if account_currency and account_currency == source.get("currency"):
		return flt(source.get("rounded_total") or source.get("grand_total"))
	return flt(source.get("base_rounded_total") or source.get("base_grand_total"))


def _share(allocated, total):
	"""The part of a document settled now: allocated / total, kept within 0..1."""
	allocated, total = flt(allocated), flt(total)
	return max(0.0, min(1.0, allocated / total)) if total else 0.0


def _customer_credit(row):
	return flt(row.get("credit_in_account_currency")) - flt(row.get("debit_in_account_currency"))


def customer_credit_lines(journal):
	"""Journal Entry lines that credit a customer (money or a settlement received from it)."""
	return [
		row
		for row in journal.get("accounts") or []
		if row.get("party_type") == "Customer"
		and row.get("party")
		and not row.get(ROW_CATEGORY)
		and _customer_credit(row) > 0
	]


def receive_sources(doc):
	"""What a receipt settles that names its amount before VAT: the Sales Invoices / Sales Orders of a Payment
	Entry's references, or of the customer lines of a Journal Entry (Reference Type / Reference Name)."""
	if doc.doctype == "Payment Entry":
		return [
			frappe._dict(
				doctype=ref.reference_doctype,
				name=ref.reference_name,
				allocated=flt(ref.allocated_amount),
				total=flt(ref.get("total_amount")),
				account_currency=doc.get("paid_from_account_currency"),
			)
			for ref in doc.get("references") or []
			if ref.reference_doctype in SOURCE_DOCTYPES and ref.reference_name and flt(ref.allocated_amount)
		]
	return [
		frappe._dict(
			doctype=row.reference_type,
			name=row.reference_name,
			allocated=_customer_credit(row),
			total=0,
			account_currency=row.get("account_currency"),
		)
		for row in customer_credit_lines(doc)
		if row.get("reference_type") in SOURCE_DOCTYPES and row.get("reference_name")
	]


def get_receive_base(doc):
	"""The amount before VAT the customer withheld on, in company currency: each Sales Invoice / Sales Order the
	entry settles counts its net total times the share settled now. A receipt that settles none counts what it
	received; a journal's customer line that names none counts its credit."""
	sources = receive_sources(doc)
	base = 0.0
	for ref in sources:
		source = _source(ref.doctype, ref.name, doc.company)
		total = ref.total or _source_total(source, ref.account_currency)
		base += flt(source.get("base_net_total")) * _share(ref.allocated, total)
	if doc.doctype == "Payment Entry":
		if not sources:
			base = flt(doc.get("base_paid_amount")) or flt(doc.get("paid_amount")) * flt(
				doc.get("source_exchange_rate") or 1
			)
	else:
		for row in customer_credit_lines(doc):
			if not (row.get("reference_type") in SOURCE_DOCTYPES and row.get("reference_name")):
				base += flt(row.get("credit")) - flt(row.get("debit"))
	return flt(base, 2)


def get_receive_item_bases(doc):
	"""{category: base} from the items of the documents the receipt settles: the item row's Tax Withholding
	Category (filled from the item or the customer), else the item's Sales Tax Withholding Category."""
	bases = {}
	for ref in receive_sources(doc):
		source = _source(ref.doctype, ref.name, doc.company)
		share = _share(ref.allocated, ref.total or _source_total(source, ref.account_currency))
		if not share:
			continue
		for item in source.get("items") or []:
			category = item.get("tax_withholding_category") or (
				item.get("item_code")
				and frappe.get_cached_value("Item", item.get("item_code"), "sales_tax_withholding_category")
			)
			if category:
				bases[category] = bases.get(category, 0.0) + flt(item.get("base_net_amount")) * share
	return {category: flt(base, 2) for category, base in sorted(bases.items()) if flt(base, 2)}


def plan_receive_rows(doc, based_on=MANUAL, category=None, base_amount=None, withholding_amount=None):
	"""The rows Withhold Tax adds on a receipt: one per type, with its Receive Account, base, rate and amount."""
	if based_on == LINE_ITEMS:
		bases = get_receive_item_bases(doc)
		if not bases:
			frappe.throw(
				_(
					"No item of the invoices this entry settles has a Tax Withholding Category (on the invoice "
					"item, the Item's Sales Tax Withholding Category or the Customer). Use Based On Manual "
					"Selection."
				),
				title=_("Withhold Tax"),
			)
		wanted = [(name, base, None) for name, base in bases.items()]
	elif based_on == MANUAL:
		if not category:
			frappe.throw(_("Choose the type of income withheld."), title=_("Withhold Tax"))
		base = get_receive_base(doc) if base_amount in (None, "") else flt(base_amount)
		wanted = [(category, base, withholding_amount)]
	else:
		frappe.throw(_("Withhold Tax works Based On Manual Selection or on the line items."))
	rows = []
	for name, base, amount in wanted:
		info = receive_category(name, doc.company, doc.posting_date)
		base = flt(base, 2)
		amount = amount_for(base, info.rate, info.round_off) if amount in (None, "") else flt(amount, 2)
		if amount <= 0 or base < 0:
			frappe.throw(
				_("Withholding tax {0}: the amount must be more than zero.").format(frappe.bold(info.title)),
				title=_("Withhold Tax"),
			)
		if base and amount > base:
			frappe.throw(
				_("Withholding tax {0}: the amount {1} is more than its base {2}.").format(
					frappe.bold(info.title), amount, base
				),
				title=_("Withhold Tax"),
			)
		rows.append(
			frappe._dict(
				info,
				base=base,
				amount=amount,
				description=_("Withholding tax {0} {1}%").format(info.title, f"{flt(info.rate):g}"),
			)
		)
	return rows


# ----------------------------------------------------------------------------- applying it


def withholding_rows(doc):
	"""The rows of the entry Withhold Tax made (for the Receive side)."""
	table = "deductions" if doc.doctype == "Payment Entry" else "accounts"
	return [row for row in doc.get(table) or [] if row.get(ROW_CATEGORY)]


def default_cost_center(company, row_doctype):
	"""The company's default cost center, when the row has the field (the fork brings Cost Center back)."""
	if not (has_field(row_doctype, "cost_center") and has_field("Company", "cost_center")):
		return None
	return frappe.get_cached_value("Company", company, "cost_center")


def check_receive_entry(doc):
	"""Withhold Tax adds rows on a receipt from a customer, or on a journal that credits a customer and does not
	withhold from a supplier itself."""
	if doc.doctype == "Payment Entry":
		if not (doc.get("payment_type") == "Receive" and doc.get("party_type") == "Customer"):
			frappe.throw(
				_(
					"Withhold Tax adds deductions on a receipt from a Customer. A payment to a Supplier withholds "
					"through Consider for Tax Withholding."
				),
				title=_("Withhold Tax"),
			)
		return
	if not customer_credit_lines(doc):
		frappe.throw(
			_(
				"Withhold Tax on a Journal Entry needs a line that credits a Customer (the tax that customer "
				"withheld), or a Supplier line for the tax we withhold."
			),
			title=_("Withhold Tax"),
		)
	if doc.get("apply_tds"):
		frappe.throw(
			_(
				"Untick Consider for Tax Withholding first: it withholds from a supplier. The tax a customer "
				"withheld is a debit line on the type's Receive Account."
			),
			title=_("Withhold Tax"),
		)


def apply_to_payment_entry(payment, rows):
	"""Replace the deductions an earlier run made with these rows, then balance the receipt."""
	check_receive_entry(payment)
	precision = payment.precision("paid_amount")
	exchange_rate = flt(payment.get("source_exchange_rate")) or 1
	restored = 0.0
	for row in withholding_rows(payment):
		restored += flt(row.get(ROW_OFFSET))
		payment.remove(row)
	if restored:
		payment.paid_amount = flt(flt(payment.paid_amount) + restored / exchange_rate, precision)
	payment.set_total_allocated_amount()
	withheld = flt(sum(row.amount for row in rows), 2)
	allocated = flt(payment.total_allocated_amount)
	# entered gross: the customer paid what the invoices ask for less the tax it withheld
	gross = allocated > 0 and abs(flt(payment.paid_amount) - allocated) < TOLERANCE
	if gross and withheld / exchange_rate >= flt(payment.paid_amount):
		frappe.throw(_("The tax withheld is more than the customer pays."), title=_("Withhold Tax"))
	cost_center = default_cost_center(payment.company, "Payment Entry Deduction")
	for row in rows:
		payment.append(
			"deductions",
			{
				"account": row.account,
				"amount": row.amount,
				"description": row.description,
				ROW_CATEGORY: row.name,
				ROW_BASE: row.base,
				ROW_OFFSET: row.amount if gross else 0,
				**({"cost_center": cost_center} if cost_center else {}),
			},
		)
	if gross:
		payment.paid_amount = flt(flt(payment.paid_amount) - withheld / exchange_rate, precision)
	payment.set_amounts()
	return {
		"withheld": withheld,
		"rows": [{"category": row.name, "title": row.title, "amount": row.amount} for row in rows],
		"money_reduced": bool(gross),
		"paid_amount": payment.paid_amount,
		"difference": flt(payment.get("difference_amount")),
	}


def _bank_debit_line(journal):
	"""The journal's one bank / cash debit line, or None when it has none or several."""
	lines = [
		row
		for row in journal.get("accounts") or []
		if not row.get(ROW_CATEGORY)
		and flt(row.get("debit_in_account_currency")) > 0
		and frappe.get_cached_value("Account", row.account, "account_type") in BANK_ACCOUNT_TYPES
	]
	return lines[0] if len(lines) == 1 else None


def _add_to_debit(row, amount):
	"""Add an amount in company currency to a debit line (and to its amount in the account's currency)."""
	exchange_rate = flt(row.get("exchange_rate")) or 1
	row.debit_in_account_currency = flt(
		flt(row.debit_in_account_currency) + amount / exchange_rate,
		row.precision("debit_in_account_currency"),
	)
	row.debit = flt(flt(row.debit) + amount, row.precision("debit"))


def apply_to_journal_entry(journal, rows):
	"""Replace the debit lines an earlier run made with these rows, then balance the journal."""
	check_receive_entry(journal)
	customer_lines = customer_credit_lines(journal)
	restored = 0.0
	for row in withholding_rows(journal):
		restored += flt(row.get(ROW_OFFSET))
		journal.remove(row)
	bank = _bank_debit_line(journal)
	if restored and bank:
		_add_to_debit(bank, restored)
	journal.set_amounts_in_company_currency()
	journal.set_total_debit_credit()
	withheld = flt(sum(row.amount for row in rows), 2)
	# entered gross: the bank line holds the full amount the customer line settles
	gross = bool(
		bank
		and abs(flt(journal.total_debit) - flt(journal.total_credit)) < TOLERANCE
		and flt(bank.debit) > withheld
	)
	cost_center = default_cost_center(journal.company, "Journal Entry Account")
	currency = frappe.get_cached_value("Company", journal.company, "default_currency")
	department = customer_lines[0].get("department")
	for row in rows:
		journal.append(
			"accounts",
			{
				"account": row.account,
				"account_currency": currency,
				"exchange_rate": 1,
				"debit_in_account_currency": row.amount,
				"debit": row.amount,
				"credit_in_account_currency": 0,
				"credit": 0,
				"user_remark": row.description,
				ROW_CATEGORY: row.name,
				ROW_BASE: row.base,
				ROW_OFFSET: row.amount if gross else 0,
				**({"department": department} if department else {}),
				**({"cost_center": cost_center} if cost_center else {}),
			},
		)
	if gross:
		_add_to_debit(bank, -withheld)
	journal.set_amounts_in_company_currency()
	journal.set_total_debit_credit()
	return {
		"withheld": withheld,
		"rows": [{"category": row.name, "title": row.title, "amount": row.amount} for row in rows],
		"money_reduced": gross,
		"bank_account": bank.account if gross else None,
		"difference": flt(journal.get("difference")),
	}


def check_draft_permission(doc):
	"""Withhold Tax changes a draft the user may change: create on a new entry, write on a saved one."""
	if doc.docstatus != 0:
		frappe.throw(_("Withhold Tax works on a draft."), title=_("Withhold Tax"))
	if doc.is_new():
		frappe.has_permission(doc.doctype, "create", throw=True)
	else:
		doc.check_permission("write")


def apply_receive_withholding(
	doc, tax_withholding_category=None, base_amount=None, withholding_amount=None, based_on=MANUAL
):
	"""Withhold Tax on a receipt (the PaymentEntry / JournalEntry method apply_thai_withholding)."""
	check_draft_permission(doc)
	check_receive_entry(doc)
	rows = plan_receive_rows(doc, based_on, tax_withholding_category, base_amount, withholding_amount)
	if doc.doctype == "Payment Entry":
		return apply_to_payment_entry(doc, rows)
	return apply_to_journal_entry(doc, rows)


@frappe.whitelist()
def get_receive_withholding_dialog(doc):
	"""What the Withhold Tax popup of a receipt shows: the types set up for receipts in the company, the base before
	VAT of what the entry settles, and the type it withholds now."""
	doc = frappe.get_doc(frappe.parse_json(doc))
	if doc.doctype not in ("Payment Entry", "Journal Entry"):
		frappe.throw(_("Withhold Tax works on a Payment Entry or a Journal Entry."))
	check_draft_permission(doc)
	current = next((row.get(ROW_CATEGORY) for row in withholding_rows(doc)), None)
	return {
		"choices": get_choices(doc.company, doc.posting_date, RECEIVE),
		"base": get_receive_base(doc),
		"settles_documents": bool(receive_sources(doc)),
		"current": current,
	}


# ----------------------------------------------------------------------------- on save


def validate_receive_rows(doc):
	"""The rows Withhold Tax made on a receipt go to the Receive Account set for their type and company."""
	rows = withholding_rows(doc)
	if not rows:
		return
	if doc.doctype == "Payment Entry":
		if not (doc.payment_type == "Receive" and doc.party_type == "Customer"):
			frappe.throw(
				_(
					"Row #{0}: a withholding tax deduction belongs on a receipt from a Customer. A payment to a "
					"Supplier withholds through Consider for Tax Withholding."
				).format(rows[0].idx)
			)
	else:
		if doc.get("apply_tds"):
			frappe.throw(
				_(
					"A Journal Entry cannot both withhold from a supplier (Consider for Tax Withholding) and carry "
					"tax a customer withheld (row #{0})."
				).format(rows[0].idx)
			)
		if not customer_credit_lines(doc):
			frappe.throw(
				_("Row #{0}: tax a customer withheld needs a line that credits that customer.").format(
					rows[0].idx
				)
			)
	for row in rows:
		if doc.doctype == "Payment Entry":
			amount = flt(row.amount)
		else:
			amount = flt(row.debit_in_account_currency)
			if flt(row.credit_in_account_currency) or row.get("party_type") or row.get("party"):
				frappe.throw(
					_("Row #{0}: the withholding tax line is a debit without a party.").format(row.idx)
				)
		info = receive_category(row.get(ROW_CATEGORY), doc.company)
		if row.account != info.account:
			frappe.throw(
				_("Row #{0}: withholding tax {1} goes to {2}, its Receive Account for {3}, not {4}.").format(
					row.idx,
					frappe.bold(info.title),
					frappe.bold(info.account),
					doc.company,
					frappe.bold(row.account),
				)
			)
		if amount <= 0:
			frappe.throw(_("Row #{0}: the withholding tax amount must be more than zero.").format(row.idx))
