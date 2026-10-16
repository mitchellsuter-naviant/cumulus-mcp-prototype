"""Department scheduling: the day a plant department actually works.

The last flow of the plant spine, and the one that makes Cumulus the **system
of record** for a schedule. Upstream of it the eligibility sweep says which
jobs are in play and the shipping-list extraction says what each department
owes in hours; here those hours become a day, that day is signed by a person,
and only then does anything leave the graph.

Three modules, and the two below the flow are read by more than one lane:

- `formulas.py` — the pure labor-hour arithmetic, which the shipping-list
  extraction runs to turn a grid into a department's hours, and which holds no
  number at all;
- `parameters.py` — the reader for `plant-scheduling-parameters`, the one page
  the jobs feed, the eligibility sweep, the extraction and this flow all read.
  One reader, so there is one answer to "is Saturday a working day";
- `domain.py` — this flow's own service and its pure rules: the dependency and
  lead-time check, the fill, and the write.

`v1/` holds the executable body and the declarative steps, which travel
together for the reason every other package here gives: the map the console
draws and the map the engine runs are one file, so they cannot drift apart
without a diff saying so.
"""

FLOW_ID = "department-scheduling"
DEFAULT_VERSION = 1
