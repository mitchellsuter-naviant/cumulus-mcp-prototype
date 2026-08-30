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

#: What a list route answers under, by app and resource.
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
}

#: Records reachable by id but absent from every list — the dereference cases.
OFF_PAGE: dict[tuple[str, str], dict] = {
    ("hris", "jobs"): JOB_OFF_PAGE,
    ("hris", "employees"): WORKER_OFF_PAGE,
    ("office", "mailboxes"): MAILBOX_OFF_PAGE,
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
