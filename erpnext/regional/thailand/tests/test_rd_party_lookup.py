"""Offline tests of the Revenue Department lookup (no network: requests.post is mocked)."""

import unittest
from unittest import mock

import requests

from erpnext.regional.thailand import utils

RD_OK = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><soap:Body>
<ServiceResponse xmlns="https://rdws.rd.go.th/serviceRD3/vatserviceRD3"><ServiceResult>
<vNID><anyType xsi:type="xsd:string">0105551058581</anyType></vNID>
<vtitleName><anyType xsi:type="xsd:string">บริษัท</anyType></vtitleName>
<vName><anyType xsi:type="xsd:string">คอนเนค ไดแอกโนสติกส์ จำกัด</anyType></vName>
<vSurname><anyType xsi:type="xsd:string">-</anyType></vSurname>
<vBranchTitleName><anyType xsi:type="xsd:string">บริษัท</anyType></vBranchTitleName>
<vBranchName><anyType xsi:type="xsd:string">คอนเนค ไดแอกโนสติกส์ จำกัด</anyType></vBranchName>
<vBranchNumber><anyType xsi:type="xsd:int">0</anyType></vBranchNumber>
<vVillageName><anyType xsi:type="xsd:string">เวนิส ดี ไอริส</anyType></vVillageName>
<vHouseNumber><anyType xsi:type="xsd:string">1/57-58</anyType></vHouseNumber>
<vSoiName><anyType xsi:type="xsd:string">วัชรพล 2/7</anyType></vSoiName>
<vStreetName><anyType xsi:type="xsd:string">-</anyType></vStreetName>
<vThambol><anyType xsi:type="xsd:string">ท่าแร้ง</anyType></vThambol>
<vAmphur><anyType xsi:type="xsd:string">บางเขน</anyType></vAmphur>
<vProvince><anyType xsi:type="xsd:string">กรุงเทพมหานคร</anyType></vProvince>
<vPostCode><anyType xsi:type="xsd:string">10220</anyType></vPostCode>
<vmsgerr><anyType xsi:type="xsd:string"></anyType></vmsgerr>
</ServiceResult></ServiceResponse></soap:Body></soap:Envelope>""".encode()

RD_NOT_FOUND = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><soap:Body>
<ServiceResponse xmlns="https://rdws.rd.go.th/serviceRD3/vatserviceRD3"><ServiceResult>
<vmsgerr><anyType xsi:type="xsd:string">ไม่พบข้อมูล</anyType></vmsgerr>
</ServiceResult></ServiceResponse></soap:Body></soap:Envelope>""".encode()


class Thrown(Exception):
	pass


class FakeCache:
	def __init__(self):
		self.store = {}

	def get_value(self, key):
		return self.store.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.store[key] = value


def response(content, status=200):
	r = mock.Mock(content=content, status_code=status)
	r.raise_for_status = mock.Mock()
	if status >= 400:
		r.raise_for_status.side_effect = requests.HTTPError("bad")
	return r


class Doc:
	"""A Document as far as the insert hook sees it: attributes, get and update."""

	def __init__(self, **values):
		self.__dict__.update(values)

	def get(self, key):
		return self.__dict__.get(key)

	def update(self, values):
		self.__dict__.update(values)


class TestRDLookup(unittest.TestCase):
	def setUp(self):
		self.cache = FakeCache()

		def throw(msg, *args, **kwargs):
			raise Thrown(msg)

		patches = [
			mock.patch.object(utils.frappe, "cache", self.cache, create=True),
			mock.patch.object(utils.frappe, "throw", throw),
			mock.patch.object(utils.frappe, "log_error", mock.Mock()),
			mock.patch.object(utils, "_", lambda text: text),
			mock.patch.object(utils, "get_prefix_for_address", lambda data: ("", "", "")),
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

	def test_found_verifies_certificate_with_timeout_and_sends_only_tin_and_branch(self):
		with mock.patch.object(utils.requests, "post", return_value=response(RD_OK)) as post:
			data = utils.fetch_rd_vat_record("0105-55105-8581", "00000")
		kwargs = post.call_args.kwargs
		self.assertNotIn("verify", kwargs)  # requests verifies by default
		self.assertEqual(kwargs["timeout"], utils.RD_TIMEOUT)
		payload = kwargs["data"].decode()
		self.assertIn("<vat:TIN>0105551058581</vat:TIN>", payload)
		self.assertIn("<vat:BranchNumber>0</vat:BranchNumber>", payload)
		self.assertIn("<vat:Name></vat:Name>", payload)
		self.assertEqual(utils.rd_legal_name(data), "บริษัท คอนเนค ไดแอกโนสติกส์ จำกัด")
		address = utils.finalize_address_dict(data)
		self.assertEqual(address["address_line1"], "1/57-58 Village เวนิส ดี ไอริส Soi วัชรพล 2/7")
		self.assertEqual(address["pincode"], "10220")

	def test_dash_parts_are_skipped(self):
		with mock.patch.object(utils, "get_prefix_for_address", return_value=("", "", "")):
			address = utils.finalize_address_dict(
				{
					"vHouseNumber": "-",
					"vSoiName": "-",
					"vStreetName": "พหลโยธิน",
					"vThambol": "-",
					"vAmphur": "เมือง",
					"vProvince": "ชลบุรี",
					"vPostCode": "-",
				}
			)
		self.assertEqual(address["address_line1"], "Road พหลโยธิน")
		self.assertEqual((address["address_line2"], address["city"], address["pincode"]), ("", "เมือง", ""))
		self.assertNotIn("county", address)

	def test_cached_answer_is_not_asked_again(self):
		with mock.patch.object(utils.requests, "post", return_value=response(RD_OK)) as post:
			utils.fetch_rd_vat_record("0105551058581", "0")
			utils.fetch_rd_vat_record("0105551058581", "00000")
		self.assertEqual(post.call_count, 1)

	def test_not_found_is_none_not_an_error(self):
		with mock.patch.object(utils.requests, "post", return_value=response(RD_NOT_FOUND)):
			self.assertIsNone(utils.fetch_rd_vat_record("1234567890123", "0"))

	def test_timeout_bad_status_and_bad_xml_are_one_friendly_message(self):
		for side in (requests.Timeout("slow"), response(b"x", 500), response(b"<not xml")):
			self.cache.store.clear()
			kwargs = {"side_effect": side} if isinstance(side, Exception) else {"return_value": side}
			with mock.patch.object(utils.requests, "post", **kwargs):
				with self.assertRaises(Thrown) as raised:
					utils.fetch_rd_vat_record("0105551058581", "0")
			self.assertEqual(str(raised.exception), utils.RD_UNREACHABLE)

	def test_input_is_checked_before_any_call(self):
		with mock.patch.object(utils.requests, "post") as post:
			for tax_id, branch in (("12345", "0"), ("0105551058581", "abc"), ("0105551058581", "123456")):
				with self.assertRaises(Thrown):
					utils.fetch_rd_vat_record(tax_id, branch)
		post.assert_not_called()

	def test_legal_name_does_not_double_the_title(self):
		self.assertEqual(
			utils.rd_legal_name({"vtitleName": "นาย", "vName": "สมชาย", "vSurname": "ใจดี"}), "นาย สมชาย ใจดี"
		)
		self.assertEqual(
			utils.rd_legal_name({"vBranchTitleName": "บริษัท", "vBranchName": "บริษัท เอ จำกัด"}), "บริษัท เอ จำกัด"
		)

	def test_no_session_with_verify_false_left(self):
		import inspect

		self.assertNotIn("verify = False", inspect.getsource(utils))
		self.assertNotIn("verify=False", inspect.getsource(utils))

	def test_existing_party_is_offered_without_asking_rd(self):
		import frappe

		rows = [
			frappe._dict(name="S-1", supplier_name="A", tax_id="0105-551058581", branch_code="1"),
			frappe._dict(name="S-2", supplier_name="B", tax_id="0105551058581", branch_code="00000"),
			frappe._dict(name="S-3", supplier_name="C", tax_id="9105551058581", branch_code="00000"),
			frappe._dict(name="S-4", supplier_name="D", tax_id="0105551058581", branch_code="00000"),
		]
		with (
			mock.patch.object(utils.frappe, "get_all", return_value=rows),
			mock.patch.object(utils.frappe, "has_permission", lambda dt, ptype, doc=None: doc != "S-4"),
			mock.patch.object(utils.requests, "post") as post,
		):
			out = utils.get_party_by_tax_id("Supplier", "0105551058581", "0")
		post.assert_not_called()
		self.assertEqual([p["name"] for p in out["existing"]], ["S-2", "S-1"])
		self.assertEqual(out["existing"][1]["branch_code"], "00001")
		self.assertFalse(out["found"])

	def _lookup_with(self, doctype, rows):
		import frappe

		with (
			mock.patch.object(utils.frappe, "get_all", return_value=rows),
			mock.patch.object(utils.frappe, "has_permission", lambda *a, **k: True),
			mock.patch.object(utils.requests, "post", return_value=response(RD_OK)) as post,
		):
			out = utils.get_party_by_tax_id(doctype, "0105551058581", "0")
		return out, post, frappe

	def test_supplier_at_another_branch_still_asks_rd_and_hints(self):
		import frappe

		rows = [frappe._dict(name="S-1", supplier_name="A", tax_id="0105551058581", branch_code="00001")]
		out, post, _f = self._lookup_with("Supplier", rows)
		post.assert_called_once()
		self.assertTrue(out["found"])
		self.assertEqual([p["name"] for p in out["existing"]], ["S-1"])
		self.assertFalse(out["existing"][0]["same_branch"])

	def test_customer_at_any_branch_is_offered_without_asking_rd(self):
		import frappe

		rows = [frappe._dict(name="C-1", customer_name="A", tax_id="0105551058581", branch_code="00001")]
		out, post, _f = self._lookup_with("Customer", rows)
		post.assert_not_called()
		self.assertFalse(out["found"])

	def _create(self, doctype, rows=(), settings=None, post_content=RD_OK):
		import frappe

		settings = (
			settings
			if settings is not None
			else {"customer_group": "Commercial", "territory": "Thailand", "supplier_group": "Local"}
		)
		inserted = []

		class Party(frappe._dict):
			meta = mock.Mock(has_field=lambda f: f == "branch_code")

			def insert(self):
				inserted.append(self)
				self.name = "P-1"

		with (
			mock.patch.object(utils.frappe, "get_all", return_value=list(rows)),
			mock.patch.object(utils.frappe, "has_permission", lambda *a, **k: True),
			mock.patch.object(utils.frappe, "get_doc", lambda d: Party(d)),
			mock.patch.object(
				utils.frappe, "db", mock.Mock(get_single_value=lambda dt, f: settings.get(f)), create=True
			),
			mock.patch.object(utils.frappe, "unscrub", lambda f: f, create=True),
			mock.patch.object(utils.requests, "post", return_value=response(post_content)) as post,
		):
			out = utils.create_party_from_tax_id(doctype, "0105551058581", "0")
		return out, inserted, post

	def test_save_creates_the_party_with_defaults_and_rd_address_read_on_the_server(self):
		out, inserted, post = self._create("Customer")
		post.assert_called_once()
		self.assertEqual(out, {"name": "P-1"})
		party = inserted[0]
		self.assertEqual(party.customer_name, "บริษัท คอนเนค ไดแอกโนสติกส์ จำกัด")
		self.assertEqual(party.customer_type, "Company")
		self.assertEqual((party.customer_group, party.territory), ("Commercial", "Thailand"))
		self.assertEqual(party.branch_code, "00000")
		self.assertEqual(party.address_line2, "ท่าแร้ง")
		self.assertEqual(party.city, "บางเขน")
		self.assertEqual(party.state, "กรุงเทพมหานคร")
		self.assertEqual(party.country, "Thailand")

	def test_save_with_a_missing_default_creates_nothing(self):
		out, inserted, post = self._create("Supplier", settings={})
		self.assertEqual(out, {"missing": ["supplier_group"]})
		self.assertEqual(inserted, [])
		post.assert_not_called()

	def test_save_of_an_existing_party_or_unknown_tax_id_creates_nothing(self):
		import frappe

		rows = [frappe._dict(name="C-1", customer_name="A", tax_id="0105551058581", branch_code="00001")]
		out, inserted, _post = self._create("Customer", rows=rows)
		self.assertEqual([p["name"] for p in out["existing"]], ["C-1"])
		out, inserted2, _post = self._create("Supplier", post_content=RD_NOT_FOUND)
		self.assertEqual(out, {"found": False})
		self.assertEqual(inserted + inserted2, [])

	def test_edit_before_saving_reads_rd_on_insert_only_for_the_same_tax_id(self):
		import frappe

		doc = Doc(doctype="Supplier", tax_id="0105551058581", rd_address_lookup="0105551058581:00000")
		with mock.patch.object(utils.requests, "post", return_value=response(RD_OK)):
			utils.fill_rd_address_before_insert(doc)
		self.assertEqual(doc.city, "บางเขน")
		self.assertIsNone(doc.get("rd_address_lookup"))
		changed = Doc(doctype="Supplier", tax_id="1111111111111", rd_address_lookup="0105551058581:00000")
		with mock.patch.object(utils.requests, "post") as post:
			utils.fill_rd_address_before_insert(changed)
		post.assert_not_called()
		self.assertIsNone(changed.get("address_line1"))

	def test_rd_down_on_insert_never_blocks_saving(self):
		import frappe

		doc = Doc(doctype="Customer", tax_id="0105551058581", rd_address_lookup="0105551058581:00000")
		with (
			mock.patch.object(utils.frappe, "throw", side_effect=frappe.ValidationError("down")),
			mock.patch.object(utils.frappe, "clear_last_message", mock.Mock(), create=True),
			mock.patch.object(utils.frappe, "msgprint", mock.Mock()) as msg,
			mock.patch.object(utils.requests, "post", side_effect=requests.ConnectionError()),
		):
			utils.fill_rd_address_before_insert(doc)
		msg.assert_called_once()
		self.assertIsNone(doc.get("address_line1"))

	def test_only_customer_or_supplier(self):
		with self.assertRaises(Thrown):
			utils.get_party_by_tax_id("User", "0105551058581")


if __name__ == "__main__":
	unittest.main()
