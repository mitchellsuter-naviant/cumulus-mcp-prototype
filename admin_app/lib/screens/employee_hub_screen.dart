import 'dart:convert';

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
      final all = await _api.listReimbursements();
      if (mounted) {
        setState(() {
          _profile = profile;
          _pto = pto;
          _mine = all.where((r) => r.employee == profile.uid).toList();
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
            title: _profile?.name.split('@').first ?? 'Your hub',
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

  Widget _leftColumn() {
    final profile = _profile!;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
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

class _ExpenseCardState extends State<_ExpenseCard> {
  XFile? _file;
  final _note = TextEditingController();
  bool _busy = false;
  String? _message;

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  Future<void> _pick() async {
    final file = await openFile(
      acceptedTypeGroups: const [
        XTypeGroup(label: 'receipts', extensions: ['png', 'jpg', 'jpeg']),
      ],
    );
    if (file != null) setState(() => _file = file);
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      final bytes = _file == null ? null : await _file!.readAsBytes();
      final row = await widget.api.submitReimbursement(
        imageBase64: bytes == null ? '' : base64Encode(bytes),
        filename: _file?.name ?? '',
        note: _note.text.trim(),
      );
      setState(() {
        _busy = false;
        _file = null;
        _note.clear();
        _message = switch (row.state) {
          'approved' =>
            'Approved automatically — ${row.vendor}, '
                '\$${row.totalUsd.toStringAsFixed(2)} (${row.verdictReason}).',
          _ =>
            'Elevated for review — ${row.verdictReason}. '
                'It will appear in the expense-review queue.',
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
      title: 'Expenses',
      subtitle:
          'a submitted receipt runs the expense-reimbursement flow: extraction, '
          'policy check, then auto-approval or review',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              OutlinedButton.icon(
                onPressed: _busy ? null : _pick,
                icon: const Icon(Icons.receipt_long_outlined, size: 16),
                label: Text(_file?.name ?? 'Choose receipt image'),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: TextField(
                  controller: _note,
                  enabled: !_busy,
                  // The Submit button's enabled state reads this text.
                  onChanged: (_) => setState(() {}),
                  decoration: const InputDecoration(
                    labelText: 'Note (what this was for)',
                    isDense: true,
                  ),
                ),
              ),
              const SizedBox(width: 10),
              FilledButton(
                onPressed: _busy || (_file == null && _note.text.trim().isEmpty)
                    ? null
                    : _submit,
                child: _busy
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Text('Submit'),
              ),
            ],
          ),
          if (_message != null) ...[
            const SizedBox(height: 10),
            Text(_message!, style: CumulusText.body),
          ],
          if (widget.mine.isNotEmpty) ...[
            const SizedBox(height: 14),
            const FieldLabel('Your reimbursements'),
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
                      '${r.vendor} · \$${r.totalUsd.toStringAsFixed(2)}',
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
