/// The server's account of which review screen answers which task.
///
/// The console used to hold this itself, as a table compiled into the binary
/// beside its own copy of the "newest package at or below the task's version"
/// rule. That let a console be shipped believing in a version arrangement the
/// server does not have — a screen registered at a version the fleet never
/// deployed — and let the version rule be corrected on one side only.
///
/// So the server resolves, and this is what arrives: one entry per version it
/// actually shipped, already resolved, plus the entry a version it never
/// shipped falls back to. There is no version arithmetic on this side, which
/// is the point — the rule now exists once, where the shipped set is a fact.
///
/// What cannot arrive is the widget. A constructor is code, so the manifest
/// carries a [TaskScreenSpec.screenId] the console looks up in a compile-time
/// map, and an id this build does not know is a degrade rather than a blank.
library;

/// One review screen: what to build, what to call it, what it is for.
class TaskScreenSpec {
  /// The compile-time key for a widget. Names the screen, not the version that
  /// declared it — accounts-payable v7 reads v6's screen, and an id carrying a
  /// version number would make one screen look like two.
  final String screenId;

  /// The button in the review pane.
  final String label;

  /// What the full screen is *for*, shown under the button.
  final String hint;

  const TaskScreenSpec({
    required this.screenId,
    required this.label,
    required this.hint,
  });

  factory TaskScreenSpec.fromJson(Map<String, dynamic> json) => TaskScreenSpec(
    screenId: json['screen_id'] as String? ?? '',
    label: json['label'] as String? ?? '',
    hint: json['hint'] as String? ?? '',
  );
}

/// One version's screens: the kinds that get their own, and the rest.
class TaskUiEntry {
  final Map<String, TaskScreenSpec> byKind;

  /// The screen for every kind [byKind] does not name, or null when this
  /// version answers only the kinds it listed. Null is a real answer: it says
  /// the generic approve/reject pane, not a screen built for another question.
  final TaskScreenSpec? otherwise;

  const TaskUiEntry({this.byKind = const {}, this.otherwise});

  TaskScreenSpec? screenFor(String kind) => byKind[kind] ?? otherwise;

  factory TaskUiEntry.fromJson(Map<String, dynamic> json) => TaskUiEntry(
    byKind: ((json['by_kind'] as Map?) ?? const {}).map(
      (key, value) => MapEntry(
        key as String,
        TaskScreenSpec.fromJson((value as Map).cast<String, dynamic>()),
      ),
    ),
    otherwise: json['otherwise'] == null
        ? null
        : TaskScreenSpec.fromJson(
            (json['otherwise'] as Map).cast<String, dynamic>(),
          ),
  );
}

/// One flow's screens, by the version that raised the task.
class FlowScreens {
  final Map<int, TaskUiEntry> versions;

  /// What a version this deployment never shipped resolves to — the newest
  /// declaration. Covers a task carrying version 0 (an older record, or a
  /// locally built double) and one raised by a version since retired.
  final TaskUiEntry? fallback;

  const FlowScreens({this.versions = const {}, this.fallback});

  /// Never null when the flow is in the manifest at all: falling off the
  /// version list is what [fallback] is for, and a clerk with a task from a
  /// retired version still has to be able to open it.
  TaskUiEntry? entryFor(int flowVersion) => versions[flowVersion] ?? fallback;

  factory FlowScreens.fromJson(Map<String, dynamic> json) => FlowScreens(
    versions: {
      for (final row in (json['versions'] as List? ?? const []))
        (row as Map)['version'] as int: TaskUiEntry.fromJson(
          (row['ui'] as Map).cast<String, dynamic>(),
        ),
    },
    fallback: json['fallback'] == null
        ? null
        : TaskUiEntry.fromJson(
            (json['fallback'] as Map).cast<String, dynamic>(),
          ),
  );
}

class TaskScreenManifest {
  /// Keyed by task kind, for the screens that belong to no flow. A
  /// `merge_review` raised by accounts-payable is still a merge review, so
  /// these are answered before the task's flow is consulted.
  final Map<String, TaskScreenSpec> shared;

  final Map<String, FlowScreens> flows;

  const TaskScreenManifest({this.shared = const {}, this.flows = const {}});

  /// Null when this manifest says nothing about the flow — which is different
  /// from saying the flow has no screen for this kind, and is the difference
  /// between falling back to what is compiled in and not.
  TaskUiEntry? entryFor(String flowId, int flowVersion) =>
      flows[flowId]?.entryFor(flowVersion);

  factory TaskScreenManifest.fromJson(Map<String, dynamic> json) =>
      TaskScreenManifest(
        shared: ((json['shared'] as Map?) ?? const {}).map(
          (key, value) => MapEntry(
            key as String,
            TaskScreenSpec.fromJson((value as Map).cast<String, dynamic>()),
          ),
        ),
        flows: {
          for (final row in (json['flows'] as List? ?? const []))
            (row as Map)['flow_id'] as String: FlowScreens.fromJson(
              row.cast<String, dynamic>(),
            ),
        },
      );
}
