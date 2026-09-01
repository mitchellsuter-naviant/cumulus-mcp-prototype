import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/models/run.dart';
import 'package:cumulus_admin/screens/flow_detail_screen.dart';

/// The cut behind the Runs tab's search box, status lenses and time window.
///
/// Asserted rather than eyeballed because every one of these is a case where
/// the wrong answer is silent: a filter that quietly drops a run looks
/// exactly like a flow that never ran it. The two that matter most:
///
/// - **an unparseable timestamp stays in.** A run the clock cannot place is
///   still a run that happened; excluding it would hide execution history
///   behind a formatting accident.
/// - **the search reaches the fields a run is looked up *by*.** Someone
///   arrives at this tab with a run id from a ticket, a case reference from
///   an invoice, or the name of the step a run is stuck at — not with a
///   substring of the title the list happens to render.
void main() {
  final now = DateTime.utc(2026, 9, 1, 12);

  FlowRunSummary run({
    String id = 'run_1',
    String status = 'succeeded',
    String caseRef = '',
    String actor = '',
    String suspendedAtStep = '',
    String error = '',
    String openTaskId = '',
    List<String> refs = const [],
    String? startedAtIso,
    Duration age = Duration.zero,
  }) => FlowRunSummary(
    id: id,
    flowId: 'ap-invoice',
    status: status,
    caseRef: caseRef,
    actor: actor,
    steps: 4,
    modelCalls: 2,
    costUsd: 0.01,
    durationMs: 0,
    openTaskId: openTaskId,
    suspendedAtStep: suspendedAtStep,
    resumeCount: 0,
    error: error,
    startedAtIso: startedAtIso ?? now.subtract(age).toIso8601String(),
    finishedAtIso: '',
    refs: refs,
    outputs: const {},
  );

  List<String> ids(List<FlowRunSummary> runs) => [for (final r in runs) r.id];

  group('filterRuns', () {
    test('with nothing set, the history comes back whole and in order', () {
      final runs = [run(id: 'run_3'), run(id: 'run_2'), run(id: 'run_1')];
      expect(ids(filterRuns(runs, now: now)), ['run_3', 'run_2', 'run_1']);
    });

    test('a status lens keeps only that state', () {
      final runs = [
        run(id: 'ok'),
        run(id: 'stuck', status: 'suspended'),
        run(id: 'broken', status: 'failed'),
      ];
      expect(
        ids(filterRuns(runs, status: 'suspended', now: now)),
        ['stuck'],
      );
    });

    test('a window cuts on when the run started, and keeps the boundary', () {
      final runs = [
        run(id: 'fresh', age: const Duration(hours: 2)),
        run(id: 'edge', age: const Duration(hours: 24)),
        run(id: 'stale', age: const Duration(days: 9)),
      ];
      expect(
        ids(filterRuns(runs, window: RunWindow.day, now: now)),
        ['fresh', 'edge'],
      );
      expect(
        ids(filterRuns(runs, window: RunWindow.week, now: now)),
        ['fresh', 'edge'],
      );
      expect(
        ids(filterRuns(runs, window: RunWindow.month, now: now)),
        ['fresh', 'edge', 'stale'],
      );
    });

    test('a run the clock cannot place stays in every window', () {
      final runs = [run(id: 'undated', startedAtIso: '')];
      expect(ids(filterRuns(runs, window: RunWindow.day, now: now)), ['undated']);
      expect(runWithin(runs.single, now, const Duration(hours: 1)), isTrue);
    });

    test('search reaches the fields a run is looked up by', () {
      final runs = [
        run(id: 'run_a1b2', caseRef: 'INV-4471'),
        run(id: 'run_c3d4', actor: 'dana.ruiz@naviant.com'),
        run(id: 'run_e5f6', status: 'suspended', suspendedAtStep: 'k2_review'),
        run(id: 'run_g7h8', status: 'failed', error: 'vendor lookup timed out'),
        run(id: 'run_i9j0', openTaskId: 'task_9931'),
        run(id: 'run_k1l2', refs: const ['asset:ap-policy@7']),
      ];
      for (final (query, expected) in const [
        ('a1b2', 'run_a1b2'),
        ('inv-4471', 'run_a1b2'),
        ('dana.ruiz', 'run_c3d4'),
        ('k2_review', 'run_e5f6'),
        ('timed out', 'run_g7h8'),
        ('task_9931', 'run_i9j0'),
        ('ap-policy', 'run_k1l2'),
      ]) {
        expect(
          ids(filterRuns(runs, query: query, now: now)),
          [expected],
          reason: 'searching "$query" should find $expected',
        );
      }
    });

    test('search ignores case and surrounding whitespace', () {
      final runs = [run(id: 'run_a1b2', caseRef: 'INV-4471')];
      expect(ids(filterRuns(runs, query: '  inv-4471 ', now: now)), ['run_a1b2']);
      expect(ids(filterRuns(runs, query: '   ', now: now)), ['run_a1b2']);
    });

    test('the three cuts compose', () {
      final runs = [
        run(id: 'hit', status: 'failed', caseRef: 'INV-4471', age: const Duration(hours: 3)),
        run(id: 'wrong-state', caseRef: 'INV-4471', age: const Duration(hours: 3)),
        run(id: 'too-old', status: 'failed', caseRef: 'INV-4471', age: const Duration(days: 40)),
        run(id: 'other-case', status: 'failed', caseRef: 'INV-9999', age: const Duration(hours: 3)),
      ];
      expect(
        ids(filterRuns(
          runs,
          status: 'failed',
          window: RunWindow.week,
          query: 'INV-4471',
          now: now,
        )),
        ['hit'],
      );
    });
  });
}
