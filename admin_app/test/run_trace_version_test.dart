import 'package:cumulus_admin/models/flow.dart';
import 'package:cumulus_admin/models/run.dart';
import 'package:cumulus_admin/screens/flow_detail_screen.dart';
import 'package:flutter_test/flutter_test.dart';

/// Which graph a run's path gets drawn over.
///
/// The bug this guards is the quietest kind: the engine pins `flow_version`
/// at the first step and honours it on resume, but the console traced every
/// run over the *active* version — so a v6 execution was painted on v7's
/// canvas, node ids matching, nothing on screen saying otherwise. Every case
/// below is about the console either drawing the right graph or admitting it
/// is not.
void main() {
  // Two published versions and the draft nobody has run.
  final versions = [
    _version(8, status: 'draft'),
    _version(7, status: 'active'),
    _version(6, status: 'archived'),
  ];

  group('the wire carries the version that ran', () {
    test('a run reads its version off the journal', () {
      final run = FlowRun.fromJson({
        'id': 'run_ap_1',
        'flow_id': 'accounts-payable',
        'flow_version': 6,
      });
      expect(run.flowVersion, 6);
      expect(run.claimsVersion, isTrue);
    });

    test('a summary row carries it too — the list is what gets traced', () {
      final row = FlowRunSummary.fromJson({
        'id': 'run_ap_1',
        'flow_id': 'accounts-payable',
        'flow_version': 6,
      });
      expect(row.flowVersion, 6);
    });

    test('a record written before the field claims nothing', () {
      final row = FlowRunSummary.fromJson({'id': 'run_ap_0'});
      expect(row.flowVersion, 0);
      // Not "version zero". The absence of a claim, which is why the trace
      // has something to say rather than something to assume.
      expect(row.claimsVersion, isFalse);
    });
  });

  group('traceTarget', () {
    test('the version the run named, when the history still holds it', () {
      final target = traceTarget(
        runVersion: 6,
        versions: versions,
        selected: 8,
      );
      expect(target.version, 6);
      expect(target.exact, isTrue);
      expect(target.caveat, isEmpty);
    });

    test('a run of the active version still moves the canvas off a draft', () {
      final target = traceTarget(
        runVersion: 7,
        versions: versions,
        selected: 8,
      );
      expect(target.version, 7);
      expect(target.exact, isTrue);
    });

    test('a version the history no longer holds is said, not substituted', () {
      final target = traceTarget(
        runVersion: 4,
        versions: versions,
        selected: 7,
      );
      // The path is still worth drawing — over the active version, named.
      expect(target.version, 7);
      expect(target.exact, isFalse);
      expect(target.caveat, contains('v4'));
      expect(target.caveat, contains('v7'));
      expect(target.caveat, contains('no longer'));
    });

    test('no recorded version is a gap in the journal, not the active one', () {
      final target = traceTarget(
        runVersion: 0,
        versions: versions,
        selected: 7,
      );
      expect(target.version, 7);
      expect(target.exact, isFalse);
      expect(target.caveat, contains('recorded no version'));
    });

    test('with no versions loaded the canvas is left where it is', () {
      final target = traceTarget(
        runVersion: 6,
        versions: const [],
        selected: null,
      );
      expect(target.version, isNull);
      expect(target.caveat, isEmpty);
    });

    test('with nothing active the stand-in is whatever is on screen', () {
      final target = traceTarget(
        runVersion: 4,
        versions: [_version(6, status: 'archived')],
        selected: 6,
      );
      expect(target.version, 6);
      expect(target.caveat, contains('v6'));
    });

    test('a labelled version is named the way the picker names it', () {
      final target = traceTarget(
        runVersion: 4,
        versions: [_version(7, status: 'active', label: 'v7 · receiving leg')],
        selected: 7,
      );
      expect(target.caveat, contains('v7 · receiving leg'));
    });
  });
}

FlowVersion _version(int version, {required String status, String label = ''}) =>
    FlowVersion.fromJson({
      'version': version,
      'label': label,
      'status': status,
      'steps': const [],
    });
