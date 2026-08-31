"""A recorded ERP, in app code, so a graded run can never reach a real one.

`estate_writes.EstateWriter` takes its wire injected, and every test in the
server suite hands it `tests/estate_fixtures.FakeEstate`. That is the right
home for a connector test and the wrong home for this: a **console-triggered**
eval run of `order-release` executes inside the deployment, where `tests/` is
not importable and would be no safeguard if it were. A graded run that must
never reach a customer's ERP needs its fake to live on the same side of the
wall as the flow it is grading.

So this is the smallest honest estate: `salesOrders`, read and patched, with
`erp.py`'s own semantics and nothing else. Two properties make it worth having
rather than a stub:

1. **It is as permissive as the real one.** `functions/erp.py:275-322` validates
   the picklist and stops — no transition validation, no `If-Match`, no
   optimistic concurrency — so `Pending Approval` straight to `Closed` is a
   legal PATCH here because it is a legal PATCH there. A tidier fake would test
   a system nothing serves and would quietly hide the only argument for
   `estate_writes.WRITABLE`.
2. **It is seeded from the fixture's own recorded rows.** One recording
   establishes the graph and the estate together, so an eval world cannot hold
   an order the ERP has never heard of.

`tests/estate_fixtures.FakeEstate` stays where it is — it serves eighteen
resources, list routes, watermarks and outages, none of which this needs — and
a test asserts the two agree on the `salesOrders` semantics they share, so they
cannot drift apart in silence.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger("cumulus.estate_fake")

#: The one app and resource this serves. Anything else is a 404, which is the
#: honest answer: a write plane with one row in its table has one door.
ERP_APP = "erp"
SALES_ORDERS = "salesOrders"

#: `erp.py`'s picklist, verbatim (functions/erp.py:49-69). The only validation
#: standing between a caller and a stored value.
SALES_ORDER_STATUSES: tuple[str, ...] = (
    "Pending Approval", "Pending Fulfillment", "Partially Fulfilled",
    "Pending Billing", "Billed", "Closed",
)

#: Settable fields, from `erp.py`'s ENTITIES. `lines` is here because the real
#: entity has it and is refused below, so a caller that started sending lines
#: gets an error rather than a quietly wrong total.
SALES_ORDER_FIELDS: tuple[str, ...] = (
    "customerId", "lines", "tranDate", "status", "memo",
)

#: Server-owned. Sent in a body they are **ignored rather than rejected**, so a
#: caller can round-trip a record it just read.
READ_ONLY: tuple[str, ...] = ("id", "tranId", "total", "createdAt", "updatedAt")


class FakeEstateError(Exception):
    """What the recorded estate raises where its API answers with a status.

    `status` and `message` are `namespaces.ApiError`'s pair, verbatim, and
    `estate_writes._rejected` duck-types on exactly those two — so this needs
    no relationship to the exception classes in `estate_writes`, and the wire
    stays a plain injected callable rather than a protocol with a base class.
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


def _bumped(previous: str) -> str:
    """A fresh `updatedAt`, guaranteed later than the one it replaces.

    The estate stamps server time; a fixture's rows carry a recorded date, and
    a world built on a frozen clock would otherwise write a timestamp that does
    not move. Watermarks order strictly, so "later" is the property that
    matters and "now" is not.
    """
    now = datetime.now(timezone.utc)
    try:
        was = datetime.fromisoformat(previous)
    except (TypeError, ValueError):
        return now.isoformat()
    return max(now, was + timedelta(microseconds=1)).isoformat()


def parse(url: str) -> tuple[str, str, str]:
    """`(app, resource, record_id)` out of an estate URL.

    The connector builds `https://{project}-{app}.web.app/api/{ns}/v1/{res}/{id}`
    and this reads it back the way `tests/estate_fixtures.FakeEstate` does, so
    the two fakes disagree about nothing — including which record a URL names.
    """
    parsed = urlparse(url)
    app = parsed.netloc.split(".")[0].rsplit("-", 1)[-1]
    parts = [p for p in parsed.path.split("/") if p]
    resource = parts[3] if len(parts) > 3 else ""
    record_id = parts[4] if len(parts) > 4 else ""
    return app, resource, record_id


def sales_order_rows(deliveries: list[dict]) -> list[dict]:
    """The ERP's own rows, rebuilt from the sync payloads a fixture declares.

    The fixture records what the feed *delivered*; the write plane addresses
    what the ERP *holds*, and the two are the same rows in two shapes. Deriving
    one from the other is what makes "one recording" true rather than a
    slogan — a fixture that declared the estate separately would be a second
    copy of the same facts, free to drift from the graph they built.

    `external_id` is the ERP's `id` and the PATCH URL keys on it;
    `order_number` is the `tranId` every human uses. Keeping both is the whole
    reason the release flow can address a row it only knows by transaction
    number.
    """
    rows: list[dict] = []
    for delivery in deliveries:
        for row in delivery.get("sales_orders") or []:
            record_id = str(row.get("external_id") or "")
            if not record_id:
                continue
            rows.append({
                "id": record_id,
                "tranId": str(row.get("order_number") or ""),
                "customerId": str(row.get("customer_external_id") or ""),
                "status": str(row.get("status") or ""),
                "total": row.get("total"),
                "tranDate": str(row.get("ordered_date") or ""),
                "memo": str(row.get("memo") or ""),
                "updatedAt": str(
                    delivery.get("watermark") or "2026-08-30T00:00:00+00:00"
                ),
            })
    return rows


def as_delivery_row(row: dict, status: str = "") -> dict:
    """The inverse of the row above: the ERP's record, in the feed's shape.

    What the echo pass needs — a delivery carrying the record that was written
    — and it is derived rather than declared for `sales_order_rows`'s reason:
    two hand-written copies of one order is exactly how a delivery stops
    matching the row it is supposed to be an echo of.
    """
    return {
        "external_id": str(row.get("id") or ""),
        "order_number": str(row.get("tranId") or ""),
        "customer_external_id": str(row.get("customerId") or ""),
        "ordered_date": str(row.get("tranDate") or ""),
        "total": row.get("total"),
        "memo": str(row.get("memo") or ""),
        "status": status or str(row.get("status") or ""),
    }


class FakeErp:
    """`salesOrders`, as the ERP serves and stores them. No network anywhere.

    Rows are held by id and copied on the way in and out, so nothing a graded
    run writes can reach the declaration it was seeded from — the same property
    the eval world has, for the same reason.
    """

    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows: dict[str, dict] = {}
        self.calls: list[str] = []
        self.seed_rows(rows or [])

    # -- seeding ------------------------------------------------------------

    def seed_rows(self, rows: list[dict]) -> None:
        for row in rows:
            record_id = str(row.get("id") or "")
            if record_id:
                self._rows[record_id] = dict(row)

    def seed(self, deliveries: list[dict]) -> int:
        """Establish the estate from a fixture's own recorded deliveries."""
        rows = sales_order_rows(deliveries)
        self.seed_rows(rows)
        return len(rows)

    def hold(self, record_id: str, **fields: Any) -> dict:
        """Put a row where a case needs the ERP to be holding it.

        Distinct from `patch` on purpose: this is the world moving, not Cumulus
        writing. It goes through no picklist and leaves no call in `calls`,
        because a case saying "by the time the desk answered, somebody had
        billed it" is describing a fact rather than making a request.
        """
        row = self._rows.get(record_id)
        if row is None:
            raise FakeEstateError(404, f"sales order {record_id} not found")
        row.update(fields)
        row["updatedAt"] = _bumped(str(row.get("updatedAt", "")))
        return dict(row)

    def record(self, record_id: str) -> dict:
        """One row as the fake currently holds it — what a write actually did,
        as opposed to what the door said about it."""
        row = self._rows.get(record_id)
        if row is None:
            raise FakeEstateError(404, f"sales order {record_id} not found")
        return dict(row)

    @property
    def patches(self) -> list[str]:
        return [c for c in self.calls if c.startswith("PATCH ")]

    # -- the wire -----------------------------------------------------------

    def fetch(self, url: str) -> dict:
        """`GET /api/{ns}/v1/salesOrders/{id}`, and nothing else."""
        self.calls.append(f"GET {url}")
        app, resource, record_id = parse(url)
        self._route(app, resource, record_id, "GET", url)
        return self.record(record_id)

    def patch(self, url: str, body: dict) -> dict:
        """`erp.py`'s PATCH route: `_update` over `_clean(partial=True)`.

        Faithful where it matters and deliberately not tidy. Unknown fields are
        a 400 naming them; the picklist and the string-ness of a value are
        checked; the server-owned fields are *ignored* rather than rejected; an
        empty body is refused; `updatedAt` is bumped; there is no `If-Match`,
        because the estate offers none.

        What it does **not** do is validate the transition — see this module's
        docstring, and `estate_writes.WRITABLE`, which is where the legality of
        a state change is written down because it is written down nowhere else.
        """
        self.calls.append(f"PATCH {url}")
        app, resource, record_id = parse(url)
        self._route(app, resource, record_id, "PATCH", url)
        row = self._rows.get(record_id)
        if row is None:
            raise FakeEstateError(404, f"sales order {record_id} not found")
        updates = clean(body)
        if not updates:
            raise FakeEstateError(
                400, "nothing to update — send at least one field"
            )
        updates["updatedAt"] = _bumped(str(row.get("updatedAt", "")))
        row.update(updates)
        return dict(row)

    @staticmethod
    def _route(app: str, resource: str, record_id: str, verb: str, url: str) -> None:
        if (app, resource) != (ERP_APP, SALES_ORDERS) or not record_id:
            raise FakeEstateError(404, f"no route for {verb} {urlparse(url).path}")


def clean(body: dict) -> dict:
    """`erp.py`'s `_clean(..., partial=True)` over the scalar fields.

    Same order of business as the original: unknown fields first (a 400 naming
    them), then per field — strip, empty becomes null, non-strings refused,
    picklist last. The server-owned fields never appear because they are not in
    `SALES_ORDER_FIELDS`, which is precisely how the real one ignores them.
    """
    unknown = sorted(set(body) - set(SALES_ORDER_FIELDS) - set(READ_ONLY))
    if unknown:
        raise FakeEstateError(400, f"unknown fields: {', '.join(unknown)}")
    fields: dict = {}
    for key in SALES_ORDER_FIELDS:
        if key not in body:
            continue
        if key == "lines":
            # Out of scope, as in the connector suite's fake: rates and the
            # order total come from the item catalogue. Nothing in the write
            # plane sends lines, and a fake that pretended to edit them would
            # hand back a quietly wrong total.
            raise NotImplementedError("the recorded estate does not edit order lines")
        value = body[key]
        if isinstance(value, str):
            value = value.strip()
        if value in (None, ""):
            fields[key] = None
            continue
        if not isinstance(value, str):
            raise FakeEstateError(400, f"'{key}' must be a string")
        if key == "status" and value not in SALES_ORDER_STATUSES:
            raise FakeEstateError(
                400, f"'status' must be one of: {', '.join(SALES_ORDER_STATUSES)}"
            )
        fields[key] = value
    return fields
