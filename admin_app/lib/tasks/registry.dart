/// The one place that answers "what does a person open to decide this task?"
///
/// My Tasks used to hold three hand-maintained tables — which kinds have a
/// bespoke screen, what the button says, what the screen is for — beside a
/// fourth if/else chain in a widgets file that actually picked the widget.
/// Four lists of the same facts is three chances to add a screen nobody can
/// reach, and the console has shipped that bug: a kind in the dispatcher and
/// not in `hasReviewScreen` is a screen with no button.
///
/// So: one dispatch point — and, since WP-G, one that asks the server first.
///
/// **The wiring comes off the wire.** Which flow raised a task, at which
/// version, and which screen answers it is a fact about what the *server*
/// deployed: it knows which `v<N>/` packages shipped and which version is
/// live. Holding that here as a compiled table let a console be shipped
/// believing in a version arrangement the server does not have, and held a
/// second copy of the "newest package at or below the task's version" rule
/// that could be corrected on one side only. `GET /api/ui/v1/task-screens`
/// answers both, already resolved — see [TaskScreens]. That copy is now gone
/// rather than kept as a fallback: the fleet only talks to servers that serve
/// the manifest, and a duplicate of the strings a person reads is a liability
/// once it is no longer load-bearing.
///
/// **The widget stays compiled in.** A constructor cannot cross a wire, so
/// [taskScreenFactories] maps the manifest's `screen_id` onto the real widget
/// and is the one thing a deploy of the console changes.
///
/// **Nothing here may blank the console.** A manifest that never loaded and a
/// `screen_id` this build has never heard of both land on the generic
/// approve/reject pane, which is a page a person can still work — see
/// [resolveReviewScreen] for the chain and what each rung is for.
library;

import 'package:flutter/material.dart';

import '../models/task.dart';
import '../models/task_screens.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'accounts_payable/v6/extraction_review.dart';
import 'accounts_payable/v6/verify_three_way_match.dart';
import 'accounts_payable/v8/invoice_review.dart';
import 'compliance/v1/exception_review.dart';
import 'expense_reimbursement/v10/confirm_values.dart';
import 'expense_reimbursement/v9/expense_review.dart';
import 'field_documents/v1/gap_review.dart';
import 'plant_schedule/v1/day_approval.dart';
// Two screens are called `ExtractionReview`, and they answer to two different
// documents: the accounts-payable one reads an invoice, the plant one reads a
// shipping list. Neither is renamed to make this import tidy — a class is
// named for what it is in its own package, and the disambiguation belongs at
// the one place that has to hold both.
import 'plant_schedule/v1/extraction_review.dart' as plant_extraction;
import 'shared/disagreement_review.dart';
import 'shared/entity_match_review.dart';
import 'shared/merge_review.dart';
import 'shared/review_frame.dart';

export 'shared/review_frame.dart' show ReviewOutcome;

/// What My Tasks needs to know about a task's screen before it opens it: what
/// to call the button, what to say under it, and how to build the body.
class TaskReviewScreen {
  /// The button in the review pane. Names the moment, not the widget — a
  /// person opening this is being called into a piece of work.
  final String label;

  /// What the full screen is *for*. Not one sentence for all of them: most of
  /// these screens exist so a person can correct the facts, and two of them
  /// have no facts to correct.
  final String hint;

  final Widget Function(ApiClient api, QueueTask task) build;

  const TaskReviewScreen({
    required this.label,
    required this.hint,
    required this.build,
  });
}

// ---------------------------------------------------------------------------
// screen_id → widget
// ---------------------------------------------------------------------------

/// The half of the registry that cannot be served: a constructor is code.
///
/// Every id the server can name has to appear here, and an id that does not is
/// a console older than the deployment it is talking to. That case degrades to
/// the generic approve/reject pane rather than guessing with a neighbouring
/// widget: the manifest is right that this task needs a screen this build does
/// not have, and showing a different one would answer the wrong question with
/// a clerk's signature on it.
///
/// Ids name screens, not versions. accounts-payable v7 reads v6's screens, and
/// an id carrying a version number would make one screen look like two.
const Map<String, Widget Function(ApiClient api, QueueTask task)>
taskScreenFactories = {
  'accounts-payable/extraction-review': _extractionReview,
  'accounts-payable/three-way-match': _verifyThreeWayMatch,
  'accounts-payable/invoice-review': _invoiceReview,
  'expense-reimbursement/expense-review': _expenseReview,
  'expense-reimbursement/confirm-values': _confirmValues,
  'field-documents/gap-review': _gapReview,
  'compliance/exception-review': _exceptionReview,
  // The plant's extraction review, not accounts-payable's. Two flows read two
  // documents and both call the moment an extraction review; the ids are what
  // keep them apart, and the id is keyed by the flow rather than by the widget
  // for exactly this reason.
  'plant-schedule/extraction-review': _plantExtractionReview,
  'plant-schedule/day-approval': _dayApproval,
  'shared/entity-match-review': _entityMatchReview,
  'shared/merge-review': _mergeReview,
  'shared/disagreement-review': _disagreementReview,
};

Widget _extractionReview(ApiClient api, QueueTask task) =>
    ExtractionReview(api: api, task: task);

Widget _verifyThreeWayMatch(ApiClient api, QueueTask task) =>
    VerifyThreeWayMatch(api: api, task: task);

Widget _invoiceReview(ApiClient api, QueueTask task) =>
    InvoiceReview(api: api, task: task);

Widget _expenseReview(ApiClient api, QueueTask task) =>
    ExpenseReview(api: api, task: task);

Widget _confirmValues(ApiClient api, QueueTask task) =>
    ConfirmValues(api: api, task: task);

Widget _gapReview(ApiClient api, QueueTask task) =>
    GapReview(api: api, task: task);

Widget _exceptionReview(ApiClient api, QueueTask task) =>
    ExceptionReview(api: api, task: task);

Widget _plantExtractionReview(ApiClient api, QueueTask task) =>
    plant_extraction.ExtractionReview(api: api, task: task);

Widget _dayApproval(ApiClient api, QueueTask task) =>
    DayApproval(api: api, task: task);

Widget _entityMatchReview(ApiClient api, QueueTask task) =>
    EntityMatchReview(task: task);

Widget _mergeReview(ApiClient api, QueueTask task) =>
    MergeReview(api: api, task: task);

Widget _disagreementReview(ApiClient api, QueueTask task) =>
    DisagreementReview(api: api, task: task);

// ---------------------------------------------------------------------------
// The manifest, fetched once
// ---------------------------------------------------------------------------

/// The server's account of the wiring, held for the life of the process.
///
/// Cached rather than fetched per task because [hasReviewScreen] and its three
/// siblings are called from `tasks_view.dart`'s `build`. A build method cannot
/// await, and making the whole review pane asynchronous to learn a button's
/// label would be a far larger change than the drift this lane is closing.
///
/// Static for the same reason `ApiClient.persona` is: the answer belongs to
/// the deployment, not to a screen's client, and a copy per screen is how two
/// panes start disagreeing about what a task opens.
class TaskScreens {
  TaskScreens._();

  static TaskScreenManifest? _manifest;

  /// Null until [load] lands, and after a load that failed. Every caller
  /// treats null as "ask what is compiled in" rather than as an error, so a
  /// console talking to an unreachable or older server still opens screens.
  static TaskScreenManifest? get manifest => _manifest;

  @visibleForTesting
  static set manifest(TaskScreenManifest? value) => _manifest = value;

  /// Fetched after sign-in — the console door needs a human session, so there
  /// is no earlier moment — and not refetched on a persona switch: which
  /// screens exist is a property of the deployment, identical for everyone.
  ///
  /// A failure leaves [manifest] null, and every task then opens the generic
  /// approve/reject pane over its own evidence rows. That is a real loss — the
  /// bespoke screens are how a person corrects facts — and it is still the
  /// honest degrade: the compiled table that used to cover for this could only
  /// answer with this build's belief about a deployment it had just failed to
  /// reach. The failure is not turned into a banner, because a console
  /// reporting its own plumbing over a working queue is noise; what a person
  /// sees is a pane that offers less, not a screen that claims more.
  static Future<void> load(ApiClient api) async {
    try {
      _manifest = await api.taskScreens();
    } on ApiException {
      // Left as it was: a stale manifest is a better answer than none.
    }
  }
}

// ---------------------------------------------------------------------------
// The chain
// ---------------------------------------------------------------------------

/// The screen a task opens, or null for the generic approve/reject pane.
///
/// Two rungs now, and the order is still the whole design:
///
/// 1. **The manifest's kind-keyed screens.** A `merge_review` raised by
///    accounts-payable is still a merge review; if the flow were consulted
///    first, a steward judging two records would land on an invoice.
/// 2. **The manifest's flow entry**, already resolved to the task's version by
///    the side that knows which versions shipped.
///
/// Below both is the generic pane — and there is nothing else below them any
/// more. Two rungs used to sit between these, holding the console's own copy
/// of the labels, the hints, and the "newest package at or below the task's
/// version" rule; they went when the fleet stopped talking to servers that do
/// not serve the manifest. What they defended against was a manifest that
/// never loaded, and what they cost was a second set of strings a person reads
/// that could be corrected on one side only, plus the standing possibility of a
/// console asserting a version arrangement its server does not have. The
/// generic pane is the honest answer to a fetch that failed: an approve/reject
/// over the task's own evidence rows, rather than a bespoke screen drawn from
/// this build's guess about a deployment it could not reach.
///
/// A manifest entry naming a `screen_id` this build does not have also lands on
/// the pane, and that is a degrade rather than a fall-through on purpose: the
/// manifest is right about what this task needs, and opening a neighbouring
/// widget would answer a different question with a clerk's signature on it.
TaskReviewScreen? resolveReviewScreen(
  QueueTask task, {
  TaskScreenManifest? manifest,
}) {
  // Each `return` below is a rung that answered. Answering with null is how a
  // rung says "no screen here, use the generic pane" — distinct from saying
  // nothing at all, which is expressed by falling through to the next rung.
  final served = manifest?.shared[task.kind];
  if (served != null) return _fromSpec(served);

  final entry = manifest?.entryFor(task.flowId, task.flowVersion);
  if (entry == null) return null;
  // A flow the manifest knows has answered, even when the answer is "no
  // bespoke screen for this kind".
  final spec = entry.screenFor(task.kind);
  return spec == null ? null : _fromSpec(spec);
}

/// Null when this build has no widget under that id — a console older than
/// the deployment it is talking to, which the caller turns into the generic
/// pane rather than into a guess.
TaskReviewScreen? _fromSpec(TaskScreenSpec spec) {
  final build = taskScreenFactories[spec.screenId];
  if (build == null) return null;
  return TaskReviewScreen(label: spec.label, hint: spec.hint, build: build);
}

TaskReviewScreen? _screenFor(QueueTask task) =>
    resolveReviewScreen(task, manifest: TaskScreens.manifest);

// ---------------------------------------------------------------------------
// What My Tasks asks
// ---------------------------------------------------------------------------

/// Whether this task has a bespoke review screen. Everything else gets the
/// pane's approve/reject, which is the honest affordance when there is nothing
/// structured to edit.
bool hasReviewScreen(QueueTask task) => _screenFor(task) != null;

/// The label on the button that opens it. The generic string is never
/// rendered: the pane only draws the button when [hasReviewScreen] is true.
String reviewLabel(QueueTask task) =>
    _screenFor(task)?.label ?? 'Open the review';

/// What the screen is for, or null when there is no screen to describe.
String? reviewHint(QueueTask task) => _screenFor(task)?.hint;

/// Opens the right screen for a task, or answers null when it has none (a
/// plain approve/reject is then the honest affordance).
Future<ReviewOutcome?> openReviewScreen(
  BuildContext context,
  ApiClient api,
  QueueTask task,
) {
  final screen = _screenFor(task);
  if (screen == null) return Future.value(null);
  return showDialog<ReviewOutcome>(
    context: context,
    barrierColor: CumulusColors.ink.withValues(alpha: 0.24),
    builder: (context) => Dialog(
      backgroundColor: CumulusColors.canvas,
      insetPadding: const EdgeInsets.symmetric(horizontal: 40, vertical: 32),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(CumulusRadius.card),
      ),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 1080, maxHeight: 900),
        child: screen.build(api, task),
      ),
    ),
  );
}
