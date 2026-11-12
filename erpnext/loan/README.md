# Custody Loan document flow

Create opens an unsaved document for the user to review, save and submit. Preparing or submitting a Custody Loan or Custody Loan Return does not post another document.

- Stock outgoing: submit the Custody Loan, then Create → Pick List. Save, pick and submit it, then Create → Delivery Note. Submit the Delivery Note to issue the goods.
- Asset outgoing: submit the Custody Loan, then Create → Asset Movement. Save and submit the movement to issue the assets. A Stock Delivery Note does not submit an Asset Movement.
- Incoming: Create → Custody Loan Return from the active loan, review the returning items, save and submit it. Then Create → Receiving Delivery Note (Stock) or Asset Movement (Asset). Use the Stock User workflow action Receive Loan Return on the receiving Delivery Note. Only submission of that receiving document records the physical receipt. Stock requiring inspection enters Quarantine and retains its normal inspection checks. Asset return inspections and holds start only after the receiving Asset Movement is submitted.
- Different returned batches: use Create → Batch Exchange on the submitted Return. Review, save and submit each two-row Repack, repeating until all exchanges are submitted, then submit the receiving Delivery Note. Each exchange preserves one Item's value and links to its Return. No Repack posts automatically.

- Write-offs: create, review and submit the Loan Adjustment. It reserves its quantities as Pending Issue. Create → Stock Entry opens its unsaved Material Issue; save and submit it to post the write-off. Cancellation restores the pending reservation until the Adjustment is cancelled.
- Billing: Create → Asset Sale Invoice and Create → Consumption Invoice open unsaved invoices for the accounting user to review, save and submit through normal invoice approvals. Consumption billing requires the write-off Stock Entry to be submitted.

Source and movement documents link in both directions. Server validation locks the loan and permits only one active Pick List, Delivery Note or Asset Movement for each source; row quantities, items, warehouses and company must match the saved source. Pending returns and write-offs reserve quantities, and a return's receiving document excludes its own reservation when rechecking availability.

Use native Cancel on movement documents. Cancel dependent returns, adjustments, inspections and shipments first where the native link checks require it. Cancelling the owning Loan or Return retains its existing guarded reversal of its own documents and inspections. Delete a draft or cancelled movement through the native Delete action: its own source and Material Request row links are cleared transactionally, while unrelated linked documents still block deletion. Cancel a receiving Delivery Note before cancelling its batch exchanges.
