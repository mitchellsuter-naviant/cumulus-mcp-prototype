/// The day-approval proposal contract, pinned against the server's own payload.
///
/// `TaskProposal.fields` is untyped *by contract*: the flow that raised the
/// Task is the only thing that interprets it, and the flows that use it put
/// genuinely different shapes there. Accounts payable puts per-field verdicts.
/// Expense-reimbursement v10 puts one row per document. Department scheduling
/// puts one row of a plant's day — a path, a job number and label, the hours,
/// the row kind, who placed it and on what basis.
///
/// That is a seam with no type behind it, which means nothing else in either
/// suite runs the two ends against each other: the server tests assert on
/// Python objects and the screen tests build Dart fixtures, so both can be
/// green while the JSON between them disagrees. The payload below is copied
/// verbatim from `server/app/tasks/department_scheduling/domain.py`'s
/// `build_approval_task`. If a change to either side breaks this test, that is
/// the seam breaking, and the fix is a conversation between both halves rather
/// than an edit to whichever one is closer to hand.
///
/// The property this file exists to hold is one sentence long: **the
/// day-approval screen reads `rawFields`, and reading `.fields` instead would
/// fail silently.** A scheduling row parsed as a [FieldVerdict] does not throw
/// — it produces a verdict with no path, no status and no reason, which the
/// verdict model reads as *unsettled and therefore worth showing*. A screen
/// wired to `.fields` would render a plausible, empty question over a real
/// day, and nothing anywhere would say so. So that failure mode is asserted
/// here rather than described in a comment.
library;

import 'dart:convert';

import 'package:cumulus_admin/models/task.dart';
import 'package:cumulus_admin/tasks/plant_schedule/v1/day_approval.dart';
import 'package:flutter_test/flutter_test.dart';

/// Verbatim from the server: an approval task's proposal carrying two rows —
/// one the fill placed, one a supervisor already placed by hand.
const String _serverProposal = '''
{"action": "mark-day-approved", "target": "production-records",
 "summary": "Approve truss-fab's day for 2026-09-08: 3 row(s), 28.5 h. ...",
 "fields": [
   {"path": "rows.260118", "job_number": "260118", "job_label": "260118 Evermore",
    "labor_hours": 12.5, "row_kind": "job", "placed_by": "flow",
    "basis": "truss formula at rev3"},
   {"path": "rows.260478", "job_number": "260478", "job_label": "260478 Willows",
    "labor_hours": 16.0, "row_kind": "job", "placed_by": "human",
    "basis": "placed by a supervisor"}]}
''';

TaskProposal _parse(String json) =>
    TaskProposal.fromJson(jsonDecode(json) as Map<String, dynamic>);

void main() {
  group('the server payload parses into what the screen reads', () {
    test('every key of every row survives the wire', () {
      final proposal = _parse(_serverProposal);

      expect(proposal.action, 'mark-day-approved');
      expect(proposal.target, 'production-records');
      expect(proposal.rawFields, hasLength(2));

      final first = proposal.rawFields![0];
      // Asserted key by key rather than by shape. Each of these is read by the
      // screen for a different reason — the path is the identity a correction
      // travels under, the hours are what the bar is drawn from, the basis is
      // the rule a supervisor argues with — so losing any one of them is a
      // different bug and should fail as one.
      expect(first['path'], 'rows.260118');
      expect(first['job_number'], '260118');
      expect(first['job_label'], '260118 Evermore');
      expect(first['labor_hours'], 12.5);
      expect(first['row_kind'], 'job');
      expect(first['placed_by'], 'flow');
      expect(first['basis'], 'truss formula at rev3');
    });

    test('the hours arrive as numbers the bar can add up', () {
      final rows = _parse(_serverProposal).rawFields!;
      final total = rows.fold<double>(
        0,
        (sum, row) => sum + (row['labor_hours'] as num).toDouble(),
      );
      // 12.5 + 16.0. The summary the flow wrote says 28.5 h for three rows;
      // the two rows pinned here are two of them, and the point of the
      // assertion is that the values arrive as numbers rather than as the
      // strings a laxer serializer would send.
      expect(total, closeTo(28.5, 1e-9));
      expect(rows[1]['labor_hours'], isA<num>());
    });

    test('a row the fill placed is distinguishable from one a person placed', () {
      final rows = _parse(_serverProposal).rawFields!;

      expect(rows[0]['placed_by'], 'flow');
      expect(rows[1]['placed_by'], 'human');
      // The whole reason the screen draws them differently. A row a supervisor
      // placed is a claim that outranks the fill and that no later run may
      // overwrite; a row the fill computed is a proposal. Drawing them the
      // same way would ask somebody to re-decide their own placement without
      // telling them it was theirs.
      expect(rows[0]['placed_by'], isNot(rows[1]['placed_by']));
    });
  });

  group('why the screen reads rawFields and not fields', () {
    test('a scheduling row read as a verdict is empty, not an error', () {
      final proposal = _parse(_serverProposal);

      // It parses. That is the problem: nothing throws, nothing logs, and the
      // screen gets two verdicts that carry none of what a verdict is for.
      expect(proposal.fields, hasLength(2));
      final asVerdict = proposal.fields![0];

      // The one field that does survive, and it is the one that makes the
      // failure convincing rather than obvious: `path` is spelled the same on
      // both shapes, so the verdict comes out carrying a real-looking fact
      // path. A screen wired to `.fields` would show a row keyed by
      // `rows.260118` — which reads exactly like a field it is entitled to
      // ask about.
      expect(asVerdict.path, 'rows.260118');

      // Everything a verdict is actually for is gone. There is no label, so
      // the screen would fall back to the path; no value, so the input would
      // open empty over a row that really does have 12.5 hours on it; and no
      // sentence, so the card would ask a question with no reason under it.
      expect(asVerdict.label, '');
      expect(asVerdict.value, '');
      expect(asVerdict.status, '');
      expect(asVerdict.reason, '');
      expect(asVerdict.region, isNull);
      expect(asVerdict.confidence, 0.0);

      // And an unstated status reads as unsettled, so every one of these would
      // be shown as a field needing a person — a full screen of questions with
      // nothing in them. This is the assertion that makes "read `rawFields`"
      // a property rather than a convention.
      expect(asVerdict.disputed, isTrue);
      expect(proposal.fields!.every((verdict) => verdict.disputed), isTrue);
    });

    test('rawFields keeps keys the verdict model has no room for', () {
      final proposal = _parse(_serverProposal);

      // `job_number`, `labor_hours`, `row_kind`, `placed_by` and `basis` have
      // no counterpart on FieldVerdict at all. Projecting through it is lossy
      // in a way no assertion on the projection could notice, which is why the
      // raw map is what the screen holds on to.
      expect(proposal.rawFields![1].keys, containsAll(<String>[
        'path', 'job_number', 'job_label', 'labor_hours', 'row_kind',
        'placed_by', 'basis',
      ]));
    });
  });

  group('the screen survives a payload the server has not promised', () {
    test('a sparse row defaults rather than throwing', () {
      // A task already raised, whose payload predates a field, must still open
      // a screen. A parse that threw here would take out the day rather than
      // one row — and the row is still identifiable, which is what matters.
      final proposal = _parse(
        '{"action": "mark-day-approved", "target": "production-records", '
        '"summary": "", "fields": [{"path": "rows.260900"}]}',
      );
      final row = proposal.rawFields!.single;
      expect(row['path'], 'rows.260900');
      expect(row['labor_hours'], isNull);
      expect(row['placed_by'], isNull);
      expect(row['basis'], isNull);
      // The screen's own defaulting is what turns those nulls into zero hours
      // and an unattributed placement; the model's job is to hand them over
      // rather than to refuse the task.
    });

    test('a row with no path is still carried, not dropped', () {
      // Nothing on this screen can address such a row in a correction, and
      // that is a reason to *show* it — a row on a plant's day that no
      // correction can reach is a fact a supervisor should be able to see
      // before signing, not one the parser quietly removes.
      final proposal = _parse(
        '{"action": "mark-day-approved", "target": "t", "summary": "", '
        '"fields": [{"job_number": "260900", "labor_hours": 4.0}]}',
      );
      expect(proposal.rawFields, hasLength(1));
      expect(proposal.rawFields!.single['job_number'], '260900');
    });
  });

  // The other half of the seam, and it is arithmetic rather than JSON. The
  // proposal does not carry the goal, the absolute max, the fudge or the
  // incentive figures — those live on the schedule cell — so the screen has to
  // find the cell from the case ref alone: split the day key, derive the ISO
  // week, ask for that week, take the cell out of it. Every step of that is a
  // place to be quietly wrong about which week a Tuesday is in, and being
  // wrong there does not fail: it fetches a real week that does not hold this
  // day, and the screen degrades as though the deployment were broken.
  group('the day key and the week the screen asks for', () {
    test('a day key splits into a department and a date', () {
      expect(dayKeyParts('truss-fab:2026-09-08'), ('truss-fab', '2026-09-08'));
    });

    test('a case ref that is not a day key yields nothing, not a guess', () {
      // The screen turns this into "no cell can be fetched" and says so, which
      // is why it must be distinguishable rather than defaulted.
      expect(dayKeyParts('nothing'), ('', ''));
      expect(dayKeyParts(''), ('', ''));
      expect(dayKeyParts(':2026-09-08'), ('', ''));
      expect(dayKeyParts('truss-fab:'), ('', ''));
    });

    test('the ISO week matches the server, including at the year boundary', () {
      // Every one of these is `datetime.date.fromisoformat(d).isocalendar()`
      // in Python, which is what `_iso_week` in
      // `server/app/tasks/department_scheduling/domain.py` calls. The boundary
      // cases are the point: a week numbered from January 1 gets the first
      // four rows wrong, and a week starting on Sunday gets every row wrong.
      expect(isoWeekForDate('2026-09-08'), '2026-W37');
      expect(isoWeekForDate('2026-09-07'), '2026-W37'); // Monday
      expect(isoWeekForDate('2026-09-13'), '2026-W37'); // Sunday
      expect(isoWeekForDate('2026-09-14'), '2026-W38');
      expect(isoWeekForDate('2026-01-01'), '2026-W01');
      expect(isoWeekForDate('2025-12-29'), '2026-W01'); // next year's week 1
      expect(isoWeekForDate('2026-12-31'), '2026-W53');
      expect(isoWeekForDate('2027-01-01'), '2026-W53'); // last year's week 53
      expect(isoWeekForDate('2024-12-30'), '2025-W01');
      expect(isoWeekForDate('2021-01-03'), '2020-W53');
      expect(isoWeekForDate('2021-01-04'), '2021-W01');
      expect(isoWeekForDate('2016-01-01'), '2015-W53');
      expect(isoWeekForDate('2000-01-01'), '1999-W52');
    });

    test('a date nothing can parse asks for no week at all', () {
      // Empty rather than a fabricated week: the screen reads this as "there
      // is no week to ask for" and degrades with a sentence, which is honest.
      // A guessed week would fetch somebody else's days.
      expect(isoWeekForDate('not-a-date'), '');
      expect(isoWeekForDate(''), '');
    });
  });

  group('absent and empty stay different facts', () {
    test('no fields key at all is null — the task carries no rows', () {
      final proposal = _parse(
        '{"action": "mark-day-approved", "target": "production-records", '
        '"summary": ""}',
      );
      expect(proposal.rawFields, isNull);
      expect(proposal.fields, isNull);
    });

    test('an empty list is a day the fill placed nothing on', () {
      // A real state, and a different one: the fill ran, found nothing
      // placeable, and asked anyway. Collapsing it into "no proposal rows"
      // would tell a supervisor the task was raised without a proposal, which
      // is a different failure with a different answer.
      final proposal = _parse(
        '{"action": "mark-day-approved", "target": "production-records", '
        '"summary": "", "fields": []}',
      );
      expect(proposal.rawFields, isNotNull);
      expect(proposal.rawFields, isEmpty);
      expect(proposal.fields, isNotNull);
      expect(proposal.fields, isEmpty);
    });
  });
}
