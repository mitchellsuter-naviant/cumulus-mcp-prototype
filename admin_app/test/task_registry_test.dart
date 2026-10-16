import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/models/task.dart';
import 'package:cumulus_admin/models/task_screens.dart';
import 'package:cumulus_admin/tasks/registry.dart';

/// The floor under the registry: what a console does when the server has not
/// told it which screen answers a task.
///
/// This file used to be the rung below `task_screen_manifest_test.dart` — a
/// compiled table of flows, versions, labels and hints, plus the console's own
/// copy of the "newest package at or below the task's version" rule, kept
/// against a fetch that failed. Its job was to prove that the table could be
/// removed without changing anything a person sees, and the manifest tests
/// discharged that by reproducing every one of its properties from the wire.
/// So the table is gone, and this file now proves the other half of the same
/// claim — the half that is a *promise* rather than a regression:
///
/// - **Nothing is reachable except through the manifest.** No flow, kind or
///   version gets a bespoke screen from this build's own belief about a
///   deployment. A console that could still answer "Open the invoice review"
///   with no manifest could also answer it for a server that had dropped that
///   screen, and a clerk would sign a decision on a page the deployment
///   retired.
/// - **The floor is a working page, not a blank one.** The generic
///   approve/reject pane over the task's own evidence rows is what a failed
///   fetch degrades to. It offers less — no correcting the facts — and it
///   claims nothing that is not true.
/// - **Every widget the server can name is still compiled in.** The one thing
///   that cannot cross a wire. An id in a manifest with no constructor under it
///   is a screen nobody can reach, which is the bug the single dispatch point
///   exists to end.
void main() {
  tearDown(() => TaskScreens.manifest = null);

  group('with no manifest, no task opens a bespoke screen', () {
    test('not a flow whose screens this build has', () {
      // Both flows, at the versions that are live, and a shared kind that used
      // to be answered from a compiled table of its own.
      for (final task in [
        _task(kind: 'decision', flowId: 'accounts-payable', flowVersion: 9),
        _task(kind: 'curation', flowId: 'accounts-payable', flowVersion: 6),
        _task(
          kind: 'decision',
          flowId: 'expense-reimbursement',
          flowVersion: 10,
        ),
        _task(kind: 'merge_review', flowId: 'vendor-master-sync'),
        _task(kind: 'state_disagreement', flowId: 'order-desk'),
      ]) {
        expect(
          resolveReviewScreen(task),
          isNull,
          reason: '${task.flowId} v${task.flowVersion} ${task.kind}',
        );
        expect(hasReviewScreen(task), isFalse);
        expect(reviewHint(task), isNull);
      }
    });

    test('and the pane it falls to is named, not blank', () {
      // The label is never rendered — the pane only draws the button when
      // `hasReviewScreen` is true — but it is what the fallback says it is,
      // rather than a stale promise about a screen this build cannot open.
      final task = _task(kind: 'decision', flowId: 'accounts-payable');
      expect(reviewLabel(task), 'Open the review');
      expect(reviewLabel(task), isNot('Open the invoice review'));
    });
  });

  group('the manifest is the only thing that opens one', () {
    test('a served entry answers where the compiled table used to', () {
      TaskScreens.manifest = _manifest(
        'expense-reimbursement',
        10,
        'expense-reimbursement/confirm-values',
        'Confirm the values',
      );
      final task = _task(
        kind: 'decision',
        flowId: 'expense-reimbursement',
        flowVersion: 10,
      );
      expect(hasReviewScreen(task), isTrue);
      expect(reviewLabel(task), 'Confirm the values');
    });

    test('a manifest silent about a flow leaves that flow on the pane', () {
      // Saying nothing about a flow is not a gap for this side to fill in.
      // There is nothing to fill it in *with* any more, and that is the point:
      // an older server that has never heard of a flow cannot have its queue
      // answered by a screen the console invented for it.
      TaskScreens.manifest = _manifest(
        'accounts-payable',
        9,
        'accounts-payable/invoice-review',
        'Open the invoice review',
      );
      expect(
        hasReviewScreen(_task(
          kind: 'decision',
          flowId: 'expense-reimbursement',
          flowVersion: 10,
        )),
        isFalse,
      );
    });
  });

  group('the compiled half is the widgets and nothing else', () {
    test('every screen a shipped flow declares has a constructor', () {
      // Named literally rather than read off a shared constant: these strings
      // are a contract with the server's `TaskScreen.screen_id`, and a test
      // that imported them from the same place the code does would pass
      // through a rename on either side.
      for (final id in [
        'accounts-payable/invoice-review',
        'accounts-payable/extraction-review',
        'accounts-payable/three-way-match',
        'expense-reimbursement/confirm-values',
        'expense-reimbursement/expense-review',
        'shared/entity-match-review',
        'shared/merge-review',
        'shared/disagreement-review',
        // The plant spine's four. `plant-schedule/extraction-review` is a
        // different screen from `accounts-payable/extraction-review` over a
        // different document, and the two Dart classes share a name — so the
        // registry imports one of them prefixed. A missing entry here would
        // send a supervisor's shipping list to the generic pane with no
        // boxes on it, which is exactly the silent failure this list exists
        // to catch.
        'field-documents/gap-review',
        'compliance/exception-review',
        'plant-schedule/extraction-review',
        'plant-schedule/day-approval',
      ]) {
        expect(taskScreenFactories.containsKey(id), isTrue, reason: id);
      }
    });

    test('an id this build has never heard of is a degrade, not a guess', () {
      // A console older than the deployment it is talking to. The manifest is
      // right that this task needs a screen; opening a neighbouring one would
      // answer a different question with a clerk's signature on it.
      TaskScreens.manifest = _manifest(
        'accounts-payable',
        11,
        'accounts-payable/v11-not-shipped-yet',
        'Open the invoice review',
      );
      final task = _task(
        kind: 'decision',
        flowId: 'accounts-payable',
        flowVersion: 11,
      );
      expect(hasReviewScreen(task), isFalse);
      expect(reviewHint(task), isNull);
    });
  });
}

/// One flow, one version, one screen — the smallest manifest that answers.
TaskScreenManifest _manifest(
  String flowId,
  int version,
  String screenId,
  String label,
) => TaskScreenManifest(
  flows: {
    flowId: FlowScreens(
      versions: {
        version: TaskUiEntry(
          otherwise: TaskScreenSpec(
            screenId: screenId,
            label: label,
            hint: 'what the screen is for',
          ),
        ),
      },
    ),
  },
);

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
