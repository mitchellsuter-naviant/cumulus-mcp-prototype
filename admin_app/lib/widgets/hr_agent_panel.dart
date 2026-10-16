/// The HR Agent: a grounded, cited conversation over the knowledge layer.
///
/// Lives beside the hub rather than inside it because it is the one part of
/// that screen with real behaviour to pin down — a transcript it owns, a "new
/// chat" that has to actually end one, and a disclosure under every answer
/// saying what the answer was allowed to stand on. A private widget inside a
/// twelve-hundred-line screen is a widget nothing can assert about.
///
/// The panel holds the conversation and replays it on every send. Nothing is
/// kept on the other end: a surface that answers from somebody's own health
/// plan and PTO balance is the last place to grow a server-side record of who
/// asked what, and "new chat" is only an honest control if the history it
/// drops was never anyone else's to keep.
library;

import 'package:flutter/material.dart';

import '../models/employee.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import '../theme/domain_tones.dart';
import 'ui_kit.dart';

/// How the panel asks. Injected rather than reached for, so the transcript,
/// the retry and the grounding disclosure can be exercised without a server.
typedef HrAsk = Future<HrChatReply> Function(String message, List<HrTurn> history);

/// One exchange, held by the panel and replayed to the server on every send.
///
/// The transcript lives here rather than on the other end because a surface
/// that answers from somebody's own health plan and PTO balance is the last
/// place to grow a server-side record of who asked what — and because "new
/// chat" is only an honest control if the history it drops was never anyone
/// else's to keep.
class _ChatMessage {
  final bool fromAgent;
  final String text;
  final List<HrCitation> citations;

  /// What the answer was allowed to stand on. Null on a question, on the
  /// opening greeting, and on an error — anywhere there was no retrieval to
  /// disclose.
  final HrGrounding? grounding;
  final List<String> groundedOn;
  final String backend;

  /// True for a failed send, which is offered again rather than left as a
  /// dead end in the transcript.
  final bool failed;

  const _ChatMessage(
    this.fromAgent,
    this.text, {
    this.citations = const [],
    this.grounding,
    this.groundedOn = const [],
    this.backend = '',
    this.failed = false,
  });

  /// The turn as the server will accept it.
  ///
  /// `HrTurn.text` is capped at [_maxTurnChars] on the other end, and the whole
  /// transcript is replayed on every send — so one long answer used to make
  /// every *following* question fail validation, surfacing as an opaque
  /// "Something went wrong" on a question that had nothing wrong with it. A
  /// replayed turn is context rather than a record, so trimming its tail is the
  /// cheap half of the trade; the untruncated text stays in [text] and on
  /// screen.
  HrTurn get turn => HrTurn(
    fromAgent: fromAgent,
    text: text.length <= _maxTurnChars
        ? text
        : '${text.substring(0, _maxTurnChars - _truncationMark.length)}'
              '$_truncationMark',
  );
}

/// The server's `max_length` on one replayed turn.
const _maxTurnChars = 4000;
const _truncationMark = '… [trimmed]';

class HrAgentPanel extends StatefulWidget {
  /// How to ask. Defaults are the hub's business, not this widget's.
  final HrAsk ask;

  /// Whether this reader has personal records to answer from. The opening
  /// message promises different things to an employee and to an operator, and
  /// promising a PTO balance to somebody who has none is how the panel used to
  /// start every conversation it could not finish.
  final bool inWorkforce;

  /// Land on the cited document in the Library. A citation you cannot open is
  /// a claim you cannot check, which is most of what a citation is for.
  final void Function(String assetId)? onOpenAsset;

  const HrAgentPanel({
    super.key,
    required this.ask,
    required this.inWorkforce,
    this.onOpenAsset,
  });

  @override
  State<HrAgentPanel> createState() => HrAgentPanelState();
}

class HrAgentPanelState extends State<HrAgentPanel> {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  final _focus = FocusNode();
  late List<_ChatMessage> _messages = [_greeting()];
  bool _busy = false;

  /// Which suggestions are worth offering depends on who is reading. An
  /// operator has no balance and no enrolment, so offering "how much PTO do I
  /// have?" would be inviting them to ask the one question this account cannot
  /// answer.
  List<String> get _suggestions => widget.inWorkforce
      ? const [
          'How much PTO do I have?',
          'What is the 401(k) match?',
          'How do I get reimbursed for a flight?',
          'Who is my manager?',
        ]
      : const [
          'What is our expense approval policy?',
          'How do I get reimbursed for a flight?',
          'What does the PTO policy say about carry-over?',
        ];

  _ChatMessage _greeting() => _ChatMessage(
    true,
    widget.inWorkforce
        ? 'Hi — I’m the HR Agent. I answer from the governed corpus and your '
              'own records, and I cite what I used. Where two documents '
              'disagree I’ll say which one governs; where nothing covers your '
              'question I’ll say that instead of guessing.'
        : 'Hi — I’m the HR Agent. This account is not on the org chart, so '
              'there are no personal records to answer from, but I still '
              'answer policy questions from the governed corpus with '
              'citations.',
  );

  @override
  void dispose() {
    _input.dispose();
    _scroll.dispose();
    _focus.dispose();
    super.dispose();
  }

  /// Drop the transcript and start over.
  ///
  /// The panel is a section inside the shell, so its state survives every
  /// navigation away and back — which meant a conversation from twenty minutes
  /// and three screens ago was still sitting there, and the only way to be rid
  /// of it was a page reload. History that cannot be ended is not history, it
  /// is clutter, and it is also the context the next answer is conditioned on.
  void _newChat() {
    setState(() {
      _messages = [_greeting()];
      _input.clear();
    });
    _focus.requestFocus();
  }

  Future<void> _send([String? preset]) async {
    final text = (preset ?? _input.text).trim();
    if (text.isEmpty || _busy) return;
    // Everything before this question, which is what makes "what about
    // dental?" a question at all. The failed turns are excluded: a send that
    // never reached the server is not part of the conversation.
    final history = [
      for (final m in _messages.skip(1))
        if (!m.failed) m.turn,
    ];
    setState(() {
      _messages.add(_ChatMessage(false, text));
      _input.clear();
      _busy = true;
    });
    _autoScroll();
    try {
      final reply = await widget.ask(text, history);
      if (mounted) {
        setState(() {
          _messages.add(
            _ChatMessage(
              true,
              reply.reply,
              citations: reply.citations,
              grounding: reply.grounding,
              groundedOn: reply.groundedOn,
              backend: reply.backend,
            ),
          );
          _busy = false;
        });
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _messages.add(
            _ChatMessage(true, 'Something went wrong: ${e.message}', failed: true),
          );
          _busy = false;
        });
      }
    }
    _autoScroll();
    if (mounted) _focus.requestFocus();
  }

  /// The last thing the employee asked, for the retry on a failed send.
  String? get _lastQuestion {
    for (final m in _messages.reversed) {
      if (!m.fromAgent) return m.text;
    }
    return null;
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
    // Only the opening message means nothing has been asked yet, which is when
    // suggestions help and after which they are in the way.
    final fresh = _messages.length == 1;
    return CumulusCard(
      padding: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _header(fresh),
          Expanded(
            child: ListView.builder(
              controller: _scroll,
              padding: const EdgeInsets.all(16),
              itemCount: _messages.length + (_busy ? 1 : 0),
              itemBuilder: (context, i) {
                if (i == _messages.length) return const _Thinking();
                return _Bubble(
                  message: _messages[i],
                  onOpenAsset: widget.onOpenAsset,
                  onRetry: _messages[i].failed && !_busy && _lastQuestion != null
                      ? () => _send(_lastQuestion)
                      : null,
                );
              },
            ),
          ),
          if (fresh && !_busy) _suggestionStrip(),
          _composer(),
        ],
      ),
    );
  }

  Widget _header(bool fresh) {
    return Container(
      padding: const EdgeInsets.fromLTRB(18, 12, 12, 10),
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
          const Spacer(),
          // Disabled on a conversation that has not started, so the control
          // says what it does: there is nothing here to clear.
          TextButton.icon(
            onPressed: fresh || _busy ? null : _newChat,
            icon: const Icon(Icons.add_comment_outlined, size: 15),
            label: const Text('New chat'),
            style: TextButton.styleFrom(
              visualDensity: VisualDensity.compact,
              foregroundColor: CumulusColors.inkSecondary,
            ),
          ),
        ],
      ),
    );
  }

  Widget _suggestionStrip() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
      child: Wrap(
        spacing: 6,
        runSpacing: 6,
        children: [
          for (final s in _suggestions)
            ActionChip(
              label: Text(s, style: CumulusText.meta),
              onPressed: () => _send(s),
              visualDensity: VisualDensity.compact,
              side: const BorderSide(color: CumulusColors.border),
              backgroundColor: CumulusColors.surface,
            ),
        ],
      ),
    );
  }

  Widget _composer() {
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
      decoration: const BoxDecoration(
        border: Border(top: BorderSide(color: CumulusColors.border)),
      ),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: _input,
              focusNode: _focus,
              enabled: !_busy,
              onSubmitted: (_) => _send(),
              decoration: const InputDecoration(
                hintText: 'Ask about PTO, benefits, expenses, policies…',
                isDense: true,
                border: InputBorder.none,
              ),
            ),
          ),
          IconButton(
            tooltip: 'Send',
            onPressed: _busy ? null : () => _send(),
            icon: const Icon(Icons.send, size: 18),
            color: CumulusColors.accentInk,
          ),
        ],
      ),
    );
  }
}

class _Thinking extends StatelessWidget {
  const _Thinking();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(8),
      child: Row(
        children: [
          const SizedBox(
            width: 14,
            height: 14,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
          const SizedBox(width: 10),
          Text('Reading the corpus…', style: CumulusText.meta),
        ],
      ),
    );
  }
}

/// One turn, and — for an answer — what it stood on.
class _Bubble extends StatelessWidget {
  final _ChatMessage message;
  final void Function(String assetId)? onOpenAsset;
  final VoidCallback? onRetry;

  const _Bubble({required this.message, this.onOpenAsset, this.onRetry});

  @override
  Widget build(BuildContext context) {
    final agent = message.fromAgent;
    return Align(
      alignment: agent ? Alignment.centerLeft : Alignment.centerRight,
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: const BoxConstraints(maxWidth: 460),
        decoration: BoxDecoration(
          color: agent ? CumulusColors.surfaceMuted : CumulusColors.ink,
          borderRadius: BorderRadius.circular(14),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            SelectableText(
              message.text,
              style: CumulusText.body.copyWith(
                color: agent ? CumulusColors.ink : Colors.white,
              ),
            ),
            if (message.citations.isNotEmpty) ...[
              const SizedBox(height: 10),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  for (final c in message.citations)
                    _CitationChip(citation: c, onOpen: onOpenAsset),
                ],
              ),
            ],
            if (message.grounding != null)
              _GroundingLine(
                grounding: message.grounding!,
                groundedOn: message.groundedOn,
                backend: message.backend,
                citations: message.citations.length,
              ),
            if (onRetry != null) ...[
              const SizedBox(height: 6),
              TextButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh, size: 14),
                label: const Text('Try again'),
                style: TextButton.styleFrom(
                  visualDensity: VisualDensity.compact,
                  padding: EdgeInsets.zero,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// A cited document: what it is, how authoritative, and a way in.
class _CitationChip extends StatelessWidget {
  final HrCitation citation;
  final void Function(String assetId)? onOpen;

  const _CitationChip({required this.citation, this.onOpen});

  @override
  Widget build(BuildContext context) {
    final level = citation.authorityLevel;
    final chip = Container(
      padding: const EdgeInsets.fromLTRB(8, 4, 8, 4),
      // A Wrap gives its children their intrinsic width, so a long document
      // title plus two badges is wider than the bubble it sits in and the row
      // overflows. The title is the part that gives: the badges are the whole
      // point of the chip and the full title is in the tooltip.
      constraints: const BoxConstraints(maxWidth: 300),
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: CumulusColors.border),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Flexible(
            child: Text(
              citation.title.isEmpty ? citation.id : citation.title,
              style: CumulusText.mono.copyWith(fontSize: 10.5),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (level != null) ...[
            const SizedBox(width: 6),
            StatusBadge(level, tone: toneForAuthority(level), dense: true),
          ],
          // A draft or a deprecated document is retrievable and not
          // authoritative, and the difference has to be visible on the chip —
          // it is the one place a reader looks before believing the answer.
          if (citation.status != 'published') ...[
            const SizedBox(width: 4),
            StatusBadge(
              citation.status,
              tone: toneForStatus(citation.status),
              dense: true,
            ),
          ],
          if (onOpen != null) ...[
            const SizedBox(width: 4),
            const Icon(
              Icons.north_east,
              size: 11,
              color: CumulusColors.inkSecondary,
            ),
          ],
        ],
      ),
    );
    final grounding = citation.grounding;
    final tooltip = [
      if (citation.path.isNotEmpty) citation.path,
      // Omitted rather than defaulted when the answer's own planner cited this
      // without a ranking behind it. A measurement nobody made must not be
      // printed as 0%.
      if (grounding != null)
        'grounded ${(grounding * 100).round()}% in the question',
      if (onOpen != null) 'Open in the Library',
    ].join(' · ');
    return Tooltip(
      message: tooltip,
      child: onOpen == null
          ? chip
          : InkWell(
              borderRadius: BorderRadius.circular(6),
              onTap: () => onOpen!(citation.id),
              child: chip,
            ),
    );
  }
}

/// The disclosure under an answer: the rule it ranked under, and what it threw
/// away getting there.
///
/// Collapsed, because most readers want the answer — and present, because
/// "the governing document is X" is a claim about precedence that a reader has
/// no way to check otherwise. The number of documents dropped below the floor
/// is the part that was never shown anywhere before: an agent that silently
/// discards six near-misses and names the seventh looks identical to one that
/// found a single clean answer.
class _GroundingLine extends StatelessWidget {
  final HrGrounding grounding;
  final List<String> groundedOn;
  final String backend;
  final int citations;

  const _GroundingLine({
    required this.grounding,
    required this.groundedOn,
    required this.backend,
    required this.citations,
  });

  @override
  Widget build(BuildContext context) {
    final summary = [
      if (backend.isNotEmpty)
        backend == 'retrieval' ? 'retrieval only' : 'answered by $backend',
      '$citations citation${citations == 1 ? '' : 's'}',
      if (grounding.belowFloor > 0)
        '${grounding.belowFloor} below the floor',
    ].join(' · ');
    return Theme(
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        tilePadding: EdgeInsets.zero,
        childrenPadding: const EdgeInsets.only(bottom: 4),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        dense: true,
        visualDensity: VisualDensity.compact,
        title: Text('How this was grounded — $summary', style: CumulusText.meta),
        children: [
          if (grounding.precedence.isNotEmpty)
            _line('Precedence', grounding.precedence),
          if (grounding.policyRef.isNotEmpty)
            _line('Governed by', grounding.policyRef),
          _line(
            'Relevance floor',
            '${(grounding.minCoverage * 100).round()}% of the question, '
                '${grounding.belowFloor} document(s) dropped',
          ),
          if (grounding.query.isNotEmpty)
            _line('Searched for', grounding.query),
          if (groundedOn.isNotEmpty)
            _line('Your records used', groundedOn.take(4).join(' · ')),
        ],
      ),
    );
  }

  Widget _line(String label, String value) => Padding(
    padding: const EdgeInsets.only(bottom: 3),
    child: Text.rich(
      TextSpan(
        children: [
          TextSpan(
            text: '$label: ',
            style: CumulusText.meta.copyWith(fontWeight: FontWeight.w600),
          ),
          TextSpan(text: value),
        ],
      ),
      style: CumulusText.meta,
    ),
  );
}
