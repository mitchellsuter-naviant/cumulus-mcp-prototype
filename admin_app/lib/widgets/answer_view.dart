import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../theme/cumulus_theme.dart';
import '../theme/domain_tones.dart';
import 'ui_kit.dart';

/// One answer, rendered the same way everywhere it appears.
///
/// The palette dropdown and the full search page both show the output of the
/// same endpoint, so they have no business formatting it differently — a
/// citation that is a tappable chip in one place and a literal `[asset-id]` in
/// the other reads as a bug. Everything below is shared; the only difference
/// between the two surfaces is [dense], which trades a little air for the
/// dropdown's tighter budget.
class AnswerPanel extends StatelessWidget {
  final AskAnswer answer;
  final void Function(String assetId) onOpenAsset;

  /// The dropdown's tighter measure. The page uses the roomier default.
  final bool dense;

  const AnswerPanel({
    super.key,
    required this.answer,
    required this.onOpenAsset,
    this.dense = false,
  });

  @override
  Widget build(BuildContext context) {
    final pad = dense ? 16.0 : 18.0;
    return Container(
      padding: EdgeInsets.fromLTRB(pad, pad - 2, pad, pad - 2),
      decoration: BoxDecoration(
        color: answer.isGenerated
            ? CumulusColors.accentSoft
            : CumulusColors.surfaceMuted,
        borderRadius: BorderRadius.circular(
          dense ? CumulusRadius.control : CumulusRadius.card,
        ),
        border: Border.all(
          color: answer.isGenerated
              ? CumulusColors.accent.withValues(alpha: 0.28)
              : CumulusColors.border,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                answer.isGenerated
                    ? Icons.auto_awesome
                    : Icons.format_list_bulleted,
                size: 14,
                color: CumulusColors.accentInk,
              ),
              const SizedBox(width: 8),
              Text(
                answer.isGenerated ? 'ANSWER' : 'RANKED DOCUMENTS',
                style: CumulusText.statLabel,
              ),
              if (!dense) ...[
                const SizedBox(width: 10),
                Flexible(
                  child: Text(
                    answer.isGenerated
                        ? 'grounded in the corpus, with citations'
                        : 'answer generation is not enabled',
                    style: CumulusText.sectionSubtitle,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
              const Spacer(),
              if (answer.truncated)
                const MetaPill('incomplete', tone: CumulusTone.warn),
            ],
          ),
          SizedBox(height: dense ? 10 : 14),
          AnswerText(
            text: answer.answer,
            citable: {for (final c in answer.citations) c.assetId},
            onCitationTap: onOpenAsset,
            dense: dense,
          ),
          if (answer.citations.isNotEmpty) ...[
            SizedBox(height: dense ? 12 : 16),
            const Text('SOURCES', style: CumulusText.statLabel),
            const SizedBox(height: 6),
            for (final citation in answer.citations)
              CitationRow(
                citation: citation,
                onTap: () => onOpenAsset(citation.assetId),
              ),
          ],
          if (answer.steps.isNotEmpty) AnswerStepTrace(steps: answer.steps),
        ],
      ),
    );
  }
}

/// Minimal markdown: `**bold**`, `` `code` ``, `* ` / `1. ` lists, blank-line
/// paragraphs, and `[asset-id]` citations rendered as tappable chips.
///
/// A full markdown engine is not worth a dependency for what a two-paragraph
/// answer uses, and the citation spans have to be interactive anyway — no
/// renderer would give us that for free.
class AnswerText extends StatelessWidget {
  final String text;
  final Set<String> citable;
  final void Function(String assetId) onCitationTap;
  final bool dense;

  const AnswerText({
    super.key,
    required this.text,
    required this.citable,
    required this.onCitationTap,
    this.dense = false,
  });

  static final _inline = RegExp(
    r'\*\*(.+?)\*\*'
    r'|`(.+?)`'
    r'|\[([a-z0-9][a-z0-9\-]*(?:\s*,\s*[a-z0-9][a-z0-9\-]*)*)\]',
  );

  /// `* item`, `- item`, or `1. item`, with any leading indent.
  static final _bullet = RegExp(r'^\s*(?:[*\-•]|\d+[.)])\s+');

  TextStyle get _base => TextStyle(
    fontSize: dense ? 13 : 13.5,
    height: dense ? 1.55 : 1.6,
    color: CumulusColors.ink,
  );

  @override
  Widget build(BuildContext context) {
    final blocks = <Widget>[];
    var paragraph = <String>[];

    void flushParagraph() {
      if (paragraph.isEmpty) return;
      blocks.add(_line(paragraph.join(' ')));
      paragraph = [];
    }

    for (final raw in text.split('\n')) {
      final line = raw.trimRight();
      if (line.trim().isEmpty) {
        flushParagraph();
        continue;
      }
      final bullet = _bullet.firstMatch(line);
      if (bullet != null) {
        flushParagraph();
        blocks.add(
          Padding(
            padding: const EdgeInsets.only(left: 2, bottom: 2),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: EdgeInsets.only(top: dense ? 3 : 4, right: 8),
                  child: Container(
                    width: 4,
                    height: 4,
                    decoration: const BoxDecoration(
                      color: CumulusColors.inkMuted,
                      shape: BoxShape.circle,
                    ),
                  ),
                ),
                Expanded(child: _line(line.substring(bullet.end))),
              ],
            ),
          ),
        );
        continue;
      }
      paragraph.add(line.trim());
    }
    flushParagraph();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < blocks.length; i++)
          Padding(
            padding: EdgeInsets.only(bottom: i == blocks.length - 1 ? 0 : 4),
            child: blocks[i],
          ),
      ],
    );
  }

  /// One line of prose, with inline marks resolved.
  Widget _line(String line) {
    final spans = <InlineSpan>[];
    var cursor = 0;
    for (final match in _inline.allMatches(line)) {
      if (match.start > cursor) {
        spans.add(TextSpan(text: line.substring(cursor, match.start)));
      }
      if (match.group(1) != null) {
        spans.add(
          TextSpan(
            text: match.group(1),
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
        );
      } else if (match.group(2) != null) {
        spans.add(TextSpan(text: match.group(2), style: CumulusText.mono));
      } else {
        final ids = [
          for (final id in match.group(3)!.split(',')) id.trim(),
        ];
        // An id the server did not vouch for stays literal text. Rendering it
        // as a link would promise a document that may not exist — and a mixed
        // group stays literal whole, rather than half chip and half bracket.
        if (ids.every(citable.contains)) {
          for (final id in ids) {
            spans.add(
              WidgetSpan(
                alignment: PlaceholderAlignment.middle,
                child: CitationChip(id: id, onTap: () => onCitationTap(id)),
              ),
            );
          }
        } else {
          spans.add(TextSpan(text: match.group(0)));
        }
      }
      cursor = match.end;
    }
    if (cursor < line.length) spans.add(TextSpan(text: line.substring(cursor)));

    return SelectableText.rich(TextSpan(style: _base, children: spans));
  }
}

/// An inline `[asset-id]` citation, tappable back to its document.
class CitationChip extends StatelessWidget {
  final String id;
  final VoidCallback onTap;

  const CitationChip({super.key, required this.id, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 2),
      child: Material(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(6),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(6),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
            child: Text(
              id,
              style: CumulusText.mono.copyWith(
                fontSize: 11,
                color: CumulusColors.accentInk,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// One document an answer leaned on, under SOURCES.
class CitationRow extends StatelessWidget {
  final Citation citation;
  final VoidCallback onTap;

  const CitationRow({super.key, required this.citation, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 5, horizontal: 2),
        child: Row(
          children: [
            Monogram(
              citation.authorityLevel.substring(0, 1).toUpperCase(),
              tone: toneForAuthority(citation.authorityLevel),
              size: 24,
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    citation.title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: CumulusText.rowTitle,
                  ),
                  Text(
                    citation.path,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: CumulusText.meta,
                  ),
                ],
              ),
            ),
            if (citation.status != 'published') ...[
              const SizedBox(width: 8),
              StatusBadge(
                citation.status,
                tone: toneForStatus(citation.status),
                dense: true,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// The tool calls behind an answer, collapsed by default.
///
/// An answer nobody can trace is not usable for governance work, which is what
/// this corpus is for — so the queries that produced it are always one click
/// away rather than absent.
class AnswerStepTrace extends StatelessWidget {
  final List<AskStep> steps;

  const AnswerStepTrace({super.key, required this.steps});

  @override
  Widget build(BuildContext context) {
    return Theme(
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        tilePadding: EdgeInsets.zero,
        childrenPadding: const EdgeInsets.only(bottom: 4),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        dense: true,
        visualDensity: VisualDensity.compact,
        title: Text(
          'How this was answered · ${steps.length} '
          '${steps.length == 1 ? "step" : "steps"}',
          style: CumulusText.meta,
        ),
        children: [
          for (final step in steps)
            Padding(
              padding: const EdgeInsets.only(bottom: 3),
              child: Text(
                '${step.tool}('
                '${step.arguments.entries.map((e) => "${e.key}: ${e.value}").join(", ")}'
                ')${step.summary.isEmpty ? "" : " → ${step.summary}"}',
                style: CumulusText.mono.copyWith(
                  fontSize: 11,
                  color: CumulusColors.inkSecondary,
                ),
              ),
            ),
        ],
      ),
    );
  }
}
