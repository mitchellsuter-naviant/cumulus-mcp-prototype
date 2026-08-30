import 'package:cumulus_admin/models/ontology_screen.dart';
import 'package:cumulus_admin/widgets/ontology/ontology_layout.dart';
import 'package:flutter_test/flutter_test.dart';

/// The diagram's whole argument is in [layoutOntology], and it is pure, so it
/// is tested here rather than by squinting at a canvas. The fixture is a fixed
/// slice of the model in the shape `/api/ui/v1/ontology` serves — the
/// seventeen types the expense and payables flows write, seven Collections
/// that exactly partition them, twenty-four links of which two are self-loops.
///
/// Deliberately a snapshot and not a mirror of the live ontology, which has
/// since grown a counterparty book, a sell side and a mail mirror. What is
/// pinned below is layout *shape* — ranks, partitioning, fan-out, self-loops —
/// and a fixture that grew with every new type would need rewriting each time
/// the model did, which is how a test stops asserting anything.
void main() {
  group('layoutOntology', () {
    test('is deterministic across repeated runs', () {
      final a = layoutOntology(_seeded());
      final b = layoutOntology(_seeded());

      expect(
        a.nodes.map((n) => '${n.name}@${n.rect}').toList(),
        b.nodes.map((n) => '${n.name}@${n.rect}').toList(),
      );
      expect(
        a.edges.map((e) => '${e.link.name}:${e.start}->${e.end}').toList(),
        b.edges.map((e) => '${e.link.name}:${e.start}->${e.end}').toList(),
      );
      expect(a.size, b.size);
    });

    test('draws every type once, in the Collection that claims it', () {
      final layout = layoutOntology(_seeded());
      final screen = _seeded();

      expect(layout.nodes.length, screen.types.length);
      for (final node in layout.nodes) {
        expect(node.clusterId, node.type.collection);
      }
      // The partition holds, so the exception cluster must not appear.
      expect(
        layout.clusters.map((c) => c.cluster.id),
        isNot(contains(ungovernedCluster)),
      );
      expect(layout.clusters.length, screen.clusters.length);
    });

    test('ranks parts one column right of the owner that identifies them', () {
      final layout = layoutOntology(_seeded());
      final invoice = layout.nodeNamed('Invoice')!;
      final line = layout.nodeNamed('InvoiceLine')!;

      expect(invoice.rank, 0);
      expect(line.rank, 1);
      expect(line.rect.left, greaterThan(invoice.rect.left));
      // MatchSet is a part of Invoice by composition even though nothing in
      // the payload says "part" — the rank is derived, not declared.
      expect(layout.nodeNamed('MatchSet')!.rank, 1);
    });

    test('never routes a self-loop as an edge', () {
      final layout = layoutOntology(_seeded());

      for (final edge in layout.edges) {
        expect(edge.link.fromType, isNot(edge.link.toType));
      }
      expect(
        layout.selfLoops.map((l) => l.link.name).toList()..sort(),
        ['reports_to', 'subteam_of'],
      );
      expect(layout.nodeNamed('Employee'), isNotNull);
    });

    test('routes every other link, one edge per link', () {
      final layout = layoutOntology(_seeded());
      final routable = _seeded().links.where((l) => !l.isSelfLoop).length;

      expect(layout.edges.length, routable);
      // Two links between the same pair are two edges with two control
      // points — the bug the old graph had was one line carrying both.
      final pair = layout.edges
          .where((e) => {e.link.fromType, e.link.toType}
              .containsAll({'Employee', 'ExpenseRequest'}))
          .toList();
      expect(pair.map((e) => e.link.name).toList()..sort(), [
        'decided_by',
        'submitted',
      ]);
      expect(pair.first.control, isNot(pair.last.control));
    });

    test('marks back-edges instead of reversing them', () {
      final layout = layoutOntology(_seeded());
      final back = layout.edges.where((e) => e.backEdge).toList();

      expect(back, isNotEmpty);
      for (final edge in back) {
        // Marked, not flipped: the arrow still starts at the declared source.
        final from = layout.nodeNamed(edge.link.fromType)!;
        final to = layout.nodeNamed(edge.link.toType)!;
        expect(to.center.dx, lessThan(from.center.dx));
      }
    });

    test('gives every node its own cell', () {
      final layout = layoutOntology(_seeded());

      for (var i = 0; i < layout.nodes.length; i++) {
        for (var j = i + 1; j < layout.nodes.length; j++) {
          final a = layout.nodes[i].rect;
          final b = layout.nodes[j].rect;
          expect(
            a.overlaps(b),
            isFalse,
            reason: '${layout.nodes[i].name} overlaps ${layout.nodes[j].name}',
          );
        }
      }
    });

    test('keeps every node inside its own Collection frame', () {
      final layout = layoutOntology(_seeded());
      final frames = {for (final c in layout.clusters) c.cluster.id: c.rect};

      for (final node in layout.nodes) {
        final frame = frames[node.clusterId]!;
        expect(frame.contains(node.rect.topLeft), isTrue);
        expect(frame.contains(node.rect.bottomRight), isTrue);
      }
    });

    test('falls back to an ungoverned cluster for an unclaimed type', () {
      // Strip Capability out of the Workforce Collection without removing the
      // type: exactly the shape of the failure the server route warns about.
      final screen = _seeded();
      final clusters = [
        for (final c in screen.clusters)
          OntologyCluster(
            id: c.id,
            label: c.label,
            role: c.role,
            origin: c.origin,
            typeNames: c.typeNames.where((n) => n != 'Capability').toList(),
          ),
      ];
      final layout = layoutOntology(
        OntologyScreen(
          types: screen.types,
          links: screen.links,
          clusters: clusters,
          overlay: null,
          flows: const [],
          conformance: null,
        ),
      );

      final orphaned = layout.clusters
          .firstWhere((c) => c.cluster.id == ungovernedCluster);
      expect(orphaned.cluster.typeNames, ['Capability']);
      expect(layout.nodeNamed('Capability')!.clusterId, ungovernedCluster);
      // Still drawn, and still connected: an exception report, not a hole.
      expect(
        layout.edges.map((e) => e.link.name),
        contains('has_capability'),
      );
    });

    test('caps fan-out onto a stub, and the stub gives the edges back', () {
      // Eleven links touch Employee counting the self-loop twice, so a cap of
      // six has to fold something; the real model sits under the default.
      final capped = layoutOntology(_seeded(), maxFanOut: 6);
      final stub = capped.stubs.firstWhere((s) => s.node.name == 'Employee');

      expect(stub.hidden, isNotEmpty);
      final folded = stub.hidden.map((l) => l.name).toSet();
      expect(capped.edges.map((e) => e.link.name), isNot(anyOf(folded)));
      // The incidental many-to-manys fold first; nothing compositional does.
      expect(stub.hidden.every((l) => l.nature == 'association'), isTrue);

      final expanded = layoutOntology(
        _seeded(),
        maxFanOut: 6,
        expanded: const {'Employee'},
      );
      expect(expanded.stubs.where((s) => s.node.name == 'Employee'), isEmpty);
      expect(
        expanded.edges.map((e) => e.link.name).toSet(),
        containsAll(folded),
      );
    });

    test('finds the edge under a scene point and misses the empty canvas', () {
      final layout = layoutOntology(_seeded());
      final edge = layout.edges.first;

      expect(layout.edgeAt(edge.labelAt)?.link.name, edge.link.name);
      expect(layout.edgeAt(Offset(-500, -500)), isNull);
    });

    test('survives an empty model rather than dividing by its size', () {
      final layout = layoutOntology(
        const OntologyScreen(
          types: [],
          links: [],
          clusters: [],
          overlay: null,
          flows: [],
          conformance: null,
        ),
      );
      expect(layout.nodes, isEmpty);
      expect(layout.size.width, greaterThan(0));
    });
  });

  group('OntologyOverlay', () {
    test('separates a governance gap from a stale declaration', () {
      const overlay = OntologyOverlay(
        flowId: 'accounts-payable',
        state: 'observed',
        scannedRuns: 7,
        totalRuns: 7,
        reads: ['PurchaseOrder'],
        writes: ['Invoice'],
        establishes: ['issued'],
        knowledge: [],
        declaredReads: ['Vendor'],
        declaredWrites: ['Invoice', 'ReceivingReport'],
      );

      expect(overlay.read('PurchaseOrder'), Footprint.undeclared);
      expect(overlay.read('Vendor'), Footprint.stale);
      expect(overlay.write('Invoice'), Footprint.matched);
      expect(overlay.write('ReceivingReport'), Footprint.stale);
      expect(overlay.write('Employee'), Footprint.absent);
      expect(overlay.gaps, ['PurchaseOrder']);
    });
  });
}

// ---------------------------------------------------------------------------
// The seeded model, as the route serves it.
// ---------------------------------------------------------------------------

OntologyType _type(String name, String collection, {String owner = ''}) =>
    OntologyType(
      name: name,
      label: name,
      summary: '',
      naturalKey: '',
      fields: const [],
      tags: const [],
      competencyQuestions: const [],
      instances: 1,
      conflicted: 0,
      collection: collection,
      ownerType: owner,
      partTypes: const [],
    );

OntologyLink _link(
  String name,
  String from,
  String to, {
  String nature = 'association',
  int instances = 1,
}) => OntologyLink(
  name: name,
  fromType: from,
  toType: to,
  nature: nature,
  cardinality: 'many-to-one',
  enforced: true,
  instances: instances,
);

OntologyScreen _seeded() {
  const members = {
    'workforce': ['Employee', 'Team', 'Capability'],
    'calendars': ['Calendar', 'CalendarEvent'],
    'vendor-master': ['Vendor'],
    'source-landing': ['SourceRecord'],
    'expense-records': ['ExpenseRequest', 'ExpenseLine'],
    'ap-records': [
      'Invoice',
      'InvoiceLine',
      'PurchaseOrder',
      'PurchaseOrderLine',
      'ReceivingReport',
      'ReceivingReportLine',
      'MatchSet',
    ],
    'audit-documents': ['Document'],
  };
  const synced = {'workforce', 'calendars', 'vendor-master', 'source-landing'};
  const owners = {
    'ExpenseLine': 'ExpenseRequest',
    'CalendarEvent': 'Calendar',
    'InvoiceLine': 'Invoice',
    'PurchaseOrderLine': 'PurchaseOrder',
    'ReceivingReportLine': 'ReceivingReport',
    'MatchSet': 'Invoice',
  };

  return OntologyScreen(
    types: [
      for (final entry in members.entries)
        for (final name in entry.value)
          _type(name, entry.key, owner: owners[name] ?? ''),
    ],
    links: [
      _link('submitted', 'Employee', 'ExpenseRequest', instances: 10),
      _link(
        'has_expense_line',
        'ExpenseRequest',
        'ExpenseLine',
        nature: 'composition',
        instances: 12,
      ),
      _link('evidenced_by', 'ExpenseLine', 'Document', instances: 12),
      _link('decided_by', 'ExpenseRequest', 'Employee', instances: 4),
      _link('member_of', 'Employee', 'Team', instances: 11),
      _link('reports_to', 'Employee', 'Employee', instances: 10),
      _link('subteam_of', 'Team', 'Team', instances: 5),
      _link('led_by', 'Team', 'Employee', instances: 6),
      _link('has_capability', 'Employee', 'Capability', instances: 26),
      _link('has_calendar', 'Employee', 'Calendar', instances: 11),
      _link(
        'has_event',
        'Calendar',
        'CalendarEvent',
        nature: 'composition',
        instances: 275,
      ),
      _link('organized_by', 'CalendarEvent', 'Employee', instances: 275),
      _link('attended_by', 'CalendarEvent', 'Employee', instances: 1123),
      _link(
        'identified_as',
        'Vendor',
        'SourceRecord',
        nature: 'identity',
        instances: 5,
      ),
      _link('issued', 'Vendor', 'Invoice', instances: 5),
      _link(
        'has_invoice_line',
        'Invoice',
        'InvoiceLine',
        nature: 'composition',
        instances: 13,
      ),
      _link('evidenced_by_document', 'Invoice', 'Document', instances: 5),
      _link('ordered_from', 'PurchaseOrder', 'Vendor', instances: 3),
      _link(
        'has_po_line',
        'PurchaseOrder',
        'PurchaseOrderLine',
        nature: 'composition',
        instances: 6,
      ),
      _link('receives_against', 'ReceivingReport', 'PurchaseOrder',
          instances: 3),
      _link(
        'has_receiving_line',
        'ReceivingReport',
        'ReceivingReportLine',
        nature: 'composition',
        instances: 6,
      ),
      _link('billed_against', 'Invoice', 'PurchaseOrder', instances: 3),
      _link(
        'matched_by',
        'Invoice',
        'MatchSet',
        nature: 'composition',
        instances: 5,
      ),
      _link('matches_invoice_line', 'MatchSet', 'InvoiceLine', instances: 13),
    ],
    clusters: [
      for (final entry in members.entries)
        OntologyCluster(
          id: entry.key,
          label: entry.key,
          role: 'data',
          origin: synced.contains(entry.key) ? 'synced' : 'flow-written',
          typeNames: entry.value,
        ),
    ],
    overlay: null,
    flows: const [],
    conformance: 0,
  );
}
