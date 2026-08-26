"""Anomaly screening: is this invoice unusual *for this vendor*?

Not fraud detection. Fraud is a claim about intent, and nothing here can see
intent — so the step is named for what it does. What it does is compare an
invoice against the vendor's own history, because "unusual" only has a useful
definition per vendor: $40,000 is routine from a steel supplier and alarming
from a print shop.

The split that makes this governable:

- **`VendorProfile` is derived data.** Per-vendor rolling statistics, rebuilt
  from the graph by the `vendor-profile-refresh` Flow on a windowed trigger.
  Delete the whole collection and nothing is lost — it recomputes. So it is
  Role=data, Origin=derived, not authoritative, and its retention is short.
- **The thresholds that judge it are governed knowledge.** How many multiples
  of the median is "an outlier", how wide the duplicate window is, what counts
  as first-time-large: those are business decisions, parsed from the
  `invoice-posting-policy` asset at run time and editable in the console.

Derived statistics are data; the thresholds that judge them are knowledge. That
line is the reusable part of this module.

The four checks are deliberately cheap and explainable — an amount outlier
against the vendor's median, a first-time-large invoice, a suspected duplicate,
and an out-of-cadence arrival. The duplicate check is the one that earns its
keep in practice, and it only became possible once vendors had canonical ids.
"""

from __future__ import annotations

import logging
import re
import statistics
import threading
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .graph import GraphService
from .models import utcnow

log = logging.getLogger("cumulus.anomaly")

PROFILE_FLOW_ID = "vendor-profile-refresh"

FindingKind = Literal[
    "amount-outlier", "first-time-large", "duplicate-suspect",
    "out-of-cadence", "round-number",
]


class AnomalyThresholds(BaseModel):
    outlier_multiple: float = 3.0
    first_time_large_usd: float = 5000.0
    duplicate_window_days: int = 30
    cadence_multiple: float = 4.0
    window_days: int = 365
    source_ref: str = ""
    found_asset: bool = False


def parse_anomaly_thresholds(content: str, source_ref: str = "") -> AnomalyThresholds:
    values = AnomalyThresholds(found_asset=True, source_ref=source_ref)
    outlier = re.search(r"amount outlier multiple:?\s*(\d+(?:\.\d+)?)", content, re.I)
    if outlier:
        values.outlier_multiple = float(outlier.group(1))
    first = re.search(
        r"first-time-large threshold:?\s*\$([\d,]+(?:\.\d{1,2})?)", content, re.I
    )
    if first:
        values.first_time_large_usd = float(first.group(1).replace(",", ""))
    window = re.search(r"duplicate window:?\s*(\d+)\s*days?", content, re.I)
    if window:
        values.duplicate_window_days = int(window.group(1))
    cadence = re.search(
        r"cadence deviation multiple:?\s*(\d+(?:\.\d+)?)", content, re.I
    )
    if cadence:
        values.cadence_multiple = float(cadence.group(1))
    profile = re.search(r"anomaly profile window:?\s*(\d+)\s*days?", content, re.I)
    if profile:
        values.window_days = int(profile.group(1))
    return values


# ---------------------------------------------------------------------------
# The derived profile
# ---------------------------------------------------------------------------


class VendorProfile(BaseModel):
    vendor_id: str
    vendor_label: str = ""
    invoices: int = 0
    total_usd: float = 0.0
    mean_usd: float = 0.0
    median_usd: float = 0.0
    p95_usd: float = 0.0
    max_usd: float = 0.0
    stdev_usd: float = 0.0
    median_line_count: float = 0.0
    # Median days between consecutive invoices. 0 when there is not enough
    # history to have a cadence, which is a different thing from "arrives
    # daily" and is why the check skips rather than firing.
    cadence_days: float = 0.0
    round_number_rate: float = 0.0
    first_invoice_iso: str = ""
    last_invoice_iso: str = ""
    window_days: int = 365
    computed_at_iso: str = Field(default_factory=lambda: utcnow().isoformat())
    # (invoice_number, total, date) for the duplicate check, newest first and
    # capped: the check only looks back `duplicate_window_days`.
    recent: list[dict] = Field(default_factory=list)


class ProfileStore(Protocol):
    def list(self) -> list[VendorProfile]: ...
    def get(self, vendor_id: str) -> VendorProfile | None: ...
    def put(self, profile: VendorProfile) -> VendorProfile: ...


class InMemoryProfileStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, VendorProfile] = {}

    def list(self) -> list[VendorProfile]:
        with self._lock:
            return list(self._items.values())

    def get(self, vendor_id: str) -> VendorProfile | None:
        with self._lock:
            return self._items.get(vendor_id)

    def put(self, profile: VendorProfile) -> VendorProfile:
        with self._lock:
            self._items[profile.vendor_id] = profile
            return profile


class FirestoreProfileStore:
    def __init__(self, project: str | None, collection: str) -> None:
        from google.cloud import firestore  # deferred: not needed for local dev

        self._client = firestore.Client(project=project)
        self._col = self._client.collection(collection)

    def list(self) -> list[VendorProfile]:
        out: list[VendorProfile] = []
        for doc in self._col.stream():
            try:
                out.append(VendorProfile.model_validate(doc.to_dict()))
            except Exception:
                log.exception("Skipping unparseable profile", extra={"docId": doc.id})
        return out

    def get(self, vendor_id: str) -> VendorProfile | None:
        snap = self._col.document(vendor_id).get()
        if not snap.exists:
            return None
        try:
            return VendorProfile.model_validate(snap.to_dict())
        except Exception:
            log.exception("Unparseable profile", extra={"vendorId": vendor_id})
            return None

    def put(self, profile: VendorProfile) -> VendorProfile:
        self._col.document(profile.vendor_id).set(profile.model_dump(mode="json"))
        return profile


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _is_round(amount: float) -> bool:
    """A suspiciously round total: whole hundreds, or ending in .00 at scale."""
    return amount >= 1000 and abs(amount - round(amount / 100) * 100) < 0.005


class AnomalyService:
    """Builds profiles from the graph, and screens one invoice against one."""

    def __init__(self, store: ProfileStore, graph: GraphService) -> None:
        self.store = store
        self.graph = graph

    # -- the derived dataset ------------------------------------------------

    def rebuild(self, window_days: int = 365) -> list[VendorProfile]:
        """Recompute every vendor's profile from the invoice graph.

        This is the body of a windowed Flow, not a per-case one — the doctrine
        retired the DataFlow/Workflow split precisely so this could be a Flow
        with a different Trigger rather than a different kind of thing.
        """
        cutoff = utcnow() - timedelta(days=window_days)
        profiles: list[VendorProfile] = []
        for vendor in self.graph.find("Vendor"):
            invoices = [
                other for _, other in self.graph.neighbors(vendor.id, "issued", "out")
            ]
            rows: list[tuple[datetime, float, int, str]] = []
            for invoice in invoices:
                when = _parse_iso(str(invoice.props.get("invoice_date") or "")) or (
                    _parse_iso(invoice.created_at_iso)
                )
                if when is None:
                    continue
                if when.tzinfo is None:
                    when = when.replace(tzinfo=cutoff.tzinfo)
                if when < cutoff:
                    continue
                total = float(invoice.props.get("total_usd") or 0)
                if total <= 0:
                    continue
                line_count = len(
                    self.graph.neighbors(invoice.id, "has_invoice_line", "out")
                )
                rows.append((
                    when, total, line_count,
                    str(invoice.props.get("invoice_number") or invoice.id),
                ))
            profile = self._profile_from(vendor.id, vendor.label, rows, window_days)
            self.store.put(profile)
            profiles.append(profile)
        log.info(
            "Vendor profiles rebuilt",
            extra={"vendors": len(profiles), "windowDays": window_days},
        )
        return profiles

    @staticmethod
    def _profile_from(
        vendor_id: str,
        label: str,
        rows: list[tuple[datetime, float, int, str]],
        window_days: int,
    ) -> VendorProfile:
        profile = VendorProfile(
            vendor_id=vendor_id, vendor_label=label, window_days=window_days
        )
        if not rows:
            return profile
        rows.sort(key=lambda item: item[0])
        totals = [row[1] for row in rows]
        profile.invoices = len(rows)
        profile.total_usd = round(sum(totals), 2)
        profile.mean_usd = round(statistics.fmean(totals), 2)
        profile.median_usd = round(statistics.median(totals), 2)
        profile.max_usd = round(max(totals), 2)
        profile.p95_usd = round(
            sorted(totals)[min(len(totals) - 1, int(len(totals) * 0.95))], 2
        )
        profile.stdev_usd = (
            round(statistics.stdev(totals), 2) if len(totals) > 1 else 0.0
        )
        profile.median_line_count = statistics.median([row[2] for row in rows])
        gaps = [
            (rows[i][0] - rows[i - 1][0]).days for i in range(1, len(rows))
        ]
        profile.cadence_days = statistics.median(gaps) if gaps else 0.0
        profile.round_number_rate = round(
            sum(1 for total in totals if _is_round(total)) / len(totals), 3
        )
        profile.first_invoice_iso = rows[0][0].isoformat()
        profile.last_invoice_iso = rows[-1][0].isoformat()
        profile.recent = [
            {"invoice_number": row[3], "total_usd": row[1], "date": row[0].isoformat()}
            for row in reversed(rows[-25:])
        ]
        return profile

    def profile_for(self, vendor_id: str) -> VendorProfile | None:
        return self.store.get(vendor_id)

    # -- the screen ---------------------------------------------------------

    def screen(
        self,
        vendor_id: str,
        invoice_number: str,
        invoice_date_iso: str,
        total_usd: float,
        thresholds: AnomalyThresholds,
    ) -> dict:
        """Score one invoice against the vendor's profile.

        Returns a dict (a step's output is persisted as JSON) holding the
        findings and the basis, so the journal can say *why* — an anomaly
        finding with no baseline attached is an accusation, not a check.
        """
        profile = self.store.get(vendor_id)
        findings: list[dict] = []
        if profile is None or profile.invoices == 0:
            return {
                "findings": [],
                "basis": "no history for this vendor — nothing to compare against",
                "profile_invoices": 0,
                "thresholds_ref": thresholds.source_ref,
            }

        if (
            profile.median_usd
            and total_usd > profile.median_usd * thresholds.outlier_multiple
        ):
            findings.append({
                "kind": "amount-outlier",
                "detail": (
                    f"${total_usd:,.2f} is "
                    f"{total_usd / profile.median_usd:.1f}× this vendor's median "
                    f"of ${profile.median_usd:,.2f} over {profile.invoices} "
                    f"invoice(s) (flag above {thresholds.outlier_multiple:.1f}×)"
                ),
            })
        if (
            profile.invoices < 3
            and total_usd >= thresholds.first_time_large_usd
        ):
            findings.append({
                "kind": "first-time-large",
                "detail": (
                    f"${total_usd:,.2f} from a vendor with only "
                    f"{profile.invoices} prior invoice(s), at or above the "
                    f"${thresholds.first_time_large_usd:,.2f} first-time "
                    "threshold"
                ),
            })

        when = _parse_iso(invoice_date_iso) or utcnow()
        for previous in profile.recent:
            prior_date = _parse_iso(str(previous.get("date") or ""))
            if prior_date is None:
                continue
            if prior_date.tzinfo is None and when.tzinfo is not None:
                prior_date = prior_date.replace(tzinfo=when.tzinfo)
            if when.tzinfo is None and prior_date.tzinfo is not None:
                when = when.replace(tzinfo=prior_date.tzinfo)
            within = abs((when - prior_date).days) <= thresholds.duplicate_window_days
            same_amount = abs(float(previous.get("total_usd", 0)) - total_usd) < 0.005
            different_number = str(previous.get("invoice_number")) != invoice_number
            if within and same_amount and different_number:
                findings.append({
                    "kind": "duplicate-suspect",
                    "detail": (
                        f"${total_usd:,.2f} matches invoice "
                        f"{previous.get('invoice_number')} dated "
                        f"{str(previous.get('date'))[:10]} to the cent, within "
                        f"the {thresholds.duplicate_window_days}-day duplicate "
                        "window"
                    ),
                })
                break

        if profile.cadence_days and profile.invoices >= 3:
            last = _parse_iso(profile.last_invoice_iso)
            if last is not None:
                if last.tzinfo is None and when.tzinfo is not None:
                    last = last.replace(tzinfo=when.tzinfo)
                gap = abs((when - last).days)
                if gap > profile.cadence_days * thresholds.cadence_multiple:
                    findings.append({
                        "kind": "out-of-cadence",
                        "detail": (
                            f"{gap} days since the last invoice against a usual "
                            f"{profile.cadence_days:.0f}-day cadence (flag above "
                            f"{thresholds.cadence_multiple:.0f}×)"
                        ),
                    })

        if _is_round(total_usd) and profile.round_number_rate < 0.2:
            findings.append({
                "kind": "round-number",
                "detail": (
                    f"${total_usd:,.2f} is a round hundred; only "
                    f"{profile.round_number_rate:.0%} of this vendor's invoices "
                    "are"
                ),
            })

        return {
            "findings": findings,
            "basis": (
                f"{profile.invoices} invoice(s) over {profile.window_days} days · "
                f"median ${profile.median_usd:,.2f} · p95 "
                f"${profile.p95_usd:,.2f} · cadence "
                f"{profile.cadence_days:.0f}d"
            ),
            "profile_invoices": profile.invoices,
            "thresholds_ref": thresholds.source_ref,
        }

    def stats(self) -> dict:
        profiles = self.store.list()
        return {
            "vendors_profiled": len(profiles),
            "invoices_covered": sum(p.invoices for p in profiles),
            "computed_at": max(
                (p.computed_at_iso for p in profiles), default=""
            ),
        }


def build_anomaly_service(
    backend: str, project: str | None, collection: str, graph: GraphService
) -> AnomalyService:
    store: ProfileStore
    if backend == "firestore":
        store = FirestoreProfileStore(project, collection)
    else:
        store = InMemoryProfileStore()
    return AnomalyService(store, graph)
