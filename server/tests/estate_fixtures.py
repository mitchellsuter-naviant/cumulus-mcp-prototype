"""A fake estate over recorded responses — the shapes the connector really sees.

Recorded from the live `naviant` namespace of `cumulus-demo-mitchell-suter`
(2026-08-30), field for field: nulls where the estate sends nulls, the ISO
`updatedAt` strings it actually orders by, and `fullName` alongside the legal
names. A hand-written fixture would have been tidier and would have tested a
connector against a shape nothing serves.

`FakeEstate.fetch` is the injectable the connector takes instead of `httpx`,
and it reproduces the two behaviours the watermark logic depends on: a list
route filters `updatedAt` **strictly** greater than `?updatedSince=` and orders
ascending, and a single-record route answers the declared-key dereference. The
tests drive pulls against this and deliver through the real HTTP door, so the
only thing faked is the network.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

# The discovery document, verbatim — two namespaces, and `naviant` running the
# five apps the FEEDS registry is filtered against.
NAMESPACES: dict = {
    "namespaces": [
        {
            "id": "naviant",
            "name": "Naviant",
            "description": "Naviant's own back office — the everyday demo environment.",
            "industry": "software-reseller",
            "apps": ["crm", "erp", "office", "hris", "dms"],
            "processes": ["employee_lifecycle", "deal_to_order", "procure_to_pay"],
            "pulse": {
                "enabled": True,
                "apps": {"crm": True, "erp": True, "office": True,
                         "hris": True, "dms": True},
                "intensity": "low",
            },
        },
        {
            "id": "insurance",
            "name": "Insurance",
            "description": "Meridian Mutual, a regional property & casualty carrier.",
            "industry": "pnc-insurance",
            "apps": ["crm", "pas", "office", "hris", "dms"],
            "processes": ["employee_lifecycle", "claim_lifecycle", "policy_renewal"],
            "pulse": {
                "enabled": True,
                "apps": {"crm": True, "pas": True, "office": True,
                         "hris": True, "dms": True},
                "intensity": "low",
            },
        },
    ],
    "default": "naviant",
}

DEPARTMENTS: list[dict] = [
    {"id": "DEPT-1001", "name": "Executive", "costCenter": "CC-1000",
     "parentDepartmentId": None, "headEmployeeId": "EMP-1001",
     "location": "Madison, WI",
     "createdAt": "2026-08-16T22:16:20.542130+00:00",
     "updatedAt": "2026-08-16T22:16:24.233812+00:00"},
    {"id": "DEPT-1002", "name": "Sales", "costCenter": "CC-2000",
     "parentDepartmentId": "DEPT-1001", "headEmployeeId": "EMP-1002",
     "location": "Madison, WI",
     "createdAt": "2026-08-16T22:16:20.646029+00:00",
     "updatedAt": "2026-08-16T22:16:24.255298+00:00"},
    {"id": "DEPT-1003", "name": "Professional Services", "costCenter": "CC-3000",
     "parentDepartmentId": "DEPT-1001", "headEmployeeId": "EMP-1003",
     "location": "Madison, WI",
     "createdAt": "2026-08-16T22:16:20.734116+00:00",
     "updatedAt": "2026-08-16T22:16:24.271988+00:00"},
    {"id": "DEPT-1004", "name": "Customer Support", "costCenter": "CC-3500",
     "parentDepartmentId": "DEPT-1003", "headEmployeeId": "EMP-1003",
     "location": "Madison, WI",
     "createdAt": "2026-08-16T22:16:20.868468+00:00",
     "updatedAt": "2026-08-16T22:16:24.290944+00:00"},
    {"id": "DEPT-1005", "name": "Software Engineering", "costCenter": "CC-4000",
     "parentDepartmentId": "DEPT-1001", "headEmployeeId": "EMP-1004",
     "location": "Madison, WI",
     "createdAt": "2026-08-16T22:16:20.942018+00:00",
     "updatedAt": "2026-08-16T22:16:24.343898+00:00"},
    {"id": "DEPT-1006", "name": "Finance & Administration", "costCenter": "CC-5000",
     "parentDepartmentId": "DEPT-1001", "headEmployeeId": "EMP-1005",
     "location": "Madison, WI",
     "createdAt": "2026-08-16T22:16:21.038909+00:00",
     "updatedAt": "2026-08-16T22:16:24.358920+00:00"},
    {"id": "DEPT-1007", "name": "Human Resources", "costCenter": "CC-5500",
     "parentDepartmentId": "DEPT-1001", "headEmployeeId": "EMP-1006",
     "location": "Madison, WI",
     "createdAt": "2026-08-16T22:16:21.127202+00:00",
     "updatedAt": "2026-08-16T22:16:24.438947+00:00"},
]

#: Deliberately missing JOB-1003, the job EMP-1016 holds: the pulled page not
#: containing a referenced record is the case the dereference exists for.
JOBS: list[dict] = [
    {"id": "JOB-1001", "jobCode": "EXEC-CEO", "title": "Chief Executive Officer",
     "jobFamily": "Executive", "jobLevel": "E1", "flsaClassification": "Exempt",
     "minAnnualSalary": 240000, "maxAnnualSalary": 320000,
     "createdAt": "2026-08-16T22:16:21.367843+00:00",
     "updatedAt": "2026-08-16T22:16:21.367843+00:00"},
    {"id": "JOB-1002", "jobCode": "SLS-VP", "title": "VP of Sales",
     "jobFamily": "Sales", "jobLevel": "M3", "flsaClassification": "Exempt",
     "minAnnualSalary": 165000, "maxAnnualSalary": 215000,
     "createdAt": "2026-08-16T22:16:21.429976+00:00",
     "updatedAt": "2026-08-16T22:16:21.429976+00:00"},
    {"id": "JOB-1005", "jobCode": "SVC-DIR",
     "title": "Director of Professional Services", "jobFamily": "Services",
     "jobLevel": "M3", "flsaClassification": "Exempt",
     "minAnnualSalary": 150000, "maxAnnualSalary": 190000,
     "createdAt": "2026-08-16T22:16:21.650615+00:00",
     "updatedAt": "2026-08-16T22:16:21.650615+00:00"},
    {"id": "JOB-1006", "jobCode": "SVC-IC", "title": "Implementation Consultant",
     "jobFamily": "Services", "jobLevel": "IC2", "flsaClassification": "Exempt",
     "minAnnualSalary": 82000, "maxAnnualSalary": 110000,
     "createdAt": "2026-08-16T22:16:21.684794+00:00",
     "updatedAt": "2026-08-16T22:16:21.684794+00:00"},
    {"id": "JOB-1012", "jobCode": "FIN-CTL", "title": "Controller",
     "jobFamily": "Finance", "jobLevel": "M2", "flsaClassification": "Exempt",
     "minAnnualSalary": 125000, "maxAnnualSalary": 165000,
     "createdAt": "2026-08-16T22:16:21.929959+00:00",
     "updatedAt": "2026-08-16T22:16:21.929959+00:00"},
]

#: Only referenced, never listed — the job EMP-1016 holds.
JOB_OFF_PAGE: dict = {
    "id": "JOB-1003", "jobCode": "SLS-AE", "title": "Account Executive",
    "jobFamily": "Sales", "jobLevel": "IC3", "flsaClassification": "Exempt",
    "minAnnualSalary": 95000, "maxAnnualSalary": 135000,
    "createdAt": "2026-08-16T22:16:21.491497+00:00",
    "updatedAt": "2026-08-16T22:16:21.491497+00:00",
}


def _worker(**over) -> dict:
    """One HRIS worker row, with every column the estate actually sends."""
    row = {
        "id": "", "legalFirstName": "", "legalLastName": "",
        "preferredName": None, "workEmail": "", "personalEmail": None,
        "mobilePhone": None, "dateOfBirth": None, "homeAddress": None,
        "workLocation": "Madison, WI", "employmentType": "Full-time",
        "hireDate": "", "jobId": "", "departmentId": "", "managerId": None,
        "payType": "Salary", "annualSalary": None, "hourlyRate": None,
        "workerStatus": "Active", "vacationBalanceHours": 0.0,
        "sickBalanceHours": 0.0, "leaveType": None, "leaveStartDate": None,
        "expectedReturnDate": None, "terminationDate": None,
        "terminationReason": None, "eligibleForRehire": None, "fullName": "",
        "createdAt": "", "updatedAt": "",
    }
    row.update(over)
    return row


EMPLOYEES: list[dict] = [
    _worker(id="EMP-1016", legalFirstName="Alan", legalLastName="Whitcomb",
            workEmail="alan.whitcomb@naviant.com", fullName="Alan Whitcomb",
            mobilePhone="(608) 555-0115", dateOfBirth="1969-10-22",
            hireDate="2020-11-15", jobId="JOB-1003", departmentId="DEPT-1002",
            managerId="EMP-1002", annualSalary=108000,
            workerStatus="Terminated", vacationBalanceHours=70.0,
            sickBalanceHours=16.0, terminationDate="2026-07-02",
            terminationReason="Resignation — accepted an outside offer",
            eligibleForRehire=True,
            createdAt="2026-08-16T22:16:24.140758+00:00",
            updatedAt="2026-08-16T22:16:24.808648+00:00"),
    _worker(id="EMP-1001", legalFirstName="Diane", legalLastName="Vogel",
            workEmail="diane.vogel@naviant.com", fullName="Diane Vogel",
            mobilePhone="(608) 555-0100", dateOfBirth="1962-01-01",
            hireDate="2016-03-21", jobId="JOB-1001", departmentId="DEPT-1001",
            managerId=None, annualSalary=285000,
            vacationBalanceHours=86.68, sickBalanceHours=42.68,
            createdAt="2026-08-16T22:16:22.076771+00:00",
            updatedAt="2026-08-20T23:12:20.987813+00:00"),
    _worker(id="EMP-1002", legalFirstName="Marcus", legalLastName="Reyes",
            workEmail="marcus.reyes@naviant.com", fullName="Marcus Reyes",
            mobilePhone="(608) 555-0101", dateOfBirth="1967-08-06",
            hireDate="2019-07-04", jobId="JOB-1002", departmentId="DEPT-1002",
            managerId="EMP-1001", annualSalary=195000,
            vacationBalanceHours=146.68, sickBalanceHours=42.68,
            createdAt="2026-08-16T22:16:22.275975+00:00",
            updatedAt="2026-08-20T23:12:21.137989+00:00"),
    _worker(id="EMP-1003", legalFirstName="Gregory", legalLastName="Lindholm",
            workEmail="gregory.lindholm@naviant.com", fullName="Gregory Lindholm",
            mobilePhone="(608) 555-0102", dateOfBirth="1972-03-11",
            hireDate="2018-02-19", jobId="JOB-1005", departmentId="DEPT-1003",
            managerId="EMP-1001", annualSalary=168000,
            vacationBalanceHours=106.68, sickBalanceHours=32.68,
            createdAt="2026-08-16T22:16:22.426066+00:00",
            updatedAt="2026-08-20T23:12:21.349237+00:00"),
    _worker(id="EMP-1005", legalFirstName="Raymond", legalLastName="Fitzgerald",
            workEmail="raymond.fitzgerald@naviant.com",
            fullName="Raymond Fitzgerald", mobilePhone="(608) 555-0104",
            dateOfBirth="1982-05-21", hireDate="2018-09-07", jobId="JOB-1012",
            departmentId="DEPT-1006", managerId="EMP-1001", annualSalary=142000,
            vacationBalanceHours=86.68, sickBalanceHours=42.68,
            createdAt="2026-08-16T22:16:22.645036+00:00",
            updatedAt="2026-08-20T23:12:21.746250+00:00"),
    _worker(id="EMP-1010", legalFirstName="Nina", legalLastName="Alvarez",
            workEmail="nina.alvarez@naviant.com", fullName="Nina Alvarez",
            mobilePhone="(608) 555-0109", dateOfBirth="1973-04-19",
            hireDate="2024-09-15", jobId="JOB-1006", departmentId="DEPT-1004",
            managerId="EMP-1003", annualSalary=96000,
            vacationBalanceHours=136.68, sickBalanceHours=32.68,
            createdAt="2026-08-16T22:16:23.179831+00:00",
            updatedAt="2026-08-20T23:12:22.396889+00:00"),
    # The sim's real drift, recorded as it stands: two worker records under
    # one work email — a terminated one and its rehire — for both Andre Mbeki
    # and Corey Landsman. Stage 1 does nothing about it on purpose: the
    # connector mints nothing and scores nothing, and the natural key is what
    # collapses them. B5 grades the merge.
    _worker(id="EMP-1017", legalFirstName="Andre", legalLastName="Mbeki",
            workEmail="andre.mbeki@naviant.com", fullName="Andre Mbeki",
            personalEmail="andre.mbeki@mail.example.com",
            mobilePhone="(608) 555-0200", hireDate="2026-09-10",
            jobId="JOB-1006", departmentId="DEPT-1003", managerId="EMP-1003",
            annualSalary=91000.0, workerStatus="Terminated",
            terminationDate="2027-01-22",
            createdAt="2026-08-19T22:12:11.108314+00:00",
            updatedAt="2026-08-19T22:12:12.582222+00:00"),
    _worker(id="EMP-1023", legalFirstName="Andre", legalLastName="Mbeki",
            workEmail="andre.mbeki@naviant.com", fullName="Andre Mbeki",
            personalEmail="andre.mbeki@mail.example.com",
            mobilePhone="(608) 555-0200", hireDate="2026-09-20",
            jobId="JOB-1006", departmentId="DEPT-1003", managerId="EMP-1003",
            annualSalary=91000.0,
            createdAt="2026-08-26T18:12:09.572001+00:00",
            updatedAt="2026-08-26T18:27:11.934143+00:00"),
    _worker(id="EMP-1022", legalFirstName="Corey", legalLastName="Landsman",
            workEmail="corey.landsman@naviant.com", fullName="Corey Landsman",
            personalEmail="corey.landsman@mail.example.com",
            mobilePhone="(608) 555-0200", hireDate="2026-09-19",
            jobId="JOB-1006", departmentId="DEPT-1003", managerId="EMP-1003",
            annualSalary=94000.0, workerStatus="Terminated",
            terminationDate="2027-01-31",
            createdAt="2026-08-28T08:12:09.114427+00:00",
            updatedAt="2026-08-28T08:12:10.000369+00:00"),
    _worker(id="EMP-1025", legalFirstName="Corey", legalLastName="Landsman",
            workEmail="corey.landsman@naviant.com", fullName="Corey Landsman",
            personalEmail="corey.landsman@mail.example.com",
            mobilePhone="(608) 555-0200", hireDate="2026-09-22",
            jobId="JOB-1006", departmentId="DEPT-1003", managerId="EMP-1003",
            annualSalary=94000.0,
            createdAt="2026-08-29T19:42:05.411978+00:00",
            updatedAt="2026-08-29T20:42:09.970713+00:00"),
]

#: Only referenced, never listed — EMP-1006 heads Human Resources.
WORKER_OFF_PAGE: dict = _worker(
    id="EMP-1006", legalFirstName="Karen", legalLastName="Boothe",
    workEmail="karen.boothe@naviant.com", fullName="Karen Boothe",
    mobilePhone="(608) 555-0105", hireDate="2021-04-05", jobId="JOB-1013",
    departmentId="DEPT-1007", managerId="EMP-1001", annualSalary=94000,
    createdAt="2026-08-16T22:16:22.812048+00:00",
    updatedAt="2026-08-16T22:16:24.942981+00:00",
)

#: What a list route answers under, by app and resource.
RESOURCES: dict[tuple[str, str], list[dict]] = {
    ("hris", "departments"): DEPARTMENTS,
    ("hris", "jobs"): JOBS,
    ("hris", "employees"): EMPLOYEES,
}

#: Records reachable by id but absent from every list — the dereference cases.
OFF_PAGE: dict[tuple[str, str], dict] = {
    ("hris", "jobs"): JOB_OFF_PAGE,
    ("hris", "employees"): WORKER_OFF_PAGE,
}


class EstateDown(Exception):
    """What the fake raises where the network would fail."""


class FakeEstate:
    """The estate's list and single-record routes, over the recorded rows.

    `fails` names (app, resource) pairs that answer with an outage, which is
    how the all-or-nothing rule and the last_error path are exercised without
    a real network to break.
    """

    def __init__(
        self,
        *,
        namespaces: dict | None = None,
        resources: dict[tuple[str, str], list[dict]] | None = None,
        off_page: dict[tuple[str, str], dict] | None = None,
        fails: set[tuple[str, str]] | None = None,
    ) -> None:
        self.namespaces = namespaces if namespaces is not None else NAMESPACES
        self.resources = dict(resources if resources is not None else RESOURCES)
        self.off_page = dict(off_page if off_page is not None else OFF_PAGE)
        self.fails = set(fails or ())
        self.calls: list[str] = []

    def fetch(self, url: str) -> dict:
        self.calls.append(url)
        parsed = urlparse(url)
        app = parsed.netloc.split(".")[0].rsplit("-", 1)[-1]
        parts = [p for p in parsed.path.split("/") if p]
        if parts[:1] == ["admin"]:
            return self.namespaces
        # /api/{ns}/v1/{resource}[/{id}]
        resource = parts[3]
        if (app, resource) in self.fails:
            raise EstateDown(f"GET {url} → 503")
        if len(parts) > 4:
            return self._one(app, resource, parts[4])
        rows = self.resources.get((app, resource))
        if rows is None:
            raise EstateDown(f"GET {url} → 404")
        since = parse_qs(parsed.query).get("updatedSince", [""])[0]
        if since:
            # Strictly greater than, ascending — exactly what `_list` does.
            rows = [r for r in rows if str(r.get("updatedAt", "")) > since]
        return {resource: sorted(rows, key=lambda r: str(r.get("updatedAt", "")))}

    def _one(self, app: str, resource: str, record_id: str) -> dict:
        for row in self.resources.get((app, resource), []):
            if row.get("id") == record_id:
                return row
        off = self.off_page.get((app, resource))
        if off is not None and off.get("id") == record_id:
            return off
        raise EstateDown(f"GET {resource}/{record_id} → 404")

    def calls_for(self, resource: str) -> list[str]:
        """Every call touching one resource, so a test can read the watermark
        it asked with and count the dereferences it made."""
        return [u for u in self.calls if f"/v1/{resource}" in u]
