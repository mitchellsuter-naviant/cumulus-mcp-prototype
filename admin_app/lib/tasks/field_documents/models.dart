/// The Field Documents workspace — **packets, not folders**.
///
/// Mirrors `FieldDocumentsScreen` and its parts in `server/app/ui_api.py`.
///
/// Nothing in this file describes a directory, and that is the whole design of
/// the surface it feeds. A packet is `Job —documented_by→ Document` at the
/// latest revision of each governed type; a crew move is an edge whose
/// validity ended and which is *still on the record*; a superseded revision is
/// a row that kept its filename and its date. No folder is created, copied,
/// moved or deleted anywhere in this system, and a screen that drew one would
/// be promising an act nothing performs.
library;

import '../shared/governed_clause.dart';

/// The crew a job is assigned to now.
class FieldCrewRef {
  final String teamSlug;
  final String name;
  final String foreman;

  const FieldCrewRef({
    required this.teamSlug,
    required this.name,
    required this.foreman,
  });

  factory FieldCrewRef.fromJson(Map<String, dynamic> json) => FieldCrewRef(
    teamSlug: json['team_slug'] as String? ?? '',
    name: json['name'] as String? ?? '',
    foreman: json['foreman'] as String? ?? '',
  );
}

/// A crew that held the job before. The edge ended; nothing was deleted.
class FormerCrew {
  final String teamSlug;
  final String name;

  /// When the assignment's validity closed.
  final String untilIso;

  const FormerCrew({
    required this.teamSlug,
    required this.name,
    required this.untilIso,
  });

  factory FormerCrew.fromJson(Map<String, dynamic> json) => FormerCrew(
    teamSlug: json['team_slug'] as String? ?? '',
    name: json['name'] as String? ?? '',
    untilIso: json['until_iso'] as String? ?? '',
  );
}

/// One revision of one governed document type.
class PacketDocument {
  /// The governed type — the edge's own `document_type`, not a guess made
  /// from the filename.
  final String kind;
  final String filename;
  final String version;
  final String filedAt;

  /// True for a revision the packet has moved past: its edge is withdrawn and
  /// its row is kept, because a crew that already printed it should be able to
  /// find out what they are holding.
  final bool superseded;

  const PacketDocument({
    required this.kind,
    required this.filename,
    required this.version,
    required this.filedAt,
    required this.superseded,
  });

  factory PacketDocument.fromJson(Map<String, dynamic> json) => PacketDocument(
    kind: json['kind'] as String? ?? '',
    filename: json['filename'] as String? ?? '',
    version: json['version'] as String? ?? '',
    filedAt: json['filed_at'] as String? ?? '',
    superseded: json['superseded'] as bool? ?? false,
  );
}

class FieldJobRow {
  final String jobNumber;
  final String packetLabel;

  /// The convention rule that derived [packetLabel], read off the claim's own
  /// note. The screen shows the rule the run applied rather than describing
  /// the convention a second time in Dart.
  final String labelRule;
  final String customer;
  final String builder;
  final String plantSite;
  final String status;
  final String loadDate;
  final FieldCrewRef? currentCrew;
  final List<FormerCrew> formerCrews;

  /// Set when the job names a crew that resolves to no team. The identity
  /// question, kept apart from the document one: no document question was
  /// asked about this job.
  final String unresolvedCrewId;
  final List<PacketDocument> documents;
  final List<String> missing;
  final bool complete;

  /// How many consecutive windows found none of the governed types.
  final int emptyWindows;
  final String lastRunId;
  final String lastRunAtIso;
  final String objectRef;

  const FieldJobRow({
    required this.jobNumber,
    required this.packetLabel,
    required this.labelRule,
    required this.customer,
    required this.builder,
    required this.plantSite,
    required this.status,
    required this.loadDate,
    required this.currentCrew,
    required this.formerCrews,
    required this.unresolvedCrewId,
    required this.documents,
    required this.missing,
    required this.complete,
    required this.emptyWindows,
    required this.lastRunId,
    required this.lastRunAtIso,
    required this.objectRef,
  });

  /// The revisions this packet currently stands on.
  List<PacketDocument> get current =>
      documents.where((d) => !d.superseded).toList();

  /// What a revision replaced — kept, never removed.
  List<PacketDocument> get replaced =>
      documents.where((d) => d.superseded).toList();

  /// Some of the governed types but not all. Informational at every window
  /// and never a Task on its own: plans and a site map but no crew hours
  /// sheet is a normal Tuesday.
  bool get partial => current.isNotEmpty && missing.isNotEmpty;

  /// None of the governed types at all — the case the recurrence threshold
  /// counts.
  bool get empty => current.isEmpty;

  factory FieldJobRow.fromJson(Map<String, dynamic> json) => FieldJobRow(
    jobNumber: json['job_number'] as String? ?? '',
    packetLabel: json['packet_label'] as String? ?? '',
    labelRule: json['label_rule'] as String? ?? '',
    customer: json['customer'] as String? ?? '',
    builder: json['builder'] as String? ?? '',
    plantSite: json['plant_site'] as String? ?? '',
    status: json['status'] as String? ?? '',
    loadDate: json['load_date'] as String? ?? '',
    currentCrew: json['current_crew'] == null
        ? null
        : FieldCrewRef.fromJson(
            (json['current_crew'] as Map).cast<String, dynamic>(),
          ),
    formerCrews: [
      for (final row in (json['former_crews'] as List<dynamic>? ?? const []))
        FormerCrew.fromJson((row as Map).cast<String, dynamic>()),
    ],
    unresolvedCrewId: json['unresolved_crew_id'] as String? ?? '',
    documents: [
      for (final row in (json['documents'] as List<dynamic>? ?? const []))
        PacketDocument.fromJson((row as Map).cast<String, dynamic>()),
    ],
    missing: [
      for (final row in (json['missing'] as List<dynamic>? ?? const []))
        '$row',
    ],
    complete: json['complete'] as bool? ?? false,
    emptyWindows: json['empty_windows'] as int? ?? 0,
    lastRunId: json['last_run_id'] as String? ?? '',
    lastRunAtIso: json['last_run_at_iso'] as String? ?? '',
    objectRef: json['object_ref'] as String? ?? '',
  );
}

class FieldCrewSummary {
  final String teamSlug;
  final String name;
  final String foreman;
  final int jobs;
  final int complete;

  const FieldCrewSummary({
    required this.teamSlug,
    required this.name,
    required this.foreman,
    required this.jobs,
    required this.complete,
  });

  factory FieldCrewSummary.fromJson(Map<String, dynamic> json) =>
      FieldCrewSummary(
        teamSlug: json['team_slug'] as String? ?? '',
        name: json['name'] as String? ?? '',
        foreman: json['foreman'] as String? ?? '',
        jobs: json['jobs'] as int? ?? 0,
        complete: json['complete'] as int? ?? 0,
      );
}

class FieldDocumentsStats {
  final int jobs;
  final int inHorizon;
  final int packetsComplete;
  final int missing;
  final int unresolvedCrews;

  const FieldDocumentsStats({
    required this.jobs,
    required this.inHorizon,
    required this.packetsComplete,
    required this.missing,
    required this.unresolvedCrews,
  });

  factory FieldDocumentsStats.fromJson(Map<String, dynamic> json) =>
      FieldDocumentsStats(
        jobs: json['jobs'] as int? ?? 0,
        inHorizon: json['in_horizon'] as int? ?? 0,
        packetsComplete: json['packets_complete'] as int? ?? 0,
        missing: json['missing'] as int? ?? 0,
        unresolvedCrews: json['unresolved_crews'] as int? ?? 0,
      );
}

class FieldDocumentsScreen {
  final List<FieldJobRow> jobs;
  final List<FieldCrewSummary> crews;
  final FieldDocumentsStats stats;
  final List<GovernedClause> policyClauses;

  /// The open aggregate Tasks the flow raised, so the screen can send somebody
  /// to the question rather than describing it.
  final List<String> gapTaskIds;
  final String policyRef;

  /// False when the corpus holds no such page and the built-in default is
  /// standing in — said out loud, because a screen drawn from a default
  /// nobody wrote is not showing a customer their own policy.
  final bool policyFound;

  const FieldDocumentsScreen({
    required this.jobs,
    required this.crews,
    required this.stats,
    required this.policyClauses,
    required this.gapTaskIds,
    required this.policyRef,
    required this.policyFound,
  });

  factory FieldDocumentsScreen.fromJson(Map<String, dynamic> json) =>
      FieldDocumentsScreen(
        jobs: [
          for (final row in (json['jobs'] as List<dynamic>? ?? const []))
            FieldJobRow.fromJson((row as Map).cast<String, dynamic>()),
        ],
        crews: [
          for (final row in (json['crews'] as List<dynamic>? ?? const []))
            FieldCrewSummary.fromJson((row as Map).cast<String, dynamic>()),
        ],
        stats: FieldDocumentsStats.fromJson(
          (json['stats'] as Map? ?? const {}).cast<String, dynamic>(),
        ),
        policyClauses: GovernedClause.listFrom(json['policy_clauses']),
        gapTaskIds: [
          for (final row
              in (json['gap_task_ids'] as List<dynamic>? ?? const []))
            '$row',
        ],
        policyRef: json['policy_ref'] as String? ?? '',
        policyFound: json['policy_found'] as bool? ?? false,
      );
}
