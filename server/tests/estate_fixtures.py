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

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

# The discovery document, verbatim — two namespaces, and `naviant` running the
# six apps the FEEDS registry is filtered against. `psa` is the sixth, and it
# arrived here the way the registry's extension point always said it would: by
# re-recording this payload, with nothing in the connector changed.
NAMESPACES: dict = {
    "namespaces": [
        {
            "id": "naviant",
            "name": "Naviant",
            "description": "Naviant's own back office — the everyday demo environment.",
            "industry": "software-reseller",
            "apps": ["crm", "erp", "office", "hris", "dms", "psa"],
            "processes": ["employee_lifecycle", "deal_to_order",
                          "procure_to_pay", "project_delivery"],
            "pulse": {
                "enabled": True,
                "apps": {"crm": True, "erp": True, "office": True,
                         "hris": True, "dms": True, "psa": True},
                "intensity": "low",
                "simDayMinutes": 15,
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

# ---------------------------------------------------------------------------
# CRM — the sales book
# ---------------------------------------------------------------------------
#
# The five accounts as the CRM spells them, which is the fixture's whole point:
# four of them are the same firms the ERP bills under their legal names
# (`Northwind Traders` here, `Northwind Traders Inc.` there), and Fairview is
# the fifth that only the CRM knows. A tidied fixture that spelled them alike
# would test a resolver against a problem the estate does not have.

ACCOUNTS: list[dict] = [
    {"id": "0012a330a03e1e8", "name": "Apex Manufacturing",
     "industry": "Industrial Manufacturing", "phone": "(414) 555-0188",
     "website": "https://apexmfg.example.com",
     "createdAt": "2026-08-16T22:16:14.911840+00:00",
     "updatedAt": "2026-08-16T22:16:14.911840+00:00"},
    {"id": "001abbf392844aa", "name": "Blue Harbor Logistics",
     "industry": "Transportation & Logistics", "phone": "(206) 555-0142",
     "website": "https://blueharbor.example.com",
     "createdAt": "2026-08-16T22:16:15.093915+00:00",
     "updatedAt": "2026-08-16T22:16:15.093915+00:00"},
    {"id": "00183c5049623dd", "name": "Fairview Insurance Group",
     "industry": "Insurance", "phone": "(920) 555-0163",
     "website": "https://fairviewinsurance.example.com",
     "createdAt": "2026-08-16T22:16:15.312091+00:00",
     "updatedAt": "2026-08-16T22:16:15.312091+00:00"},
    {"id": "0010e4954cbbedf", "name": "Northwind Traders",
     "industry": "Wholesale Distribution", "phone": "(312) 555-0134",
     "website": "https://northwindtraders.example.com",
     "createdAt": "2026-08-16T22:16:14.776980+00:00",
     "updatedAt": "2026-08-16T22:16:14.776980+00:00"},
    {"id": "001a441a7e0ac44", "name": "Summit Health Partners",
     "industry": "Healthcare", "phone": "(608) 555-0117",
     "website": "https://summithealth.example.com",
     "createdAt": "2026-08-16T22:16:15.203631+00:00",
     "updatedAt": "2026-08-16T22:16:15.203631+00:00"},
]

#: Nine contacts, every one of them with an address — so the wire shape's
#: no-address fallback has no recording to come from and is exercised as a
#: constructed delivery in `test_sync_commerce.py` instead. Said here rather
#: than invented above, because a fixture that quietly grew a row the estate
#: never served would stop being a recording.
CONTACTS: list[dict] = [
    {"id": "003f3c42a2692f8", "accountId": "001abbf392844aa",
     "firstName": "Alicia", "lastName": "Grant", "title": "COO",
     "email": "alicia.grant@blueharbor.example.com", "phone": "(206) 555-0109",
     "createdAt": "2026-08-16T22:16:15.961894+00:00",
     "updatedAt": "2026-08-16T22:16:15.961894+00:00"},
    {"id": "003778e6b53c501", "accountId": "00183c5049623dd",
     "firstName": "Bill", "lastName": "Trask", "title": "CFO",
     "email": "bill.trask@fairviewinsurance.example.com",
     "phone": "(920) 555-0102",
     "createdAt": "2026-08-16T22:16:16.397207+00:00",
     "updatedAt": "2026-08-16T22:16:16.397207+00:00"},
    {"id": "003d38f010882b5", "accountId": "0012a330a03e1e8",
     "firstName": "Derek", "lastName": "Hsu", "title": "Director of IT",
     "email": "derek.hsu@apexmfg.example.com", "phone": "(414) 555-0177",
     "createdAt": "2026-08-16T22:16:15.841629+00:00",
     "updatedAt": "2026-08-16T22:16:15.841629+00:00"},
    {"id": "003c23bdfff33be", "accountId": "001a441a7e0ac44",
     "firstName": "Evan", "lastName": "Walsh", "title": "IT Director",
     "email": "evan.walsh@summithealth.example.com", "phone": "(608) 555-0128",
     "createdAt": "2026-08-16T22:16:16.174825+00:00",
     "updatedAt": "2026-08-16T22:16:16.174825+00:00"},
    {"id": "00301edf8283f61", "accountId": "00183c5049623dd",
     "firstName": "Janet", "lastName": "Okafor", "title": "Claims Director",
     "email": "janet.okafor@fairviewinsurance.example.com",
     "phone": "(920) 555-0139",
     "createdAt": "2026-08-16T22:16:16.267423+00:00",
     "updatedAt": "2026-08-16T22:16:16.267423+00:00"},
    {"id": "00370f0b603d30b", "accountId": "0010e4954cbbedf",
     "firstName": "Maria", "lastName": "Delgado", "title": "VP Operations",
     "email": "maria.delgado@northwindtraders.example.com",
     "phone": "(312) 555-0135",
     "createdAt": "2026-08-16T22:16:15.441052+00:00",
     "updatedAt": "2026-08-16T22:16:15.441052+00:00"},
    {"id": "003fb5317d7564e", "accountId": "001a441a7e0ac44",
     "firstName": "Priya", "lastName": "Raman",
     "title": "Chief Medical Officer",
     "email": "priya.raman@summithealth.example.com", "phone": "(608) 555-0150",
     "createdAt": "2026-08-16T22:16:16.051502+00:00",
     "updatedAt": "2026-08-16T22:16:16.051502+00:00"},
    {"id": "00378e33631b8ca", "accountId": "0012a330a03e1e8",
     "firstName": "Susan", "lastName": "Kowalski", "title": "Plant Manager",
     "email": "susan.kowalski@apexmfg.example.com", "phone": "(414) 555-0121",
     "createdAt": "2026-08-16T22:16:15.745926+00:00",
     "updatedAt": "2026-08-16T22:16:15.745926+00:00"},
    {"id": "003090b92282986", "accountId": "0010e4954cbbedf",
     "firstName": "Tom", "lastName": "Berg", "title": "Purchasing Manager",
     "email": "tom.berg@northwindtraders.example.com", "phone": "(312) 555-0181",
     "createdAt": "2026-08-16T22:16:15.566882+00:00",
     "updatedAt": "2026-08-16T22:16:15.566882+00:00"},
]

#: Eight of the twenty-five live deals — one per (account, stage) pair, so the
#: whole declared stage vocabulary is represented without carrying the pulse's
#: churn. Note `Closed Won` and `Closed Lost`: title case on the wire, which is
#: the flow's job to slug, not the connector's.
OPPORTUNITIES: list[dict] = [
    {"id": "0066efd6fda119d", "accountId": "0010e4954cbbedf",
     "name": "Northwind — Annual Restock Program", "stage": "Negotiation",
     "amount": 180000, "closeDate": "2026-09-30",
     "createdAt": "2026-08-16T22:16:16.576376+00:00",
     "updatedAt": "2026-08-16T22:16:16.576376+00:00"},
    {"id": "006574ab2ec7ad9", "accountId": "0010e4954cbbedf",
     "name": "Northwind — Warehouse Scanning Pilot", "stage": "Qualification",
     "amount": 45000, "closeDate": "2026-11-15",
     "createdAt": "2026-08-16T22:16:16.664099+00:00",
     "updatedAt": "2026-08-16T22:16:16.664099+00:00"},
    {"id": "006b8a5548735d2", "accountId": "0012a330a03e1e8",
     "name": "Apex — Plant Floor Automation Phase 1", "stage": "Proposal",
     "amount": 420000, "closeDate": "2026-10-31",
     "createdAt": "2026-08-16T22:16:16.745899+00:00",
     "updatedAt": "2026-08-16T22:16:16.745899+00:00"},
    {"id": "00622eb2dc4cc9d", "accountId": "001abbf392844aa",
     "name": "Blue Harbor — Fleet Telematics Rollout", "stage": "Prospecting",
     "amount": 95000, "closeDate": "2026-12-01",
     "createdAt": "2026-08-16T22:16:16.813024+00:00",
     "updatedAt": "2026-08-16T22:16:16.813024+00:00"},
    {"id": "0067d4d05e966d5", "accountId": "001a441a7e0ac44",
     "name": "Summit — Patient Portal Migration", "stage": "Closed Won",
     "amount": 310000, "closeDate": "2026-07-15",
     "createdAt": "2026-08-16T22:16:16.901825+00:00",
     "updatedAt": "2026-08-16T22:16:16.901825+00:00"},
    {"id": "006acdf9f3b98cb", "accountId": "001a441a7e0ac44",
     "name": "Summit — Records Digitization", "stage": "Proposal",
     "amount": 120000, "closeDate": "2026-10-15",
     "createdAt": "2026-08-16T22:16:16.976893+00:00",
     "updatedAt": "2026-08-16T22:16:16.976893+00:00"},
    {"id": "006aa148670b4d1", "accountId": "00183c5049623dd",
     "name": "Fairview — Claims Intake Modernization", "stage": "Closed Lost",
     "amount": 250000, "closeDate": "2026-06-30",
     "createdAt": "2026-08-16T22:16:17.041474+00:00",
     "updatedAt": "2026-08-16T22:16:17.041474+00:00"},
    {"id": "00623810b4b2a86", "accountId": "0010e4954cbbedf",
     "name": "Northwind — Annual Restock Program Phase 2", "stage": "Closed Won",
     "amount": 129000.0, "closeDate": "2026-09-06",
     "createdAt": "2026-08-16T23:07:39.523796+00:00",
     "updatedAt": "2026-08-17T13:57:15.434553+00:00"},
]

# ---------------------------------------------------------------------------
# ERP — customers, catalogue, orders, and the buy side
# ---------------------------------------------------------------------------

#: Four customers, spelled the way a contract spells them. `Summit Health
#: Partners` is the one that matches its CRM account verbatim; the other three
#: carry the legal suffix that `normalize_key` is supposed to collapse.
CUSTOMERS: list[dict] = [
    {"id": "1001", "companyName": "Apex Manufacturing Co.",
     "email": "accounts.payable@apexmfg.example.com",
     "phone": "(414) 555-0188", "terms": "Net 60",
     "createdAt": "2026-08-15T18:18:55.497843+00:00",
     "updatedAt": "2026-08-15T18:18:55.497843+00:00"},
    {"id": "1002", "companyName": "Blue Harbor Logistics LLC",
     "email": "billing@blueharbor.example.com",
     "phone": "(206) 555-0142", "terms": "Net 30",
     "createdAt": "2026-08-15T18:18:55.631849+00:00",
     "updatedAt": "2026-08-15T18:18:55.631849+00:00"},
    {"id": "1000", "companyName": "Northwind Traders Inc.",
     "email": "ap@northwindtraders.example.com",
     "phone": "(312) 555-0134", "terms": "Net 30",
     "createdAt": "2026-08-15T18:18:55.348380+00:00",
     "updatedAt": "2026-08-15T18:18:55.348380+00:00"},
    {"id": "1003", "companyName": "Summit Health Partners",
     "email": "ap@summithealth.example.com",
     "phone": "(608) 555-0117", "terms": "Net 45",
     "createdAt": "2026-08-15T18:18:55.768274+00:00",
     "updatedAt": "2026-08-15T18:18:55.768274+00:00"},
]

#: The catalogue. `itemId` is the SKU and `id` is the record — the transposition
#: an order line depends on, and the reason items travel with orders.
ITEMS: list[dict] = [
    {"id": "3000", "itemId": "SCN-4200", "displayName": "Production Scanner 4200",
     "itemType": "Inventory Item", "basePrice": 4750, "quantityOnHand": 18,
     "createdAt": "2026-08-15T18:18:56.609696+00:00",
     "updatedAt": "2026-08-15T18:18:56.609696+00:00"},
    {"id": "3001", "itemId": "SCN-2100", "displayName": "Desktop Scanner 2100",
     "itemType": "Inventory Item", "basePrice": 1250, "quantityOnHand": 46,
     "createdAt": "2026-08-15T18:18:56.741930+00:00",
     "updatedAt": "2026-08-15T18:18:56.741930+00:00"},
    {"id": "3002", "itemId": "KIT-ROLLER", "displayName": "Scanner Roller Kit",
     "itemType": "Inventory Item", "basePrice": 189, "quantityOnHand": 220,
     "createdAt": "2026-08-15T18:18:56.878954+00:00",
     "updatedAt": "2026-08-15T18:18:56.878954+00:00"},
    {"id": "3003", "itemId": "LIC-CAPTURE",
     "displayName": "Capture Software License (annual)",
     "itemType": "Non-Inventory Item", "basePrice": 2400, "quantityOnHand": 0,
     "createdAt": "2026-08-15T18:18:56.995868+00:00",
     "updatedAt": "2026-08-15T18:18:56.995868+00:00"},
    {"id": "3004", "itemId": "SVC-INSTALL", "displayName": "On-site Installation",
     "itemType": "Service", "basePrice": 1800, "quantityOnHand": 0,
     "createdAt": "2026-08-15T18:18:57.062848+00:00",
     "updatedAt": "2026-08-15T18:18:57.062848+00:00"},
    {"id": "3005", "itemId": "SVC-SCANDAY",
     "displayName": "Scanning Bureau Day Rate",
     "itemType": "Service", "basePrice": 950, "quantityOnHand": 0,
     "createdAt": "2026-08-15T18:18:57.128185+00:00",
     "updatedAt": "2026-08-15T18:18:57.128185+00:00"},
]

#: The five seeded orders, verbatim — every customer and, between them, the
#: estate's whole status vocabulary. The ten orders the pulse has generated
#: since are all `Billed` and would have made the fixture look like a system
#: with one state. The line's `itemId` is the item's *record* id (`"3000"`),
#: never its SKU, and SO5003 prices SCN-2100 at 1150 against a list price of
#: 1250 — a line charging something other than the catalogue, which is exactly
#: why `unit_price_usd` lives on the line and not on the item.
SALES_ORDERS: list[dict] = [
    {"id": "5000", "tranId": "SO5000", "customerId": "1000",
     "tranDate": "2026-07-08", "status": "Billed", "total": 23136.0,
     "memo": "Annual restock — warehouse rollout",
     "lines": [
         {"itemId": "3001", "quantity": 12, "rate": 1250, "amount": 15000},
         {"itemId": "3002", "quantity": 24, "rate": 189, "amount": 4536},
         {"itemId": "3004", "quantity": 2, "rate": 1800, "amount": 3600},
     ],
     "createdAt": "2026-08-15T18:18:57.345453+00:00",
     "updatedAt": "2026-08-15T18:18:57.345453+00:00"},
    {"id": "5001", "tranId": "SO5001", "customerId": "1000",
     "tranDate": "2026-08-03", "status": "Pending Fulfillment", "total": 10950.0,
     "memo": "Scanning pilot hardware",
     "lines": [
         {"itemId": "3001", "quantity": 3, "rate": 1250, "amount": 3750},
         {"itemId": "3003", "quantity": 3, "rate": 2400, "amount": 7200},
     ],
     "createdAt": "2026-08-15T18:18:57.533759+00:00",
     "updatedAt": "2026-08-15T18:18:57.533759+00:00"},
    {"id": "5002", "tranId": "SO5002", "customerId": "1001",
     "tranDate": "2026-07-22", "status": "Partially Fulfilled", "total": 50200.0,
     "memo": "Plant floor phase 1",
     "lines": [
         {"itemId": "3000", "quantity": 4, "rate": 4750, "amount": 19000},
         {"itemId": "3004", "quantity": 4, "rate": 1800, "amount": 7200},
         {"itemId": "3003", "quantity": 10, "rate": 2400, "amount": 24000},
     ],
     "createdAt": "2026-08-15T18:18:57.815976+00:00",
     "updatedAt": "2026-08-15T18:18:57.815976+00:00"},
    {"id": "5003", "tranId": "SO5003", "customerId": "1002",
     "tranDate": "2026-08-11", "status": "Pending Approval", "total": 6900.0,
     "memo": "Dock scanning refresh",
     "lines": [
         {"itemId": "3001", "quantity": 6, "rate": 1150, "amount": 6900},
     ],
     "createdAt": "2026-08-15T18:18:57.976625+00:00",
     "updatedAt": "2026-08-15T18:18:57.976625+00:00"},
    {"id": "5004", "tranId": "SO5004", "customerId": "1003",
     "tranDate": "2026-06-29", "status": "Closed", "total": 47500.0,
     "memo": "Records digitization — phase 1",
     "lines": [
         {"itemId": "3005", "quantity": 40, "rate": 950, "amount": 38000},
         {"itemId": "3000", "quantity": 2, "rate": 4750, "amount": 9500},
     ],
     "createdAt": "2026-08-15T18:18:58.203986+00:00",
     "updatedAt": "2026-08-15T18:18:58.203986+00:00"},
]

#: The buy side, on the same shape as customers — and with no tax id anywhere,
#: which is why an ERP vendor can still reach a steward.
VENDORS: list[dict] = [
    {"id": "2000", "companyName": "Lakeshore Paper & Packaging",
     "email": "orders@lakeshorepaper.example.com", "phone": "(920) 555-0121",
     "terms": "Net 30",
     "createdAt": "2026-08-15T18:18:55.948548+00:00",
     "updatedAt": "2026-08-15T18:18:55.948548+00:00"},
    {"id": "2001", "companyName": "Midwest Component Supply",
     "email": "sales@midwestcomponent.example.com", "phone": "(414) 555-0155",
     "terms": "Net 30",
     "createdAt": "2026-08-15T18:18:56.084368+00:00",
     "updatedAt": "2026-08-15T18:18:56.084368+00:00"},
    {"id": "2002", "companyName": "Granite State Freight",
     "email": "dispatch@granitefreight.example.com", "phone": "(603) 555-0177",
     "terms": "Net 15",
     "createdAt": "2026-08-15T18:18:56.252219+00:00",
     "updatedAt": "2026-08-15T18:18:56.252219+00:00"},
    {"id": "2003", "companyName": "Cedarline Office Systems",
     "email": "service@cedarlineoffice.example.com", "phone": "(608) 555-0163",
     "terms": "Due on receipt",
     "createdAt": "2026-08-15T18:18:56.429954+00:00",
     "updatedAt": "2026-08-15T18:18:56.429954+00:00"},
]

#: Purchase orders name their vendor and their items by internal id only —
#: no `companyName`, no SKU anywhere on the row. That absence is what the
#: `erp-purchase-orders` feed's declared dereference exists for.
PURCHASE_ORDERS: list[dict] = [
    {"id": "6011", "tranId": "PO6011", "vendorId": "2002",
     "tranDate": "2026-08-29", "status": "Closed", "total": 1280.0,
     "memo": "Inbound freight — September",
     "lines": [{"itemId": "3004", "quantity": 2, "rate": 640, "amount": 1280}],
     "createdAt": "2026-08-28T01:27:04.784828+00:00",
     "updatedAt": "2026-08-29T11:12:07.572443+00:00"},
    {"id": "6012", "tranId": "PO6012", "vendorId": "2002",
     "tranDate": "2026-08-29", "status": "Closed", "total": 2560.0,
     "memo": "Inbound freight — September",
     "lines": [{"itemId": "3004", "quantity": 4, "rate": 640, "amount": 2560}],
     "createdAt": "2026-08-29T06:27:08.022190+00:00",
     "updatedAt": "2026-08-30T04:57:14.049912+00:00"},
    {"id": "6013", "tranId": "PO6013", "vendorId": "2000",
     "tranDate": "2026-08-31", "status": "Pending Receipt", "total": 3360.0,
     "memo": "Crating stock — fourth quarter",
     "lines": [{"itemId": "3002", "quantity": 35, "rate": 96, "amount": 3360}],
     "createdAt": "2026-08-30T11:57:06.211944+00:00",
     "updatedAt": "2026-08-30T13:12:04.620976+00:00"},
]

#: Five of the twelve live mailboxes. Four here and one deliberately off-page
#: below, because both office feeds reference a mailbox by internal id while
#: both wire shapes key on the address — so a message or an event whose mailbox
#: the delta did not carry is the dereference case, and it is also the case
#: where a *message* legitimately arrives with nowhere to hang.
MAILBOXES: list[dict] = [
    {'id': '54c4b697-b69c-447f-927d-79556e886ebd',
     'address': 'john.smith@naviant.com',
     'displayName': 'John Smith',
     'jobTitle': 'Account Executive',
     'department': 'Sales',
     'timeZone': 'America/Chicago',
     'createdAt': '2026-08-16T22:16:17.817795+00:00',
     'updatedAt': '2026-08-16T22:16:17.817795+00:00'},
    {'id': '4fcef8aa-dc97-4455-be44-ed149399b5cd',
     'address': 'raymond.fitzgerald@naviant.com',
     'displayName': 'Raymond Fitzgerald',
     'jobTitle': 'Controller',
     'department': 'Finance & Administration',
     'timeZone': 'America/Chicago',
     'createdAt': '2026-08-16T23:07:40.879264+00:00',
     'updatedAt': '2026-08-16T23:07:40.879264+00:00'},
    {'id': '52a02a75-105a-4af1-9154-6340013c9c7f',
     'address': 'gregory.lindholm@naviant.com',
     'displayName': 'Gregory Lindholm',
     'jobTitle': 'Director of Professional Services',
     'department': 'Professional Services',
     'timeZone': 'America/Chicago',
     'createdAt': '2026-08-16T23:07:40.764168+00:00',
     'updatedAt': '2026-08-16T23:07:40.764168+00:00'},
    {'id': '5ae3d6e7-c8d1-4d1d-81a7-254b03bc58f0',
     'address': 'marcus.reyes@naviant.com',
     'displayName': 'Marcus Reyes',
     'jobTitle': 'VP of Sales',
     'department': 'Sales',
     'timeZone': 'America/Chicago',
     'createdAt': '2026-08-19T03:57:05.725218+00:00',
     'updatedAt': '2026-08-19T03:57:05.725218+00:00'},
]

#: Reachable by id, absent from every list — Karen Boothe, who is also the
#: off-page HRIS worker above. One person missing from two deltas at once is
#: what a delta window actually looks like.
MAILBOX_OFF_PAGE: dict = {
    'id': '9825e255-3b3f-4a96-888f-09463fc8cad2',
    'address': 'karen.boothe@naviant.com',
    'displayName': 'Karen Boothe',
    'jobTitle': 'HR Generalist',
    'department': 'Human Resources',
    'timeZone': 'America/Chicago',
    'createdAt': '2026-08-17T09:12:04.365926+00:00',
    'updatedAt': '2026-08-17T09:12:04.365926+00:00',
}

#: Nine of the live 163 messages, chosen for what they cover rather than for
#: tidiness. Every row carries its **whole `body`** alongside `bodyPreview`,
#: exactly as the estate serves it — which is the only way a test can prove the
#: body never lands. Between them: a sender who is an Employee, one who is a
#: known Person, one nobody has ever heard of, and the same thread sitting in
#: two mailboxes under two ids and one `conversationId`.
#:
#: `billing@granitefreight.example.com` signing itself `GSF Logistics` is the
#: estate's real vendor drift. It arrives here as evidence and stays evidence:
#: `mail-sync` counts it and B5 is the flow that gets to have an opinion.
MAIL_MESSAGES: list[dict] = [
    {'id': 'AAMkA1TkazWITQry_6hnLp5qjTw',
     'mailboxId': '52a02a75-105a-4af1-9154-6340013c9c7f',
     'subject': 'Welcome to Naviant — your first week',
     'folder': 'Inbox',
     'from': 'karen.boothe@naviant.com',
     'fromName': 'Karen Boothe',
     'toRecipients': ['bethany.kruse@naviant.com',
                      'gregory.lindholm@naviant.com'],
     'ccRecipients': [],
     'body': 'Bethany,\n'
             '\n'
             'Welcome to Naviant. You start 2026-09-17 as Implementation '
             'Consultant, reporting to Gregory Lindholm. Your address here is '
             'bethany.kruse@naviant.com and your laptop will be on your desk '
             'when you arrive.\n'
             '\n'
             'The first weeks look like this: sign offer letter and i-9, '
             'provision laptop and accounts, complete tax withholding forms, '
             'manager welcome 1:1. Gregory will put a standing 1:1 on your '
             'calendar, and benefits enrolment opens inside your first thirty '
             'days.\n'
             '\n'
             'Anything you need before then, just ask.\n'
             '\n'
             'Karen',
     'importance': 'Normal',
     'isRead': False,
     'hasAttachments': False,
     'conversationId': 'AAQkC_0JeLdseT2-X6CXXuohfnA',
     'receivedDateTime': '2026-08-24T00:42:08.692166+00:00',
     'sentDateTime': '2026-08-24T00:42:08.692166+00:00',
     'bodyPreview': 'Bethany, Welcome to Naviant. You start 2026-09-17 as '
                    'Implementation Consultant, reporting to Gregory Lindholm. '
                    'Your address here is bethany…',
     'createdAt': '2026-08-24T00:42:09.034479+00:00',
     'updatedAt': '2026-08-24T00:42:09.034479+00:00'},
    {'id': 'AAMkAxD2Z36czThyRixDYIchVMQ',
     'mailboxId': '54c4b697-b69c-447f-927d-79556e886ebd',
     'subject': 'Northwind — Returns Dock Capture decision',
     'folder': 'Inbox',
     'from': 'tom.berg@northwindtraders.example.com',
     'fromName': 'Tom Berg',
     'toRecipients': ['john.smith@naviant.com'],
     'ccRecipients': [],
     'body': 'John,\n'
             '\n'
             'Thank you for the work on Northwind — Returns Dock Capture. We '
             'have decided to stay with our incumbent for this phase. The '
             "proposal was thorough and I'd like to keep talking about next "
             'year.\n'
             '\n'
             'Tom',
     'importance': 'Normal',
     'isRead': False,
     'hasAttachments': False,
     'conversationId': 'AAQkCFqOLee3IT4-MU1tUbyl__g',
     'receivedDateTime': '2026-08-26T08:27:10.902871+00:00',
     'sentDateTime': None,
     'bodyPreview': 'John, Thank you for the work on Northwind — Returns Dock '
                    'Capture. We have decided to stay with our incumbent for '
                    'this phase. The proposal w…',
     'createdAt': '2026-08-26T08:27:10.902876+00:00',
     'updatedAt': '2026-08-26T08:27:10.902876+00:00'},
    {'id': 'AAMkASU6akN-lQ0a9H_6Bq2jHng',
     'mailboxId': '9825e255-3b3f-4a96-888f-09463fc8cad2',
     'subject': 'Welcome to Naviant — your first week',
     'folder': 'Sent Items',
     'from': 'karen.boothe@naviant.com',
     'fromName': 'Karen Boothe',
     'toRecipients': ['corey.landsman@naviant.com',
                      'gregory.lindholm@naviant.com'],
     'ccRecipients': [],
     'body': 'Corey,\n'
             '\n'
             'Welcome to Naviant. You start 2026-09-22 as Implementation '
             'Consultant, reporting to Gregory Lindholm. Your address here is '
             'corey.landsman@naviant.com and your laptop will be on your desk '
             'when you arrive.\n'
             '\n'
             'The first weeks look like this: sign offer letter and i-9, '
             'provision laptop and accounts, complete tax withholding forms, '
             'manager welcome 1:1. Gregory will put a standing 1:1 on your '
             'calendar, and benefits enrolment opens inside your first thirty '
             'days.\n'
             '\n'
             'Anything you need before then, just ask.\n'
             '\n'
             'Karen',
     'importance': 'Normal',
     'isRead': True,
     'hasAttachments': False,
     'conversationId': 'AAQkCii943nrMQoa3CkoTmw3vPw',
     'receivedDateTime': None,
     'sentDateTime': '2026-08-29T22:27:05.245508+00:00',
     'bodyPreview': 'Corey, Welcome to Naviant. You start 2026-09-22 as '
                    'Implementation Consultant, reporting to Gregory Lindholm. '
                    'Your address here is corey.lan…',
     'createdAt': '2026-08-29T22:27:04.958381+00:00',
     'updatedAt': '2026-08-29T22:27:05.245508+00:00'},
    {'id': 'AAMkAqYAuv8dESS-p_OjT3T9QGQ',
     'mailboxId': '4fcef8aa-dc97-4455-be44-ed149399b5cd',
     'subject': 'Invoice reminder — PO6012',
     'folder': 'Inbox',
     'from': 'billing@granitefreight.example.com',
     'fromName': 'GSF Logistics',
     'toRecipients': ['raymond.fitzgerald@naviant.com'],
     'ccRecipients': [],
     'body': 'This is a courtesy reminder that the balance on our invoice for '
             'purchase order PO6012, $2,560.00, is due on Net 15 terms. If '
             'payment has already been released, please disregard.',
     'importance': 'Normal',
     'isRead': False,
     'hasAttachments': False,
     'conversationId': 'AAQkCneWLJzqcSQedcIRA0kxong',
     'receivedDateTime': '2026-08-29T23:57:04.355808+00:00',
     'sentDateTime': None,
     'bodyPreview': 'This is a courtesy reminder that the balance on our invoice '
                    'for purchase order PO6012, $2,560.00, is due on Net 15 '
                    'terms. If payment has al…',
     'createdAt': '2026-08-29T23:57:04.355823+00:00',
     'updatedAt': '2026-08-29T23:57:04.355823+00:00'},
    {'id': 'AAMkA7Pf7ZuYTRKa-aISbmbAemA',
     'mailboxId': '54c4b697-b69c-447f-927d-79556e886ebd',
     'subject': 'Summit Health — Records Digitization Phase 2',
     'folder': 'Sent Items',
     'from': 'john.smith@naviant.com',
     'fromName': 'John Smith',
     'toRecipients': ['priya.raman@summithealth.example.com'],
     'ccRecipients': ['evan.walsh@summithealth.example.com'],
     'body': 'Priya,\n'
             '\n'
             'Good talking today. To recap what we scoped: bureau day rate for '
             'the basement records plus two production units on site. That is '
             'the Records Digitization Phase 2 work we walked through, and the '
             'digitization schedule is the piece still open.\n'
             '\n'
             "I'll have numbers to you by Friday.\n"
             '\n'
             'John',
     'importance': 'Normal',
     'isRead': True,
     'hasAttachments': False,
     'conversationId': 'AAQkC4Hgn3031T4Wjwm6HW6Hf3Q',
     'receivedDateTime': None,
     'sentDateTime': '2026-08-30T10:27:06.782790+00:00',
     'bodyPreview': 'Priya, Good talking today. To recap what we scoped: bureau '
                    'day rate for the basement records plus two production units '
                    'on site. That is the…',
     'createdAt': '2026-08-30T10:27:06.473503+00:00',
     'updatedAt': '2026-08-30T10:27:06.782790+00:00'},
    {'id': 'AAMkAdiv48e4_TGWA7VijoLQLSA',
     'mailboxId': '52a02a75-105a-4af1-9154-6340013c9c7f',
     'subject': 'Restock — Lakeshore Paper and Packaging',
     'folder': 'Sent Items',
     'from': 'gregory.lindholm@naviant.com',
     'fromName': 'Gregory Lindholm',
     'toRecipients': ['raymond.fitzgerald@naviant.com'],
     'ccRecipients': ['curtis.boyd@naviant.com'],
     'body': 'Raymond,\n'
             '\n'
             'The shipping supplies for the Northwind crates are short. Can we '
             'cut a PO to Lakeshore Paper and Packaging this week? Lead time has '
             "been running about two weeks, so I'd rather not wait on it.\n"
             '\n'
             "I'll have Curtis check the shipment in when it lands.\n"
             '\n'
             'Gregory',
     'importance': 'Normal',
     'isRead': True,
     'hasAttachments': False,
     'conversationId': 'AAQkC0EauXR_HQTSwEkVNiIihNw',
     'receivedDateTime': None,
     'sentDateTime': '2026-08-30T11:27:10.429389+00:00',
     'bodyPreview': 'Raymond, The shipping supplies for the Northwind crates are '
                    'short. Can we cut a PO to Lakeshore Paper and Packaging '
                    'this week? Lead time ha…',
     'createdAt': '2026-08-30T11:27:10.079555+00:00',
     'updatedAt': '2026-08-30T11:27:10.429389+00:00'},
    {'id': 'AAMkA6CKvgQBDSF6MMHPEBmcnwQ',
     'mailboxId': '4fcef8aa-dc97-4455-be44-ed149399b5cd',
     'subject': 'Restock — Lakeshore Paper and Packaging',
     'folder': 'Inbox',
     'from': 'gregory.lindholm@naviant.com',
     'fromName': 'Gregory Lindholm',
     'toRecipients': ['raymond.fitzgerald@naviant.com'],
     'ccRecipients': ['curtis.boyd@naviant.com'],
     'body': 'Raymond,\n'
             '\n'
             'The shipping supplies for the Northwind crates are short. Can we '
             'cut a PO to Lakeshore Paper and Packaging this week? Lead time has '
             "been running about two weeks, so I'd rather not wait on it.\n"
             '\n'
             "I'll have Curtis check the shipment in when it lands.\n"
             '\n'
             'Gregory',
     'importance': 'Normal',
     'isRead': False,
     'hasAttachments': False,
     'conversationId': 'AAQkC0EauXR_HQTSwEkVNiIihNw',
     'receivedDateTime': '2026-08-30T11:27:10.429389+00:00',
     'sentDateTime': '2026-08-30T11:27:10.429389+00:00',
     'bodyPreview': 'Raymond, The shipping supplies for the Northwind crates are '
                    'short. Can we cut a PO to Lakeshore Paper and Packaging '
                    'this week? Lead time ha…',
     'createdAt': '2026-08-30T11:27:10.668843+00:00',
     'updatedAt': '2026-08-30T11:27:10.668843+00:00'},
    {'id': 'AAMkAdliML1ppRHiNgR5Cq76BVg',
     'mailboxId': '4fcef8aa-dc97-4455-be44-ed149399b5cd',
     'subject': 'PO6013 — Crating stock — fourth quarter',
     'folder': 'Sent Items',
     'from': 'raymond.fitzgerald@naviant.com',
     'fromName': 'Raymond Fitzgerald',
     'toRecipients': ['orders@lakeshorepaper.example.com'],
     'ccRecipients': ['curtis.boyd@naviant.com'],
     'body': 'Good morning,\n'
             '\n'
             'PO6013 attached — confirm the ship date and the freight terms. The '
             'order is crating stock — fourth quarter, $3,360.00 on Net 30 '
             'terms.\n'
             '\n'
             'Deliveries go to the bureau floor entrance; Curtis Boyd signs for '
             'them.\n'
             '\n'
             'Raymond Fitzgerald | Controller | Naviant',
     'importance': 'Normal',
     'isRead': True,
     'hasAttachments': True,
     'conversationId': 'AAQkC6hrsnMKETumavHpEtdsyvQ',
     'receivedDateTime': None,
     'sentDateTime': '2026-08-30T16:42:09.107888+00:00',
     'bodyPreview': 'Good morning, PO6013 attached — confirm the ship date and '
                    'the freight terms. The order is crating stock — fourth '
                    'quarter, $3,360.00 on Net …',
     'createdAt': '2026-08-30T16:42:08.844088+00:00',
     'updatedAt': '2026-08-30T16:42:09.107888+00:00'},
    {'id': 'AAMkAjdMx-40_Tkm2mEGON2lpYw',
     'mailboxId': '54c4b697-b69c-447f-927d-79556e886ebd',
     'subject': 'RE: Summit Health — Records Digitization Phase 2',
     'folder': 'Inbox',
     'from': 'priya.raman@summithealth.example.com',
     'fromName': 'Priya Raman',
     'toRecipients': ['john.smith@naviant.com'],
     'ccRecipients': [],
     'body': 'John,\n'
             '\n'
             'Two things on Summit — Records Digitization Phase 2. The timeline '
             'has us installing during our busiest month, and finance is asking '
             "whether the capture licences can move to next year's budget. What "
             'does that do to the number?\n'
             '\n'
             'Priya',
     'importance': 'Normal',
     'isRead': False,
     'hasAttachments': False,
     'conversationId': 'AAQkCCf3No3zURxSziW7yLNUpLQ',
     'receivedDateTime': '2026-08-30T16:57:09.129520+00:00',
     'sentDateTime': None,
     'bodyPreview': 'John, Two things on Summit — Records Digitization Phase 2. '
                    'The timeline has us installing during our busiest month, '
                    'and finance is asking w…',
     'createdAt': '2026-08-30T16:57:09.129526+00:00',
     'updatedAt': '2026-08-30T16:57:09.129526+00:00'},
]

#: Six of the live 116 calendar events, on three of the mailboxes above: two
#: with external attendees, one all-day trip the provider marks `Out of Office`
#: (availability, which is why it does not become an event `kind`), and two
#: copies of one interview loop sitting on one calendar under two ids.
OFFICE_EVENTS: list[dict] = [
    {'id': 'AAQkACFb1CmUCQaekMF3pfRrULg',
     'mailboxId': '54c4b697-b69c-447f-927d-79556e886ebd',
     'subject': 'Sales pipeline stand-up',
     'start': '2026-08-16T14:00:00+00:00',
     'end': '2026-08-16T14:30:00+00:00',
     'isAllDay': False,
     'location': 'Teams',
     'organizer': 'john.smith@naviant.com',
     'attendees': ['tom.berg@northwindtraders.example.com'],
     'body': 'Weekly review of open opportunities.',
     'showAs': 'Busy',
     'isCancelled': False,
     'createdAt': '2026-08-16T22:16:19.028001+00:00',
     'updatedAt': '2026-08-16T22:16:19.028001+00:00'},
    {'id': 'AAQkAcD_d8X_gRsCVkPyXNY5eQQ',
     'mailboxId': '54c4b697-b69c-447f-927d-79556e886ebd',
     'subject': 'Northwind — restock pricing review',
     'start': '2026-08-16T18:00:00+00:00',
     'end': '2026-08-16T19:00:00+00:00',
     'isAllDay': False,
     'location': 'Teams',
     'organizer': 'john.smith@naviant.com',
     'attendees': ['maria.delgado@northwindtraders.example.com',
                   'tom.berg@northwindtraders.example.com'],
     'body': 'Walk through the revised scanner quantities.',
     'showAs': 'Busy',
     'isCancelled': False,
     'createdAt': '2026-08-16T22:16:19.088171+00:00',
     'updatedAt': '2026-08-16T22:16:19.088171+00:00'},
    {'id': 'AAQkAh6WKsEo_RE-rGXsWQmZf4g',
     'mailboxId': '54c4b697-b69c-447f-927d-79556e886ebd',
     'subject': 'Blue Harbor site visit — Seattle',
     'start': '2026-08-19T05:00:00+00:00',
     'end': '2026-08-20T05:00:00+00:00',
     'isAllDay': True,
     'location': 'Seattle, WA',
     'organizer': 'john.smith@naviant.com',
     'attendees': [],
     'body': None,
     'showAs': 'Out of Office',
     'isCancelled': False,
     'createdAt': '2026-08-16T22:16:19.298839+00:00',
     'updatedAt': '2026-08-16T22:16:19.298839+00:00'},
    {'id': 'AAQkAKONKmriJRMGv0frOTl75CQ',
     'mailboxId': '52a02a75-105a-4af1-9154-6340013c9c7f',
     'subject': 'Interview — Andre Mbeki (Implementation Consultant)',
     'start': '2026-08-31T15:00:00+00:00',
     'end': '2026-08-31T16:00:00+00:00',
     'isAllDay': False,
     'location': 'Conference room B',
     'organizer': 'gregory.lindholm@naviant.com',
     'attendees': ['karen.boothe@naviant.com', 'gregory.lindholm@naviant.com'],
     'body': 'Implementation Consultant loop. Bureau volume is up; '
             'implementation backlog is at six weeks.',
     'showAs': 'Busy',
     'isCancelled': False,
     'createdAt': '2026-08-17T09:12:04.504964+00:00',
     'updatedAt': '2026-08-17T09:12:04.504964+00:00'},
    {'id': 'AAQkAN6W-4OTPR1imgzMUeq4mFQ',
     'mailboxId': '52a02a75-105a-4af1-9154-6340013c9c7f',
     'subject': 'Interview — Andre Mbeki (Implementation Consultant)',
     'start': '2026-09-01T19:00:00+00:00',
     'end': '2026-09-01T20:00:00+00:00',
     'isAllDay': False,
     'location': 'Teams',
     'organizer': 'gregory.lindholm@naviant.com',
     'attendees': ['karen.boothe@naviant.com', 'gregory.lindholm@naviant.com'],
     'body': 'Implementation Consultant loop. Bureau volume is up; '
             'implementation backlog is at six weeks.',
     'showAs': 'Busy',
     'isCancelled': False,
     'createdAt': '2026-08-17T09:12:04.625169+00:00',
     'updatedAt': '2026-08-17T09:12:04.625169+00:00'},
    {'id': 'AAQkAQU0i-ebVRpG_dGol1KjVkQ',
     'mailboxId': '5ae3d6e7-c8d1-4d1d-81a7-254b03bc58f0',
     'subject': 'Interview — Renata Cordova (Account Executive)',
     'start': '2026-09-01T15:00:00+00:00',
     'end': '2026-09-01T16:00:00+00:00',
     'isAllDay': False,
     'location': 'Conference room B',
     'organizer': 'marcus.reyes@naviant.com',
     'attendees': ['karen.boothe@naviant.com', 'marcus.reyes@naviant.com'],
     'body': 'Account Executive loop. Backfill for the Wisconsin territory.',
     'showAs': 'Busy',
     'isCancelled': False,
     'createdAt': '2026-08-19T03:57:06.004445+00:00',
     'updatedAt': '2026-08-19T03:57:06.004445+00:00'},
]


def _event(**over) -> dict:
    """One HRIS ledger row, with every column the estate actually sends.

    The `previous*` half is the reason this feed lands whole: a promotion says
    what the job, department, manager and salary *became* and what each of them
    *was*, and a hire carries nulls in all four because there was no before.
    """
    row = {
        "id": "", "employeeId": "", "eventType": "", "effectiveDate": "",
        "jobId": None, "departmentId": None, "managerId": None,
        "payType": "Salary", "annualSalary": None, "hourlyRate": None,
        "previousJobId": None, "previousDepartmentId": None,
        "previousManagerId": None, "previousAnnualSalary": None,
        "previousHourlyRate": None, "reason": None, "note": None,
        "createdAt": "", "updatedAt": "",
    }
    row.update(over)
    return row


#: The append-only employment ledger — one row of each kind the estate emits.
#: EVT-1019 is Alan Whitcomb's termination, the row behind the `Terminated`
#: worker status on EMP-1016; EVT-1017 is the promotion whose before/after pair
#: no ontology type holds, which is the whole argument for landing these whole.
EMPLOYMENT_EVENTS: list[dict] = [
    _event(id="EVT-1016", employeeId="EMP-1016", eventType="Hire",
           effectiveDate="2020-11-15", jobId="JOB-1003",
           departmentId="DEPT-1002", managerId="EMP-1002",
           annualSalary=108000, note="New hire",
           createdAt="2026-08-16T22:16:24.190459+00:00",
           updatedAt="2026-08-16T22:16:24.190459+00:00"),
    _event(id="EVT-1017", employeeId="EMP-1014", eventType="Promotion",
           effectiveDate="2026-01-28", jobId="JOB-1010",
           departmentId="DEPT-1005", managerId="EMP-1004",
           annualSalary=152000.0, previousJobId="JOB-1011",
           previousDepartmentId="DEPT-1005", previousManagerId="EMP-1004",
           previousAnnualSalary=128000,
           note="Promoted after the capture-platform rebuild.",
           createdAt="2026-08-16T22:16:24.514674+00:00",
           updatedAt="2026-08-16T22:16:24.514674+00:00"),
    _event(id="EVT-1018", employeeId="EMP-1010", eventType="Transfer",
           effectiveDate="2026-04-18", jobId="JOB-1006",
           departmentId="DEPT-1004", managerId="EMP-1003",
           annualSalary=96000, previousJobId="JOB-1006",
           previousDepartmentId="DEPT-1003", previousManagerId="EMP-1003",
           previousAnnualSalary=96000,
           note="Moved to support the scanning bureau accounts.",
           createdAt="2026-08-16T22:16:24.689869+00:00",
           updatedAt="2026-08-16T22:16:24.689869+00:00"),
    _event(id="EVT-1019", employeeId="EMP-1016", eventType="Termination",
           effectiveDate="2026-07-02", jobId="JOB-1003",
           departmentId="DEPT-1002", managerId="EMP-1002",
           annualSalary=108000, previousJobId="JOB-1003",
           previousDepartmentId="DEPT-1002", previousManagerId="EMP-1002",
           previousAnnualSalary=108000,
           reason="Resignation — accepted an outside offer",
           createdAt="2026-08-16T22:16:24.856952+00:00",
           updatedAt="2026-08-16T22:16:24.856952+00:00"),
    _event(id="EVT-1020", employeeId="EMP-1006",
           eventType="Leave of Absence", effectiveDate="2026-07-27",
           jobId="JOB-1013", departmentId="DEPT-1007", managerId="EMP-1001",
           annualSalary=92000, previousJobId="JOB-1013",
           previousDepartmentId="DEPT-1007", previousManagerId="EMP-1001",
           previousAnnualSalary=92000, reason="Parental",
           createdAt="2026-08-16T22:16:24.971065+00:00",
           updatedAt="2026-08-16T22:16:24.971065+00:00"),
    _event(id="EVT-1033", employeeId="EMP-1025", eventType="Hire",
           effectiveDate="2026-09-22", jobId="JOB-1006",
           departmentId="DEPT-1003", managerId="EMP-1003",
           annualSalary=94000.0, note="New hire",
           createdAt="2026-08-29T19:42:05.567533+00:00",
           updatedAt="2026-08-29T19:42:05.567533+00:00"),
]

#: The DMS tree. Three of these — `Folder1`, `Folder2`, `Folder1/Folder3` —
#: are somebody's real hand-made scratch folders from before the namespace was
#: seeded, and they carry `updatedAt: null`, which is the shape a delta filter
#: has to survive rather than a tidy-up to be made in the fixture.
FOLDERS: list[dict] = [
    {"id": "a0374b47504b", "name": "Folder1", "parentId": None,
     "createdAt": "2026-08-15T16:14:16.400759+00:00", "updatedAt": None},
    {"id": "d9d516051550", "name": "Folder2", "parentId": None,
     "createdAt": "2026-08-15T16:15:13.958828+00:00", "updatedAt": None},
    {"id": "d2970492a735", "name": "Folder3", "parentId": "a0374b47504b",
     "createdAt": "2026-08-15T16:15:20.660191+00:00", "updatedAt": None},
    {"id": "da7a1f768eb8", "name": "Customers", "parentId": None,
     "createdAt": "2026-08-17T01:27:04.245169+00:00",
     "updatedAt": "2026-08-17T01:27:04.245169+00:00"},
    {"id": "b6a714547ead", "name": "Northwind Traders",
     "parentId": "da7a1f768eb8",
     "createdAt": "2026-08-17T01:27:04.483358+00:00",
     "updatedAt": "2026-08-17T01:27:04.483358+00:00"},
    {"id": "e3463e01a5d7", "name": "Proposals", "parentId": "b6a714547ead",
     "createdAt": "2026-08-17T01:27:04.646644+00:00",
     "updatedAt": "2026-08-17T01:27:04.646644+00:00"},
    {"id": "e27859fd2fdf", "name": "Orders", "parentId": "b6a714547ead",
     "createdAt": "2026-08-18T00:57:04.480021+00:00",
     "updatedAt": "2026-08-18T00:57:04.480021+00:00"},
    {"id": "15824c62699b", "name": "Accounts Payable", "parentId": None,
     "createdAt": "2026-08-17T11:57:04.340211+00:00",
     "updatedAt": "2026-08-17T11:57:04.340211+00:00"},
    {"id": "65fccd01e8e8", "name": "GSF", "parentId": "15824c62699b",
     "createdAt": "2026-08-18T23:42:04.804815+00:00",
     "updatedAt": "2026-08-18T23:42:04.804815+00:00"},
    {"id": "32bde0f261fc", "name": "2026", "parentId": "65fccd01e8e8",
     "createdAt": "2026-08-18T23:42:05.029508+00:00",
     "updatedAt": "2026-08-18T23:42:05.029508+00:00"},
    {"id": "9327d395a7dd", "name": "Employee Files", "parentId": None,
     "createdAt": "2026-08-17T16:27:06.893050+00:00",
     "updatedAt": "2026-08-17T16:27:06.893050+00:00"},
    {"id": "26c73f300e72", "name": "Andre Mbeki", "parentId": "9327d395a7dd",
     "createdAt": "2026-08-17T16:27:07.075648+00:00",
     "updatedAt": "2026-08-17T16:27:07.075648+00:00"},
]

#: The files in that tree. `versions` travels because the estate sends it; the
#: connector reads `latestVersion` off the row and writes down an address, and
#: nothing here has ever been opened. The two `GSF invoice 2026-09-12.pdf` rows
#: share a filename and differ by id — the sim filed the same invoice twice,
#: which is exactly the kind of thing a document mirror must not merge away.
DMS_DOCUMENTS: list[dict] = [
    {"id": "16f6fd42de84", "name": "Cumulus Overview copy.pdf",
     "folderId": "d2970492a735", "contentType": "application/octet-stream",
     "size": 30142410, "latestVersion": 1,
     "createdAt": "2026-08-15T16:15:46.632252+00:00",
     "updatedAt": "2026-08-15T16:15:46.632252+00:00",
     "versions": [{"version": 1, "filename": "Cumulus Overview copy.pdf",
                   "contentType": "application/octet-stream",
                   "size": 30142410,
                   "uploadedAt": "2026-08-15T16:15:46.632099+00:00"}]},
    {"id": "cb0e2fe64899", "name": "HR_Separation.pdf",
     "folderId": "a0374b47504b", "contentType": "application/octet-stream",
     "size": 153549, "latestVersion": 1,
     "createdAt": "2026-08-15T16:14:23.422928+00:00",
     "updatedAt": "2026-08-15T16:14:23.422928+00:00",
     "versions": [{"version": 1, "filename": "HR_Separation.pdf",
                   "contentType": "application/octet-stream",
                   "size": 153549,
                   "uploadedAt": "2026-08-15T16:14:23.422843+00:00"}]},
    {"id": "c87a1fac055d",
     "name": "Northwind Traders proposal 2026-08-19.pdf",
     "folderId": "e3463e01a5d7", "contentType": "text/plain",
     "size": 320, "latestVersion": 2,
     "createdAt": "2026-08-17T01:27:04.967008+00:00",
     "updatedAt": "2026-08-17T08:12:14.556058+00:00",
     "versions": [
         {"version": 1,
          "filename": "Northwind Traders proposal 2026-08-19.txt",
          "contentType": "text/plain", "size": 358,
          "uploadedAt": "2026-08-17T01:27:04.966994+00:00"},
         {"version": 2,
          "filename": "Northwind Traders proposal 2026-08-19.txt",
          "contentType": "text/plain", "size": 320,
          "uploadedAt": "2026-08-17T08:12:14.556047+00:00"}]},
    {"id": "243c8eda7dba",
     "name": "Northwind Traders signed order acknowledgement 2026-09-19.pdf",
     "folderId": "e27859fd2fdf", "contentType": "text/plain",
     "size": 270, "latestVersion": 1,
     "createdAt": "2026-08-18T00:57:04.807823+00:00",
     "updatedAt": "2026-08-18T00:57:04.807823+00:00",
     "versions": [
         {"version": 1,
          "filename":
              "Northwind Traders signed order acknowledgement 2026-09-19.txt",
          "contentType": "text/plain", "size": 270,
          "uploadedAt": "2026-08-18T00:57:04.807810+00:00"}]},
    {"id": "82e6b9be140a", "name": "Andre Mbeki offer letter 2026-09-05.pdf",
     "folderId": "26c73f300e72", "contentType": "text/plain",
     "size": 195, "latestVersion": 2,
     "createdAt": "2026-08-17T16:27:07.424570+00:00",
     "updatedAt": "2026-08-18T02:42:04.503705+00:00",
     "versions": [
         {"version": 1,
          "filename": "Andre Mbeki offer letter 2026-09-05.txt",
          "contentType": "text/plain", "size": 400,
          "uploadedAt": "2026-08-17T16:27:07.424562+00:00"},
         {"version": 2,
          "filename": "Andre Mbeki offer letter 2026-09-05.txt",
          "contentType": "text/plain", "size": 195,
          "uploadedAt": "2026-08-18T02:42:04.503694+00:00"}]},
    {"id": "2f3c3799a309",
     "name": "Andre Mbeki resignation letter 2027-01-12.pdf",
     "folderId": "26c73f300e72", "contentType": "text/plain",
     "size": 238, "latestVersion": 1,
     "createdAt": "2026-08-20T00:42:04.709263+00:00",
     "updatedAt": "2026-08-20T00:42:04.709263+00:00",
     "versions": [
         {"version": 1,
          "filename": "Andre Mbeki resignation letter 2027-01-12.txt",
          "contentType": "text/plain", "size": 238,
          "uploadedAt": "2026-08-20T00:42:04.709253+00:00"}]},
    {"id": "8467902a9c82", "name": "GSF invoice 2026-09-02.pdf",
     "folderId": "32bde0f261fc", "contentType": "text/plain",
     "size": 195, "latestVersion": 2,
     "createdAt": "2026-08-18T23:42:05.482789+00:00",
     "updatedAt": "2026-08-19T01:12:05.355569+00:00",
     "versions": [
         {"version": 1, "filename": "GSF invoice 2026-09-02.txt",
          "contentType": "text/plain", "size": 225,
          "uploadedAt": "2026-08-18T23:42:05.482774+00:00"},
         {"version": 2, "filename": "GSF invoice 2026-09-02.txt",
          "contentType": "text/plain", "size": 195,
          "uploadedAt": "2026-08-19T01:12:05.355558+00:00"}]},
    {"id": "1c22882f31cd", "name": "GSF invoice 2026-09-12.pdf",
     "folderId": "32bde0f261fc", "contentType": "text/plain",
     "size": 213, "latestVersion": 1,
     "createdAt": "2026-08-28T16:57:07.979947+00:00",
     "updatedAt": "2026-08-28T16:57:07.979947+00:00",
     "versions": [
         {"version": 1, "filename": "GSF invoice 2026-09-12.txt",
          "contentType": "text/plain", "size": 213,
          "uploadedAt": "2026-08-28T16:57:07.979932+00:00"}]},
    {"id": "4f8ad735aac3", "name": "GSF invoice 2026-09-12.pdf",
     "folderId": "32bde0f261fc", "contentType": "text/plain",
     "size": 213, "latestVersion": 1,
     "createdAt": "2026-08-29T20:27:11.617867+00:00",
     "updatedAt": "2026-08-29T20:27:11.617867+00:00",
     "versions": [
         {"version": 1, "filename": "GSF invoice 2026-09-12.txt",
          "contentType": "text/plain", "size": 213,
          "uploadedAt": "2026-08-29T20:27:11.617856+00:00"}]},
]


#: What a list route answers under, by app and resource.
#: The delivery system's own book, recorded whole for the clients, projects,
#: phases and tasks — thirteen, eighteen, seventy-seven and forty-eight rows,
#: exactly as the live app serves them. Every drifted spelling the roster
#: designs is here, because the identity work turns on them: `Apex Mfg` (the
#: abbreviation that is also a vendor's legal name), `Kestrel CU`, `Northwind`
#: without its `Traders`, and the two clients nobody filled a contact address
#: in for.
PSA_CLIENTS: list[dict] = [
    {"id": "101", "name": "Apex Mfg", "contactName": "Susan Kowalski",
     "contactEmail": "susan.kowalski@apexmfg.example.com",
     "createdAt": "2026-08-30T22:39:56.218626+00:00",
     "updatedAt": "2026-08-30T22:39:56.218626+00:00"},
    {"id": "102", "name": "Blue Harbor", "contactName": "Alicia Grant",
     "contactEmail": None, "createdAt": "2026-08-30T22:39:56.340016+00:00",
     "updatedAt": "2026-08-30T22:39:56.340016+00:00"},
    {"id": "108", "name": "Dane County ROD", "contactName": "Lucy Hargrove",
     "contactEmail": "lucille.hargrove@danecounty.example.com",
     "createdAt": "2026-08-30T22:39:56.762795+00:00",
     "updatedAt": "2026-08-30T22:39:56.762795+00:00"},
    {"id": "111", "name": "Driftless Tech", "contactName": "Stuart Menzel",
     "contactEmail": "stuart.menzel@driftlesstech.example.com",
     "createdAt": "2026-08-30T22:39:56.910253+00:00",
     "updatedAt": "2026-08-30T22:39:56.910253+00:00"},
    {"id": "106", "name": "Four Lakes Ortho", "contactName": "Ellen Cho",
     "contactEmail": "ellen.cho@fourlakesortho.example.com",
     "createdAt": "2026-08-30T22:39:56.656976+00:00",
     "updatedAt": "2026-08-30T22:39:56.656976+00:00"},
    {"id": "105", "name": "Kestrel CU", "contactName": "Angela Marsh",
     "contactEmail": "angela.marsh@kestrelcu.example.com",
     "createdAt": "2026-08-30T22:39:56.599876+00:00",
     "updatedAt": "2026-08-30T22:39:56.599876+00:00"},
    {"id": "107", "name": "Mercer and Vance",
     "contactName": "Gwendolyn Sorensen",
     "contactEmail": "gwen.sorensen@mercervance.example.com",
     "createdAt": "2026-08-30T22:39:56.722001+00:00",
     "updatedAt": "2026-08-30T22:39:56.722001+00:00"},
    {"id": "100", "name": "Northwind", "contactName": "Maria Delgado",
     "contactEmail": "maria.delgado@northwindtraders.example.com",
     "createdAt": "2026-08-30T22:39:55.988739+00:00",
     "updatedAt": "2026-08-30T22:39:55.988739+00:00"},
    {"id": "112", "name": "Prairie Records", "contactName": "Vic Malloy",
     "contactEmail": None, "createdAt": "2026-08-30T22:39:56.961389+00:00",
     "updatedAt": "2026-08-30T22:39:56.961389+00:00"},
    {"id": "110", "name": "Stonebridge", "contactName": "Renee Calloway",
     "contactEmail": "renee.calloway@stonebridgepm.example.com",
     "createdAt": "2026-08-30T22:39:56.857031+00:00",
     "updatedAt": "2026-08-30T22:39:56.857031+00:00"},
    {"id": "103", "name": "Summit Health", "contactName": "Evan Walsh",
     "contactEmail": "evan.walsh@summithealth.example.com",
     "createdAt": "2026-08-30T22:39:56.438381+00:00",
     "updatedAt": "2026-08-30T22:39:56.438381+00:00"},
    {"id": "104", "name": "Verona Foods", "contactName": "Carol Brandt",
     "contactEmail": "carol.brandt@veronafoods.example.com",
     "createdAt": "2026-08-30T22:39:56.535141+00:00",
     "updatedAt": "2026-08-30T22:39:56.535141+00:00"},
    {"id": "109", "name": "Voss Auto", "contactName": "Brad Kettner",
     "contactEmail": "brad.kettner@vossauto.example.com",
     "createdAt": "2026-08-30T22:39:56.811304+00:00",
     "updatedAt": "2026-08-30T22:39:56.811304+00:00"},
]

PSA_PROJECTS: list[dict] = [
    {"id": "100", "projectCode": "ENG-0100", "clientId": "101",
     "name": "Apex Mfg — plant floor phase 1", "status": "Active",
     "projectManager": "S. Hoffmann", "startDate": "2026-07-16",
     "targetEndDate": "2026-10-04", "budgetHours": 420, "actualHours": 261.0,
     "createdAt": "2026-08-30T22:39:57.034691+00:00",
     "updatedAt": "2026-08-30T22:39:57.034691+00:00"},
    {"id": "107", "projectCode": "ENG-0107", "clientId": "102",
     "name": "Blue Harbor — dock scanning refresh install",
     "status": "Completed", "projectManager": "Danielle G.",
     "startDate": "2025-11-03", "targetEndDate": "2025-12-23",
     "budgetHours": 210, "actualHours": 0.0,
     "createdAt": "2026-08-30T22:40:21.613801+00:00",
     "updatedAt": "2026-08-30T22:40:21.613801+00:00"},
    {"id": "102", "projectCode": "ENG-0102", "clientId": "108",
     "name": "Dane County ROD — deed backfile conversion", "status": "Active",
     "projectManager": "Yolanda J.", "startDate": "2026-06-21",
     "targetEndDate": "2026-10-19", "budgetHours": 1200, "actualHours": 512.0,
     "createdAt": "2026-08-30T22:40:04.972876+00:00",
     "updatedAt": "2026-08-30T22:40:04.972876+00:00"},
    {"id": "104", "projectCode": "ENG-0104", "clientId": "111",
     "name": "Driftless Tech — financial aid document workflow",
     "status": "Active", "projectManager": "Yolanda J.",
     "startDate": "2026-07-21", "targetEndDate": "2026-10-14",
     "budgetHours": 340, "actualHours": 169.5,
     "createdAt": "2026-08-30T22:40:17.139750+00:00",
     "updatedAt": "2026-08-30T22:40:17.139750+00:00"},
    {"id": "117", "projectCode": "ENG-0117", "clientId": "111",
     "name": "Driftless Tech — transcript capture install",
     "status": "Completed", "projectManager": "Danielle G.",
     "startDate": "2025-08-25", "targetEndDate": "2025-10-14",
     "budgetHours": 240, "actualHours": 0.0,
     "createdAt": "2026-08-30T22:40:26.266794+00:00",
     "updatedAt": "2026-08-30T22:40:26.266794+00:00"},
    {"id": "114", "projectCode": "ENG-0114", "clientId": "106",
     "name": "Four Lakes Ortho — clinic intake capture install",
     "status": "Completed", "projectManager": "Aaron N.",
     "startDate": "2025-10-14", "targetEndDate": "2025-12-13",
     "budgetHours": 420, "actualHours": 0.0,
     "createdAt": "2026-08-30T22:40:25.455320+00:00",
     "updatedAt": "2026-08-30T22:40:25.455320+00:00"},
    {"id": "103", "projectCode": "ENG-0103", "clientId": "105",
     "name": "Kestrel CU — branch capture rollout", "status": "Active",
     "projectManager": "Caitlin F.", "startDate": "2026-07-06",
     "targetEndDate": "2026-09-29", "budgetHours": 380, "actualHours": 289.0,
     "createdAt": "2026-08-30T22:40:12.278537+00:00",
     "updatedAt": "2026-08-30T22:40:12.278537+00:00"},
    {"id": "113", "projectCode": "ENG-0113", "clientId": "105",
     "name": "Kestrel CU — member file capture install", "status": "Completed",
     "projectManager": "Danielle G.", "startDate": "2025-08-05",
     "targetEndDate": "2025-09-14", "budgetHours": 120, "actualHours": 0.0,
     "createdAt": "2026-08-30T22:40:25.190414+00:00",
     "updatedAt": "2026-08-30T22:40:25.190414+00:00"},
    {"id": "109", "projectCode": "ENG-0109", "clientId": "107",
     "name": "Mercer and Vance — case file backfile conversion",
     "status": "Completed", "projectManager": "Yolanda J.",
     "startDate": "2025-09-24", "targetEndDate": "2025-11-23",
     "budgetHours": 300, "actualHours": 0.0,
     "createdAt": "2026-08-30T22:40:22.153388+00:00",
     "updatedAt": "2026-08-30T22:40:22.153388+00:00"},
    {"id": "106", "projectCode": "ENG-0106", "clientId": "100",
     "name": "Northwind — warehouse scanning pilot install",
     "status": "Completed", "projectManager": "Caitlin F.",
     "startDate": "2025-07-26", "targetEndDate": "2025-09-14",
     "budgetHours": 160, "actualHours": 0.0,
     "createdAt": "2026-08-30T22:40:21.287307+00:00",
     "updatedAt": "2026-08-30T22:40:21.287307+00:00"},
    {"id": "110", "projectCode": "ENG-0110", "clientId": "112",
     "name": "Prairie Records — overflow scanning", "status": "Completed",
     "projectManager": "Aaron N.", "startDate": "2025-11-03",
     "targetEndDate": "2025-12-03", "budgetHours": 180, "actualHours": 0.0,
     "createdAt": "2026-08-30T22:40:22.406018+00:00",
     "updatedAt": "2026-08-30T22:40:22.406018+00:00"},
    {"id": "116", "projectCode": "ENG-0116", "clientId": "110",
     "name": "Stonebridge — lease file conversion", "status": "Completed",
     "projectManager": "Yolanda J.", "startDate": "2025-09-04",
     "targetEndDate": "2025-10-04", "budgetHours": 80, "actualHours": 0.0,
     "createdAt": "2026-08-30T22:40:25.985632+00:00",
     "updatedAt": "2026-08-30T22:40:25.985632+00:00"},
    {"id": "108", "projectCode": "ENG-0108", "clientId": "103",
     "name": "Summit Health — records digitization phase 1",
     "status": "Completed", "projectManager": "S. Hoffmann",
     "startDate": "2025-10-04", "targetEndDate": "2026-01-02",
     "budgetHours": 640, "actualHours": 0.0,
     "createdAt": "2026-08-30T22:40:21.878420+00:00",
     "updatedAt": "2026-08-30T22:40:21.878420+00:00"},
    {"id": "101", "projectCode": "ENG-0101", "clientId": "103",
     "name": "Summit Health — records digitization phase 2",
     "status": "Active", "projectManager": "S. Hoffmann",
     "startDate": "2026-07-26", "targetEndDate": "2026-09-24",
     "budgetHours": 520, "actualHours": 338.0,
     "createdAt": "2026-08-30T22:40:00.664843+00:00",
     "updatedAt": "2026-08-30T22:40:00.664843+00:00"},
    {"id": "112", "projectCode": "ENG-0112", "clientId": "104",
     "name": "Verona Foods — plant scanner refresh", "status": "Completed",
     "projectManager": "Caitlin F.", "startDate": "2026-06-21",
     "targetEndDate": "2026-07-21", "budgetHours": 120, "actualHours": 141.0,
     "createdAt": "2026-08-30T22:40:22.914531+00:00",
     "updatedAt": "2026-08-30T22:40:22.914531+00:00"},
    {"id": "111", "projectCode": "ENG-0111", "clientId": "104",
     "name": "Verona Foods — receiving dock capture install",
     "status": "Completed", "projectManager": "Aaron N.",
     "startDate": "2025-09-14", "targetEndDate": "2025-10-24",
     "budgetHours": 140, "actualHours": 0.0,
     "createdAt": "2026-08-30T22:40:22.659019+00:00",
     "updatedAt": "2026-08-30T22:40:22.659019+00:00"},
    {"id": "115", "projectCode": "ENG-0115", "clientId": "109",
     "name": "Voss Auto — deal jacket capture install", "status": "Completed",
     "projectManager": "Danielle G.", "startDate": "2025-11-23",
     "targetEndDate": "2025-12-23", "budgetHours": 110, "actualHours": 0.0,
     "createdAt": "2026-08-30T22:40:25.725905+00:00",
     "updatedAt": "2026-08-30T22:40:25.725905+00:00"},
    {"id": "105", "projectCode": "ENG-0105", "clientId": "109",
     "name": "Voss Auto — title and service records capture",
     "status": "On Hold", "projectManager": "S. Hoffmann",
     "startDate": "2026-08-22", "targetEndDate": "2026-11-18",
     "budgetHours": 260, "actualHours": 12.0,
     "createdAt": "2026-08-30T22:40:20.435632+00:00",
     "updatedAt": "2026-08-30T22:40:20.435632+00:00"},
]

PSA_PHASES: list[dict] = [
    {"id": "100", "projectId": "100", "name": "Discovery", "sequence": 1,
     "status": "Complete", "targetDate": "2026-08-01",
     "createdAt": "2026-08-30T22:39:57.152739+00:00",
     "updatedAt": "2026-08-30T22:39:57.152739+00:00"},
    {"id": "101", "projectId": "100", "name": "Configuration", "sequence": 2,
     "status": "Complete", "targetDate": "2026-08-17",
     "createdAt": "2026-08-30T22:39:57.242278+00:00",
     "updatedAt": "2026-08-30T22:39:57.242278+00:00"},
    {"id": "102", "projectId": "100", "name": "Install", "sequence": 3,
     "status": "In Progress", "targetDate": "2026-09-02",
     "createdAt": "2026-08-30T22:39:57.316991+00:00",
     "updatedAt": "2026-08-30T22:39:57.316991+00:00"},
    {"id": "103", "projectId": "100", "name": "Training", "sequence": 4,
     "status": "Not Started", "targetDate": "2026-09-18",
     "createdAt": "2026-08-30T22:39:57.365422+00:00",
     "updatedAt": "2026-08-30T22:39:57.365422+00:00"},
    {"id": "104", "projectId": "100", "name": "Go-live", "sequence": 5,
     "status": "Not Started", "targetDate": "2026-10-04",
     "createdAt": "2026-08-30T22:39:57.434492+00:00",
     "updatedAt": "2026-08-30T22:39:57.434492+00:00"},
    {"id": "105", "projectId": "101", "name": "Prep", "sequence": 1,
     "status": "Complete", "targetDate": "2026-08-07",
     "createdAt": "2026-08-30T22:40:00.713927+00:00",
     "updatedAt": "2026-08-30T22:40:00.713927+00:00"},
    {"id": "106", "projectId": "101", "name": "Scan", "sequence": 2,
     "status": "Complete", "targetDate": "2026-08-19",
     "createdAt": "2026-08-30T22:40:00.754074+00:00",
     "updatedAt": "2026-08-30T22:40:00.754074+00:00"},
    {"id": "107", "projectId": "101", "name": "QC", "sequence": 3,
     "status": "In Progress", "targetDate": "2026-08-31",
     "createdAt": "2026-08-30T22:40:00.796881+00:00",
     "updatedAt": "2026-08-30T22:40:00.796881+00:00"},
    {"id": "108", "projectId": "101", "name": "Install", "sequence": 4,
     "status": "Not Started", "targetDate": "2026-09-12",
     "createdAt": "2026-08-30T22:40:00.850057+00:00",
     "updatedAt": "2026-08-30T22:40:00.850057+00:00"},
    {"id": "109", "projectId": "101", "name": "Training", "sequence": 5,
     "status": "Not Started", "targetDate": "2026-09-24",
     "createdAt": "2026-08-30T22:40:00.920107+00:00",
     "updatedAt": "2026-08-30T22:40:00.920107+00:00"},
    {"id": "110", "projectId": "102", "name": "Prep", "sequence": 1,
     "status": "Complete", "targetDate": "2026-07-21",
     "createdAt": "2026-08-30T22:40:05.014529+00:00",
     "updatedAt": "2026-08-30T22:40:05.014529+00:00"},
    {"id": "111", "projectId": "102", "name": "Scan", "sequence": 2,
     "status": "In Progress", "targetDate": "2026-08-20",
     "createdAt": "2026-08-30T22:40:05.059838+00:00",
     "updatedAt": "2026-08-30T22:40:05.059838+00:00"},
    {"id": "112", "projectId": "102", "name": "QC", "sequence": 3,
     "status": "Not Started", "targetDate": "2026-09-19",
     "createdAt": "2026-08-30T22:40:05.110657+00:00",
     "updatedAt": "2026-08-30T22:40:05.110657+00:00"},
    {"id": "113", "projectId": "102", "name": "Delivery", "sequence": 4,
     "status": "Not Started", "targetDate": "2026-10-19",
     "createdAt": "2026-08-30T22:40:05.164311+00:00",
     "updatedAt": "2026-08-30T22:40:05.164311+00:00"},
    {"id": "114", "projectId": "103", "name": "Discovery", "sequence": 1,
     "status": "Complete", "targetDate": "2026-07-23",
     "createdAt": "2026-08-30T22:40:12.319652+00:00",
     "updatedAt": "2026-08-30T22:40:12.319652+00:00"},
    {"id": "115", "projectId": "103", "name": "Configuration", "sequence": 2,
     "status": "Complete", "targetDate": "2026-08-09",
     "createdAt": "2026-08-30T22:40:12.382502+00:00",
     "updatedAt": "2026-08-30T22:40:12.382502+00:00"},
    {"id": "116", "projectId": "103", "name": "Install", "sequence": 3,
     "status": "In Progress", "targetDate": "2026-08-26",
     "createdAt": "2026-08-30T22:40:12.426221+00:00",
     "updatedAt": "2026-08-30T22:40:12.426221+00:00"},
    {"id": "117", "projectId": "103", "name": "Training", "sequence": 4,
     "status": "Not Started", "targetDate": "2026-09-12",
     "createdAt": "2026-08-30T22:40:12.473093+00:00",
     "updatedAt": "2026-08-30T22:40:12.473093+00:00"},
    {"id": "118", "projectId": "103", "name": "Go-live", "sequence": 5,
     "status": "Not Started", "targetDate": "2026-09-29",
     "createdAt": "2026-08-30T22:40:12.524977+00:00",
     "updatedAt": "2026-08-30T22:40:12.524977+00:00"},
    {"id": "119", "projectId": "104", "name": "Discovery", "sequence": 1,
     "status": "Complete", "targetDate": "2026-08-07",
     "createdAt": "2026-08-30T22:40:17.188280+00:00",
     "updatedAt": "2026-08-30T22:40:17.188280+00:00"},
    {"id": "120", "projectId": "104", "name": "Configuration", "sequence": 2,
     "status": "Complete", "targetDate": "2026-08-24",
     "createdAt": "2026-08-30T22:40:17.233946+00:00",
     "updatedAt": "2026-08-30T22:40:17.233946+00:00"},
    {"id": "121", "projectId": "104", "name": "Install", "sequence": 3,
     "status": "In Progress", "targetDate": "2026-09-10",
     "createdAt": "2026-08-30T22:40:17.280195+00:00",
     "updatedAt": "2026-08-30T22:40:17.280195+00:00"},
    {"id": "122", "projectId": "104", "name": "Training", "sequence": 4,
     "status": "Not Started", "targetDate": "2026-09-27",
     "createdAt": "2026-08-30T22:40:17.332727+00:00",
     "updatedAt": "2026-08-30T22:40:17.332727+00:00"},
    {"id": "123", "projectId": "104", "name": "Go-live", "sequence": 5,
     "status": "Not Started", "targetDate": "2026-10-14",
     "createdAt": "2026-08-30T22:40:17.386609+00:00",
     "updatedAt": "2026-08-30T22:40:17.386609+00:00"},
    {"id": "124", "projectId": "105", "name": "Discovery", "sequence": 1,
     "status": "Not Started", "targetDate": "2026-09-09",
     "createdAt": "2026-08-30T22:40:20.478822+00:00",
     "updatedAt": "2026-08-30T22:40:20.478822+00:00"},
    {"id": "125", "projectId": "105", "name": "Configuration", "sequence": 2,
     "status": "Not Started", "targetDate": "2026-09-26",
     "createdAt": "2026-08-30T22:40:20.524188+00:00",
     "updatedAt": "2026-08-30T22:40:20.524188+00:00"},
    {"id": "126", "projectId": "105", "name": "Install", "sequence": 3,
     "status": "Not Started", "targetDate": "2026-10-14",
     "createdAt": "2026-08-30T22:40:20.569220+00:00",
     "updatedAt": "2026-08-30T22:40:20.569220+00:00"},
    {"id": "127", "projectId": "105", "name": "Training", "sequence": 4,
     "status": "Not Started", "targetDate": "2026-10-31",
     "createdAt": "2026-08-30T22:40:20.614757+00:00",
     "updatedAt": "2026-08-30T22:40:20.614757+00:00"},
    {"id": "128", "projectId": "105", "name": "Go-live", "sequence": 5,
     "status": "Not Started", "targetDate": "2026-11-18",
     "createdAt": "2026-08-30T22:40:20.662506+00:00",
     "updatedAt": "2026-08-30T22:40:20.662506+00:00"},
    {"id": "129", "projectId": "106", "name": "Discovery", "sequence": 1,
     "status": "Complete", "targetDate": "2025-08-05",
     "createdAt": "2026-08-30T22:40:21.334431+00:00",
     "updatedAt": "2026-08-30T22:40:21.334431+00:00"},
    {"id": "130", "projectId": "106", "name": "Configuration", "sequence": 2,
     "status": "Complete", "targetDate": "2025-08-15",
     "createdAt": "2026-08-30T22:40:21.401897+00:00",
     "updatedAt": "2026-08-30T22:40:21.401897+00:00"},
    {"id": "131", "projectId": "106", "name": "Install", "sequence": 3,
     "status": "Complete", "targetDate": "2025-08-25",
     "createdAt": "2026-08-30T22:40:21.453215+00:00",
     "updatedAt": "2026-08-30T22:40:21.453215+00:00"},
    {"id": "132", "projectId": "106", "name": "Training", "sequence": 4,
     "status": "Complete", "targetDate": "2025-09-04",
     "createdAt": "2026-08-30T22:40:21.515220+00:00",
     "updatedAt": "2026-08-30T22:40:21.515220+00:00"},
    {"id": "133", "projectId": "106", "name": "Go-live", "sequence": 5,
     "status": "Complete", "targetDate": "2025-09-14",
     "createdAt": "2026-08-30T22:40:21.565087+00:00",
     "updatedAt": "2026-08-30T22:40:21.565087+00:00"},
    {"id": "134", "projectId": "107", "name": "Discovery", "sequence": 1,
     "status": "Complete", "targetDate": "2025-11-15",
     "createdAt": "2026-08-30T22:40:21.658067+00:00",
     "updatedAt": "2026-08-30T22:40:21.658067+00:00"},
    {"id": "135", "projectId": "107", "name": "Configuration", "sequence": 2,
     "status": "Complete", "targetDate": "2025-11-28",
     "createdAt": "2026-08-30T22:40:21.721309+00:00",
     "updatedAt": "2026-08-30T22:40:21.721309+00:00"},
    {"id": "136", "projectId": "107", "name": "Install", "sequence": 3,
     "status": "Complete", "targetDate": "2025-12-11",
     "createdAt": "2026-08-30T22:40:21.773316+00:00",
     "updatedAt": "2026-08-30T22:40:21.773316+00:00"},
    {"id": "137", "projectId": "107", "name": "Training", "sequence": 4,
     "status": "Complete", "targetDate": "2025-12-23",
     "createdAt": "2026-08-30T22:40:21.823309+00:00",
     "updatedAt": "2026-08-30T22:40:21.823309+00:00"},
    {"id": "138", "projectId": "108", "name": "Prep", "sequence": 1,
     "status": "Complete", "targetDate": "2025-10-26",
     "createdAt": "2026-08-30T22:40:21.924886+00:00",
     "updatedAt": "2026-08-30T22:40:21.924886+00:00"},
    {"id": "139", "projectId": "108", "name": "Scan", "sequence": 2,
     "status": "Complete", "targetDate": "2025-11-18",
     "createdAt": "2026-08-30T22:40:21.977047+00:00",
     "updatedAt": "2026-08-30T22:40:21.977047+00:00"},
    {"id": "140", "projectId": "108", "name": "QC", "sequence": 3,
     "status": "Complete", "targetDate": "2025-12-11",
     "createdAt": "2026-08-30T22:40:22.040736+00:00",
     "updatedAt": "2026-08-30T22:40:22.040736+00:00"},
    {"id": "141", "projectId": "108", "name": "Delivery", "sequence": 4,
     "status": "Complete", "targetDate": "2026-01-02",
     "createdAt": "2026-08-30T22:40:22.092431+00:00",
     "updatedAt": "2026-08-30T22:40:22.092431+00:00"},
    {"id": "142", "projectId": "109", "name": "Prep", "sequence": 1,
     "status": "Complete", "targetDate": "2025-10-09",
     "createdAt": "2026-08-30T22:40:22.198847+00:00",
     "updatedAt": "2026-08-30T22:40:22.198847+00:00"},
    {"id": "143", "projectId": "109", "name": "Scan", "sequence": 2,
     "status": "Complete", "targetDate": "2025-10-24",
     "createdAt": "2026-08-30T22:40:22.247032+00:00",
     "updatedAt": "2026-08-30T22:40:22.247032+00:00"},
    {"id": "144", "projectId": "109", "name": "QC", "sequence": 3,
     "status": "Complete", "targetDate": "2025-11-08",
     "createdAt": "2026-08-30T22:40:22.296658+00:00",
     "updatedAt": "2026-08-30T22:40:22.296658+00:00"},
    {"id": "145", "projectId": "109", "name": "Delivery", "sequence": 4,
     "status": "Complete", "targetDate": "2025-11-23",
     "createdAt": "2026-08-30T22:40:22.353196+00:00",
     "updatedAt": "2026-08-30T22:40:22.353196+00:00"},
    {"id": "146", "projectId": "110", "name": "Prep", "sequence": 1,
     "status": "Complete", "targetDate": "2025-11-11",
     "createdAt": "2026-08-30T22:40:22.454512+00:00",
     "updatedAt": "2026-08-30T22:40:22.454512+00:00"},
    {"id": "147", "projectId": "110", "name": "Scan", "sequence": 2,
     "status": "Complete", "targetDate": "2025-11-18",
     "createdAt": "2026-08-30T22:40:22.504778+00:00",
     "updatedAt": "2026-08-30T22:40:22.504778+00:00"},
    {"id": "148", "projectId": "110", "name": "QC", "sequence": 3,
     "status": "Complete", "targetDate": "2025-11-25",
     "createdAt": "2026-08-30T22:40:22.556573+00:00",
     "updatedAt": "2026-08-30T22:40:22.556573+00:00"},
    {"id": "149", "projectId": "110", "name": "Delivery", "sequence": 4,
     "status": "Complete", "targetDate": "2025-12-03",
     "createdAt": "2026-08-30T22:40:22.610727+00:00",
     "updatedAt": "2026-08-30T22:40:22.610727+00:00"},
    {"id": "150", "projectId": "111", "name": "Discovery", "sequence": 1,
     "status": "Complete", "targetDate": "2025-09-24",
     "createdAt": "2026-08-30T22:40:22.700361+00:00",
     "updatedAt": "2026-08-30T22:40:22.700361+00:00"},
    {"id": "151", "projectId": "111", "name": "Configuration", "sequence": 2,
     "status": "Complete", "targetDate": "2025-10-04",
     "createdAt": "2026-08-30T22:40:22.752892+00:00",
     "updatedAt": "2026-08-30T22:40:22.752892+00:00"},
    {"id": "152", "projectId": "111", "name": "Install", "sequence": 3,
     "status": "Complete", "targetDate": "2025-10-14",
     "createdAt": "2026-08-30T22:40:22.806979+00:00",
     "updatedAt": "2026-08-30T22:40:22.806979+00:00"},
    {"id": "153", "projectId": "111", "name": "Training", "sequence": 4,
     "status": "Complete", "targetDate": "2025-10-24",
     "createdAt": "2026-08-30T22:40:22.859895+00:00",
     "updatedAt": "2026-08-30T22:40:22.859895+00:00"},
    {"id": "154", "projectId": "112", "name": "Discovery", "sequence": 1,
     "status": "Complete", "targetDate": "2026-07-01",
     "createdAt": "2026-08-30T22:40:22.958704+00:00",
     "updatedAt": "2026-08-30T22:40:22.958704+00:00"},
    {"id": "155", "projectId": "112", "name": "Install", "sequence": 2,
     "status": "Complete", "targetDate": "2026-07-11",
     "createdAt": "2026-08-30T22:40:23.011194+00:00",
     "updatedAt": "2026-08-30T22:40:23.011194+00:00"},
    {"id": "156", "projectId": "112", "name": "Go-live", "sequence": 3,
     "status": "Complete", "targetDate": "2026-07-21",
     "createdAt": "2026-08-30T22:40:23.068042+00:00",
     "updatedAt": "2026-08-30T22:40:23.068042+00:00"},
    {"id": "157", "projectId": "113", "name": "Discovery", "sequence": 1,
     "status": "Complete", "targetDate": "2025-08-15",
     "createdAt": "2026-08-30T22:40:25.237461+00:00",
     "updatedAt": "2026-08-30T22:40:25.237461+00:00"},
    {"id": "158", "projectId": "113", "name": "Configuration", "sequence": 2,
     "status": "Complete", "targetDate": "2025-08-25",
     "createdAt": "2026-08-30T22:40:25.290167+00:00",
     "updatedAt": "2026-08-30T22:40:25.290167+00:00"},
    {"id": "159", "projectId": "113", "name": "Install", "sequence": 3,
     "status": "Complete", "targetDate": "2025-09-04",
     "createdAt": "2026-08-30T22:40:25.346068+00:00",
     "updatedAt": "2026-08-30T22:40:25.346068+00:00"},
    {"id": "160", "projectId": "113", "name": "Training", "sequence": 4,
     "status": "Complete", "targetDate": "2025-09-14",
     "createdAt": "2026-08-30T22:40:25.401126+00:00",
     "updatedAt": "2026-08-30T22:40:25.401126+00:00"},
    {"id": "161", "projectId": "114", "name": "Discovery", "sequence": 1,
     "status": "Complete", "targetDate": "2025-10-29",
     "createdAt": "2026-08-30T22:40:25.500101+00:00",
     "updatedAt": "2026-08-30T22:40:25.500101+00:00"},
    {"id": "162", "projectId": "114", "name": "Configuration", "sequence": 2,
     "status": "Complete", "targetDate": "2025-11-13",
     "createdAt": "2026-08-30T22:40:25.554636+00:00",
     "updatedAt": "2026-08-30T22:40:25.554636+00:00"},
    {"id": "163", "projectId": "114", "name": "Install", "sequence": 3,
     "status": "Complete", "targetDate": "2025-11-28",
     "createdAt": "2026-08-30T22:40:25.607092+00:00",
     "updatedAt": "2026-08-30T22:40:25.607092+00:00"},
    {"id": "164", "projectId": "114", "name": "Training", "sequence": 4,
     "status": "Complete", "targetDate": "2025-12-13",
     "createdAt": "2026-08-30T22:40:25.663213+00:00",
     "updatedAt": "2026-08-30T22:40:25.663213+00:00"},
    {"id": "165", "projectId": "115", "name": "Discovery", "sequence": 1,
     "status": "Complete", "targetDate": "2025-12-01",
     "createdAt": "2026-08-30T22:40:25.770132+00:00",
     "updatedAt": "2026-08-30T22:40:25.770132+00:00"},
    {"id": "166", "projectId": "115", "name": "Configuration", "sequence": 2,
     "status": "Complete", "targetDate": "2025-12-08",
     "createdAt": "2026-08-30T22:40:25.820404+00:00",
     "updatedAt": "2026-08-30T22:40:25.820404+00:00"},
    {"id": "167", "projectId": "115", "name": "Install", "sequence": 3,
     "status": "Complete", "targetDate": "2025-12-15",
     "createdAt": "2026-08-30T22:40:25.872528+00:00",
     "updatedAt": "2026-08-30T22:40:25.872528+00:00"},
    {"id": "168", "projectId": "115", "name": "Training", "sequence": 4,
     "status": "Complete", "targetDate": "2025-12-23",
     "createdAt": "2026-08-30T22:40:25.930613+00:00",
     "updatedAt": "2026-08-30T22:40:25.930613+00:00"},
    {"id": "169", "projectId": "116", "name": "Discovery", "sequence": 1,
     "status": "Complete", "targetDate": "2025-09-12",
     "createdAt": "2026-08-30T22:40:26.030858+00:00",
     "updatedAt": "2026-08-30T22:40:26.030858+00:00"},
    {"id": "170", "projectId": "116", "name": "Configuration", "sequence": 2,
     "status": "Complete", "targetDate": "2025-09-19",
     "createdAt": "2026-08-30T22:40:26.090038+00:00",
     "updatedAt": "2026-08-30T22:40:26.090038+00:00"},
    {"id": "171", "projectId": "116", "name": "Install", "sequence": 3,
     "status": "Complete", "targetDate": "2025-09-26",
     "createdAt": "2026-08-30T22:40:26.153660+00:00",
     "updatedAt": "2026-08-30T22:40:26.153660+00:00"},
    {"id": "172", "projectId": "116", "name": "Training", "sequence": 4,
     "status": "Complete", "targetDate": "2025-10-04",
     "createdAt": "2026-08-30T22:40:26.209856+00:00",
     "updatedAt": "2026-08-30T22:40:26.209856+00:00"},
    {"id": "173", "projectId": "117", "name": "Discovery", "sequence": 1,
     "status": "Complete", "targetDate": "2025-09-06",
     "createdAt": "2026-08-30T22:40:26.316559+00:00",
     "updatedAt": "2026-08-30T22:40:26.316559+00:00"},
    {"id": "174", "projectId": "117", "name": "Configuration", "sequence": 2,
     "status": "Complete", "targetDate": "2025-09-19",
     "createdAt": "2026-08-30T22:40:26.369520+00:00",
     "updatedAt": "2026-08-30T22:40:26.369520+00:00"},
    {"id": "175", "projectId": "117", "name": "Install", "sequence": 3,
     "status": "Complete", "targetDate": "2025-10-02",
     "createdAt": "2026-08-30T22:40:26.426178+00:00",
     "updatedAt": "2026-08-30T22:40:26.426178+00:00"},
    {"id": "176", "projectId": "117", "name": "Training", "sequence": 4,
     "status": "Complete", "targetDate": "2025-10-14",
     "createdAt": "2026-08-30T22:40:26.488566+00:00",
     "updatedAt": "2026-08-30T22:40:26.488566+00:00"},
]

PSA_TASKS: list[dict] = [
    {"id": "100", "projectId": "100",
     "name": "Site survey — plant floor and shipping", "phaseId": "100",
     "assignee": "M. Ortega", "status": "Done", "dueDate": "2026-07-23",
     "estimateHours": 8, "createdAt": "2026-08-30T22:39:57.508080+00:00",
     "updatedAt": "2026-08-30T22:39:57.508080+00:00"},
    {"id": "101", "projectId": "100",
     "name": "Capture licence keys from DocuFlow", "phaseId": "100",
     "assignee": "Caitlin F.", "status": "Done", "dueDate": "2026-07-31",
     "estimateHours": 2, "createdAt": "2026-08-30T22:39:57.616376+00:00",
     "updatedAt": "2026-08-30T22:39:57.616376+00:00"},
    {"id": "102", "projectId": "100", "name": "Configure release scripts",
     "phaseId": "101", "assignee": "M. Ortega", "status": "In Progress",
     "dueDate": "2026-08-24", "estimateHours": 16,
     "createdAt": "2026-08-30T22:39:57.713869+00:00",
     "updatedAt": "2026-08-30T22:39:57.713869+00:00"},
    {"id": "103", "projectId": "100",
     "name": "Build the quality records batch class", "phaseId": "101",
     "assignee": "Donna H.", "status": "In Progress", "dueDate": "2026-08-28",
     "estimateHours": 12, "createdAt": "2026-08-30T22:39:57.771883+00:00",
     "updatedAt": "2026-08-30T22:39:57.771883+00:00"},
    {"id": "104", "projectId": "100", "name": "Rack and image the two 4200s",
     "phaseId": "102", "assignee": "Curt Boyd", "status": "Open",
     "dueDate": "2026-09-03", "estimateHours": 16,
     "createdAt": "2026-08-30T22:39:57.828453+00:00",
     "updatedAt": "2026-08-30T22:39:57.828453+00:00"},
    {"id": "105", "projectId": "100",
     "name": "Feeder calibration on the second unit", "phaseId": "102",
     "assignee": "Curt Boyd", "status": "Open", "dueDate": "2026-09-05",
     "estimateHours": 4, "createdAt": "2026-08-30T22:39:57.922015+00:00",
     "updatedAt": "2026-08-30T22:39:57.922015+00:00"},
    {"id": "106", "projectId": "100",
     "name": "Operator training — first shift", "phaseId": "103",
     "assignee": "Jerome K.", "status": "Open", "dueDate": "2026-09-11",
     "estimateHours": 8, "createdAt": "2026-08-30T22:39:57.987322+00:00",
     "updatedAt": "2026-08-30T22:39:57.987322+00:00"},
    {"id": "107", "projectId": "100",
     "name": "Operator training — second shift", "phaseId": "103",
     "assignee": "Jerome K.", "status": "Open", "dueDate": "2026-09-12",
     "estimateHours": 8, "createdAt": "2026-08-30T22:39:58.060940+00:00",
     "updatedAt": "2026-08-30T22:39:58.060940+00:00"},
    {"id": "108", "projectId": "100", "name": "Go-live checklist with Susan",
     "phaseId": "104", "assignee": "S. Hoffmann", "status": "Open",
     "dueDate": "2026-09-19", "estimateHours": 4,
     "createdAt": "2026-08-30T22:39:58.125446+00:00",
     "updatedAt": "2026-08-30T22:39:58.125446+00:00"},
    {"id": "109", "projectId": "101",
     "name": "Prep the basement records — 3,200 boxes", "phaseId": "105",
     "assignee": "Rosa N.", "status": "Done", "dueDate": "2026-08-02",
     "estimateHours": 120, "createdAt": "2026-08-30T22:40:00.967405+00:00",
     "updatedAt": "2026-08-30T22:40:00.967405+00:00"},
    {"id": "110", "projectId": "101", "name": "Scan first shift",
     "phaseId": "105", "assignee": "Leon T.", "status": "In Progress",
     "dueDate": "2026-08-27", "estimateHours": 160,
     "createdAt": "2026-08-30T22:40:01.034034+00:00",
     "updatedAt": "2026-08-30T22:40:01.034034+00:00"},
    {"id": "111", "projectId": "101", "name": "Scan second shift",
     "phaseId": "106", "assignee": "Elaine M.", "status": "In Progress",
     "dueDate": "2026-08-27", "estimateHours": 160,
     "createdAt": "2026-08-30T22:40:01.084182+00:00",
     "updatedAt": "2026-08-30T22:40:01.084182+00:00"},
    {"id": "113", "projectId": "101",
     "name": "Feeder replacement — 6400 on back order from Talon",
     "phaseId": "107", "assignee": "Curt Boyd", "status": "Blocked",
     "dueDate": "2026-08-29", "estimateHours": 6,
     "createdAt": "2026-08-30T22:40:01.204834+00:00",
     "updatedAt": "2026-08-30T22:40:01.204834+00:00"},
    {"id": "112", "projectId": "101", "name": "QC sampling — exception trays",
     "phaseId": "106", "assignee": "Ivan M.", "status": "Open",
     "dueDate": "2026-09-04", "estimateHours": 24,
     "createdAt": "2026-08-30T22:40:01.155095+00:00",
     "updatedAt": "2026-08-30T22:40:01.155095+00:00"},
    {"id": "114", "projectId": "101",
     "name": "Install the second production unit", "phaseId": "107",
     "assignee": "Curt Boyd", "status": "Open", "dueDate": "2026-09-08",
     "estimateHours": 12, "createdAt": "2026-08-30T22:40:01.258106+00:00",
     "updatedAt": "2026-08-30T22:40:01.258106+00:00"},
    {"id": "115", "projectId": "101",
     "name": "Index rebuild for the 2019 boxes", "phaseId": "108",
     "assignee": "Donna H.", "status": "Open", "dueDate": "2026-09-10",
     "estimateHours": 16, "createdAt": "2026-08-30T22:40:01.325844+00:00",
     "updatedAt": "2026-08-30T22:40:01.325844+00:00"},
    {"id": "116", "projectId": "101", "name": "Training day — HIM staff",
     "phaseId": "108", "assignee": "Kayla J.", "status": "Open",
     "dueDate": "2026-09-13", "estimateHours": 8,
     "createdAt": "2026-08-30T22:40:01.393061+00:00",
     "updatedAt": "2026-08-30T22:40:01.393061+00:00"},
    {"id": "117", "projectId": "101",
     "name": "Delivery manifest for the returned boxes", "phaseId": "109",
     "assignee": "W. Fleck", "status": "Open", "dueDate": "2026-09-17",
     "estimateHours": 4, "createdAt": "2026-08-30T22:40:01.467686+00:00",
     "updatedAt": "2026-08-30T22:40:01.467686+00:00"},
    {"id": "118", "projectId": "101", "name": "Phase 2 closeout with Evan",
     "phaseId": "109", "assignee": "S. Hoffmann", "status": "Open",
     "dueDate": "2026-09-21", "estimateHours": 3,
     "createdAt": "2026-08-30T22:40:01.522440+00:00",
     "updatedAt": "2026-08-30T22:40:01.522440+00:00"},
    {"id": "119", "projectId": "102",
     "name": "Vault room setup — nothing leaves the building",
     "phaseId": "110", "assignee": "W. Fleck", "status": "Done",
     "dueDate": "2026-06-27", "estimateHours": 12,
     "createdAt": "2026-08-30T22:40:05.270036+00:00",
     "updatedAt": "2026-08-30T22:40:05.270036+00:00"},
    {"id": "120", "projectId": "102", "name": "Prep the 1978–1994 deed books",
     "phaseId": "110", "assignee": "Rosa N.", "status": "Done",
     "dueDate": "2026-07-21", "estimateHours": 180,
     "createdAt": "2026-08-30T22:40:05.323685+00:00",
     "updatedAt": "2026-08-30T22:40:05.323685+00:00"},
    {"id": "121", "projectId": "102", "name": "Scan the 1978–1994 books",
     "phaseId": "111", "assignee": "Leon T.", "status": "In Progress",
     "dueDate": "2026-08-25", "estimateHours": 220,
     "createdAt": "2026-08-30T22:40:05.387507+00:00",
     "updatedAt": "2026-08-30T22:40:05.387507+00:00"},
    {"id": "123", "projectId": "102",
     "name": "QC — page counts against the index cards", "phaseId": "112",
     "assignee": "Ivan M.", "status": "In Progress", "dueDate": "2026-08-28",
     "estimateHours": 60, "createdAt": "2026-08-30T22:40:05.499894+00:00",
     "updatedAt": "2026-08-30T22:40:05.499894+00:00"},
    {"id": "126", "projectId": "102", "name": "Weekly count for Lucy",
     "phaseId": "113", "assignee": "Yolanda J.", "status": "In Progress",
     "dueDate": "2026-09-01", "estimateHours": 2,
     "createdAt": "2026-08-30T22:40:05.671150+00:00",
     "updatedAt": "2026-08-30T22:40:05.671150+00:00"},
    {"id": "122", "projectId": "102", "name": "Scan the plat volumes",
     "phaseId": "111", "assignee": "Elaine M.", "status": "Open",
     "dueDate": "2026-09-11", "estimateHours": 140,
     "createdAt": "2026-08-30T22:40:05.445551+00:00",
     "updatedAt": "2026-08-30T22:40:05.445551+00:00"},
    {"id": "124", "projectId": "102", "name": "Re-scan flagged exceptions",
     "phaseId": "112", "assignee": "Rosa N.", "status": "Open",
     "dueDate": "2026-09-19", "estimateHours": 40,
     "createdAt": "2026-08-30T22:40:05.555238+00:00",
     "updatedAt": "2026-08-30T22:40:05.555238+00:00"},
    {"id": "125", "projectId": "102",
     "name": "Deliver the first tranche to the county's system",
     "phaseId": "113", "assignee": "Andrei N.", "status": "Open",
     "dueDate": "2026-10-03", "estimateHours": 24,
     "createdAt": "2026-08-30T22:40:05.606909+00:00",
     "updatedAt": "2026-08-30T22:40:05.606909+00:00"},
    {"id": "127", "projectId": "103", "name": "Branch survey — six locations",
     "phaseId": "114", "assignee": "Keith K.", "status": "Done",
     "dueDate": "2026-07-11", "estimateHours": 12,
     "createdAt": "2026-08-30T22:40:12.579057+00:00",
     "updatedAt": "2026-08-30T22:40:12.579057+00:00"},
    {"id": "128", "projectId": "103", "name": "Workflow module configuration",
     "phaseId": "114", "assignee": "Keith K.", "status": "In Progress",
     "dueDate": "2026-08-26", "estimateHours": 24,
     "createdAt": "2026-08-30T22:40:12.636175+00:00",
     "updatedAt": "2026-08-30T22:40:12.636175+00:00"},
    {"id": "129", "projectId": "103", "name": "Teller line capture profiles",
     "phaseId": "115", "assignee": "Bonnie M.", "status": "In Progress",
     "dueDate": "2026-08-29", "estimateHours": 16,
     "createdAt": "2026-08-30T22:40:12.690282+00:00",
     "updatedAt": "2026-08-30T22:40:12.690282+00:00"},
    {"id": "130", "projectId": "103",
     "name": "Install desktop units — Fitchburg and Verona", "phaseId": "116",
     "assignee": "Cameron G.", "status": "Open", "dueDate": "2026-09-04",
     "estimateHours": 10, "createdAt": "2026-08-30T22:40:12.746353+00:00",
     "updatedAt": "2026-08-30T22:40:12.746353+00:00"},
    {"id": "131", "projectId": "103",
     "name": "Install desktop units — east side branches", "phaseId": "116",
     "assignee": "Cameron G.", "status": "Open", "dueDate": "2026-09-07",
     "estimateHours": 10, "createdAt": "2026-08-30T22:40:12.800404+00:00",
     "updatedAt": "2026-08-30T22:40:12.800404+00:00"},
    {"id": "132", "projectId": "103", "name": "Records supervisor training",
     "phaseId": "117", "assignee": "Kayla J.", "status": "Open",
     "dueDate": "2026-09-14", "estimateHours": 8,
     "createdAt": "2026-08-30T22:40:12.850662+00:00",
     "updatedAt": "2026-08-30T22:40:12.850662+00:00"},
    {"id": "133", "projectId": "103", "name": "Go-live sign-off with Angela",
     "phaseId": "118", "assignee": "Caitlin F.", "status": "Open",
     "dueDate": "2026-09-23", "estimateHours": 3,
     "createdAt": "2026-08-30T22:40:12.909554+00:00",
     "updatedAt": "2026-08-30T22:40:12.909554+00:00"},
    {"id": "134", "projectId": "104",
     "name": "Requirements workshop — enrollment services", "phaseId": "119",
     "assignee": "Maya P.", "status": "Done", "dueDate": "2026-07-27",
     "estimateHours": 10, "createdAt": "2026-08-30T22:40:17.440902+00:00",
     "updatedAt": "2026-08-30T22:40:17.440902+00:00"},
    {"id": "135", "projectId": "104",
     "name": "Forms processing module configuration", "phaseId": "119",
     "assignee": "Maya P.", "status": "In Progress", "dueDate": "2026-08-27",
     "estimateHours": 30, "createdAt": "2026-08-30T22:40:17.498325+00:00",
     "updatedAt": "2026-08-30T22:40:17.498325+00:00"},
    {"id": "136", "projectId": "104",
     "name": "Financial aid document types and routing", "phaseId": "120",
     "assignee": "Frank G.", "status": "In Progress", "dueDate": "2026-08-29",
     "estimateHours": 20, "createdAt": "2026-08-30T22:40:17.551160+00:00",
     "updatedAt": "2026-08-30T22:40:17.551160+00:00"},
    {"id": "137", "projectId": "104",
     "name": "Test batch — 500 award letters", "phaseId": "120",
     "assignee": "Frank G.", "status": "Open", "dueDate": "2026-09-06",
     "estimateHours": 8, "createdAt": "2026-08-30T22:40:17.621433+00:00",
     "updatedAt": "2026-08-30T22:40:17.621433+00:00"},
    {"id": "138", "projectId": "104",
     "name": "Install the scanner in enrollment services", "phaseId": "121",
     "assignee": "Ivan H.", "status": "Open", "dueDate": "2026-09-10",
     "estimateHours": 6, "createdAt": "2026-08-30T22:40:17.684933+00:00",
     "updatedAt": "2026-08-30T22:40:17.684933+00:00"},
    {"id": "139", "projectId": "104", "name": "Train the aid office",
     "phaseId": "122", "assignee": "Jerome K.", "status": "Open",
     "dueDate": "2026-09-17", "estimateHours": 8,
     "createdAt": "2026-08-30T22:40:17.740191+00:00",
     "updatedAt": "2026-08-30T22:40:17.740191+00:00"},
    {"id": "140", "projectId": "104", "name": "Cutover plan with Stuart",
     "phaseId": "122", "assignee": "Yolanda J.", "status": "Open",
     "dueDate": "2026-09-25", "estimateHours": 4,
     "createdAt": "2026-08-30T22:40:17.822351+00:00",
     "updatedAt": "2026-08-30T22:40:17.822351+00:00"},
    {"id": "141", "projectId": "104", "name": "Post go-live support week",
     "phaseId": "123", "assignee": "Maya P.", "status": "Open",
     "dueDate": "2026-10-02", "estimateHours": 12,
     "createdAt": "2026-08-30T22:40:17.884580+00:00",
     "updatedAt": "2026-08-30T22:40:17.884580+00:00"},
    {"id": "145", "projectId": "105",
     "name": "Confirm the phase 1 order with Finance", "phaseId": "126",
     "assignee": "S. Hoffmann", "status": "Open", "dueDate": "2026-09-09",
     "estimateHours": 1, "createdAt": "2026-08-30T22:40:20.881650+00:00",
     "updatedAt": "2026-08-30T22:40:20.881650+00:00"},
    {"id": "142", "projectId": "105",
     "name": "Scope the title clerk workflow", "phaseId": "124",
     "assignee": "M. Ortega", "status": "Open", "dueDate": "2026-09-13",
     "estimateHours": 8, "createdAt": "2026-08-30T22:40:20.714816+00:00",
     "updatedAt": "2026-08-30T22:40:20.714816+00:00"},
    {"id": "143", "projectId": "105", "name": "Rooftop survey — four stores",
     "phaseId": "124", "assignee": "Cameron G.", "status": "Open",
     "dueDate": "2026-09-17", "estimateHours": 8,
     "createdAt": "2026-08-30T22:40:20.769731+00:00",
     "updatedAt": "2026-08-30T22:40:20.769731+00:00"},
    {"id": "144", "projectId": "105",
     "name": "Licence count for the service drive", "phaseId": "125",
     "assignee": "S. Hoffmann", "status": "Open", "dueDate": "2026-09-19",
     "estimateHours": 2, "createdAt": "2026-08-30T22:40:20.827708+00:00",
     "updatedAt": "2026-08-30T22:40:20.827708+00:00"},
    {"id": "146", "projectId": "105", "name": "Draft the install schedule",
     "phaseId": "127", "assignee": "M. Ortega", "status": "Open",
     "dueDate": "2026-09-25", "estimateHours": 4,
     "createdAt": "2026-08-30T22:40:20.948750+00:00",
     "updatedAt": "2026-08-30T22:40:20.948750+00:00"},
    {"id": "147", "projectId": "105", "name": "Kickoff agenda for Brad",
     "phaseId": "128", "assignee": "S. Hoffmann", "status": "Open",
     "dueDate": "2026-09-29", "estimateHours": 2,
     "createdAt": "2026-08-30T22:40:21.010335+00:00",
     "updatedAt": "2026-08-30T22:40:21.010335+00:00"},
]

#: Time entries for three of the eighteen projects rather than all 266 rows:
#: the Apex engagement (booked against tasks, so the hours reach a phase), the
#: Voss one that has barely started, and the finished Verona refresh — which
#: has no task list at all, so every one of its entries carries a null
#: `taskId` and lands as time booked past every phase. That third case is the
#: one a tidier fixture would have thrown away.
PSA_TIME_ENTRIES: list[dict] = [
    {"id": "355", "projectId": "112", "person": "Ivan H.",
     "workDate": "2026-06-23", "hours": 7.5, "taskId": None, "billable": False,
     "notes": "On site — Verona plant",
     "createdAt": "2026-08-30T22:40:24.164311+00:00",
     "updatedAt": "2026-08-30T22:40:24.164311+00:00"},
    {"id": "345", "projectId": "112", "person": "Caitlin F.",
     "workDate": "2026-06-25", "hours": 7.5, "taskId": None, "billable": True,
     "notes": "On site — Verona plant",
     "createdAt": "2026-08-30T22:40:23.125898+00:00",
     "updatedAt": "2026-08-30T22:40:23.125898+00:00"},
    {"id": "356", "projectId": "112", "person": "Ivan H.",
     "workDate": "2026-06-25", "hours": 6.0, "taskId": None, "billable": True,
     "notes": "On site — Verona plant",
     "createdAt": "2026-08-30T22:40:24.263069+00:00",
     "updatedAt": "2026-08-30T22:40:24.263069+00:00"},
    {"id": "357", "projectId": "112", "person": "Ivan H.",
     "workDate": "2026-06-26", "hours": 4.0, "taskId": None, "billable": True,
     "notes": "Operator refresher",
     "createdAt": "2026-08-30T22:40:24.351525+00:00",
     "updatedAt": "2026-08-30T22:40:24.351525+00:00"},
    {"id": "346", "projectId": "112", "person": "Caitlin F.",
     "workDate": "2026-06-29", "hours": 8.0, "taskId": None, "billable": True,
     "notes": "Old units decommissioned",
     "createdAt": "2026-08-30T22:40:23.232135+00:00",
     "updatedAt": "2026-08-30T22:40:23.232135+00:00"},
    {"id": "358", "projectId": "112", "person": "Ivan H.",
     "workDate": "2026-06-29", "hours": 6.0, "taskId": None, "billable": True,
     "notes": "Old units decommissioned",
     "createdAt": "2026-08-30T22:40:24.449060+00:00",
     "updatedAt": "2026-08-30T22:40:24.449060+00:00"},
    {"id": "359", "projectId": "112", "person": "Ivan H.",
     "workDate": "2026-06-30", "hours": 4.0, "taskId": None, "billable": False,
     "notes": "Old units decommissioned",
     "createdAt": "2026-08-30T22:40:24.542233+00:00",
     "updatedAt": "2026-08-30T22:40:24.542233+00:00"},
    {"id": "347", "projectId": "112", "person": "Caitlin F.",
     "workDate": "2026-07-01", "hours": 7.5, "taskId": None, "billable": True,
     "notes": "On site — Verona plant",
     "createdAt": "2026-08-30T22:40:23.336365+00:00",
     "updatedAt": "2026-08-30T22:40:23.336365+00:00"},
    {"id": "348", "projectId": "112", "person": "Caitlin F.",
     "workDate": "2026-07-02", "hours": 7.5, "taskId": None, "billable": True,
     "notes": "On site — Verona plant",
     "createdAt": "2026-08-30T22:40:23.431916+00:00",
     "updatedAt": "2026-08-30T22:40:23.431916+00:00"},
    {"id": "360", "projectId": "112", "person": "Ivan H.",
     "workDate": "2026-07-03", "hours": 4.0, "taskId": None, "billable": True,
     "notes": "Unit swap and imaging",
     "createdAt": "2026-08-30T22:40:24.631955+00:00",
     "updatedAt": "2026-08-30T22:40:24.631955+00:00"},
    {"id": "349", "projectId": "112", "person": "Caitlin F.",
     "workDate": "2026-07-06", "hours": 8.0, "taskId": None, "billable": True,
     "notes": "On site — Verona plant",
     "createdAt": "2026-08-30T22:40:23.542287+00:00",
     "updatedAt": "2026-08-30T22:40:23.542287+00:00"},
    {"id": "361", "projectId": "112", "person": "Ivan H.",
     "workDate": "2026-07-06", "hours": 8.0, "taskId": None, "billable": True,
     "notes": "Operator refresher",
     "createdAt": "2026-08-30T22:40:24.729088+00:00",
     "updatedAt": "2026-08-30T22:40:24.729088+00:00"},
    {"id": "350", "projectId": "112", "person": "Caitlin F.",
     "workDate": "2026-07-07", "hours": 8.0, "taskId": None, "billable": True,
     "notes": "Operator refresher",
     "createdAt": "2026-08-30T22:40:23.649619+00:00",
     "updatedAt": "2026-08-30T22:40:23.649619+00:00"},
    {"id": "362", "projectId": "112", "person": "Ivan H.",
     "workDate": "2026-07-07", "hours": 7.5, "taskId": None, "billable": True,
     "notes": "Old units decommissioned",
     "createdAt": "2026-08-30T22:40:24.833500+00:00",
     "updatedAt": "2026-08-30T22:40:24.833500+00:00"},
    {"id": "363", "projectId": "112", "person": "Ivan H.",
     "workDate": "2026-07-09", "hours": 4.0, "taskId": None, "billable": True,
     "notes": "Unit swap and imaging",
     "createdAt": "2026-08-30T22:40:24.911944+00:00",
     "updatedAt": "2026-08-30T22:40:24.911944+00:00"},
    {"id": "364", "projectId": "112", "person": "Ivan H.",
     "workDate": "2026-07-10", "hours": 6.0, "taskId": None, "billable": False,
     "notes": "Old units decommissioned",
     "createdAt": "2026-08-30T22:40:25.001330+00:00",
     "updatedAt": "2026-08-30T22:40:25.001330+00:00"},
    {"id": "351", "projectId": "112", "person": "Caitlin F.",
     "workDate": "2026-07-13", "hours": 8.0, "taskId": None, "billable": True,
     "notes": "Operator refresher",
     "createdAt": "2026-08-30T22:40:23.761101+00:00",
     "updatedAt": "2026-08-30T22:40:23.761101+00:00"},
    {"id": "352", "projectId": "112", "person": "Caitlin F.",
     "workDate": "2026-07-15", "hours": 7.5, "taskId": None, "billable": True,
     "notes": "On site — Verona plant",
     "createdAt": "2026-08-30T22:40:23.853426+00:00",
     "updatedAt": "2026-08-30T22:40:23.853426+00:00"},
    {"id": "365", "projectId": "112", "person": "Ivan H.",
     "workDate": "2026-07-15", "hours": 8.0, "taskId": None, "billable": True,
     "notes": "Old units decommissioned",
     "createdAt": "2026-08-30T22:40:25.099626+00:00",
     "updatedAt": "2026-08-30T22:40:25.099626+00:00"},
    {"id": "353", "projectId": "112", "person": "Caitlin F.",
     "workDate": "2026-07-17", "hours": 8.0, "taskId": None, "billable": True,
     "notes": "Old units decommissioned",
     "createdAt": "2026-08-30T22:40:23.961059+00:00",
     "updatedAt": "2026-08-30T22:40:23.961059+00:00"},
    {"id": "100", "projectId": "100", "person": "M. Ortega",
     "workDate": "2026-07-20", "hours": 4.0, "taskId": "107", "billable": True,
     "notes": "Feeder calibration",
     "createdAt": "2026-08-30T22:39:58.187468+00:00",
     "updatedAt": "2026-08-30T22:39:58.187468+00:00"},
    {"id": "112", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-07-20", "hours": 4.0, "taskId": "103",
     "billable": False, "notes": "Test batches with QA",
     "createdAt": "2026-08-30T22:39:58.966012+00:00",
     "updatedAt": "2026-08-30T22:39:58.966012+00:00"},
    {"id": "354", "projectId": "112", "person": "Caitlin F.",
     "workDate": "2026-07-20", "hours": 6.0, "taskId": None, "billable": True,
     "notes": "Old units decommissioned",
     "createdAt": "2026-08-30T22:40:24.061331+00:00",
     "updatedAt": "2026-08-30T22:40:24.061331+00:00"},
    {"id": "101", "projectId": "100", "person": "M. Ortega",
     "workDate": "2026-07-21", "hours": 6.0, "taskId": "102", "billable": True,
     "notes": "On site — plant floor",
     "createdAt": "2026-08-30T22:39:58.280747+00:00",
     "updatedAt": "2026-08-30T22:39:58.280747+00:00"},
    {"id": "126", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-07-21", "hours": 4.0, "taskId": "105", "billable": True,
     "notes": "Batch class build",
     "createdAt": "2026-08-30T22:39:59.776919+00:00",
     "updatedAt": "2026-08-30T22:39:59.776919+00:00"},
    {"id": "113", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-07-22", "hours": 6.0, "taskId": "105",
     "billable": False, "notes": "Release script configuration",
     "createdAt": "2026-08-30T22:39:59.026677+00:00",
     "updatedAt": "2026-08-30T22:39:59.026677+00:00"},
    {"id": "127", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-07-22", "hours": 4.0, "taskId": "104",
     "billable": False, "notes": "On site — plant floor",
     "createdAt": "2026-08-30T22:39:59.829000+00:00",
     "updatedAt": "2026-08-30T22:39:59.829000+00:00"},
    {"id": "114", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-07-23", "hours": 7.5, "taskId": "108", "billable": True,
     "notes": "Capture licence install",
     "createdAt": "2026-08-30T22:39:59.081510+00:00",
     "updatedAt": "2026-08-30T22:39:59.081510+00:00"},
    {"id": "128", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-07-24", "hours": 7.5, "taskId": "106", "billable": True,
     "notes": "Batch class build",
     "createdAt": "2026-08-30T22:39:59.897005+00:00",
     "updatedAt": "2026-08-30T22:39:59.897005+00:00"},
    {"id": "102", "projectId": "100", "person": "M. Ortega",
     "workDate": "2026-07-27", "hours": 8.0, "taskId": "103",
     "billable": False, "notes": "On site — plant floor",
     "createdAt": "2026-08-30T22:39:58.347115+00:00",
     "updatedAt": "2026-08-30T22:39:58.347115+00:00"},
    {"id": "103", "projectId": "100", "person": "M. Ortega",
     "workDate": "2026-07-28", "hours": 8.0, "taskId": "100", "billable": True,
     "notes": "Release script configuration",
     "createdAt": "2026-08-30T22:39:58.407731+00:00",
     "updatedAt": "2026-08-30T22:39:58.407731+00:00"},
    {"id": "104", "projectId": "100", "person": "M. Ortega",
     "workDate": "2026-07-29", "hours": 8.0, "taskId": "101", "billable": True,
     "notes": "Batch class build",
     "createdAt": "2026-08-30T22:39:58.473385+00:00",
     "updatedAt": "2026-08-30T22:39:58.473385+00:00"},
    {"id": "129", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-07-31", "hours": 6.0, "taskId": "101", "billable": True,
     "notes": "Feeder calibration",
     "createdAt": "2026-08-30T22:39:59.967334+00:00",
     "updatedAt": "2026-08-30T22:39:59.967334+00:00"},
    {"id": "130", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-08-03", "hours": 7.5, "taskId": "103", "billable": True,
     "notes": "Test batches with QA",
     "createdAt": "2026-08-30T22:40:00.021190+00:00",
     "updatedAt": "2026-08-30T22:40:00.021190+00:00"},
    {"id": "115", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-08-04", "hours": 7.5, "taskId": "108", "billable": True,
     "notes": "Capture licence install",
     "createdAt": "2026-08-30T22:39:59.148798+00:00",
     "updatedAt": "2026-08-30T22:39:59.148798+00:00"},
    {"id": "131", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-08-04", "hours": 4.0, "taskId": "106",
     "billable": False, "notes": "Feeder calibration",
     "createdAt": "2026-08-30T22:40:00.094460+00:00",
     "updatedAt": "2026-08-30T22:40:00.094460+00:00"},
    {"id": "105", "projectId": "100", "person": "M. Ortega",
     "workDate": "2026-08-05", "hours": 6.0, "taskId": "104", "billable": True,
     "notes": "On site — plant floor",
     "createdAt": "2026-08-30T22:39:58.539590+00:00",
     "updatedAt": "2026-08-30T22:39:58.539590+00:00"},
    {"id": "116", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-08-06", "hours": 6.0, "taskId": "104", "billable": True,
     "notes": "Batch class build",
     "createdAt": "2026-08-30T22:39:59.204769+00:00",
     "updatedAt": "2026-08-30T22:39:59.204769+00:00"},
    {"id": "106", "projectId": "100", "person": "M. Ortega",
     "workDate": "2026-08-07", "hours": 8.0, "taskId": "101", "billable": True,
     "notes": "Test batches with QA",
     "createdAt": "2026-08-30T22:39:58.604452+00:00",
     "updatedAt": "2026-08-30T22:39:58.604452+00:00"},
    {"id": "132", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-08-10", "hours": 8.0, "taskId": "103", "billable": True,
     "notes": "Test batches with QA",
     "createdAt": "2026-08-30T22:40:00.192859+00:00",
     "updatedAt": "2026-08-30T22:40:00.192859+00:00"},
    {"id": "107", "projectId": "100", "person": "M. Ortega",
     "workDate": "2026-08-11", "hours": 6.0, "taskId": "104",
     "billable": False, "notes": "Test batches with QA",
     "createdAt": "2026-08-30T22:39:58.651465+00:00",
     "updatedAt": "2026-08-30T22:39:58.651465+00:00"},
    {"id": "133", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-08-11", "hours": 8.0, "taskId": "105",
     "billable": False, "notes": "Batch class build",
     "createdAt": "2026-08-30T22:40:00.295630+00:00",
     "updatedAt": "2026-08-30T22:40:00.295630+00:00"},
    {"id": "117", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-08-12", "hours": 8.0, "taskId": "103",
     "billable": False, "notes": "Test batches with QA",
     "createdAt": "2026-08-30T22:39:59.258064+00:00",
     "updatedAt": "2026-08-30T22:39:59.258064+00:00"},
    {"id": "118", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-08-13", "hours": 7.5, "taskId": "106", "billable": True,
     "notes": "Feeder calibration",
     "createdAt": "2026-08-30T22:39:59.308773+00:00",
     "updatedAt": "2026-08-30T22:39:59.308773+00:00"},
    {"id": "119", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-08-14", "hours": 6.0, "taskId": "101",
     "billable": False, "notes": "Capture licence install",
     "createdAt": "2026-08-30T22:39:59.363516+00:00",
     "updatedAt": "2026-08-30T22:39:59.363516+00:00"},
    {"id": "134", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-08-14", "hours": 8.0, "taskId": "108", "billable": True,
     "notes": "Feeder calibration",
     "createdAt": "2026-08-30T22:40:00.347250+00:00",
     "updatedAt": "2026-08-30T22:40:00.347250+00:00"},
    {"id": "108", "projectId": "100", "person": "M. Ortega",
     "workDate": "2026-08-17", "hours": 7.5, "taskId": "105", "billable": True,
     "notes": "Feeder calibration",
     "createdAt": "2026-08-30T22:39:58.720029+00:00",
     "updatedAt": "2026-08-30T22:39:58.720029+00:00"},
    {"id": "120", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-08-17", "hours": 7.5, "taskId": "101", "billable": True,
     "notes": "Test batches with QA",
     "createdAt": "2026-08-30T22:39:59.421770+00:00",
     "updatedAt": "2026-08-30T22:39:59.421770+00:00"},
    {"id": "135", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-08-17", "hours": 7.5, "taskId": "102", "billable": True,
     "notes": "Capture licence install",
     "createdAt": "2026-08-30T22:40:00.422000+00:00",
     "updatedAt": "2026-08-30T22:40:00.422000+00:00"},
    {"id": "136", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-08-18", "hours": 8.0, "taskId": "103", "billable": True,
     "notes": "Batch class build",
     "createdAt": "2026-08-30T22:40:00.481747+00:00",
     "updatedAt": "2026-08-30T22:40:00.481747+00:00"},
    {"id": "137", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-08-19", "hours": 8.0, "taskId": "105", "billable": True,
     "notes": "Batch class build",
     "createdAt": "2026-08-30T22:40:00.537140+00:00",
     "updatedAt": "2026-08-30T22:40:00.537140+00:00"},
    {"id": "109", "projectId": "100", "person": "M. Ortega",
     "workDate": "2026-08-20", "hours": 8.0, "taskId": "100", "billable": True,
     "notes": "Release script configuration",
     "createdAt": "2026-08-30T22:39:58.775373+00:00",
     "updatedAt": "2026-08-30T22:39:58.775373+00:00"},
    {"id": "121", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-08-20", "hours": 4.0, "taskId": "106", "billable": True,
     "notes": "Feeder calibration",
     "createdAt": "2026-08-30T22:39:59.493740+00:00",
     "updatedAt": "2026-08-30T22:39:59.493740+00:00"},
    {"id": "122", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-08-21", "hours": 8.0, "taskId": "102", "billable": True,
     "notes": "Test batches with QA",
     "createdAt": "2026-08-30T22:39:59.557154+00:00",
     "updatedAt": "2026-08-30T22:39:59.557154+00:00"},
    {"id": "138", "projectId": "100", "person": "Curt Boyd",
     "workDate": "2026-08-21", "hours": 6.0, "taskId": "103", "billable": True,
     "notes": "Capture licence install",
     "createdAt": "2026-08-30T22:40:00.600163+00:00",
     "updatedAt": "2026-08-30T22:40:00.600163+00:00"},
    {"id": "343", "projectId": "105", "person": "S. Hoffmann",
     "workDate": "2026-08-25", "hours": 4.0, "taskId": "147",
     "billable": False, "notes": "Scoping call with Brad",
     "createdAt": "2026-08-30T22:40:21.080381+00:00",
     "updatedAt": "2026-08-30T22:40:21.080381+00:00"},
    {"id": "123", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-08-26", "hours": 8.0, "taskId": "105", "billable": True,
     "notes": "Capture licence install",
     "createdAt": "2026-08-30T22:39:59.608202+00:00",
     "updatedAt": "2026-08-30T22:39:59.608202+00:00"},
    {"id": "110", "projectId": "100", "person": "M. Ortega",
     "workDate": "2026-08-27", "hours": 7.5, "taskId": "105", "billable": True,
     "notes": "Test batches with QA",
     "createdAt": "2026-08-30T22:39:58.828792+00:00",
     "updatedAt": "2026-08-30T22:39:58.828792+00:00"},
    {"id": "124", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-08-27", "hours": 4.0, "taskId": "101", "billable": True,
     "notes": "Feeder calibration",
     "createdAt": "2026-08-30T22:39:59.656960+00:00",
     "updatedAt": "2026-08-30T22:39:59.656960+00:00"},
    {"id": "111", "projectId": "100", "person": "M. Ortega",
     "workDate": "2026-08-28", "hours": 6.0, "taskId": "108", "billable": True,
     "notes": "Batch class build",
     "createdAt": "2026-08-30T22:39:58.903170+00:00",
     "updatedAt": "2026-08-30T22:39:58.903170+00:00"},
    {"id": "125", "projectId": "100", "person": "Donna H.",
     "workDate": "2026-08-28", "hours": 7.5, "taskId": "106", "billable": True,
     "notes": "Test batches with QA",
     "createdAt": "2026-08-30T22:39:59.727534+00:00",
     "updatedAt": "2026-08-30T22:39:59.727534+00:00"},
    {"id": "344", "projectId": "105", "person": "S. Hoffmann",
     "workDate": "2026-08-28", "hours": 8.0, "taskId": "146", "billable": True,
     "notes": "Waiting on the order",
     "createdAt": "2026-08-30T22:40:21.194758+00:00",
     "updatedAt": "2026-08-30T22:40:21.194758+00:00"},
]

RESOURCES: dict[tuple[str, str], list[dict]] = {
    ("hris", "departments"): DEPARTMENTS,
    ("hris", "jobs"): JOBS,
    ("hris", "employees"): EMPLOYEES,
    ("crm", "accounts"): ACCOUNTS,
    ("crm", "contacts"): CONTACTS,
    ("crm", "opportunities"): OPPORTUNITIES,
    ("erp", "customers"): CUSTOMERS,
    ("erp", "items"): ITEMS,
    ("erp", "salesOrders"): SALES_ORDERS,
    ("erp", "vendors"): VENDORS,
    ("erp", "purchaseOrders"): PURCHASE_ORDERS,
    ("office", "mailboxes"): MAILBOXES,
    ("office", "messages"): MAIL_MESSAGES,
    ("office", "events"): OFFICE_EVENTS,
    ("hris", "employmentEvents"): EMPLOYMENT_EVENTS,
    ("dms", "folders"): FOLDERS,
    ("dms", "documents"): DMS_DOCUMENTS,
    ("psa", "clients"): PSA_CLIENTS,
    ("psa", "projects"): PSA_PROJECTS,
    ("psa", "phases"): PSA_PHASES,
    ("psa", "tasks"): PSA_TASKS,
    ("psa", "timeEntries"): PSA_TIME_ENTRIES,
}

#: Records reachable by id but absent from every list — the dereference cases.
OFF_PAGE: dict[tuple[str, str], dict] = {
    ("hris", "jobs"): JOB_OFF_PAGE,
    ("hris", "employees"): WORKER_OFF_PAGE,
    ("office", "mailboxes"): MAILBOX_OFF_PAGE,
}


class EstateDown(Exception):
    """What the fake raises where the network would fail."""


class EstateRefused(Exception):
    """What the fake raises where the estate's API answers with a status.

    `status` and `message` are `namespaces.ApiError`'s pair, verbatim: the
    estate's own sentence is what an operator ends up reading on the Task a
    refused write raises, so a paraphrase here would be a paraphrase there.
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


#: `erp.py`'s picklists, verbatim (functions/erp.py:49-69) — the only
#: validation standing between a caller and a stored value.
TERMS = ["Due on receipt", "Net 15", "Net 30", "Net 45", "Net 60"]
SALES_ORDER_STATUSES = [
    "Pending Approval", "Pending Fulfillment", "Partially Fulfilled",
    "Pending Billing", "Billed", "Closed",
]
PURCHASE_ORDER_STATUSES = [
    "Pending Approval", "Pending Receipt", "Partially Received",
    "Pending Billing", "Fully Billed", "Closed",
]

#: Server-owned fields. Sent in a body they are **ignored rather than
#: rejected**, so a caller can round-trip a record it just read.
PATCH_READ_ONLY = ["id", "tranId", "total", "createdAt", "updatedAt"]

#: `erp.py`'s ENTITIES (functions/erp.py:88-142) narrowed to what a PATCH can
#: reach: the settable field names, and the picklists that gate them. Three
#: entities rather than all five — the two transactions, so a status change
#: the write table refuses can be shown landing here anyway, and one master
#: record, so these rules are visibly per-entity and not a hardcoded `status`.
PATCH_SPECS: dict[tuple[str, str], dict] = {
    ("erp", "salesOrders"): {
        "fields": ["customerId", "lines", "tranDate", "status", "memo"],
        "picklists": {"status": SALES_ORDER_STATUSES},
    },
    ("erp", "purchaseOrders"): {
        "fields": ["vendorId", "lines", "tranDate", "status", "memo"],
        "picklists": {"status": PURCHASE_ORDER_STATUSES},
    },
    ("erp", "customers"): {
        "fields": ["companyName", "email", "phone", "terms"],
        "picklists": {"terms": TERMS},
    },
}


def _singular(entity: str) -> str:
    """'salesOrders' -> 'sales order', the way erp.py spells it in a 404."""
    words: list[str] = []
    for char in entity[:-1]:
        if char.isupper():
            words.append(" ")
            char = char.lower()
        words.append(char)
    return "".join(words)


def _clean(spec: dict, body: dict) -> dict:
    """`erp.py`'s `_clean(..., partial=True)` over the scalar fields.

    Same order of business as the original: unknown fields first (a 400 naming
    them), then per field — strip, empty becomes null, non-strings refused,
    picklist last. The server-owned fields never appear because they are not
    in `fields`, which is precisely how the real one ignores them.
    """
    unknown = sorted(set(body) - set(spec["fields"]) - set(PATCH_READ_ONLY))
    if unknown:
        raise EstateRefused(400, f"unknown fields: {', '.join(unknown)}")
    fields: dict = {}
    for key in spec["fields"]:
        if key not in body:
            continue
        if key == "lines":
            # Out of scope on purpose: rates and the order total come from
            # `_clean_lines` and the item catalogue. Nothing in the write
            # plane sends lines, and a test that wants to should say so here
            # rather than get a quietly wrong total.
            raise NotImplementedError("the fake estate does not edit order lines")
        value = body[key]
        if isinstance(value, str):
            value = value.strip()
        if value in (None, ""):
            fields[key] = None
            continue
        if not isinstance(value, str):
            raise EstateRefused(400, f"'{key}' must be a string")
        choices = spec.get("picklists", {}).get(key)
        if choices and value not in choices:
            raise EstateRefused(
                400, f"'{key}' must be one of: {', '.join(choices)}"
            )
        fields[key] = value
    return fields


def _bumped(previous: str) -> str:
    """A fresh `updatedAt`, guaranteed later than the one it replaces.

    The estate stamps server time; these rows were recorded on a fixed date,
    and a suite that ever runs with a frozen clock would otherwise write a
    timestamp that does not move. The watermark logic is ordered strictly, so
    "later" is the property that matters, not "now".
    """
    now = datetime.now(timezone.utc)
    try:
        was = datetime.fromisoformat(previous)
    except (TypeError, ValueError):
        return now.isoformat()
    return max(now, was + timedelta(microseconds=1)).isoformat()


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

    # -- the write route ----------------------------------------------------

    def patch(self, url: str, body: dict) -> dict:
        """`erp.py`'s PATCH route: `_update` over `_clean(partial=True)`.

        Faithful where it matters and deliberately not tidy. It refuses an
        unknown field with a 400, checks the picklist and the string-ness of a
        value, *ignores* the server-owned fields rather than rejecting them,
        refuses an empty body, bumps `updatedAt`, and offers no `If-Match`
        because the estate offers none.

        What it does **not** do is validate the transition. `Pending Approval`
        straight to `Closed` is a legal PATCH here because it is a legal PATCH
        there (functions/erp.py:275-322 — a picklist check and nothing else),
        and that is the whole reason `estate_writes.WRITABLE` exists. A tidier
        fake would test a system nothing serves, and would have quietly hidden
        the only argument for the write table.
        """
        self.calls.append(f"PATCH {url}")
        parsed = urlparse(url)
        app = parsed.netloc.split(".")[0].rsplit("-", 1)[-1]
        parts = [p for p in parsed.path.split("/") if p]
        resource = parts[3] if len(parts) > 3 else ""
        record_id = parts[4] if len(parts) > 4 else ""
        if (app, resource) in self.fails:
            raise EstateDown(f"PATCH {url} → 503")
        spec = PATCH_SPECS.get((app, resource))
        if spec is None or not record_id:
            raise EstateRefused(404, f"no route for PATCH {parsed.path}")
        row = self._row(app, resource, record_id)
        if row is None:
            raise EstateRefused(
                404, f"{_singular(resource)} {record_id} not found"
            )
        updates = _clean(spec, body)
        if not updates:
            raise EstateRefused(400, "nothing to update — send at least one field")
        updates["updatedAt"] = _bumped(str(row.get("updatedAt", "")))
        return self._replace(app, resource, record_id, updates)

    def record(self, app: str, resource: str, record_id: str) -> dict:
        """One row as the fake currently holds it — a test's window on what a
        write actually did, as opposed to what the door said about it."""
        row = self._row(app, resource, record_id)
        if row is None:
            raise EstateRefused(
                404, f"{_singular(resource)} {record_id} not found"
            )
        return dict(row)

    def _row(self, app: str, resource: str, record_id: str) -> dict | None:
        for row in self.resources.get((app, resource), []):
            if row.get("id") == record_id:
                return row
        off = self.off_page.get((app, resource))
        return off if off is not None and off.get("id") == record_id else None

    def _replace(
        self, app: str, resource: str, record_id: str, updates: dict
    ) -> dict:
        """Copy-on-write, and not as a style preference.

        `RESOURCES` holds module-level lists that every other test in the
        suite reads. Mutating a row in place would make one test's write
        visible to the next test's pull, which is the exact bug an isolated
        fixture exists to make impossible.
        """
        rows = [dict(r) for r in self.resources.get((app, resource), [])]
        for index, row in enumerate(rows):
            if row.get("id") == record_id:
                rows[index] = {**row, **updates}
                self.resources[(app, resource)] = rows
                return dict(rows[index])
        off = dict(self.off_page.get((app, resource)) or {})
        off.update(updates)
        self.off_page[(app, resource)] = off
        return dict(off)
