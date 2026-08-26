import 'package:flutter/material.dart';

import '../models/flow.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'ui_kit.dart';

/// The review screen: the fixture document on the left with its grounded
/// field regions, the case's labels on the right to confirm or correct.
///
/// One surface, three jobs: working a live HITL Task, capturing corrections
/// (which feed the correction set), and onboarding a new flow by labelling
/// its first documents — every confirmation here refines a golden case.
///
/// Returns the updated golden set when the reviewer saved, null otherwise.
Future<GoldenSetInfo?> showCaseReview(
  BuildContext context, {
  required ApiClient api,
  required String flowId,
  required GoldenCase goldenCase,
  required bool canEdit,
}) {
  return showDialog<GoldenSetInfo>(
    context: context,
    builder: (context) => Dialog(
      insetPadding: const EdgeInsets.all(28),
      backgroundColor: CumulusColors.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(CumulusRadius.card),
      ),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 1220, maxHeight: 780),
        child: _CaseReview(
          api: api,
          flowId: flowId,
          goldenCase: goldenCase,
          canEdit: canEdit,
        ),
      ),
    ),
  );
}

class _CaseReview extends StatefulWidget {
  final ApiClient api;
  final String flowId;
  final GoldenCase goldenCase;
  final bool canEdit;

  const _CaseReview({
    required this.api,
    required this.flowId,
    required this.goldenCase,
    required this.canEdit,
  });

  @override
  State<_CaseReview> createState() => _CaseReviewState();
}

class _CaseReviewState extends State<_CaseReview> {
  CaseDocument? _doc;
  String? _error;
  String? _selectedPath;
  bool _saving = false;

  /// One controller per fact assertion, seeded with the current label. A
  /// changed value is a correction; unchanged values are confirmations.
  late final Map<String, TextEditingController> _fields = {
    for (final a in widget.goldenCase.assertions)
      a.path: TextEditingController(text: a.expected),
  };

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    for (final c in _fields.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final doc = await widget.api.goldenCaseDocument(
        widget.flowId,
        widget.goldenCase.id,
      );
      if (!mounted) return;
      setState(() => _doc = doc);
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _error = e.message);
    }
  }

  Map<String, String> get _corrections => {
    for (final a in widget.goldenCase.assertions)
      if (_fields[a.path]!.text.trim() != a.expected)
        a.path: _fields[a.path]!.text.trim(),
  };

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      final updated = await widget.api.reviewGoldenCase(
        widget.flowId,
        widget.goldenCase.id,
        corrections: _corrections,
      );
      if (!mounted) return;
      Navigator.of(context).pop(updated);
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _saving = false);
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(e.message)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final kase = widget.goldenCase;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(22, 16, 12, 14),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Wrap(
                      crossAxisAlignment: WrapCrossAlignment.center,
                      spacing: 8,
                      children: [
                        Text(
                          kase.name,
                          style: const TextStyle(
                            fontSize: 15,
                            fontWeight: FontWeight.w700,
                            color: CumulusColors.ink,
                          ),
                        ),
                        StatusBadge(kase.source, dense: true),
                        if (kase.reviewedBy.isNotEmpty)
                          StatusBadge(
                            'reviewed · ${kase.reviewedBy}',
                            tone: CumulusTone.success,
                            dense: true,
                          ),
                      ],
                    ),
                    const SizedBox(height: 3),
                    Text(
                      '${kase.id}'
                      '${kase.documentRef.isEmpty ? '' : ' · ${kase.documentRef}'}'
                      '${kase.runId.isEmpty ? '' : ' · from ${kase.runId}'}',
                      style: CumulusText.meta,
                    ),
                  ],
                ),
              ),
              IconButton(
                tooltip: 'Close',
                icon: const Icon(Icons.close, size: 18),
                onPressed: () => Navigator.of(context).pop(),
              ),
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Expanded(flex: 11, child: _documentPane()),
              const VerticalDivider(width: 1, color: CumulusColors.border),
              SizedBox(width: 420, child: _labelsPane()),
            ],
          ),
        ),
      ],
    );
  }

  // ---- left: the document with its grounded regions -----------------------

  Widget _documentPane() {
    if (_error != null) {
      return Center(child: ErrorNotice(message: _error!, onRetry: _load));
    }
    final doc = _doc;
    if (doc == null) return const CumulusLoading();
    return Container(
      color: CumulusColors.canvas,
      padding: const EdgeInsets.all(26),
      child: Center(
        child: AspectRatio(
          aspectRatio: 8.5 / 11,
          child: LayoutBuilder(
            builder: (context, constraints) {
              final w = constraints.maxWidth;
              final h = constraints.maxHeight;
              // Line coordinates are normalized against a US-letter page;
              // font sizes assume its 612pt width and scale with it.
              final fontScale = w / 612;
              return Container(
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(3),
                  boxShadow: CumulusShadows.card,
                ),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(3),
                  child: Stack(
                    children: [
                      for (final line in doc.lines)
                        Positioned(
                          left: line.x * w,
                          top: line.y * h,
                          child: Text(
                            line.text,
                            style:
                                (line.mono
                                        ? CumulusText.mono
                                        : const TextStyle())
                                    .copyWith(
                                      fontSize: line.size * 1.55 * fontScale,
                                      fontWeight: line.bold
                                          ? FontWeight.w700
                                          : FontWeight.w400,
                                      color: line.muted
                                          ? const Color(0xFF8B8F98)
                                          : const Color(0xFF1E2126),
                                      height: 1.0,
                                    ),
                          ),
                        ),
                      for (final region in doc.regions)
                        _regionBox(region, w, h),
                    ],
                  ),
                ),
              );
            },
          ),
        ),
      ),
    );
  }

  Widget _regionBox(DocRegion region, double w, double h) {
    final selected = region.path == _selectedPath;
    final original = widget.goldenCase.assertions
        .where((a) => a.path == region.path)
        .firstOrNull;
    final corrected =
        original != null &&
        _fields[region.path] != null &&
        _fields[region.path]!.text.trim() != original.expected;
    final color = corrected
        ? CumulusColors.warn
        : selected
        ? CumulusColors.accent
        : CumulusColors.accent.withValues(alpha: 0.45);
    return Positioned(
      left: region.box[0] * w,
      top: region.box[1] * h,
      width: region.box[2] * w,
      height: region.box[3] * h,
      child: Tooltip(
        message:
            '${region.path}\n'
            'captured "${region.value}" · '
            'confidence ${(region.confidence * 100).round()}%',
        waitDuration: const Duration(milliseconds: 300),
        child: GestureDetector(
          onTap: () => setState(
            () => _selectedPath = selected ? null : region.path,
          ),
          child: MouseRegion(
            cursor: SystemMouseCursors.click,
            child: Container(
              decoration: BoxDecoration(
                color: color.withValues(alpha: selected ? 0.16 : 0.07),
                border: Border.all(color: color, width: selected ? 2 : 1.2),
                borderRadius: BorderRadius.circular(3),
              ),
            ),
          ),
        ),
      ),
    );
  }

  // ---- right: labels to confirm or correct --------------------------------

  Widget _labelsPane() {
    final kase = widget.goldenCase;
    final grounded = {for (final r in _doc?.regions ?? <DocRegion>[]) r.path};
    final corrections = _corrections;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(20, 16, 20, 16),
            children: [
              Text('FACTS', style: CumulusText.sectionTitle),
              const SizedBox(height: 4),
              Text(
                'labels key to the flow\'s contract — they survive any '
                'reshaping of the steps underneath',
                style: CumulusText.meta,
              ),
              const SizedBox(height: 10),
              if (kase.assertions.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  child: Text(
                    'This case has no fact labels yet — it grades on its '
                    'trajectory alone.',
                    style: CumulusText.meta,
                  ),
                ),
              for (final a in kase.assertions) _fieldRow(a, grounded),
              if (kase.trajectory.isNotEmpty) ...[
                const SizedBox(height: 16),
                Text('TRAJECTORY', style: CumulusText.sectionTitle),
                const SizedBox(height: 8),
                for (final t in kase.trajectory)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 7),
                    child: Row(
                      children: [
                        const Icon(
                          Icons.route_outlined,
                          size: 14,
                          color: CumulusColors.inkMuted,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            '${t.label}: ${t.expected}',
                            style: const TextStyle(
                              fontSize: 12,
                              color: CumulusColors.inkSecondary,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
              if (kase.readSet.isNotEmpty) ...[
                const SizedBox(height: 16),
                Text('PINNED READS', style: CumulusText.sectionTitle),
                const SizedBox(height: 4),
                Text(
                  'the world this case was captured in — served back during '
                  'a pinned experiment so the grade is reproducible',
                  style: CumulusText.meta,
                ),
                const SizedBox(height: 8),
                for (final r in kase.readSet)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 7),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          r.ref,
                          style: CumulusText.mono.copyWith(fontSize: 11.5),
                        ),
                        if (r.summary.isNotEmpty)
                          Text(r.summary, style: CumulusText.meta),
                      ],
                    ),
                  ),
              ],
            ],
          ),
        ),
        const Divider(height: 1),
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 12, 20, 14),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  corrections.isEmpty
                      ? 'Saving confirms the labels as they stand.'
                      : '${corrections.length} correction'
                            '${corrections.length == 1 ? '' : 's'} — appended '
                            'to the correction set, dataset version moves.',
                  style: CumulusText.meta,
                ),
              ),
              const SizedBox(width: 12),
              FilledButton(
                onPressed: (widget.canEdit && !_saving) ? _save : null,
                child: Text(
                  _saving
                      ? 'Saving…'
                      : corrections.isEmpty
                      ? 'Confirm labels'
                      : 'Save corrections',
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _fieldRow(FactAssertion a, Set<String> grounded) {
    final controller = _fields[a.path]!;
    final selected = a.path == _selectedPath;
    final corrected = controller.text.trim() != a.expected;
    return GestureDetector(
      onTap: () => setState(() => _selectedPath = a.path),
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.fromLTRB(12, 9, 12, 10),
        decoration: BoxDecoration(
          color: selected
              ? CumulusColors.surfaceMuted
              : CumulusColors.surface,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          border: Border.all(
            color: corrected
                ? CumulusColors.warn
                : selected
                ? CumulusColors.accent
                : CumulusColors.border,
            width: selected || corrected ? 1.5 : 1,
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    a.path,
                    style: CumulusText.mono.copyWith(
                      fontSize: 11,
                      color: CumulusColors.inkSecondary,
                    ),
                  ),
                ),
                if (grounded.contains(a.path))
                  const Tooltip(
                    message: 'Grounded — click to see where on the page',
                    child: Icon(
                      Icons.center_focus_strong_outlined,
                      size: 13,
                      color: CumulusColors.inkMuted,
                    ),
                  ),
                if (a.op != 'equals') ...[
                  const SizedBox(width: 6),
                  StatusBadge(a.op, dense: true, tone: CumulusTone.violet),
                ],
                if (corrected) ...[
                  const SizedBox(width: 6),
                  const StatusBadge(
                    'corrected',
                    dense: true,
                    tone: CumulusTone.warn,
                  ),
                ],
              ],
            ),
            const SizedBox(height: 6),
            TextField(
              controller: controller,
              enabled: widget.canEdit,
              onChanged: (_) => setState(() {}),
              onTap: () => setState(() => _selectedPath = a.path),
              style: CumulusText.mono.copyWith(fontSize: 12.5),
              decoration: const InputDecoration(
                isDense: true,
                contentPadding: EdgeInsets.symmetric(
                  horizontal: 8,
                  vertical: 8,
                ),
              ),
            ),
            if (a.rubric.isNotEmpty) ...[
              const SizedBox(height: 5),
              Text('judge rubric: ${a.rubric}', style: CumulusText.meta),
            ],
          ],
        ),
      ),
    );
  }
}
