import 'package:cumulus_admin/models/flow.dart';
import 'package:cumulus_admin/models/run.dart';
import 'package:cumulus_admin/widgets/flow_canvas.dart';
import 'package:flutter_test/flutter_test.dart';

/// The canvas trace, against the ids a real journal actually carries.
///
/// Worth having because the failure it guards is silent: a run whose steps do
/// not resolve to nodes paints an empty path rather than an error, which is
/// how the loop-suffix mismatch survived unnoticed.
void main() {
  // The accounts-payable definition, trimmed to the steps the trace crosses.
  final apSteps = [
    _step('t1', 'trigger', ['s1']),
    _step('s1', 'action', ['m1']),
    _step('m1', 'modelcall', ['x5']),
    _step('x5', 'transform', ['r2']),
    _step('r2', 'rule', ['a1', 'k2']),
    _step('k2', 'task', ['a1']),
    _step('a1', 'action', ['e1']),
    _step('e1', 'event', []),
  ];

  group('RunPath.canvasNode', () {
    final ids = {for (final s in apSteps) s.id};

    test('trims the round suffix a loop mints', () {
      expect(RunPath.canvasNode('r2_1', ids), 'r2');
      expect(RunPath.canvasNode('k2_2', ids), 'k2');
    });

    test('a declared id wins over any suffix reading', () {
      expect(RunPath.canvasNode('t1', ids), 't1');
      expect(RunPath.canvasNode('x_1', {'x_1', 'x'}), 'x_1');
    });

    test('a step with no node keeps its own id and matches nothing', () {
      // The engine runs steps the declarative export does not show.
      expect(RunPath.canvasNode('x6_1', ids), 'x6_1');
      expect(RunPath.canvasNode('z1', ids), 'z1');
    });
  });

  group('RunPath.fromRun', () {
    test('traces rule → task → action through a resumed run', () {
      final path = RunPath.fromRun(
        _run('run_ap_seed07', [
          _entry('t1'), _entry('s1'), _entry('m1'), _entry('x5'),
          _entry('r2_1'), _entry('k2_1'), _entry('a1'), _entry('e1'),
        ]),
        apSteps,
      );
      expect(path.executed('r2'), isTrue);
      expect(path.executed('k2'), isTrue);
      expect(path.takenEdges, contains(RunPath.edgeKey('r2', 'k2')));
      expect(path.takenEdges, contains(RunPath.edgeKey('k2', 'a1')));
      // The rule branched to the gate, so its other arm was never traversed.
      expect(path.takenEdges, isNot(contains(RunPath.edgeKey('r2', 'a1'))));
    });

    test('a suspended gate reads as waiting, and the rest is not taken', () {
      final path = RunPath.fromRun(
        _run('run_ap_seed06', [
          _entry('t1'), _entry('r2_1'), _entry('k2_1', status: 'suspended'),
        ]),
        apSteps,
      );
      expect(path.statusByStep['k2'], 'suspended');
      expect(path.executed('a1'), isFalse);
    });

    test('the last round of a looped node is the one painted', () {
      final path = RunPath.fromRun(
        _run('run_ap_seed02', [
          _entry('r2_1'), _entry('k2_1'), _entry('x6_1'),
          _entry('r2_2'), _entry('a1'),
        ]),
        apSteps,
      );
      // The second pass of the rule sent the invoice to the Action; an
      // undecided first round must not leave the gate looking open.
      expect(path.statusByStep['r2'], 'done');
      expect(path.takenEdges, contains(RunPath.edgeKey('r2', 'a1')));
    });
  });
}

FlowStep _step(String id, String type, List<String> next) => FlowStep.fromJson({
  'id': id, 'type': type, 'name': id, 'summary': '', 'next': next,
});

RunStep _entry(String stepId, {String status = 'done'}) =>
    RunStep.fromJson({'step_id': stepId, 'status': status});

FlowRun _run(String id, List<RunStep> steps) => FlowRun.fromJson({
  'id': id,
  'steps': [
    for (final s in steps) {'step_id': s.stepId, 'status': s.status},
  ],
});
