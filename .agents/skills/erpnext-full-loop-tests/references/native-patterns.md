# Native patterns and lifecycle template

Paths below are relative to the ERPNext repository root. Read the current source
instead of copying old line numbers or assuming helper signatures remain stable.

## Sources to inspect

| Source                                                                                                               | Pattern to reuse                                                                  | Practical limit                                                                                        |
| -------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `erpnext/tests/utils.py`, `ERPNextTestSuite`                                                                         | Transaction rollback, user reset and local cache cleanup; settings/user helpers   | Rollback does not restore every external cache or filesystem effect; inspect helper cleanup on failure |
| `erpnext/stock/doctype/stock_entry/test_stock_entry.py`, `test_material_transfer_gl_entry`                           | Real stock movement, expected SLE and account-specific GL                         | SLE/GL assertion methods are local to this class, not methods of the common test base                  |
| `erpnext/accounts/doctype/payment_entry/test_payment_entry.py`, `test_payment_against_sales_invoice_to_check_status` | Submit payment, inspect invoice status/outstanding, cancel and verify restoration | Add exact account/amount assertions when the requested coverage includes accounting                    |
| `erpnext/assets/doctype/asset/test_asset.py`, `test_purchase_asset`                                                  | Linked receipt, asset, invoice and reverse dependency cancellation                | Does not define every Asset workflow                                                                   |
| `erpnext/manufacturing/doctype/work_order/test_work_order.py`, `test_reserved_qty_for_production_cancel`             | Compare reservations/projected stock before and after cancellation                | Some existing cases call other test methods; prefer shared fixture helpers for new independent cases   |
| `erpnext/stock/report/stock_balance/test_stock_balance.py`                                                           | Explicit movements, expected report fields and ledger invariants                  | A report agreeing with another report alone is not independent proof                                   |
| `erpnext/stock/report/test_reports.py`, `execute_script_report` in tests utils                                       | Report filter execution coverage                                                  | This is smoke coverage unless the test asserts meaningful business results                             |

Use the module's own examples for other domains; this list is an entry point,
not a restriction to these modules. Existing native tests may contain shortcuts
or defects. Follow verified patterns and business requirements, not every detail.

## General template

This is a structural sketch, not executable code or a generated test fixture.
Replace each comment with the assigned module's real builders and assertions:

```python
from erpnext.tests.utils import ERPNextTestSuite


class TestAssignedDocType(ERPNextTestSuite):
    def test_submit_follow_up_and_reverse_restores_baseline(self):
        # Arrange independent native fixtures and capture relevant baseline.
        # Build a Draft using the real helper with automatic Submit disabled.
        # Assert Draft status, links, and expected absence/presence of effects.

        # Insert/save and submit through public document APIs.
        # Reload owner and affected linked documents.
        # Assert state and independently calculated persisted business effects.

        # Create the supported downstream action and verify its effects.
        # If the domain blocks reversal while it is active, assert rejection.

        # Reverse/cancel dependents, then reverse the owner in native order.
        # Reload and assert net restoration and final document/link states.
        # Native base teardown rolls back fixtures after the test.
        pass
```

Do not force Draft/Submit/Cancel onto non-submittable DocTypes. For those flows,
use the actual save/status/closure/deletion APIs and supported reversibility.
Keep validation scenarios in separate tests with useful names.

## Rejected transitions and atomicity

Frappe request handling and a direct controller call can have different rollback
boundaries. Identify the real boundary first. To emulate request rollback in a
direct integration test, snapshot relevant counts/balances, set a uniquely named
savepoint, attempt the action with `assertRaises` for the specific exception,
and roll back to that savepoint in `finally`. Reload documents and verify the
snapshot and that no orphan or partially submitted records remain.

This proves request-level atomicity; it does not prove that a controller rolls
itself back when called without a transaction owner. Label that distinction.
Where the controller promises its own atomicity, inspect persisted effects before
the test's rollback as well. Never hide unexpected writes by rolling back and then
claiming the controller made none. Discard/reload mutated in-memory documents after
a failed operation.

## Isolation

Inspect `ERPNextTestSuite.tearDown` in the current checkout. Keep its rollback and
user/cache reset by calling `super().tearDown()` when overriding it. Restore changed
settings/users and nontransactional state using `addCleanup` or `try/finally`, so
failures also restore them. Do not assume a settings context manager implements a
failure-safe `finally` without reading it. Avoid `frappe.db.commit()` in tests;
DDL or explicit commits can defeat fixture rollback.
