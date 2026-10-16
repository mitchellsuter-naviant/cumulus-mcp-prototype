import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/models/task.dart';
import 'package:cumulus_admin/models/task_screens.dart';
import 'package:cumulus_admin/tasks/registry.dart';

/// The served half of the registry: what the console draws when the server
/// tells it which screen answers which task.
///
/// The wiring used to be a table compiled in here beside its own copy of the
/// "newest package at or below the task's version" rule. Two things could go
/// wrong quietly. A console could be shipped believing in a version
/// arrangement the server does not have — a screen claimed at a version the
/// fleet never deployed — and nothing failed; the task just opened the wrong
/// screen. And a rule that exists on both sides can be corrected on one.
///
/// The manifest closes both, and the properties that has to preserve are the
/// ones this file defends:
///
/// - **The served answer is the whole answer.** Every screen the compiled table
///   used to name is named here off the wire instead, with the same strings —
///   which is what made removing that table a no-op for the person reading the
///   button. The table is now gone; `task_registry_test.dart` holds the floor
///   it left behind.
/// - **Kind still wins first.** A `merge_review` raised by accounts-payable is
///   still a merge review.
/// - **Nothing blanks.** A `screen_id` this build does not know, a manifest
///   that never loaded, and a version nobody enumerated each land somewhere a
///   clerk can still work — on the generic approve/reject pane when they land
///   nowhere better.
/// - **The copy is unchanged.** The labels and hints crossed a wire in this
///   change; they are the part a person actually reads, so they are asserted
///   verbatim rather than by shape.
void main() {
  tearDown(() => TaskScreens.manifest = null);

  group('the manifest supplies what the compiled table used to', () {
    test('accounts-payable v6 splits by kind, off the wire alone', () {
      // The gate the deletion rested on: the strings are today's, and they
      // arrive from the manifest rather than from a table in this build.
      final curation = resolveReviewScreen(
        _task(kind: 'curation', flowId: 'accounts-payable', flowVersion: 6),
        manifest: _manifest(),
      );
      expect(curation, isNotNull);
      expect(curation!.label, 'Open the extraction verification');
      expect(curation.hint, _correctTheFacts);

      final decision = resolveReviewScreen(
        _task(kind: 'decision', flowId: 'accounts-payable', flowVersion: 6),
        manifest: _manifest(),
      );
      expect(decision!.label, 'Open the invoice review');
      expect(decision.hint, _correctTheFacts);
    });

    test('accounts-payable v8 answers every kind with one screen', () {
      for (final kind in ['curation', 'decision', 'exception']) {
        final screen = resolveReviewScreen(
          _task(kind: kind, flowId: 'accounts-payable', flowVersion: 8),
          manifest: _manifest(),
        );
        expect(screen!.label, 'Open the invoice review', reason: kind);
        expect(screen.hint, _v8Hint, reason: kind);
      }
    });

    test('expense reimbursement v9 opens the expense review', () {
      final screen = resolveReviewScreen(
        _task(
          kind: 'decision',
          flowId: 'expense-reimbursement',
          flowVersion: 9,
        ),
        manifest: _manifest(),
      );
      expect(screen!.label, 'Open the expense review');
      expect(screen.hint, _correctTheFacts);
    });

    test('expense reimbursement v10 opens the confirm-values screen', () {
      // A version bump that changes who is being asked changes the screen, and
      // the older question keeps the older screen: a v9 task was raised for an
      // approver and answering it through the submitter's confirmation would
      // put "your receipt" in front of somebody else's.
      final screen = resolveReviewScreen(
        _task(
          kind: 'decision',
          flowId: 'expense-reimbursement',
          flowVersion: 10,
        ),
        manifest: _manifest(),
      );
      expect(screen!.label, 'Confirm the values');
      expect(taskScreenFactories.containsKey(
        'expense-reimbursement/confirm-values',
      ), isTrue);
    });

    test('the kind-keyed screens are served too, and still win', () {
      // Raised by a flow that has its own screens: a registry consulting the
      // flow first would send a steward judging two records to an invoice.
      final merge = resolveReviewScreen(
        _task(kind: 'merge_review', flowId: 'accounts-payable', flowVersion: 6),
        manifest: _manifest(),
      );
      expect(merge!.label, 'Open the merge review');
      expect(merge.hint, contains('Nothing merges on either verdict'));

      final disagreement = resolveReviewScreen(
        _task(kind: 'state_disagreement', flowId: 'order-desk'),
        manifest: _manifest(),
      );
      expect(disagreement!.label, 'Open the disagreement');

      final match = resolveReviewScreen(
        _task(kind: 'match_review', flowId: 'vendor-master-sync'),
        manifest: _manifest(),
      );
      expect(match!.label, 'Open the match review');
    });
  });

  group('the version arithmetic happened on the server', () {
    test('a v7 task reads v6 screens, because the manifest says so', () {
      // Not because this side resolved anything: v7 is an entry in the
      // manifest, carrying v6's screens, because the server knows v7 shipped
      // and declared no UI of its own. The console looks it up.
      final screen = resolveReviewScreen(
        _task(kind: 'curation', flowId: 'accounts-payable', flowVersion: 7),
        manifest: _manifest(),
      );
      expect(screen!.label, 'Open the extraction verification');
    });

    test('a version nobody enumerated takes the fallback entry', () {
      // A v40 that never changed its screens, and a task carrying no version
      // at all — an older record, or a locally built double. The server named
      // the fallback; there is no "newest at or below" rule on this side to
      // disagree with it.
      for (final version in [0, 3, 12, 40]) {
        final screen = resolveReviewScreen(
          _task(
            kind: 'curation',
            flowId: 'accounts-payable',
            flowVersion: version,
          ),
          manifest: _manifest(),
        );
        expect(screen, isNotNull, reason: 'v$version');
        expect(screen!.label, 'Open the invoice review', reason: 'v$version');
        // v9's hint, because v9 is the newest declaration — the fallback
        // follows the live version rather than being pinned to whichever one
        // happened to be newest when this test was written.
        expect(screen.hint, _v9Hint, reason: 'v$version');
      }
    });

    test('a v9 task gets the sentence about the folded-in vendor', () {
      // v8's screen with a second question on it. The hint is the part a
      // person reads before opening it, and v8's copy does not mention the
      // vendor because v8 asked about it at a gate of its own.
      final screen = resolveReviewScreen(
        _task(kind: 'decision', flowId: 'accounts-payable', flowVersion: 9),
        manifest: _manifest(),
      );
      expect(screen!.hint, contains('the scored candidates are on the same '
          'page'));
    });
  });

  group('nothing in the chain may blank a screen', () {
    test('a screen_id this build does not have falls to the generic pane', () {
      // A console older than the deployment it is talking to. The generic
      // approve/reject is the floor: the manifest is right that this task
      // needs a screen this build lacks, and opening a neighbouring widget
      // would answer a different question with a clerk's signature on it.
      final manifest = TaskScreenManifest.fromJson(
        jsonDecode(
              jsonEncode({
                'shared': const {},
                'flows': [
                  {
                    'flow_id': 'accounts-payable',
                    'versions': [
                      {
                        'version': 6,
                        'ui': {
                          'by_kind': const {},
                          'otherwise': {
                            'screen_id': 'accounts-payable/v99-not-shipped-yet',
                            'label': 'Open the invoice review',
                            'hint': _correctTheFacts,
                          },
                        },
                      },
                    ],
                    'fallback': null,
                  },
                ],
              }),
            )
            as Map<String, dynamic>,
      );
      final task = _task(
        kind: 'curation',
        flowId: 'accounts-payable',
        flowVersion: 6,
      );
      expect(resolveReviewScreen(task, manifest: manifest), isNull);
      TaskScreens.manifest = manifest;
      expect(hasReviewScreen(task), isFalse);
      expect(reviewHint(task), isNull);
    });

    test('a manifest that never loaded leaves every task on the pane', () {
      // The fetch failed, or the server is older than this console. What a
      // clerk loses is the bespoke screen; what they get is the generic
      // approve/reject over the task's own evidence rows. The compiled table
      // that used to cover for this is gone, and deliberately: it could only
      // answer with this build's belief about a deployment it had just failed
      // to reach — see `task_registry_test.dart` for that floor in full.
      TaskScreens.manifest = null;
      final task = _task(
        kind: 'curation',
        flowId: 'accounts-payable',
        flowVersion: 6,
      );
      expect(hasReviewScreen(task), isFalse);
      expect(reviewHint(task), isNull);
    });

    test('a manifest silent about a flow leaves that flow on the pane', () {
      // Saying nothing about a flow is no longer a gap this side can fill: an
      // older server that ships accounts-payable and has never heard of
      // expense-reimbursement gets the pane for that queue, rather than a
      // screen the console kept a copy of on its behalf.
      final partial = TaskScreenManifest.fromJson(
        jsonDecode(
              jsonEncode({
                'shared': const {},
                'flows': [_apFlow()],
              }),
            )
            as Map<String, dynamic>,
      );
      TaskScreens.manifest = partial;
      expect(
        hasReviewScreen(_task(
          kind: 'decision',
          flowId: 'expense-reimbursement',
          flowVersion: 10,
        )),
        isFalse,
      );
      // And the flow it does describe still answers, so the silence is about
      // the flow rather than about the manifest.
      expect(
        reviewLabel(_task(
          kind: 'decision',
          flowId: 'accounts-payable',
          flowVersion: 9,
        )),
        'Open the invoice review',
      );
    });

    test('a manifest with no shared block puts the kind screens on the pane',
        () {
      // The kind-keyed screens exist because their generic pane offered an
      // Approve button whose whole account of itself was the word "Approve",
      // over a write to a customer's ERP — so a deployment that does not
      // serve them is a real loss. It is still the honest answer: the screens
      // are the server's to declare, and this side inventing them is how a
      // steward opens a page the deployment dropped.
      final partial = TaskScreenManifest.fromJson(
        jsonDecode(
              jsonEncode({
                'shared': const {},
                'flows': [_apFlow()],
              }),
            )
            as Map<String, dynamic>,
      );
      TaskScreens.manifest = partial;
      expect(
        hasReviewScreen(
          _task(kind: 'state_disagreement', flowId: 'order-desk'),
        ),
        isFalse,
      );
    });

    test('a flow the manifest knows, on a kind it declined, gets the pane', () {
      // A flow the manifest describes has answered even when the answer is
      // "no bespoke screen for this kind".
      final declining = TaskScreenManifest.fromJson(
        jsonDecode(
              jsonEncode({
                'shared': const {},
                'flows': [
                  {
                    'flow_id': 'accounts-payable',
                    'versions': [
                      {
                        'version': 6,
                        'ui': {
                          'by_kind': {
                            'curation': {
                              'screen_id':
                                  'accounts-payable/extraction-review',
                              'label': 'Open the extraction verification',
                              'hint': _correctTheFacts,
                            },
                          },
                          'otherwise': null,
                        },
                      },
                    ],
                    'fallback': null,
                  },
                ],
              }),
            )
            as Map<String, dynamic>,
      );
      expect(
        resolveReviewScreen(
          _task(kind: 'decision', flowId: 'accounts-payable', flowVersion: 6),
          manifest: declining,
        ),
        isNull,
      );
    });
  });

  group('the served ids and the compiled widgets agree', () {
    test('every screen_id the server can name has a factory here', () {
      // The one thing that cannot cross the wire. An id in the manifest with
      // no constructor under it is a screen nobody can reach — the exact bug
      // the single dispatch point was built to end, arriving from the other
      // side.
      final manifest = _manifest();
      final named = <String>{
        ...manifest.shared.values.map((s) => s.screenId),
        for (final flow in manifest.flows.values) ...[
          for (final entry in flow.versions.values) ...[
            ...entry.byKind.values.map((s) => s.screenId),
            if (entry.otherwise != null) entry.otherwise!.screenId,
          ],
          if (flow.fallback?.otherwise != null)
            flow.fallback!.otherwise!.screenId,
        ],
      };
      expect(named, isNotEmpty);
      for (final id in named) {
        expect(taskScreenFactories.containsKey(id), isTrue, reason: id);
      }
    });

    test('the cache is what the public entry points read', () {
      // `hasReviewScreen` and its siblings are called from a build method, so
      // the manifest has to be resolved before the frame rather than awaited
      // inside it.
      final task = _task(
        kind: 'curation',
        flowId: 'accounts-payable',
        flowVersion: 7,
      );
      TaskScreens.manifest = _manifest();
      expect(hasReviewScreen(task), isTrue);
      expect(reviewLabel(task), 'Open the extraction verification');
      expect(reviewHint(task), _correctTheFacts);
    });
  });
}

// ---------------------------------------------------------------------------
// The fixture — the shape `/api/ui/v1/task-screens` actually serves
// ---------------------------------------------------------------------------

/// Today's strings, written out rather than imported: the server owns these
/// now, and a test that read them from the same constant the code reads would
/// pass through a rewording.
const _correctTheFacts =
    'Opens the full screen, where the facts themselves can be corrected — '
    'the checks then run again over what you changed.';

const _v8Hint =
    'Opens the invoice itself, with a box on each field the run could not '
    'settle and the check’s own sentence beside it. Everything it read '
    'cleanly is folded away; correcting a field re-runs the checks over it.';

/// v9's, which is v8's plus the sentence about the folded-in vendor question.
const _v9Hint =
    'Opens the invoice itself, with a box on each field the run could not '
    'settle and the check’s own sentence beside it. Everything it read '
    'cleanly is folded away; correcting a field re-runs the checks over it. '
    'When the resolver could not settle the vendor, the scored candidates are '
    'on the same page.';

Map<String, dynamic> _screen(String id, String label, String hint) => {
  'screen_id': id,
  'label': label,
  'hint': hint,
};

Map<String, dynamic> _v6Ui() => {
  'by_kind': {
    'curation': _screen(
      'accounts-payable/extraction-review',
      'Open the extraction verification',
      _correctTheFacts,
    ),
  },
  'otherwise': _screen(
    'accounts-payable/three-way-match',
    'Open the invoice review',
    _correctTheFacts,
  ),
};

Map<String, dynamic> _v8Ui() => {
  'by_kind': const {},
  'otherwise': _screen(
    'accounts-payable/invoice-review',
    'Open the invoice review',
    _v8Hint,
  ),
};

Map<String, dynamic> _v9Ui() => {
  'by_kind': const {},
  'otherwise': _screen(
    'accounts-payable/invoice-review',
    'Open the invoice review',
    _v9Hint,
  ),
};

/// v7 carries v6's screens, resolved by the server — the console never sees a
/// gap between 6 and 8 to reason about. v9 is the live version and so is also
/// the fallback: a task carrying a version this deployment never shipped is
/// answered by the newest declaration.
Map<String, dynamic> _apFlow() => {
  'flow_id': 'accounts-payable',
  'versions': [
    {'version': 6, 'ui': _v6Ui()},
    {'version': 7, 'ui': _v6Ui()},
    {'version': 8, 'ui': _v8Ui()},
    {'version': 9, 'ui': _v9Ui()},
  ],
  'fallback': _v9Ui(),
};

TaskScreenManifest _manifest() {
  final v9ExpenseUi = {
    'by_kind': const {},
    'otherwise': _screen(
      'expense-reimbursement/expense-review',
      'Open the expense review',
      _correctTheFacts,
    ),
  };
  // v10 asks a different question of a different person — the submitter
  // confirming their own receipt rather than an approver ruling on somebody
  // else's — so it declares a screen of its own rather than reusing v9's.
  final v10ExpenseUi = {
    'by_kind': const {},
    'otherwise': _screen(
      'expense-reimbursement/confirm-values',
      'Confirm the values',
      _correctTheFacts,
    ),
  };
  final payload = {
    'shared': {
      'match_review': _screen(
        'shared/entity-match-review',
        'Open the match review',
        _correctTheFacts,
      ),
      'merge_review': _screen(
        'shared/merge-review',
        'Open the merge review',
        'Opens the two records side by side, with every signal the score was '
            'blended from and the governed policy behind the weights. Nothing '
            'merges on either verdict.',
      ),
      'state_disagreement': _screen(
        'shared/disagreement-review',
        'Open the disagreement',
        'Opens what each system says, the evidence that correlated them, and '
            'exactly what releasing would change in the ERP — and what it '
            'would leave alone.',
      ),
    },
    'flows': [
      _apFlow(),
      {
        'flow_id': 'expense-reimbursement',
        'versions': [
          {'version': 9, 'ui': v9ExpenseUi},
          {'version': 10, 'ui': v10ExpenseUi},
        ],
        'fallback': v10ExpenseUi,
      },
    ],
  };
  // Through a real encode/decode so the parser is under test, not a hand-built
  // object graph that never met the wire.
  return TaskScreenManifest.fromJson(
    jsonDecode(jsonEncode(payload)) as Map<String, dynamic>,
  );
}

QueueTask _task({
  required String kind,
  required String flowId,
  int flowVersion = 0,
}) => QueueTask(
  id: 'task_$kind',
  runId: 'run_1',
  kind: kind,
  title: 'A task',
  summary: '',
  queue: 'ap-clerk',
  priority: 'normal',
  status: 'open',
  flowId: flowId,
  flowVersion: flowVersion,
  caseRef: 'case_1',
  refs: const [],
  evidence: const [],
  proposal: null,
  resumes: '',
  createdAtIso: '2026-09-01T09:00:00Z',
  decision: null,
);
