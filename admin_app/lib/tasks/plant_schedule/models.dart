/// The Plant Schedule workspace — a department × day grid for one week.
///
/// Mirrors `PlantScheduleScreen` and its parts in `server/app/ui_api.py`.
///
/// **There is no `exportedAt`, deliberately.** An export is regenerated on
/// request and stored nowhere, so a "last handed out" date would be a record
/// of reads the graph does not keep. What a cell says instead is whether the
/// day is [exportable] — which is exactly `status == approved`, the same
/// answer `/api/v1/schedule/export` gives — and where the export is. A screen
/// that offered the control on an unapproved day would be offering what the
/// server answers 409 to.
library;

import '../shared/governed_clause.dart';

class ScheduleEntryRow {
  final String jobNumber;
  final String jobLabel;
  final double laborHours;

  /// Why the row is here and why for these hours — the rule that placed it,
  /// named so a supervisor can argue with it.
  final String basis;
  final double sequence;

  /// `job | nailing | girts | consolidated | placeholder` — not every row on
  /// a plant's day is a job.
  final String rowKind;
  final String vehicleRef;

  /// `flow` | `human`. A row a supervisor placed by hand and a row the fill
  /// computed are the same shape and must never be drawn the same way.
  final String placedBy;
  final String objectRef;

  const ScheduleEntryRow({
    required this.jobNumber,
    required this.jobLabel,
    required this.laborHours,
    required this.basis,
    required this.sequence,
    required this.rowKind,
    required this.vehicleRef,
    required this.placedBy,
    required this.objectRef,
  });

  bool get isHuman => placedBy == 'human';

  factory ScheduleEntryRow.fromJson(Map<String, dynamic> json) =>
      ScheduleEntryRow(
        jobNumber: json['job_number'] as String? ?? '',
        jobLabel: json['job_label'] as String? ?? '',
        laborHours: (json['labor_hours'] as num? ?? 0).toDouble(),
        basis: json['basis'] as String? ?? '',
        sequence: (json['sequence'] as num? ?? 0).toDouble(),
        rowKind: json['row_kind'] as String? ?? '',
        vehicleRef: json['vehicle_ref'] as String? ?? '',
        placedBy: json['placed_by'] as String? ?? 'flow',
        objectRef: json['object_ref'] as String? ?? '',
      );
}

/// A share of work the fill did not place, and the governed clause that
/// deferred it. Both the clause and the sentence, because "why is 260118 not
/// on Tuesday" has to answer as a clause and as English.
class DeferredRow {
  final String jobNumber;
  final String clause;
  final String reason;
  final double laborHours;

  const DeferredRow({
    required this.jobNumber,
    required this.clause,
    required this.reason,
    required this.laborHours,
  });

  factory DeferredRow.fromJson(Map<String, dynamic> json) => DeferredRow(
    jobNumber: json['job_number'] as String? ?? '',
    clause: json['clause'] as String? ?? '',
    reason: json['reason'] as String? ?? '',
    laborHours: (json['labor_hours'] as num? ?? 0).toDouble(),
  );
}

class ScheduleDayCell {
  final String department;
  final String date;

  /// `proposed | pending_approval | approved | rejected`, or empty when no
  /// window has opened this day.
  final String status;

  /// False when no `ScheduleDay` exists. A department with no schedule and a
  /// department scheduled for nothing are opposite facts, and a blank cell
  /// reads as the second.
  final bool exists;
  final double totalHours;
  final double? goalMaxHours;
  final double? absMaxHours;
  final double fudgeHours;

  /// The capacity row the fill chose, with the crew size beside it — the
  /// first number a supervisor will dispute.
  final String capacityRow;

  final double incentivePerHour;

  /// `$start + $rate/h × hours = $answer`, as the flow wrote it.
  final String incentiveBasis;
  final double? incentiveStartUsd;
  final double? incentiveRateUsdPerHour;

  /// Where the governed figures cross zero — the plant's "out of the hole"
  /// floor, computed rather than read off a fourth column.
  final double? incentiveCrossingHours;
  final bool outOfTheHole;
  final bool belowCapacity;

  /// The governed clause the fill stopped against.
  final String stoppedAt;
  final double headcount;
  final String headcountAsOf;
  final bool headcountStale;
  final List<ScheduleEntryRow> entries;
  final List<DeferredRow> deferred;
  final String approvedBy;
  final String approvedAt;
  final String pendingTaskId;
  final String runId;

  /// Live only on an approved day.
  final bool exportable;
  final String exportUrl;
  final String objectRef;

  const ScheduleDayCell({
    required this.department,
    required this.date,
    required this.status,
    required this.exists,
    required this.totalHours,
    required this.goalMaxHours,
    required this.absMaxHours,
    required this.fudgeHours,
    required this.capacityRow,
    required this.incentivePerHour,
    required this.incentiveBasis,
    required this.incentiveStartUsd,
    required this.incentiveRateUsdPerHour,
    required this.incentiveCrossingHours,
    required this.outOfTheHole,
    required this.belowCapacity,
    required this.stoppedAt,
    required this.headcount,
    required this.headcountAsOf,
    required this.headcountStale,
    required this.entries,
    required this.deferred,
    required this.approvedBy,
    required this.approvedAt,
    required this.pendingTaskId,
    required this.runId,
    required this.exportable,
    required this.exportUrl,
    required this.objectRef,
  });

  /// The most this day may hold: the smaller of the two governed bounds, plus
  /// the fudge. `null` when the page states no ceiling at all.
  double? get ceilingHours {
    final bounds = <double>[
      if (goalMaxHours != null) goalMaxHours! + fudgeHours,
      if (absMaxHours != null) absMaxHours! + fudgeHours,
    ];
    if (bounds.isEmpty) return null;
    return bounds.reduce((a, b) => a < b ? a : b);
  }

  /// How wide to draw the bar: the absolute max with its fudge where there is
  /// one, otherwise whatever the day actually holds. Never zero — a divisor
  /// of zero is how a bar chart draws a NaN.
  double get barScale {
    final candidates = <double>[
      if (absMaxHours != null) absMaxHours! + fudgeHours,
      if (goalMaxHours != null) goalMaxHours! + fudgeHours,
      totalHours,
    ];
    final top = candidates.isEmpty
        ? 0.0
        : candidates.reduce((a, b) => a > b ? a : b);
    return top <= 0 ? 1.0 : top;
  }

  /// Past the goal but inside the latitude the governed page allows. The page
  /// says exceeding the absolute max within the fudge is accepted and not an
  /// exception, so this is drawn as a band rather than as an alarm.
  bool get inFudgeBand =>
      goalMaxHours != null &&
      totalHours > goalMaxHours! &&
      totalHours <= goalMaxHours! + fudgeHours;

  /// Past every bound the page states, fudge included.
  bool get overCeiling {
    final ceiling = ceilingHours;
    return ceiling != null && totalHours > ceiling;
  }

  factory ScheduleDayCell.fromJson(Map<String, dynamic> json) =>
      ScheduleDayCell(
        department: json['department'] as String? ?? '',
        date: json['date'] as String? ?? '',
        status: json['status'] as String? ?? '',
        exists: json['exists'] as bool? ?? false,
        totalHours: (json['total_hours'] as num? ?? 0).toDouble(),
        goalMaxHours: (json['goal_max_hours'] as num?)?.toDouble(),
        absMaxHours: (json['abs_max_hours'] as num?)?.toDouble(),
        fudgeHours: (json['fudge_hours'] as num? ?? 0).toDouble(),
        capacityRow: json['capacity_row'] as String? ?? '',
        incentivePerHour: (json['incentive_per_hour'] as num? ?? 0).toDouble(),
        incentiveBasis: json['incentive_basis'] as String? ?? '',
        incentiveStartUsd: (json['incentive_start_usd'] as num?)?.toDouble(),
        incentiveRateUsdPerHour:
            (json['incentive_rate_usd_per_hour'] as num?)?.toDouble(),
        incentiveCrossingHours:
            (json['incentive_crossing_hours'] as num?)?.toDouble(),
        outOfTheHole: json['out_of_the_hole'] as bool? ?? false,
        belowCapacity: json['below_capacity'] as bool? ?? false,
        stoppedAt: json['stopped_at'] as String? ?? '',
        headcount: (json['headcount'] as num? ?? 0).toDouble(),
        headcountAsOf: json['headcount_as_of'] as String? ?? '',
        headcountStale: json['headcount_stale'] as bool? ?? false,
        entries: [
          for (final row in (json['entries'] as List<dynamic>? ?? const []))
            ScheduleEntryRow.fromJson((row as Map).cast<String, dynamic>()),
        ],
        deferred: [
          for (final row in (json['deferred'] as List<dynamic>? ?? const []))
            DeferredRow.fromJson((row as Map).cast<String, dynamic>()),
        ],
        approvedBy: json['approved_by'] as String? ?? '',
        approvedAt: json['approved_at'] as String? ?? '',
        pendingTaskId: json['pending_task_id'] as String? ?? '',
        runId: json['run_id'] as String? ?? '',
        exportable: json['exportable'] as bool? ?? false,
        exportUrl: json['export_url'] as String? ?? '',
        objectRef: json['object_ref'] as String? ?? '',
      );
}

class PlantScheduleStats {
  final int days;
  final int pendingApproval;
  final int approved;
  final int deferred;
  final int belowCapacity;

  const PlantScheduleStats({
    required this.days,
    required this.pendingApproval,
    required this.approved,
    required this.deferred,
    required this.belowCapacity,
  });

  factory PlantScheduleStats.fromJson(Map<String, dynamic> json) =>
      PlantScheduleStats(
        days: json['days'] as int? ?? 0,
        pendingApproval: json['pending_approval'] as int? ?? 0,
        approved: json['approved'] as int? ?? 0,
        deferred: json['deferred'] as int? ?? 0,
        belowCapacity: json['below_capacity'] as int? ?? 0,
      );
}

class PlantScheduleScreen {
  final String week;
  final List<String> departments;
  final List<String> dates;
  final List<ScheduleDayCell> cells;
  final PlantScheduleStats stats;
  final List<GovernedClause> policyClauses;
  final String policyRef;
  final bool policyFound;

  const PlantScheduleScreen({
    required this.week,
    required this.departments,
    required this.dates,
    required this.cells,
    required this.stats,
    required this.policyClauses,
    required this.policyRef,
    required this.policyFound,
  });

  /// The cell at one intersection of the grid, or null when the week holds
  /// none — which the server only answers for a department it does not draw.
  ScheduleDayCell? cellFor(String department, String date) {
    for (final cell in cells) {
      if (cell.department == department && cell.date == date) return cell;
    }
    return null;
  }

  factory PlantScheduleScreen.fromJson(Map<String, dynamic> json) =>
      PlantScheduleScreen(
        week: json['week'] as String? ?? '',
        departments: [
          for (final row in (json['departments'] as List<dynamic>? ?? const []))
            '$row',
        ],
        dates: [
          for (final row in (json['dates'] as List<dynamic>? ?? const []))
            '$row',
        ],
        cells: [
          for (final row in (json['cells'] as List<dynamic>? ?? const []))
            ScheduleDayCell.fromJson((row as Map).cast<String, dynamic>()),
        ],
        stats: PlantScheduleStats.fromJson(
          (json['stats'] as Map? ?? const {}).cast<String, dynamic>(),
        ),
        policyClauses: GovernedClause.listFrom(json['policy_clauses']),
        policyRef: json['policy_ref'] as String? ?? '',
        policyFound: json['policy_found'] as bool? ?? false,
      );
}
