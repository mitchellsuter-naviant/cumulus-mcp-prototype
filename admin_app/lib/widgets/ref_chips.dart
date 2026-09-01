import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'document_drawer.dart';
import 'side_drawer.dart';
import 'ui_kit.dart';

/// Refs as chips. Shared by run journals and task reviews so a pinned read
/// reads as the same object wherever it surfaces.
///
/// `knowledge:` and `document:` chips are tappable. The resolver answers
/// `resolved: false` for every other namespace, and a chip that opens a drawer
/// saying nothing is worse than a chip that stays put — so the rest keep the
/// tooltip they have always had until the entity store gives them something to
/// open.
///
/// `document:` was inert for the wrong reason. The bytes were stored under
/// retention and served by an endpoint the whole time, and the console simply
/// never asked: a run could say it read `document:doc_28fee8830bfcf03c` and
/// there was no way anywhere to look at the receipt.
class RefChips extends StatelessWidget {
  final ApiClient api;
  final List<String> refs;

  /// Overrides the per-namespace tooltip for the inert chips. Knowledge chips
  /// ignore it: once a chip can be opened, telling someone that is the more
  /// useful thing to say.
  final String tooltip;

  final CumulusTone tone;

  const RefChips({
    super.key,
    required this.api,
    required this.refs,
    this.tooltip = '',
    this.tone = CumulusTone.neutral,
  });

  static bool _isKnowledge(String ref) => ref.startsWith('knowledge:');

  static bool _isDocument(String ref) => ref.startsWith('document:');

  /// Kicks the fetch off before the drawer opens so the request is made once,
  /// rather than once per rebuild of the drawer's body.
  void _open(BuildContext context, String ref) {
    final pending = _resolve(ref);
    showSideDrawer<void>(
      context: context,
      width: 640,
      title: ref,
      subtitle:
          'The governed document behind this reference, and the revision the '
          'decision was made against.',
      builder: (context) => _RefDrawerBody(ref: ref, pending: pending),
    );
  }

  /// The resolution carries only the summary; the prose lives on the full
  /// asset. Fetching it is a second local call, and it is the difference
  /// between naming the policy and being able to read the clause. A failure
  /// here must not take the revision story down with it, which is the whole
  /// reason the drawer can render with a null document.
  Future<(RefResolution, Asset?)> _resolve(String ref) async {
    final resolution = await api.resolveRef(ref);
    if (!resolution.resolved) return (resolution, null);
    try {
      return (resolution, await api.getAsset(resolution.id));
    } on ApiException {
      return (resolution, null);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: 8,
      runSpacing: 6,
      children: [
        // Deduplicated at the point of display: a run-level read-set is the
        // union of its steps, so a policy two steps both read arrives twice.
        // Printing it twice says the run read it twice, which it did — but not
        // as two different facts, which is what a repeated chip looks like.
        for (final ref in {...refs})
          Tooltip(
            message: _isKnowledge(ref)
                ? 'Resolve this against the Library — what it is, and whether '
                      'the revision this run read is still the current one.'
                : _isDocument(ref)
                ? 'Open the artifact this step read, with the regions the '
                      'extractor claims it found each value in.'
                : tooltip.isEmpty
                ? _describe(ref)
                : tooltip,
            child: _isKnowledge(ref)
                ? _TappableChip(ref: ref, onTap: () => _open(context, ref))
                : _isDocument(ref)
                ? _TappableChip(
                    ref: ref,
                    onTap: () => openDocumentDrawer(context, api, ref),
                  )
                : _InertChip(ref: ref, tone: tone),
          ),
      ],
    );
  }

  static String _describe(String ref) {
    if (ref.startsWith('link:')) {
      return 'A typed edge this step asserted. Identity edges can be '
          'withdrawn later without erasing that they were made.';
    }
    if (ref.startsWith('graph:')) {
      return 'An entity this step minted or claimed on, at the revision it '
          'left behind.';
    }
    if (ref.startsWith('document:')) {
      return 'An artifact stored under its Collection’s retention.';
    }
    return ref;
  }
}

class _InertChip extends StatelessWidget {
  final String ref;
  final CumulusTone tone;

  const _InertChip({required this.ref, required this.tone});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: tone == CumulusTone.neutral
            ? CumulusColors.surface
            : tone.background,
        borderRadius: BorderRadius.circular(7),
        border: Border.all(
          color: tone == CumulusTone.neutral
              ? CumulusColors.border
              : Colors.transparent,
        ),
      ),
      child: Text(
        ref,
        style: CumulusText.mono.copyWith(
          color: tone == CumulusTone.neutral
              ? CumulusColors.inkSecondary
              : tone.foreground,
        ),
      ),
    );
  }
}

/// The same chip in the accent, which the design system reserves for governed
/// affordances — so "this one dereferences" is legible before anyone hovers.
class _TappableChip extends StatelessWidget {
  final String ref;
  final VoidCallback onTap;

  const _TappableChip({required this.ref, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return Material(
      color: CumulusColors.accentSoft,
      borderRadius: BorderRadius.circular(7),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(7),
        hoverColor: CumulusColors.surfaceMuted,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(
                ref,
                style: CumulusText.mono.copyWith(
                  color: CumulusColors.accentInk,
                ),
              ),
              const SizedBox(width: 7),
              const Icon(
                Icons.north_east,
                size: 12,
                color: CumulusColors.accentInk,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _RefDrawerBody extends StatelessWidget {
  final String ref;
  final Future<(RefResolution, Asset?)> pending;

  const _RefDrawerBody({required this.ref, required this.pending});

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<(RefResolution, Asset?)>(
      future: pending,
      builder: (context, snapshot) {
        if (snapshot.hasError) {
          final error = snapshot.error;
          return ErrorNotice(
            message: error is ApiException
                ? 'This reference does not resolve. ${error.message}'
                : 'This reference could not be resolved. $error',
          );
        }
        if (!snapshot.hasData) return const CumulusLoading();
        final (resolution, document) = snapshot.data!;
        final asset = resolution.asset;
        if (!resolution.resolved || asset == null) {
          return ErrorNotice(
            message: resolution.note.isEmpty
                ? '$ref names nothing this server can dereference.'
                : resolution.note,
          );
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(asset.title, style: CumulusText.pageTitle),
            const SizedBox(height: 8),
            Text(
              asset.path,
              style: CumulusText.mono.copyWith(
                color: CumulusColors.inkSecondary,
              ),
            ),
            const SizedBox(height: 14),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                if (asset.authorityLevel.isNotEmpty)
                  MetaPill(asset.authorityLevel, tone: CumulusTone.accent),
                MetaPill(
                  asset.status,
                  tone: asset.status == 'published'
                      ? CumulusTone.success
                      : CumulusTone.neutral,
                ),
                MetaPill('owner ${asset.owner}'),
                MetaPill('updated ${agoLabel(asset.updatedAt)}'),
              ],
            ),
            const SizedBox(height: 22),
            _RevisionStory(resolution),
            const SizedBox(height: 20),
            if (document == null)
              Text(
                'The document resolved, but its text could not be loaded.',
                style: CumulusText.meta,
              )
            else
              PayloadBlock(
                label: resolution.servesTextThatWasRead
                    ? 'The text this run read'
                    : 'The current text — not a record of what was read',
                content: document.content,
                maxLines: 24,
              ),
          ],
        );
      },
    );
  }
}

/// The reason this drawer exists.
///
/// A run journal pins a revision; the Library moves on. Holding those two
/// numbers next to each other is the doctrine's payoff claim made visible —
/// without it a read-set is a list of strings that merely look rigorous.
class _RevisionStory extends StatelessWidget {
  final RefResolution resolution;

  const _RevisionStory(this.resolution);

  /// A ref carrying no revision resolves to whatever is current, so it cannot
  /// say what the run read. Naming that gap is the honest reading; letting it
  /// render as a match would launder it into a guarantee.
  ({CumulusTone tone, IconData icon, String headline, String detail})
  get _story {
    final requested = resolution.requestedRevision;
    final current = resolution.currentRevision;
    if (requested == null) {
      return (
        tone: CumulusTone.neutral,
        icon: Icons.help_outline,
        headline: 'No revision was pinned.',
        detail:
            'The journal recorded this reference without one, so it resolves '
            'to whatever is current — rev$current today. What you can read '
            'below is that, not a record of what the run saw.',
      );
    }
    if (resolution.revisionMoved) {
      return (
        tone: CumulusTone.warn,
        icon: Icons.history,
        headline: 'The policy has moved since this decision was made.',
        detail:
            'The run read rev$requested. The document is now at rev$current, '
            'so the rule this decision was measured against is not the rule '
            'in force today.',
      );
    }
    return (
      tone: CumulusTone.success,
      icon: Icons.check_circle_outline,
      headline: 'Still the revision this run read.',
      detail:
          'Read at rev$requested, and rev$current is current — the decision '
          'and the document have not drifted apart.',
    );
  }

  @override
  Widget build(BuildContext context) {
    final requested = resolution.requestedRevision;
    final current = resolution.currentRevision;
    final (:tone, :icon, :headline, :detail) = _story;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(15),
      decoration: BoxDecoration(
        color: tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 18, color: tone.foreground),
          const SizedBox(width: 11),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  headline,
                  style: CumulusText.rowTitle.copyWith(color: tone.foreground),
                ),
                const SizedBox(height: 5),
                Text(
                  detail,
                  style: CumulusText.body.copyWith(color: tone.foreground),
                ),
                if (resolution.note.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  Text(resolution.note, style: CumulusText.meta),
                ],
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    MetaPill(
                      requested == null
                          ? 'no revision pinned'
                          : 'run read rev$requested',
                    ),
                    MetaPill('current rev$current'),
                    if (requested != null)
                      MetaPill(
                        resolution.pinnedAvailable
                            ? 'pinned text still served'
                            : 'pinned text not retained',
                        tone: resolution.pinnedAvailable
                            ? CumulusTone.success
                            : CumulusTone.warn,
                      ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
