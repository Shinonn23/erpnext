Unique serial number assigned to an instance (unit) of an Item. This is used incase each unit of the Item needs to be tracked separately for warranty or recall purposes.

Bulk receipts keep the same duplicate and historical availability checks. Legacy newline-separated stock ledger
serial numbers are matched in groups of 50 per regular expression, avoiding MariaDB's compiled regex size limit
when a receipt contains thousands of serials. Bundle entries continue to use exact serial membership.
