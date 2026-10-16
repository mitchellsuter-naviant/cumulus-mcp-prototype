/// The Compliance workspace — credentials and inspections, by days to expiry.
///
/// Mirrors `ComplianceScreen` and its parts in `server/app/ui_api.py`.
///
/// Two distinctions this file exists to keep. **An untracked asset is not an
/// overdue one**: a unit the register holds no next-due date for is excluded
/// at the *unit*, and it has its own list here rather than a row in the table
/// with an empty date, because a sweep that reported it as out of inspection
/// would chase it forever. And **`null` days-to-expiry is not zero**: a date
/// nobody could read is the first exception class, not a record that expired
/// today.
library;

import '../shared/governed_clause.dart';

class ComplianceRow {
  /// `credential` | `inspection`.
  final String kind;

  /// Whose permission this is — a person for a credential, a unit for an
  /// inspection.
  final String subject;

  /// `employee` | `equipment`.
  final String subjectKind;
  final String credentialType;
  final String category;

  /// The date as the issuing system wrote it, verbatim — including a date
  /// nothing could parse, which is the point of keeping the text.
  final String expiresOn;

  /// Null when the date could not be read. Never rounded to zero.
  final int? daysToExpiry;
  final String status;
  final String lastNotifiedOn;

  /// The narrowest governed stage already sent, or null for none.
  final int? lastNoticeStage;

  /// Every stage at or wider than [lastNoticeStage] — derived server-side
  /// from the one stamp on the row, so the screen cannot show a notice trail
  /// the record does not carry.
  final List<int> stagesSent;
  final String supervisor;

  /// The exception class this row fell into, or empty. One class per row.
  final String exception;
  final String retainUntil;
  final String objectRef;

  const ComplianceRow({
    required this.kind,
    required this.subject,
    required this.subjectKind,
    required this.credentialType,
    required this.category,
    required this.expiresOn,
    required this.daysToExpiry,
    required this.status,
    required this.lastNotifiedOn,
    required this.lastNoticeStage,
    required this.stagesSent,
    required this.supervisor,
    required this.exception,
    required this.retainUntil,
    required this.objectRef,
  });

  bool get dateReadable => daysToExpiry != null;
  bool get expired => daysToExpiry != null && daysToExpiry! < 0;

  factory ComplianceRow.fromJson(Map<String, dynamic> json) => ComplianceRow(
    kind: json['kind'] as String? ?? '',
    subject: json['subject'] as String? ?? '',
    subjectKind: json['subject_kind'] as String? ?? '',
    credentialType: json['credential_type'] as String? ?? '',
    category: json['category'] as String? ?? '',
    expiresOn: json['expires_on'] as String? ?? '',
    daysToExpiry: (json['days_to_expiry'] as num?)?.toInt(),
    status: json['status'] as String? ?? '',
    lastNotifiedOn: json['last_notified_on'] as String? ?? '',
    lastNoticeStage: (json['last_notice_stage'] as num?)?.toInt(),
    stagesSent: [
      for (final row in (json['stages_sent'] as List<dynamic>? ?? const []))
        (row as num).toInt(),
    ],
    supervisor: json['supervisor'] as String? ?? '',
    exception: json['exception'] as String? ?? '',
    retainUntil: json['retain_until'] as String? ?? '',
    objectRef: json['object_ref'] as String? ?? '',
  );
}

/// A unit the register holds no next-due date for.
class UntrackedAsset {
  final String equipmentId;
  final String name;
  final String category;
  final String homeSite;

  /// The governed page's own sentence for why it is untracked.
  final String why;
  final String objectRef;

  const UntrackedAsset({
    required this.equipmentId,
    required this.name,
    required this.category,
    required this.homeSite,
    required this.why,
    required this.objectRef,
  });

  factory UntrackedAsset.fromJson(Map<String, dynamic> json) => UntrackedAsset(
    equipmentId: json['equipment_id'] as String? ?? '',
    name: json['name'] as String? ?? '',
    category: json['category'] as String? ?? '',
    homeSite: json['home_site'] as String? ?? '',
    why: json['why'] as String? ?? '',
    objectRef: json['object_ref'] as String? ?? '',
  );
}

class ComplianceStats {
  final int rows;
  final int expiring60;
  final int expiring30;
  final int expiring15;
  final int expired;
  final int untracked;
  final Map<String, int> exceptionsByClass;
  final int retentionFlagged;

  const ComplianceStats({
    required this.rows,
    required this.expiring60,
    required this.expiring30,
    required this.expiring15,
    required this.expired,
    required this.untracked,
    required this.exceptionsByClass,
    required this.retentionFlagged,
  });

  factory ComplianceStats.fromJson(Map<String, dynamic> json) => ComplianceStats(
    rows: json['rows'] as int? ?? 0,
    expiring60: json['expiring_60'] as int? ?? 0,
    expiring30: json['expiring_30'] as int? ?? 0,
    expiring15: json['expiring_15'] as int? ?? 0,
    expired: json['expired'] as int? ?? 0,
    untracked: json['untracked'] as int? ?? 0,
    exceptionsByClass: {
      for (final entry
          in (json['exceptions_by_class'] as Map? ?? const {}).entries)
        '${entry.key}': (entry.value as num?)?.toInt() ?? 0,
    },
    retentionFlagged: json['retention_flagged'] as int? ?? 0,
  );
}

class ComplianceScreen {
  /// Ordered by days to expiry ascending, with unreadable dates last — the
  /// order the sweep itself works in.
  final List<ComplianceRow> rows;
  final List<UntrackedAsset> untrackedAssets;
  final ComplianceStats stats;
  final List<GovernedClause> policyClauses;
  final List<String> exceptionTaskIds;

  /// The date a record older than this is retained past. Nothing is deleted.
  final String retentionHorizon;
  final String policyRef;
  final bool policyFound;

  const ComplianceScreen({
    required this.rows,
    required this.untrackedAssets,
    required this.stats,
    required this.policyClauses,
    required this.exceptionTaskIds,
    required this.retentionHorizon,
    required this.policyRef,
    required this.policyFound,
  });

  factory ComplianceScreen.fromJson(Map<String, dynamic> json) =>
      ComplianceScreen(
        rows: [
          for (final row in (json['rows'] as List<dynamic>? ?? const []))
            ComplianceRow.fromJson((row as Map).cast<String, dynamic>()),
        ],
        untrackedAssets: [
          for (final row
              in (json['untracked_assets'] as List<dynamic>? ?? const []))
            UntrackedAsset.fromJson((row as Map).cast<String, dynamic>()),
        ],
        stats: ComplianceStats.fromJson(
          (json['stats'] as Map? ?? const {}).cast<String, dynamic>(),
        ),
        policyClauses: GovernedClause.listFrom(json['policy_clauses']),
        exceptionTaskIds: [
          for (final row
              in (json['exception_task_ids'] as List<dynamic>? ?? const []))
            '$row',
        ],
        retentionHorizon: json['retention_horizon'] as String? ?? '',
        policyRef: json['policy_ref'] as String? ?? '',
        policyFound: json['policy_found'] as bool? ?? false,
      );
}
