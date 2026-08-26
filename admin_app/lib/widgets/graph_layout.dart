/// The shared spatial vocabulary for the ontology.
///
/// One layout table, used by both the TBox diagram and the ABox instance maps,
/// so a `Vendor` box always appears in the same place whether you are looking
/// at the model or at instances of it. That continuity is the point: the two
/// views are the same picture at different levels, and giving them different
/// geometries would hide it.
///
/// Hand-assigned rather than computed. Each row is a spine — the expense chain,
/// the vendor/AP chain, procurement, receiving — so reading left to right
/// follows the direction facts actually flow. At this size a layout that means
/// something beats any automatic one.
library;

const ontologyLayout = <String, (int column, int row)>{
  'Employee': (0, 0),
  'ExpenseRequest': (1, 0),
  'ExpenseLine': (2, 0),
  'Document': (3, 0),
  'SourceRecord': (0, 1),
  'Vendor': (1, 1),
  'Invoice': (2, 1),
  'InvoiceLine': (3, 1),
  'MatchSet': (4, 1),
  'PurchaseOrder': (1, 2),
  'PurchaseOrderLine': (2, 2),
  'ReceivingReport': (1, 3),
  'ReceivingReportLine': (2, 3),
};

const ontologyColumns = 5;
const ontologyRows = 4;

/// Where an unlisted type goes — the far column of the last row, so a type
/// added to the ontology without a layout entry still appears rather than
/// silently vanishing from every diagram.
const ontologyFallbackCell = (4, 3);

(int, int) cellFor(String type) => ontologyLayout[type] ?? ontologyFallbackCell;
