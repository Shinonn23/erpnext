import csv
import datetime
import re
from decimal import Decimal

import frappe
import requests
from frappe import _
from frappe.defaults import get_user_default_as_list
from frappe.rate_limiter import rate_limit
from lxml import etree
from num2words import num2words
from num2words.currency import parse_currency_parts


def amount_in_bahttext(amount):
	return num2words(amount, to="currency", lang="th")


def amount_to_text(amount, currency=None, lang=None):
	try:
		currency = currency or frappe.defaults.get_global_default("currency")
		lang = lang or "en"
		if currency == "THB" and lang == "en":
			baht, satang, negative = parse_currency_parts(Decimal(str(amount)))
			prefix = "minus " if negative else ""
			return f"{prefix}{num2words(baht)} baht, {num2words(satang)} satang".title()
		return num2words(amount, to="currency", lang=lang, currency=currency).title()
	except Exception:
		return ""


def full_thai_date(date_str):
	if not date_str:
		return ""
	date = datetime.datetime.strptime(str(date_str), "%Y-%m-%d")
	month_name = (
		"x January February March April May June July August September October November December".split()[
			date.month
		]
	)
	thai_year = date.year + 543
	return f"{date.day} {month_name} {thai_year}"  # 30 October 2560


def get_prefix_for_address(data):
	prefix_thambol, prefix_amphur, prefix_province = "", "", ""
	companies = get_user_default_as_list("company", frappe.session.user)
	company = companies[0] if companies else frappe.db.get_single_value("Global Defaults", "default_company")
	company_doc = frappe.get_cached_doc("Company", company)
	if company_doc.get("enable_prefix_for_address"):
		prefix_thambol = company_doc.get("prefix_thambol_other_province") or ""
		prefix_amphur = company_doc.get("prefix_amphur_other_province") or ""
		prefix_province = company_doc.get("prefix_province_other_province") or ""
		if "กรุงเทพมหานคร" in [data.get("vProvince", ""), data.get("province", "")]:
			prefix_thambol = company_doc.get("prefix_thambol_bangkok") or ""
			prefix_amphur = company_doc.get("prefix_amphur_bangkok") or ""
			prefix_province = company_doc.get("prefix_province_bangkok") or ""
	return prefix_thambol, prefix_amphur, prefix_province


RD_URL = "https://rdws.rd.go.th/serviceRD3/vatserviceRD3.asmx"
# connect, read (seconds): a slow RD never holds a web worker for long. No retries: the user presses again.
RD_TIMEOUT = (5, 15)
RD_CACHE_SECONDS = 60 * 60
RD_UNREACHABLE = "The Revenue Department service is not reachable now. Type the details by hand."
RD_NOT_FOUND = (
	"Tax ID {0} (branch {1}) is not found at the Revenue Department as a VAT registrant. "
	"Type the details by hand."
)


def clean_tax_id(tax_id):
	"""Digits only; refuse anything but a 13-digit Thai Tax ID (it goes into the SOAP payload)."""
	tax_id = re.sub(r"[\s-]", "", str(tax_id or ""))
	if not re.fullmatch(r"[0-9]{13}", tax_id):
		frappe.throw(_("Tax ID must be 13 digits"))
	return tax_id


def clean_branch(branch):
	"""Branch 0-99999 as an int; blank is the head office (0). Anything else is refused, never guessed."""
	text = re.sub(r"\s", "", str(branch if branch is not None else ""))
	if not text:
		return 0
	if not re.fullmatch(r"[0-9]{1,5}", text):
		frappe.throw(_("Branch must be 1 to 5 digits (00000 is the head office)"))
	return int(text)


def _soap_payload(tax_id, branch_number):
	return (
		"<soap:Envelope xmlns:soap='http://www.w3.org/2003/05/soap-envelope' "
		"xmlns:vat='https://rdws.rd.go.th/serviceRD3/vatserviceRD3'>"
		"<soap:Header/>"
		"<soap:Body>"
		"<vat:Service>"
		"<vat:username>anonymous</vat:username>"
		"<vat:password>anonymous</vat:password>"
		f"<vat:TIN>{tax_id}</vat:TIN>"
		"<vat:Name></vat:Name>"
		"<vat:ProvinceCode>0</vat:ProvinceCode>"
		f"<vat:BranchNumber>{branch_number}</vat:BranchNumber>"
		"<vat:AmphurCode>0</vat:AmphurCode>"
		"</vat:Service>"
		"</soap:Body>"
		"</soap:Envelope>"
	)


def parse_rd_response(content):
	"""The vX -> anyType values of the RD SOAP answer, as a dict of stripped strings."""
	result = etree.fromstring(content)
	data = {}
	value = False
	for element in result.iter():
		tag = etree.QName(element).localname
		if not value and tag[:1] == "v":
			value = tag
			continue
		if value and tag == "anyType":
			data[value] = (element.text or "").strip()
		value = False
	return data


def fetch_rd_vat_record(tax_id, branch=None):
	"""The Revenue Department's VAT registrant record of a Tax ID and branch, or None when RD has none.

	Only the Tax ID and the branch number are sent (anonymous RD login). The certificate is verified
	(the RD chain validates with certifi), there is a timeout and no retry, and an answer is cached for an
	hour per Tax ID and branch so a form typing loop never storms RD. An unreachable RD or a broken answer
	is logged and raised as one friendly message.
	"""
	tax_id = clean_tax_id(tax_id)
	branch_number = clean_branch(branch)
	cache_key = f"rd_vat_record:{tax_id}:{branch_number}"
	cached = frappe.cache.get_value(cache_key)
	if cached is not None:
		return cached or None
	try:
		response = requests.post(
			RD_URL,
			data=_soap_payload(tax_id, branch_number).encode("utf-8"),
			headers={"content-type": "application/soap+xml; charset=utf-8"},
			params={"wsdl": ""},
			timeout=RD_TIMEOUT,
		)
		response.raise_for_status()
		data = parse_rd_response(response.content)
	except (requests.RequestException, etree.XMLSyntaxError, ValueError):
		frappe.log_error(title="Revenue Department VAT lookup failed")
		frappe.throw(_(RD_UNREACHABLE), title=_("Revenue Department"))
	found = bool(data) and not data.get("vmsgerr") and bool(data.get("vName") or data.get("vBranchName"))
	frappe.cache.set_value(cache_key, data if found else {}, expires_in_sec=RD_CACHE_SECONDS)
	return data if found else None


def _part(value):
	value = (value or "").strip()
	return "" if value in ("-", "") else value


def rd_legal_name(data):
	"""Legal name as RD writes it: title + name (+ surname), the title not doubled."""
	name = _part(data.get("vBranchName")) or _part(data.get("vName"))
	title = _part(data.get("vBranchTitleName")) or _part(data.get("vtitleName"))
	if title and not name.startswith(title):
		name = f"{title} {name}"
	surname = _part(data.get("vSurname"))
	if surname:
		name = f"{name} {surname}"
	return name.strip()


def branch_code_of(branch_number):
	return str(int(branch_number)).zfill(5)


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def get_address_by_tax_id(tax_id: str | None = None, branch: str | None = None):
	"""Address of a Tax ID and branch from the Revenue Department (Address form, "By Tax ID")."""
	if not (tax_id and branch):
		frappe.throw(_("Please provide both Tax ID and Branch number"))
	data = fetch_rd_vat_record(tax_id, branch)
	if not data:
		frappe.throw(_(RD_NOT_FOUND).format(clean_tax_id(tax_id), branch_code_of(clean_branch(branch))))
	return finalize_address_dict(data)


PARTY_DOCTYPES = {"Customer": "customer_name", "Supplier": "supplier_name"}


def existing_parties(doctype, tax_id, branch_code):
	"""Parties of the DocType with the same Tax ID that the user may read, the same branch first.

	Any branch counts: the branches of one party are its Addresses (``custom/branches.py``), so another branch of
	a known Tax ID is an Address of that party rather than a new one.
	"""
	rows = frappe.get_all(
		doctype,
		filters={"tax_id": ["like", f"%{tax_id[-6:]}%"]},
		fields=["name", PARTY_DOCTYPES[doctype], "tax_id", "branch_code"],
		ignore_permissions=True,
	)
	same = []
	for row in rows:
		if re.sub(r"\D", "", row.tax_id or "") != tax_id:
			continue
		if not frappe.has_permission(doctype, "read", doc=row.name):
			continue
		digits = re.sub(r"\D", "", row.branch_code or "")
		code = branch_code_of(int(digits)) if digits and len(digits) <= 5 else ""
		same.append(
			{
				"name": row.name,
				"title": row.get(PARTY_DOCTYPES[doctype]) or row.name,
				"branch_code": code,
				"same_branch": code == branch_code,
			}
		)
	same.sort(key=lambda party: not party["same_branch"])
	return same


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def get_party_by_tax_id(doctype: str, tax_id: str, branch: str | None = "00000"):
	"""What a new Customer or Supplier needs from a Tax ID: the parties that already have it, and RD's record.

	Returns {"found", "tax_id", "branch_code", "legal_name", "address", "existing"}; found is False for an
	individual or a party that is not a VAT registrant (the user then types by hand; nothing is blocked).
	"""
	if doctype not in PARTY_DOCTYPES:
		frappe.throw(_("Only a Customer or a Supplier can be filled from a Tax ID"))
	if not frappe.has_permission(doctype, "create"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	tax_id = clean_tax_id(tax_id)
	branch_code = branch_code_of(clean_branch(branch))
	out = {
		"found": False,
		"tax_id": tax_id,
		"branch_code": branch_code,
		"legal_name": "",
		"address": {},
		"existing": existing_parties(doctype, tax_id, branch_code),
	}
	# A Customer is one per Tax ID (unique index; another branch is an Address of it). A Supplier may be one per
	# Tax ID and branch (the app's rule), so only the same branch stops it; another branch is a hint only.
	if doctype == "Customer" and out["existing"]:
		return out
	if any(party["same_branch"] for party in out["existing"]):
		return out
	data = fetch_rd_vat_record(tax_id, branch_code)
	if data:
		out.update(found=True, legal_name=rd_legal_name(data), address=finalize_address_dict(data))
	return out


RD_LOOKUP_KEY = "rd_address_lookup"
PARTY_TYPE_FIELDS = {"Customer": "customer_type", "Supplier": "supplier_type"}
# The defaults a party saved straight from the preview takes: (settings DocType, settings field, party field).
PARTY_DEFAULTS = {
	"Customer": (
		("Selling Settings", "customer_group", "customer_group"),
		("Selling Settings", "territory", "territory"),
	),
	"Supplier": (("Buying Settings", "supplier_group", "supplier_group"),),
}


def rd_party_address(data):
	"""RD's address as the attributes ERPNext's ``create_primary_address`` reads (international layout; City is
	required by ``make_address``, so a record without อำเภอ takes จังหวัด)."""
	a = finalize_address_dict(data)
	if not (a["address_line1"] or a["address_line2"] or a["city"] or a["state"]):
		return {}
	return {
		"address_line1": a["address_line1"] or a["name"],
		"address_line2": a["address_line2"],
		"city": a["city"] or a["state"],
		"state": a["state"],
		"pincode": a["pincode"],
		"country": "Thailand",
	}


def party_defaults(doctype):
	"""The defaults a preview save needs, and the labels of the ones the settings lack."""
	values, missing = {}, []
	for settings, field, target in PARTY_DEFAULTS[doctype]:
		value = frappe.db.get_single_value(settings, field)
		if value:
			values[target] = value
		else:
			missing.append(_(frappe.unscrub(field)))
	return values, missing


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60)
def create_party_from_tax_id(doctype: str, tax_id: str, branch: str | None = "00000"):
	"""Save of the Tax ID preview: the server reads RD again and creates the party with the default group (and
	territory) and its Billing Address in one go. The browser sends only the Tax ID and branch.

	Returns {"name"} on success; {"existing": [...]} when the party already exists; {"missing": [labels]} when a
	required default is not set (the client then opens the form to edit); {"found": False} when RD has no record.
	"""
	if doctype not in PARTY_DOCTYPES:
		frappe.throw(_("Only a Customer or a Supplier can be filled from a Tax ID"))
	frappe.has_permission(doctype, "create", throw=True)
	tax_id = clean_tax_id(tax_id)
	branch_code = branch_code_of(clean_branch(branch))
	existing = existing_parties(doctype, tax_id, branch_code)
	if (doctype == "Customer" and existing) or any(p["same_branch"] for p in existing):
		return {"existing": existing}
	defaults, missing = party_defaults(doctype)
	if missing:
		return {"missing": missing}
	data = fetch_rd_vat_record(tax_id, branch_code)
	if not data:
		return {"found": False}
	party = frappe.get_doc(
		{
			"doctype": doctype,
			PARTY_DOCTYPES[doctype]: rd_legal_name(data),
			PARTY_TYPE_FIELDS[doctype]: "Company",
			"tax_id": tax_id,
			**defaults,
		}
	)
	if party.meta.has_field("branch_code"):
		party.branch_code = branch_code
	party.update(rd_party_address(data))
	party.insert()
	return {"name": party.name}


def fill_rd_address_before_insert(doc, method=None):
	"""Customer / Supplier before_insert: a form opened by "Edit Before Saving" carries only the Tax ID and branch
	(the non-stored key ``rd_address_lookup`` = "<tax id>:<branch>"); the server reads RD again here so ERPNext's
	``create_primary_address`` makes the Billing Address. The browser never supplies the stored address."""
	lookup = doc.get(RD_LOOKUP_KEY)
	if doc.doctype not in PARTY_DOCTYPES or not isinstance(lookup, str):
		return
	doc.__dict__.pop(RD_LOOKUP_KEY, None)
	if doc.get("address_line1"):
		return
	tax_id, _sep, branch = lookup.partition(":")
	tax_id = re.sub(r"\D", "", tax_id)
	if len(tax_id) != 13 or tax_id != re.sub(r"\D", "", doc.tax_id or ""):
		return  # the user changed the Tax ID: RD's address of the old one does not belong to this party
	try:
		branch_code = branch_code_of(clean_branch(branch))
		if doc.get("branch_code"):
			if branch_code_of(clean_branch(doc.branch_code)) != branch_code:
				return  # the user changed the branch: RD's address of the old branch does not belong here
		data = fetch_rd_vat_record(tax_id, branch_code)
	except frappe.ValidationError:
		# RD down or a bad branch never blocks saving: the party is saved without the address
		frappe.clear_last_message()
		frappe.msgprint(_("The address could not be read from the Revenue Department. Add it after saving."))
		return
	if data:
		doc.update(rd_party_address(data))


def finalize_address_dict(data):
	def get_part(data, key, value):
		return (_part(data.get(key)) and value % (map[key], _part(data.get(key)))) or ""

	prefix_thambol, prefix_amphur, prefix_province = get_prefix_for_address(data)

	map = {
		"vBuildingName": "Building",
		"vFloorNumber": "Floor",
		"vVillageName": "Village",
		"vRoomNumber": "Room",
		"vMooNumber": "Moo",
		"vSoiName": "Soi",
		"vStreetName": "Road ",
		"vThambol": prefix_thambol,
		"vAmphur": prefix_amphur,
		"vProvince": prefix_province,
	}

	name = rd_legal_name(data)
	house = _part(data.get("vHouseNumber"))
	village = get_part(data, "vVillageName", "%s %s")
	soi = get_part(data, "vSoiName", "%s %s")
	moo = get_part(data, "vMooNumber", "%s %s")
	building = get_part(data, "vBuildingName", "%s %s")
	floor = get_part(data, "vFloorNumber", "%s %s")
	room = get_part(data, "vRoomNumber", "%s %s")
	street = get_part(data, "vStreetName", "%s%s")
	thambon = get_part(data, "vThambol", "%s%s")
	amphur = get_part(data, "vAmphur", "%s%s")
	province = get_part(data, "vProvince", "%s%s")
	postal = _part(data.get("vPostCode"))

	address_parts = filter(lambda x: x != "", [house, village, soi, moo, building, floor, room, street])
	return {
		"name": name,
		"address_line1": " ".join(address_parts),
		"address_line2": thambon,
		"city": amphur,
		"state": province,
		"pincode": postal,
	}


def import_thai_zip_code_data():
	file_path = frappe.get_app_path("erpnext", "public", "files", "thailand", "thai_zip_code.csv")
	with open(file_path, encoding="utf-8") as csvfile:
		reader = csv.DictReader(csvfile)
		for row in reader:
			values = {
				"zip_code": row["Zip Code"],
				"tambon": row["Tambon"],
				"amphur": row["Amphur"],
				"province": row["Province"],
			}
			# A tambon can have more than one zip code, so the ID alone is not unique.
			# Keep the ID as name for the first one, use "ID-ZipCode" for the others.
			name = row["ID"]
			if frappe.db.get_value("Thai Zip Code", name, "zip_code") not in (
				None,
				row["Zip Code"],
			):
				name = f"{row['ID']}-{row['Zip Code']}"
			if frappe.db.exists("Thai Zip Code", name):
				# Update names that were corrected in the CSV
				frappe.db.set_value("Thai Zip Code", name, values, update_modified=False)
				continue
			doc = frappe.get_doc({"doctype": "Thai Zip Code", "name": name, **values})
			doc.insert(ignore_permissions=True)
		frappe.db.commit()
	return "Import completed successfully."


@frappe.whitelist()
def get_location_by_zip_code(zip_code):
	locations = frappe.get_all(
		"Thai Zip Code",
		filters={"zip_code": zip_code},
		fields=["name", "zip_code", "tambon", "amphur", "province"],
	)
	location_list = []
	for loc in locations:
		prefix_thambol, prefix_amphur, prefix_province = get_prefix_for_address(loc)
		location_list.append(
			{
				"id": loc["name"],
				"zip_code": loc["zip_code"],
				"tambon": "{}{}".format(prefix_thambol, loc["tambon"]),
				"amphur": "{}{}".format(prefix_amphur, loc["amphur"]),
				"province": "{}{}".format(prefix_province, loc["province"]),
			}
		)
	return location_list
