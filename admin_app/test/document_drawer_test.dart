import 'dart:convert';
import 'dart:typed_data';

import 'package:cumulus_admin/models/data_layer.dart';
import 'package:cumulus_admin/widgets/document_drawer.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// What the drawer says when it cannot draw the page.
///
/// The viewer is the easy half. The half worth pinning down is the three
/// states where there is nothing to render and the honest answer is a
/// sentence: a run that stored no bytes, a store that will not hand them over,
/// and — the estate's live condition — an artifact filed as a PDF that is not
/// one. Each of those has a wrong version that looks identical to a working
/// viewer with nothing in it.
void main() {
  testWidgets('a run that stored no bytes keeps saying so', (tester) async {
    await _pump(tester, _document(hasBytes: false, byteSize: 0), null);

    expect(find.textContaining('No bytes.'), findsOneWidget);
    // The state `dms-sync` leaves a document in, and now the only way to
    // reach it: a reference with no content hash is the map saying it knows
    // where the file is and nobody has read it. It used to say "composed
    // rather than uploaded", which described a submission path that no longer
    // exists.
    expect(find.textContaining('nobody has read it'), findsOneWidget);
    // The custody record is the half that survives having no artifact.
    expect(find.textContaining('Submitted by clerk@naviant.com'), findsOneWidget);
  });

  testWidgets('bytes the store would not hand over are not a missing '
      'document', (tester) async {
    await _pump(tester, _document(), null);

    expect(find.textContaining('could not be loaded just now'), findsOneWidget);
    expect(find.textContaining('custody record above is unaffected'),
        findsOneWidget);
  });

  testWidgets('text filed under a .pdf name is named, and then shown', (
    tester,
  ) async {
    await _pump(
      tester,
      _document(),
      _bytes('INVOICE INV-2481\nRidgeline Supply\nTotal: 4,120.00'),
    );

    expect(find.textContaining('do not begin'), findsOneWidget);
    expect(find.textContaining('Total: 4,120.00'), findsOneWidget);
  });

  testWidgets('a real PDF is handed to the page renderer, not to a paragraph', (
    tester,
  ) async {
    await _pump(tester, _document(), _bytes('%PDF-1.7\n1 0 obj\n'));

    // On the VM there is no browser to draw it, and the drawer says that
    // rather than pretending the bytes are broken.
    expect(find.textContaining('do not begin'), findsNothing);
    expect(find.textContaining('no page renderer'), findsOneWidget);
  });

  testWidgets('the regions are listed as claims, with their confidence', (
    tester,
  ) async {
    await _pump(
      tester,
      _document(
        regions: [
          _region('invoice.number', text: 'INV-2481'),
          _region('invoice.total_usd', text: '4,120.00', page: 2),
        ],
      ),
      null,
    );

    expect(find.text('invoice.number'), findsOneWidget);
    expect(find.text('INV-2481'), findsOneWidget);
    expect(find.text('p1 · 92%'), findsOneWidget);
    expect(find.text('p2 · 92%'), findsOneWidget);
    // A region on page 2 is still listed — the list is the whole extraction,
    // the page frame is the one page being looked at.
    expect(find.textContaining('2 field(s)'), findsOneWidget);
  });

  testWidgets('a multi-page document offers its pages', (tester) async {
    await _pump(tester, _document(pages: 3), null);

    expect(find.text('PAGE 1'), findsOneWidget);
    expect(find.text('PAGE 3'), findsOneWidget);
  });

  testWidgets('a single page is not a choice to make', (tester) async {
    await _pump(tester, _document(), null);
    expect(find.text('PAGE 1'), findsNothing);
  });
}

Future<void> _pump(
  WidgetTester tester,
  StoredDocument document,
  Uint8List? bytes,
) async {
  // The test font draws every glyph square, so the drawer's prose needs more
  // room here than in the browser.
  tester.view.physicalSize = const Size(1800, 2400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: DocumentBody(pending: Future.value((document, bytes))),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

StoredDocument _document({
  bool hasBytes = true,
  int byteSize = 18422,
  int pages = 1,
  List<DocumentRegion> regions = const [],
}) => StoredDocument(
  id: 'doc_28fee8830bfcf03c',
  sha256: '28fee8830bfcf03c',
  filename: 'ridgeline-inv-2481.pdf',
  mediaType: 'application/pdf',
  byteSize: byteSize,
  kind: 'invoice',
  pages: pages,
  regionCount: regions.length,
  collectionId: 'audit-documents',
  retentionUntilIso: '2033-09-02T00:00:00Z',
  uploadedBy: 'clerk@naviant.com',
  uploadedAtIso: '2026-09-02T09:14:00Z',
  runIds: const ['run_ap_1'],
  hasBytes: hasBytes,
  regions: regions,
);

DocumentRegion _region(
  String field, {
  String text = '',
  int page = 1,
}) => DocumentRegion(
  field: field,
  page: page,
  x: 0.62,
  y: 0.18,
  width: 0.22,
  height: 0.03,
  text: text,
  confidence: 0.92,
);

Uint8List _bytes(String text) => Uint8List.fromList(utf8.encode(text));
