import 'dart:convert';
import 'dart:typed_data';

import 'package:cumulus_admin/models/data_layer.dart';
import 'package:cumulus_admin/widgets/document_drawer.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';

/// The arithmetic that turns a stored claim into a box on a page.
///
/// Worth testing rather than eyeballing because every failure mode here is
/// silent: a region drawn a page-width to the left still draws, a region with
/// no height draws as nothing, and a region filtered onto the wrong page
/// simply does not appear — none of which looks like an error, all of which
/// would have the console pointing at the wrong part of an invoice.
void main() {
  const page = Size(600, 800);

  group('regionRect', () {
    test('normalized coordinates land on the rendered page', () {
      final rect = regionRect(
        _region(x: 0.1, y: 0.2, width: 0.5, height: 0.25),
        page,
      );
      expect(rect.left, closeTo(60, 0.001));
      expect(rect.top, closeTo(160, 0.001));
      expect(rect.width, closeTo(300, 0.001));
      expect(rect.height, closeTo(200, 0.001));
    });

    test('a box running off the edge is clamped back onto the page', () {
      final rect = regionRect(
        _region(x: 0.8, y: 0.9, width: 0.5, height: 0.4),
        page,
      );
      expect(rect.right, closeTo(600, 0.001));
      expect(rect.bottom, closeTo(800, 0.001));
      expect(rect.left, closeTo(480, 0.001));
    });

    test('a negative origin is clamped rather than drawn off-frame', () {
      final rect = regionRect(
        _region(x: -0.2, y: -0.1, width: 0.4, height: 0.3),
        page,
      );
      expect(rect.left, 0);
      expect(rect.top, 0);
      expect(rect.right, closeTo(120, 0.001));
    });

    test('a region reported as a point is still visible and still inside', () {
      final rect = regionRect(_region(x: 0.5, y: 0.5), page);
      expect(rect.width, greaterThanOrEqualTo(4));
      expect(rect.height, greaterThanOrEqualTo(4));
      expect(page.contains(rect.bottomRight), isTrue);
    });

    test('a point on the far corner is nudged in, never hung off', () {
      final rect = regionRect(_region(x: 1, y: 1), page);
      expect(rect.right, closeTo(600, 0.001));
      expect(rect.bottom, closeTo(800, 0.001));
      expect(rect.left, lessThan(600));
      expect(rect.top, lessThan(800));
    });

    test('a backwards extent reads as the span its two ends describe', () {
      final rect = regionRect(
        _region(x: 0.6, y: 0.5, width: -0.2, height: -0.25),
        page,
      );
      expect(rect.left, closeTo(240, 0.001));
      expect(rect.right, closeTo(360, 0.001));
      expect(rect.top, closeTo(200, 0.001));
      expect(rect.bottom, closeTo(400, 0.001));
    });

    test('the page size is the only scale — the same claim, drawn twice', () {
      final small = regionRect(_region(x: 0.25, y: 0.5, width: 0.5), page);
      final large = regionRect(
        _region(x: 0.25, y: 0.5, width: 0.5),
        const Size(1200, 1600),
      );
      expect(large.left, closeTo(small.left * 2, 0.001));
      expect(large.width, closeTo(small.width * 2, 0.001));
    });
  });

  group('regionsOnPage', () {
    final regions = [
      _region(field: 'invoice.number'),
      _region(field: 'invoice.total_usd', page: 2),
      _region(field: 'line_items[0].amount_usd', page: 0),
    ];

    test('a page shows only its own claims', () {
      expect(
        regionsOnPage(regions, 2).map((r) => r.field),
        ['invoice.total_usd'],
      );
    });

    test('a page number below one is the first page, not nowhere', () {
      // Dropping it would have the viewer claiming the extractor found less
      // than it did.
      expect(regionsOnPage(regions, 1).map((r) => r.field), [
        'invoice.number',
        'line_items[0].amount_usd',
      ]);
    });

    test('a page with nothing on it is empty, not an error', () {
      expect(regionsOnPage(regions, 3), isEmpty);
    });
  });

  group('looksLikePdf', () {
    test('a real PDF is recognised by its magic', () {
      expect(looksLikePdf(_bytes('%PDF-1.7\n1 0 obj')), isTrue);
    });

    test('invoice text under a .pdf name is not a PDF', () {
      // What the estate files today. Rendering an empty viewer over it would
      // say the store is broken; it is the artifact that is not what it says.
      expect(looksLikePdf(_bytes('INVOICE INV-2481\nTotal: 4,120.00')), isFalse);
    });

    test('too few bytes to tell is not a claim that it is one', () {
      expect(looksLikePdf(_bytes('%PDF')), isFalse);
      expect(looksLikePdf(Uint8List(0)), isFalse);
    });
  });
}

DocumentRegion _region({
  String field = 'invoice.total_usd',
  int page = 1,
  double x = 0,
  double y = 0,
  double width = 0,
  double height = 0,
}) => DocumentRegion(
  field: field,
  page: page,
  x: x,
  y: y,
  width: width,
  height: height,
  text: '4,120.00',
  confidence: 0.92,
);

Uint8List _bytes(String text) => Uint8List.fromList(utf8.encode(text));
