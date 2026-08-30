import 'dart:convert';
import '../models/graph.dart';

import 'package:desktop_drop/desktop_drop.dart';
import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';

import '../models/employee.dart';
import '../models/reimbursement.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import '../widgets/ui_kit.dart';

/// The Employee Hub — the one-stop personal page: PTO balance and requests,
/// benefits, expense submission, and the HR Agent.
///
/// A section inside the shell rather than a pushed route, so the rail, the
/// command palette, and the task-queue badge stay live while you are in it —
/// asking the HR Agent about your PTO should not mean leaving the console.
/// It is entered from the account menu rather than the nav rail because it is
/// about *you*, not about a workstream, which is also why the rail shows no
/// selection here and the account button carries the active ring instead.
///
/// Everything here runs on the governed machinery underneath: a PTO request
/// over the policy window raises a Task to the HR queue; a submitted receipt
/// runs the live expense-reimbursement flow; the HR Agent answers from the
/// people/ taxonomy of the knowledge layer with citations.
class EmployeeHubSection extends StatefulWidget {
  /// The shell's client — the shell owns its lifecycle, so this section
  /// never closes it.
  final ApiClient api;

  const EmployeeHubSection({super.key, required this.api});

  @override
  State<EmployeeHubSection> createState() => _EmployeeHubSectionState();
}

class _EmployeeHubSectionState extends State<EmployeeHubSection> {
  ExpenseRollup? _rollup;
  ApiClient get _api => widget.api;
  EmployeeProfile? _profile;
  List<PtoRequest>? _pto;
  List<Reimbursement>? _mine;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final profile = await _api.employeeProfile();
      final pto = await _api.listPtoRequests();
      final all = await _api.listReimbursements(mine: true);
      // The traversal, not a search: Employee --submitted--> ExpenseRequest
      // --has_expense_line--> ExpenseLine, summed. It is the question the
      // graph exists to make answerable, and until now nothing asked it.
      ExpenseRollup? rollup;
      try {
        rollup = await _api.employeeExpenses(profile.email);
      } on ApiException {
        // A persona outside the org chart has nothing to traverse from.
      }
      if (mounted) {
        setState(() {
          _profile = profile;
          _pto = pto;
          _mine = all;
          _rollup = rollup;
          _error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(28, 20, 28, 0),
          child: PageHeading(
            eyebrow: 'Employee hub · yours',
            // A principal outside the org chart has no name to show, so the
            // heading falls back to the address rather than rendering blank.
            title: _hubTitle(),
            subtitle:
                'Time off, expenses, and benefits — plus the HR Agent, which '
                'answers from the people policies in the knowledge layer.',
            trailing: OutlinedButton.icon(
              onPressed: _load,
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('Refresh'),
            ),
          ),
        ),
        Expanded(child: _body()),
      ],
    );
  }

  Widget _body() {
    if (_error != null) {
      return CumulusEmptyState(
        icon: Icons.cloud_off,
        title: 'Could not load your hub',
        message: _error!,
      );
    }
    if (_profile == null) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2.5));
    }
    return LayoutBuilder(
      builder: (context, constraints) {
        // The rail and the section padding eat into the width the standalone
        // page had, so the two-column split turns on a little earlier.
        final wide = constraints.maxWidth >= 1000;
        final left = _leftColumn();
        final chat = _HrChatPanel(api: _api);
        if (!wide) {
          return SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(28, 16, 28, 40),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                left,
                const SizedBox(height: 16),
                SizedBox(height: 560, child: chat),
              ],
            ),
          );
        }
        return Padding(
          padding: const EdgeInsets.fromLTRB(28, 16, 28, 24),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(flex: 3, child: SingleChildScrollView(child: left)),
              const SizedBox(width: 20),
              Expanded(flex: 2, child: chat),
            ],
          ),
        );
      },
    );
  }

  String _hubTitle() {
    final profile = _profile;
    if (profile == null) return 'Your hub';
    if (profile.name.isNotEmpty) return profile.name;
    return profile.email.isEmpty ? 'Your hub' : profile.email;
  }

  Widget _leftColumn() {
    final profile = _profile!;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _WhoThisIs(profile: profile),
        if (_rollup != null) ...[
          const SizedBox(height: 14),
          _SpendRollup(rollup: _rollup!),
        ],
        const SizedBox(height: 14),
        StatStrip(
          tiles: [
            StatTile(
              label: 'PTO balance',
              value: '${profile.ptoBalanceDays}',
              caption: 'days · +${profile.ptoAccrualPerMonth}/month',
            ),
            StatTile(
              label: 'Pending requests',
              value: '${(_pto ?? []).where((r) => r.state == 'pending').length}',
              caption: 'with HR',
            ),
            StatTile(
              label: 'Next holiday',
              value: profile.nextCompanyHoliday.split('·').last.trim(),
              caption: profile.nextCompanyHoliday.split('·').first.trim(),
            ),
          ],
        ),
        const SizedBox(height: 16),
        _PtoCard(api: _api, onChanged: _load, requests: _pto ?? const []),
        const SizedBox(height: 16),
        _ExpenseCard(api: _api, onChanged: _load, mine: _mine ?? const []),
        const SizedBox(height: 16),
        SectionCard(
          title: 'Benefits',
          subtitle: 'what you are enrolled in',
          child: Column(
            children: [
              for (final b in profile.benefits)
                Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Monogram(b.kind.substring(0, 1).toUpperCase()),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Text(b.name, style: CumulusText.rowTitle),
                                const SizedBox(width: 8),
                                StatusBadge(
                                  b.status,
                                  tone: CumulusTone.success,
                                  dense: true,
                                ),
                              ],
                            ),
                            const SizedBox(height: 2),
                            Text(b.summary, style: CumulusText.body),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

// ---- PTO ---------------------------------------------------------------

class _PtoCard extends StatefulWidget {
  final ApiClient api;
  final VoidCallback onChanged;
  final List<PtoRequest> requests;

  const _PtoCard({
    required this.api,
    required this.onChanged,
    required this.requests,
  });

  @override
  State<_PtoCard> createState() => _PtoCardState();
}

class _PtoCardState extends State<_PtoCard> {
  DateTimeRange? _range;
  final _note = TextEditingController();
  bool _busy = false;
  String? _message;

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  double get _days {
    final range = _range;
    if (range == null) return 0;
    // Working days, weekend-aware — the honest count for a PTO request.
    var days = 0;
    for (var d = range.start;
        !d.isAfter(range.end);
        d = d.add(const Duration(days: 1))) {
      if (d.weekday <= 5) days++;
    }
    return days.toDouble();
  }

  Future<void> _pickRange() async {
    final now = DateTime.now();
    final picked = await showDateRangePicker(
      context: context,
      firstDate: now,
      lastDate: now.add(const Duration(days: 365)),
      initialDateRange: _range,
    );
    if (picked != null) setState(() => _range = picked);
  }

  Future<void> _submit() async {
    final range = _range;
    if (range == null) return;
    setState(() {
      _busy = true;
      _message = null;
    });
    String iso(DateTime d) => d.toIso8601String().substring(0, 10);
    try {
      final request = await widget.api.requestPto(
        startDate: iso(range.start),
        endDate: iso(range.end),
        days: _days,
        note: _note.text.trim(),
      );
      setState(() {
        _busy = false;
        _range = null;
        _note.clear();
        _message = switch (request.state) {
          'approved' => 'Approved — ${request.reason}.',
          'pending' => 'Sent to HR — ${request.reason}.',
          _ => 'Declined — ${request.reason}.',
        };
      });
      widget.onChanged();
    } on ApiException catch (e) {
      setState(() {
        _busy = false;
        _message = e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return SectionCard(
      title: 'Time off',
      subtitle:
          'requests of 3 working days or fewer auto-approve — governed by the PTO policy',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              OutlinedButton.icon(
                onPressed: _busy ? null : _pickRange,
                icon: const Icon(Icons.event, size: 16),
                label: Text(
                  _range == null
                      ? 'Pick dates'
                      : '${_range!.start.toIso8601String().substring(0, 10)} → '
                            '${_range!.end.toIso8601String().substring(0, 10)}'
                            ' · ${_days.toStringAsFixed(0)}d',
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: TextField(
                  controller: _note,
                  enabled: !_busy,
                  decoration: const InputDecoration(
                    labelText: 'Note (coverage, reason)',
                    isDense: true,
                  ),
                ),
              ),
              const SizedBox(width: 10),
              FilledButton(
                onPressed: _busy || _range == null ? null : _submit,
                child: _busy
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Request'),
              ),
            ],
          ),
          if (_message != null) ...[
            const SizedBox(height: 10),
            Text(_message!, style: CumulusText.body),
          ],
          if (widget.requests.isNotEmpty) ...[
            const SizedBox(height: 14),
            const FieldLabel('Your requests'),
            for (final r in widget.requests.take(5))
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  children: [
                    StatusBadge(
                      r.state,
                      tone: switch (r.state) {
                        'approved' => CumulusTone.success,
                        'pending' => CumulusTone.warn,
                        _ => CumulusTone.danger,
                      },
                      dense: true,
                    ),
                    const SizedBox(width: 10),
                    Text(
                      '${r.startDate} → ${r.endDate} · ${r.days.toStringAsFixed(0)}d',
                      style: CumulusText.body,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        r.reason,
                        style: CumulusText.meta,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }
}

// ---- Expenses ------------------------------------------------------------

/// The expense submission form.
///
/// A submission is **N documents plus the submitter's own words**, and both
/// halves are load-bearing:
///
/// - Several receipts go in as one request, because a trip is one request. Each
///   carries its own note, because "only claiming $75 of the $110 — the rest
///   was mine" is about *that* receipt.
/// - The overall commentary is a governed input to the run: the projection
///   reads a stated claim out of a note, both are quoted in the run journal,
///   and a stated purpose is what keeps a line above the governed threshold
///   from needing a reviewer at all.
///
/// So the form's job is to make the notes feel worth writing rather than
/// optional. The hint text says what they do.
class _ExpenseCard extends StatefulWidget {
  final ApiClient api;
  final VoidCallback onChanged;
  final List<Reimbursement> mine;

  const _ExpenseCard({
    required this.api,
    required this.onChanged,
    required this.mine,
  });

  @override
  State<_ExpenseCard> createState() => _ExpenseCardState();
}

/// One attachment being composed, with its note.
class _Attachment {
  final XFile? file;
  final String name;
  final TextEditingController note = TextEditingController();

  _Attachment({this.file, required this.name});

  void dispose() => note.dispose();
}

class _ExpenseCardState extends State<_ExpenseCard> {
  final List<_Attachment> _attachments = [];
  final _commentary = TextEditingController();
  bool _busy = false;
  bool _dragging = false;
  String? _message;
  Reimbursement? _result;

  @override
  void dispose() {
    for (final attachment in _attachments) {
      attachment.dispose();
    }
    _commentary.dispose();
    super.dispose();
  }

  Future<void> _pick() async {
    final files = await openFiles(
      acceptedTypeGroups: const [
        XTypeGroup(
          label: 'receipts',
          extensions: ['png', 'jpg', 'jpeg', 'pdf'],
        ),
      ],
    );
    if (files.isEmpty) return;
    setState(() {
      for (final file in files) {
        _attachments.add(_Attachment(file: file, name: file.name));
      }
    });
  }

  void _addFromDrop(List<XFile> files) {
    setState(() {
      _dragging = false;
      for (final file in files) {
        _attachments.add(_Attachment(file: file, name: file.name));
      }
    });
  }

  void _remove(_Attachment attachment) {
    setState(() {
      _attachments.remove(attachment);
      attachment.dispose();
    });
  }

  bool get _canSubmit =>
      !_busy &&
      (_attachments.isNotEmpty || _commentary.text.trim().isNotEmpty);

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _message = null;
      _result = null;
    });
    try {
      final documents = <Map<String, String>>[];
      for (final attachment in _attachments) {
        final bytes = await attachment.file?.readAsBytes();
        documents.add({
          'content_base64': bytes == null ? '' : base64Encode(bytes),
          'filename': attachment.name,
          'note': attachment.note.text.trim(),
        });
      }
      final row = await widget.api.submitReimbursement(
        documents: documents,
        commentary: _commentary.text.trim(),
      );
      if (!mounted) return;
      setState(() {
        _busy = false;
        for (final attachment in _attachments) {
          attachment.dispose();
        }
        _attachments.clear();
        _commentary.clear();
        _result = row;
        _message = switch (row.state) {
          'approved' =>
            'Approved automatically — \$${row.requestedUsd.toStringAsFixed(2)} '
                'across ${row.lines.length} line(s). ${row.verdictReason}.',
          _ =>
            'Elevated for review — ${row.verdictReason}. It is in the '
                'expense-review queue, and the run is suspended until someone '
                'decides.',
        };
      });
      widget.onChanged();
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _message = e.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return SectionCard(
      title: 'Submit an expense',
      subtitle:
          'several receipts go in as one request · your notes are read by the '
          'flow, not filed beside it',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          DropTarget(
            onDragEntered: (_) => setState(() => _dragging = true),
            onDragExited: (_) => setState(() => _dragging = false),
            onDragDone: (detail) => _addFromDrop(detail.files),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 120),
              width: double.infinity,
              padding: const EdgeInsets.symmetric(vertical: 22, horizontal: 18),
              decoration: BoxDecoration(
                color: _dragging
                    ? CumulusColors.accentSoft
                    : CumulusColors.surfaceMuted.withValues(alpha: 0.6),
                borderRadius: BorderRadius.circular(CumulusRadius.control),
                border: Border.all(
                  color: _dragging
                      ? CumulusColors.accent
                      : CumulusColors.borderStrong,
                  width: _dragging ? 1.6 : 1,
                ),
              ),
              child: Column(
                children: [
                  Icon(
                    Icons.cloud_upload_outlined,
                    size: 22,
                    color: _dragging
                        ? CumulusColors.accent
                        : CumulusColors.inkMuted,
                  ),
                  const SizedBox(height: 8),
                  Text(
                    _dragging
                        ? 'Drop them here'
                        : 'Drag receipts here, or add them',
                    style: const TextStyle(
                      fontSize: 13.5,
                      fontWeight: FontWeight.w600,
                      color: CumulusColors.ink,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    'PNG, JPG or PDF · one request can carry several',
                    style: CumulusText.meta,
                  ),
                  const SizedBox(height: 12),
                  OutlinedButton.icon(
                    onPressed: _busy ? null : _pick,
                    icon: const Icon(Icons.add, size: 16),
                    label: const Text('Add receipts'),
                  ),
                ],
              ),
            ),
          ),
          if (_attachments.isNotEmpty) ...[
            const SizedBox(height: 14),
            const FieldLabel(
              'Attached receipts',
              hint:
                  'a note here can say what a receipt was for, or that you are '
                  'only claiming part of it',
            ),
            for (final attachment in _attachments)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Padding(
                      padding: EdgeInsets.only(top: 10),
                      child: Icon(
                        Icons.receipt_outlined,
                        size: 16,
                        color: CumulusColors.inkMuted,
                      ),
                    ),
                    const SizedBox(width: 8),
                    SizedBox(
                      width: 180,
                      child: Padding(
                        padding: const EdgeInsets.only(top: 9),
                        child: Text(
                          attachment.name,
                          style: CumulusText.body,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: TextField(
                        controller: attachment.note,
                        enabled: !_busy,
                        decoration: const InputDecoration(
                          hintText:
                              'e.g. dinner with the Acme team after the Phase 2 '
                              'review — only claiming \$75 of the \$110',
                          isDense: true,
                        ),
                      ),
                    ),
                    IconButton(
                      onPressed: _busy ? null : () => _remove(attachment),
                      icon: const Icon(Icons.close, size: 16),
                      tooltip: 'Remove',
                    ),
                  ],
                ),
              ),
          ],
          const SizedBox(height: 12),
          LabeledField(
            label: 'What was this for?',
            hint:
                'read by the flow — a stated purpose is what keeps a larger '
                'claim from needing a reviewer',
            child: TextField(
              controller: _commentary,
              enabled: !_busy,
              maxLines: 3,
              minLines: 2,
              onChanged: (_) => setState(() {}),
              decoration: const InputDecoration(
                hintText:
                    'Client visit to Acme — cab, lunch, and the team dinner '
                    'after the Phase 2 review.',
                isDense: true,
              ),
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              FilledButton.icon(
                onPressed: _canSubmit ? _submit : null,
                icon: _busy
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.send, size: 16),
                label: Text(
                  _attachments.length > 1
                      ? 'Submit ${_attachments.length} receipts'
                      : 'Submit',
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  'Submitting runs the flow now: extraction per document, the '
                  'governed policy read, then auto-approval or a review Task.',
                  style: CumulusText.meta,
                ),
              ),
            ],
          ),
          if (_message != null) ...[
            const SizedBox(height: 12),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
              decoration: BoxDecoration(
                color: (_result?.state == 'approved'
                        ? CumulusTone.success
                        : CumulusTone.warn)
                    .background,
                borderRadius: BorderRadius.circular(CumulusRadius.control),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(_message!, style: CumulusText.body),
                  if (_result != null && _result!.lines.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    for (final line in _result!.lines)
                      Text(
                        '· line ${line.ordinal}: ${line.vendor} · '
                        '${line.category} · claiming '
                        '\$${line.requestedUsd.toStringAsFixed(2)}'
                        '${line.isSplit ? ' of \$${line.documentTotalUsd.toStringAsFixed(2)} on the receipt' : ''}',
                        style: CumulusText.meta,
                      ),
                  ],
                  if (_result != null && _result!.runId.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Text(_result!.runId, style: CumulusText.mono),
                  ],
                ],
              ),
            ),
          ],
          if (widget.mine.isNotEmpty) ...[
            const SizedBox(height: 16),
            const FieldLabel('Your recent requests'),
            for (final r in widget.mine.take(5))
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  children: [
                    StatusBadge(
                      r.state,
                      tone: switch (r.state) {
                        'approved' => CumulusTone.success,
                        'pending' => CumulusTone.warn,
                        _ => CumulusTone.danger,
                      },
                      dense: true,
                    ),
                    const SizedBox(width: 10),
                    Text(
                      '${r.vendor} · \$${r.requestedUsd.toStringAsFixed(2)}'
                      '${r.lines.length > 1 ? ' · ${r.lines.length} lines' : ''}',
                      style: CumulusText.body,
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        r.verdictReason,
                        style: CumulusText.meta,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }
}

// ---- The HR Agent ----------------------------------------------------------

class _ChatMessage {
  final bool fromAgent;
  final String text;
  final List<HrCitation> citations;

  const _ChatMessage(this.fromAgent, this.text, [this.citations = const []]);
}

class _HrChatPanel extends StatefulWidget {
  final ApiClient api;

  const _HrChatPanel({required this.api});

  @override
  State<_HrChatPanel> createState() => _HrChatPanelState();
}

class _HrChatPanelState extends State<_HrChatPanel> {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  final List<_ChatMessage> _messages = [
    const _ChatMessage(
      true,
      'Hi — I\'m the HR Agent. I answer from the PTO policy, the benefits '
      'guide, and your own records, with citations. Try “how much PTO do I '
      'have?” or “what\'s the 401(k) match?”',
    ),
  ];
  bool _busy = false;

  @override
  void dispose() {
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final text = _input.text.trim();
    if (text.isEmpty || _busy) return;
    setState(() {
      _messages.add(_ChatMessage(false, text));
      _input.clear();
      _busy = true;
    });
    _autoScroll();
    try {
      final reply = await widget.api.hrChat(text);
      if (mounted) {
        setState(() {
          _messages.add(_ChatMessage(true, reply.reply, reply.citations));
          _busy = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _messages.add(_ChatMessage(true, 'Something went wrong: ${e.message}'));
          _busy = false;
        });
      }
    }
    _autoScroll();
  }

  void _autoScroll() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return CumulusCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Container(
            padding: const EdgeInsets.fromLTRB(18, 14, 18, 12),
            decoration: const BoxDecoration(
              border: Border(bottom: BorderSide(color: CumulusColors.border)),
            ),
            child: Row(
              children: [
                Container(
                  width: 30,
                  height: 30,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: CumulusColors.accentSoft,
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: const Icon(
                    Icons.support_agent,
                    size: 17,
                    color: CumulusColors.accentInk,
                  ),
                ),
                const SizedBox(width: 10),
                const Text('HR Agent', style: CumulusText.rowTitle),
                const SizedBox(width: 8),
                Text('grounded · cited', style: CumulusText.meta),
              ],
            ),
          ),
          Expanded(
            child: ListView.builder(
              controller: _scroll,
              padding: const EdgeInsets.all(16),
              itemCount: _messages.length + (_busy ? 1 : 0),
              itemBuilder: (context, i) {
                if (i == _messages.length) {
                  return const Padding(
                    padding: EdgeInsets.all(8),
                    child: Align(
                      alignment: Alignment.centerLeft,
                      child: SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                    ),
                  );
                }
                final m = _messages[i];
                return Align(
                  alignment: m.fromAgent
                      ? Alignment.centerLeft
                      : Alignment.centerRight,
                  child: Container(
                    margin: const EdgeInsets.only(bottom: 10),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 14,
                      vertical: 10,
                    ),
                    constraints: const BoxConstraints(maxWidth: 420),
                    decoration: BoxDecoration(
                      color: m.fromAgent
                          ? CumulusColors.surfaceMuted
                          : CumulusColors.ink,
                      borderRadius: BorderRadius.circular(14),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        SelectableText(
                          m.text,
                          style: CumulusText.body.copyWith(
                            color: m.fromAgent
                                ? CumulusColors.ink
                                : Colors.white,
                          ),
                        ),
                        if (m.citations.isNotEmpty) ...[
                          const SizedBox(height: 8),
                          Wrap(
                            spacing: 6,
                            runSpacing: 6,
                            children: [
                              for (final c in m.citations)
                                Container(
                                  padding: const EdgeInsets.symmetric(
                                    horizontal: 8,
                                    vertical: 3,
                                  ),
                                  decoration: BoxDecoration(
                                    color: CumulusColors.surface,
                                    borderRadius: BorderRadius.circular(6),
                                    border: Border.all(
                                      color: CumulusColors.border,
                                    ),
                                  ),
                                  child: Text(
                                    c.title.isEmpty ? c.id : c.title,
                                    style: CumulusText.mono.copyWith(
                                      fontSize: 10.5,
                                    ),
                                  ),
                                ),
                            ],
                          ),
                        ],
                      ],
                    ),
                  ),
                );
              },
            ),
          ),
          Container(
            padding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
            decoration: const BoxDecoration(
              border: Border(top: BorderSide(color: CumulusColors.border)),
            ),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _input,
                    enabled: !_busy,
                    onSubmitted: (_) => _send(),
                    decoration: const InputDecoration(
                      hintText: 'Ask about PTO, benefits, policies…',
                      isDense: true,
                      border: InputBorder.none,
                    ),
                  ),
                ),
                IconButton(
                  tooltip: 'Send',
                  onPressed: _busy ? null : _send,
                  icon: const Icon(Icons.send, size: 18),
                  color: CumulusColors.accentInk,
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}


/// Who the hub is showing, read through from the org chart.
///
/// The hub used to mint a blank profile for whoever asked, so it never had a
/// person to name. Now it resolves an `Employee` by email, and a principal the
/// org chart has never heard of — Naviant Support is the standing case — says
/// so in a sentence rather than rendering an empty page that reads as broken.
class _WhoThisIs extends StatelessWidget {
  final EmployeeProfile profile;

  const _WhoThisIs({required this.profile});

  @override
  Widget build(BuildContext context) {
    if (!profile.inWorkforce) {
      return CumulusCard(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Text(
            profile.workforceNote.isEmpty
                ? 'This account is not in the workforce, so there is no '
                      'balance, manager or enrolment to show.'
                : profile.workforceNote,
            style: CumulusText.meta,
          ),
        ),
      );
    }
    final line = [
      profile.title,
      profile.team,
      if (profile.manager.isNotEmpty) 'reports to ${profile.manager}',
      profile.location,
    ].where((s) => s.isNotEmpty).join('  ·  ');
    return CumulusCard(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(profile.name, style: CumulusText.sectionTitle),
            const SizedBox(height: 4),
            Text(line, style: CumulusText.meta),
          ],
        ),
      ),
    );
  }
}


/// What this person has spent, and the path the answer came down.
///
/// A two-hop walk from a resolved Employee rather than a search over rows —
/// which is the difference the graph exists to make, so the traversal is shown
/// rather than hidden. The endpoint has existed since the entity layer landed
/// and had no caller until now.
class _SpendRollup extends StatelessWidget {
  final ExpenseRollup rollup;

  const _SpendRollup({required this.rollup});

  @override
  Widget build(BuildContext context) {
    final byCategory = rollup.byCategory.entries.toList()
      ..sort((a, b) => b.value.compareTo(a.value));
    return CumulusCard(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Your spend · last ${rollup.windowDays} days',
              style: CumulusText.sectionTitle,
            ),
            const SizedBox(height: 4),
            Text(
              '\$${rollup.approvedUsd.toStringAsFixed(2)} approved across '
              '${rollup.requests} request(s) and ${rollup.lines} line(s)',
              style: CumulusText.body,
            ),
            if (byCategory.isNotEmpty) ...[
              const SizedBox(height: 10),
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  for (final e in byCategory)
                    MetaPill(
                      '${e.key} \$${e.value.toStringAsFixed(2)}',
                    ),
                ],
              ),
            ],
            const SizedBox(height: 10),
            Text(
              rollup.traversal.join('  '),
              style: CumulusText.mono.copyWith(
                fontSize: 11,
                color: CumulusColors.inkSecondary,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
