import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/models/flow.dart';
import 'package:cumulus_admin/models/task.dart';

/// Two new fields on the wire, and what the console does when they are absent.
///
/// Both are being added to a server that already has records in it, so both
/// have to survive reading a document written before they existed. What the
/// defaults are is a judgment, not a formality:
///
/// - **`flow_version` defaults to 0**, which is not a version — it is the
///   absence of one, and the registry reads it as "no claim" and falls back to
///   its newest package. A default of, say, the current version would be the
///   console inventing a fact about a run it did not watch.
/// - **`shipped` defaults to false**, which is the safe reading of silence
///   about a deployment. True would offer a Make-live button for a version
///   nothing can execute — a console lying about the estate in the one place
///   where the answer changes what the fleet runs.
void main() {
  group('QueueTask carries the version that raised it', () {
    test('flow_version is read off the wire', () {
      final task = QueueTask.fromJson({
        'id': 'task_ap_1',
        'flow_id': 'accounts-payable',
        'flow_version': 6,
      });
      expect(task.flowVersion, 6);
    });

    test('a record written before the field reads as 0', () {
      final task = QueueTask.fromJson({
        'id': 'task_ap_0',
        'flow_id': 'accounts-payable',
      });
      expect(task.flowVersion, 0);
    });

    test('a locally built task claims no version either', () {
      const task = QueueTask(
        id: 'task_local',
        runId: '',
        kind: 'decision',
        title: '',
        summary: '',
        queue: '',
        priority: 'normal',
        status: 'open',
        flowId: 'accounts-payable',
        caseRef: '',
        refs: [],
        evidence: [],
        proposal: null,
        resumes: '',
        createdAtIso: '',
        decision: null,
      );
      expect(task.flowVersion, 0);
    });

    test('a number arriving as a double still lands as an int', () {
      // JSON has one number type, and a server that computes the version
      // rather than storing it can hand back 6.0.
      expect(
        QueueTask.fromJson({'id': 'x', 'flow_version': 6.0}).flowVersion,
        6,
      );
    });
  });

  group('FlowVersion says whether the deployment can run it', () {
    test('shipped is read off the wire', () {
      final version = FlowVersion.fromJson({
        'version': 6,
        'status': 'archived',
        'shipped': true,
      });
      expect(version.shipped, isTrue);
      // Shipped and not the one running: exactly the state Make-live exists
      // for.
      expect(version.canActivate, isTrue);
    });

    test('a version the server says nothing about is not shipped', () {
      final version = FlowVersion.fromJson({'version': 5, 'status': 'archived'});
      expect(version.shipped, isFalse);
      expect(version.canActivate, isFalse);
    });

    test('the version already live is not offered for activation', () {
      final version = FlowVersion.fromJson({
        'version': 6,
        'status': 'active',
        'shipped': true,
      });
      expect(version.isActive, isTrue);
      expect(version.canActivate, isFalse);
    });

    test('a draft the code does not ship cannot be made live', () {
      final version = FlowVersion.fromJson({'version': 7, 'status': 'draft'});
      expect(version.isDraft, isTrue);
      expect(version.canActivate, isFalse);
    });
  });
}
