import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import '../theme/domain_tones.dart';
import '../widgets/command_palette.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/skeleton.dart';
import '../widgets/ui_kit.dart';
import 'asset_editor_screen.dart';
import 'search_screen.dart';

/// Authority levels, in descending order of force — the same order the lens
/// chips are shown in.
const _authorityLevels = ['policy', 'standard', 'guideline', 'how-to', 'note'];

/// The knowledge layer: a metric strip over the corpus, authority lenses, and
/// the taxonomy grouped into cards. Editing happens in place rather than on a
/// pushed route, so the console chrome never flickers.
///
/// This page no longer owns search. The command palette does, and drives this
/// page through [openAsset] and [pageResults]. That split is what fixes the
/// old behaviour where a query typed with an asset open ran, stored its
/// results, and rendered nothing because the editor was drawn over them.
class AssetsSection extends StatefulWidget {
  /// Published to so ⌘K can search inside whatever document is open.
  final CommandPaletteController palette;

  /// An asset to open: an id, `''` for a new asset, null for none.
  final ValueNotifier<String?> openAsset;

  /// The full result list, set only by the palette's "See all results".
  final ValueNotifier<({String query, SearchFilters filters})?> pageResults;

  /// Called after a write so the palette's jump-to catalog stays current.
  final Future<void> Function() onCorpusChanged;

  const AssetsSection({
    super.key,
    required this.palette,
    required this.openAsset,
    required this.pageResults,
    required this.onCorpusChanged,
  });

  @override
  State<AssetsSection> createState() => _AssetsSectionState();
}

class _AssetsSectionState extends State<AssetsSection> {
  final ApiClient _api = ApiClient();

  List<AssetSummary> _assets = [];
  Map<String, AssetUsage> _usage = const {};

  /// The rest of the corpus: prompts, schema exports and config. Shown in
  /// their own section rather than filtered out — the old caption promised
  /// they were "read under Ontology and Flows", and neither tab linked back,
  /// so the four generated assets were reachable only through the palette.
  List<AssetSummary> _machineReadables = const [];
  Map<String, AssetUsage> _machineUsage = const {};

  /// The whole corpus. Kept so the caption can say how the two sections add
  /// up rather than leaving the reader to reconcile the counts.
  int _corpusTotal = 0;

  /// The traffic window, echoed by the server. Held rather than written into
  /// the caption as `30d`, which is a number that keeps itself when the
  /// request changes its mind.
  int _windowDays = 30;

  /// When the newest document in the corpus was written. "No reads in 30d"
  /// over a corpus four minutes old is a fact about the corpus, not about the
  /// documents.
  DateTime? _newest;

  String? _lens;

  /// The dead-weight lenses. Two of them, because "nobody uses this" has two
  /// causes needing opposite fixes: `ignored` documents surface and get passed
  /// over (rewrite them), `undiscovered` ones never surface at all (retag
  /// them). Null means no usage lens is active.
  String? _usageLens;
  bool _loading = true;
  String? _error;

  /// Non-null while the full search view is showing.
  ({String query, SearchFilters filters})? _search;

  /// Non-null while the editor is open; the inner string is the asset id, or
  /// null for a new asset.
  ({String? id})? _editing;

  @override
  void initState() {
    super.initState();
    widget.openAsset.addListener(_onOpenAssetRequested);
    widget.pageResults.addListener(_onPageResultsRequested);
    _refresh();
  }

  @override
  void dispose() {
    widget.openAsset.removeListener(_onOpenAssetRequested);
    widget.pageResults.removeListener(_onPageResultsRequested);
    _api.close();
    super.dispose();
  }

  Future<void> _onOpenAssetRequested() async {
    final requested = widget.openAsset.value;
    if (requested == null) return;
    // Consumed immediately so opening the same asset twice in a row still
    // fires. Setting it back to null re-enters this listener, which the guard
    // above absorbs.
    widget.openAsset.value = null;

    if (!await _confirmLeavingEditor()) return;
    if (!mounted) return;
    setState(() => _editing = (id: requested.isEmpty ? null : requested));
  }

  /// Navigating away from a dirty editor asks first. Search should never be
  /// the reason someone loses a draft.
  Future<bool> _confirmLeavingEditor() async {
    final scope = widget.palette.scope;
    if (_editing == null || scope == null || !scope.isDirty()) return true;
    final leave = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Discard unsaved changes?'),
        content: Text(
          '"${scope.title}" has edits that have not been saved. Opening '
          'something else will discard them.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep editing'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Discard'),
          ),
        ],
      ),
    );
    return leave ?? false;
  }

  Future<void> _onPageResultsRequested() async {
    final request = widget.pageResults.value;
    if (request == null) {
      setState(() => _search = null);
      return;
    }
    if (!await _confirmLeavingEditor()) {
      widget.pageResults.value = null;
      return;
    }
    if (!mounted) return;
    setState(() {
      _editing = null;
      _search = request;
    });
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final screen = await _api.libraryScreen();
      if (!mounted) return;
      setState(() {
        // Documents people wrote — the half that argues and carries
        // authority — grouped by taxonomy; the machine-readable half gets
        // its own section below, so the whole corpus is on this one screen.
        // Sorted here, once, and never again: path order is a property of the
        // corpus, so every list downstream of these two inherits it rather
        // than re-establishing it on each build.
        _assets = screen.assets.where((a) => a.kind == 'document').toList()
          ..sort(_byPath);
        _machineReadables =
            screen.assets.where((a) => a.kind != 'document').toList()
              ..sort(_byPath);
        // Usage folded over the *same* set. It used to fold over all fifteen
        // assets while the count beside it said nine, so one number described
        // the documents and its neighbour described the corpus.
        final shown = {for (final a in _assets) a.id};
        _usage = {
          for (final entry in screen.usage.entries)
            if (shown.contains(entry.key)) entry.key: entry.value,
        };
        _machineUsage = {
          for (final entry in screen.usage.entries)
            if (!shown.contains(entry.key)) entry.key: entry.value,
        };
        _corpusTotal = screen.assets.length;
        _windowDays = screen.days;
        _newest = _assets.isEmpty
            ? null
            : _assets
                  .map((a) => a.updatedAt)
                  .reduce((a, b) => a.isAfter(b) ? a : b);
        _loading = false;
        _regroup();
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  /// Whether anything at all was recorded against the corpus in the window.
  ///
  /// The usage map is zero-filled, so its size says nothing. Without this the
  /// screen called every document undiscovered — in red — whenever search had
  /// simply not been run, which flags a healthy corpus as broken. No traffic
  /// is a fact about the window, not a verdict on the documents.
  bool get _hasTraffic => _usage.values.any(
    (u) => u.reads > 0 || u.citations > 0 || u.impressions > 0,
  );

  void _openEditor({String? assetId}) =>
      setState(() => _editing = (id: assetId));

  void _closeEditor({required bool changed}) {
    setState(() {
      _editing = null;
      if (changed) widget.pageResults.value = null;
    });
    if (changed) {
      _refresh();
      // Fire-and-forget: the palette's catalog going stale for a moment is not
      // worth blocking the editor closing.
      widget.onCorpusChanged();
    }
  }

  /// The taxonomy as the cards below draw it: branch → its documents, in path
  /// order, and the branch names in their own order.
  ///
  /// A function of the corpus and the two lenses, so it is computed when one
  /// of those three changes rather than on every build. It used to be built —
  /// and sorted, in place, mutating the map's own lists — once per frame,
  /// which meant a scroll or a hover paid for a regroup of the whole corpus.
  Map<String, List<AssetSummary>> _groups = const {};
  List<String> _branches = const [];
  int _shownCount = 0;

  void _regroup() {
    final visible = _assets
        .where((a) => _lens == null || a.authorityLevel == _lens)
        .where(_matchesUsageLens)
        .toList();
    final groups = <String, List<AssetSummary>>{};
    for (final asset in visible) {
      groups.putIfAbsent(asset.path.split('/').first, () => []).add(asset);
    }
    _groups = groups;
    _branches = groups.keys.toList()..sort();
    _shownCount = visible.length;
  }

  /// Every lens change goes through here, so the grouping cannot fall behind
  /// the chips that decide it.
  void _setLenses({String? authority, String? usage}) => setState(() {
    _lens = authority;
    _usageLens = usage;
    _regroup();
  });

  bool _matchesUsageLens(AssetSummary asset) {
    if (_usageLens == null || !_hasTraffic) return true;
    final usage = _usage[asset.id];
    // No row at all means no recorded traffic of any kind — undiscovered.
    if (usage == null) return _usageLens == 'undiscovered';
    return switch (_usageLens) {
      'ignored' => usage.ignored,
      'undiscovered' => usage.undiscovered,
      _ => true,
    };
  }

  @override
  Widget build(BuildContext context) {
    if (_editing != null) {
      return AssetEditorView(
        // Keyed by asset so opening a different document from the palette
        // rebuilds the editor rather than reusing the previous one's fields.
        key: ValueKey(_editing!.id ?? '<new>'),
        assetId: _editing!.id,
        onClose: _closeEditor,
        onScopeChanged: (scope) => widget.palette.scope = scope,
      );
    }
    // Search is a mode of this page rather than a separate destination: the
    // taxonomy is one click behind it, and the palette that started the search
    // stays exactly where it was.
    if (_search != null) {
      return SearchView(
        query: _search!.query,
        filters: _search!.filters,
        onClose: () => widget.pageResults.value = null,
        onOpenAsset: (id) => _openEditor(assetId: id),
        onRefine: (query, filters) =>
            widget.pageResults.value = (query: query, filters: filters),
      );
    }
    return PageBody(
      // The knowledge-layer gutter: every plane and the section title above
      // the tabs share one left edge.
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      children: [
        _heading(),
        if (_error != null) ErrorNotice(message: _error!, onRetry: _refresh),
        if (_loading)
          // The plane header above is real; what is unknown is the lens card
          // and the taxonomy branches under it.
          const SkeletonPulse(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              spacing: 16,
              children: [
                SkeletonCard(
                  padding: EdgeInsets.fromLTRB(20, 18, 20, 18),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SkeletonBar(width: 118, height: 13),
                      SizedBox(height: 8),
                      SkeletonBar(width: 300, height: 10),
                      SizedBox(height: 16),
                      Row(
                        spacing: 8,
                        children: [
                          SkeletonPill(width: 44),
                          SkeletonPill(width: 58),
                          SkeletonPill(width: 74),
                          SkeletonPill(width: 76),
                          SkeletonPill(width: 62),
                          SkeletonPill(width: 52),
                        ],
                      ),
                    ],
                  ),
                ),
                SkeletonListCard(rows: 4),
                SkeletonListCard(rows: 3),
                SkeletonListCard(rows: 3),
              ],
            ),
          )
        else
          ..._taxonomyCards(),
      ],
    );
  }

  /// "No reads" over a corpus younger than the window is a fact about the
  /// corpus. Four minutes of history cannot show thirty days of traffic, and
  /// the honest sentence says which of the two it is short of.
  String get _trafficPill {
    final reads = _usage.values.fold<int>(0, (sum, u) => sum + u.reads);
    if (_hasTraffic) return '$reads reads in ${_windowDays}d';
    final newest = _newest;
    if (newest != null &&
        DateTime.now().difference(newest) < Duration(days: _windowDays)) {
      return 'no reads yet · corpus is ${agoLabel(newest).replaceAll(' ago', '')} old';
    }
    return 'no reads recorded in ${_windowDays}d';
  }

  Widget _heading() {
    final published = _assets.where((a) => a.status == 'published').length;
    return PlaneHeader(
      // The whole corpus, on one screen: the documents that argue and carry
      // authority, and — below them — the machine-readables the flows and
      // engines read. The old caption said the latter were "read under
      // Ontology and Flows", and neither tab linked back.
      summary:
          'What must be obeyed: policies, standards, guidelines and notes — '
          'the documents that argue and carry authority — and, below them, '
          'the governed machine-readables: the prompts flows read, the '
          'schema exports the ontology publishes, and config. One corpus, '
          'all of it versioned, cited and audited the same way.',
      pills: [
        MetaPill(
          _machineReadables.isEmpty
              ? '${_assets.length} documents'
              : '${_assets.length} documents · ${_machineReadables.length} '
                    'machine-readable(s) · $_corpusTotal in the corpus',
        ),
        MetaPill('$published published', tone: CumulusTone.success),
        // Traffic belongs here rather than on a survey screen, because the
        // lenses that act on it are two chips below. Folded over the same nine
        // documents the count names, so the two agree. Stated as a plain fact
        // when there is none: nothing has been read is not an alarm.
        if (!_loading) MetaPill(_trafficPill),
      ],
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          OutlinedButton.icon(
            onPressed: _loading ? null : _refresh,
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('Refresh'),
          ),
          const SizedBox(width: 10),
          FilledButton.icon(
            onPressed: () => _openEditor(),
            icon: const Icon(Icons.add, size: 17),
            label: const Text('New asset'),
          ),
        ],
      ),
    );
  }

  Widget _lensBar() {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        LensChip(
          label: 'All',
          selected: _lens == null,
          onTap: () => _setLenses(usage: _usageLens),
        ),
        for (final level in _authorityLevels)
          LensChip(
            label: level,
            tone: toneForAuthority(level),
            selected: _lens == level,
            onTap: () => _setLenses(
              authority: _lens == level ? null : level,
              usage: _usageLens,
            ),
          ),
        if (_hasTraffic) ...[
          const SizedBox(width: 12),
          LensChip(
            label: 'ignored',
            tone: CumulusTone.warn,
            selected: _usageLens == 'ignored',
            onTap: () => _setLenses(
              authority: _lens,
              usage: _usageLens == 'ignored' ? null : 'ignored',
            ),
          ),
          LensChip(
            label: 'undiscovered',
            tone: CumulusTone.danger,
            selected: _usageLens == 'undiscovered',
            onTap: () => _setLenses(
              authority: _lens,
              usage: _usageLens == 'undiscovered' ? null : 'undiscovered',
            ),
          ),
        ],
      ],
    );
  }

  List<Widget> _taxonomyCards() {
    return [
      CumulusCard(
        padding: const EdgeInsets.fromLTRB(20, 18, 20, 18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SectionHeader(
              title: 'Authority lens',
              subtitle: _hasTraffic
                  ? 'filter by how much force a document carries, or by how '
                        'it is failing to be used'
                  : 'filter by how much force a document carries',
              trailing: Text(
                '$_shownCount shown',
                style: CumulusText.sectionSubtitle,
              ),
            ),
            const SizedBox(height: 14),
            _lensBar(),
          ],
        ),
      ),
      if (_branches.isEmpty)
        CumulusCard(
          child: CumulusEmptyState(
            icon: Icons.inventory_2_outlined,
            title: _assets.isEmpty ? 'No assets yet' : 'Nothing at this lens',
            message: _assets.isEmpty
                ? 'Author the first document in the knowledge layer and every '
                      'agent that reasons over Cumulus will see it.'
                : 'No documents carry this authority level. Clear the lens to '
                      'see the whole corpus.',
            action: _assets.isEmpty
                ? FilledButton.icon(
                    onPressed: () => _openEditor(),
                    icon: const Icon(Icons.add, size: 17),
                    label: const Text('New asset'),
                  )
                : OutlinedButton(
                    onPressed: () => _setLenses(usage: _usageLens),
                    child: const Text('Clear lens'),
                  ),
          ),
        )
      else
        for (final branch in _branches)
          ListCard(
            title: branch,
            subtitle:
                '${_groups[branch]!.length} '
                '${_groups[branch]!.length == 1 ? "document" : "documents"}',
            children: [
              for (final asset in _groups[branch]!) _assetRow(asset),
            ],
          ),
      // The machine-readable half of the corpus, below the taxonomy rather
      // than filtered out of the screen. Deliberately outside the authority
      // lens: a schema export carries no authority level, and hiding it under
      // a lens it can never match would re-orphan it.
      if (_machineReadables.isNotEmpty)
        ListCard(
          title: 'Governed machine-readables',
          subtitle:
              'what flows and engines read: prompts, schema exports, config '
              '— versioned and cited like every document above',
          children: [
            for (final asset in _machineReadables) _machineRow(asset),
          ],
        ),
    ];
  }

  /// A machine-readable asset: same row, keyed by kind rather than authority.
  ///
  /// A generated export opens read-only in the editor — regenerated from
  /// Python on every start, so an edit would be overwritten by the next
  /// deploy and the editor refuses rather than pretending.
  Widget _machineRow(AssetSummary asset) {
    return CumulusListRow(
      leading: Monogram(
        asset.kind.substring(0, 1).toUpperCase(),
        tone: CumulusTone.neutral,
      ),
      title: Row(
        children: [
          Flexible(child: Text(asset.title, overflow: TextOverflow.ellipsis)),
          const SizedBox(width: 10),
          StatusBadge(asset.kind, dense: true, uppercase: true),
        ],
      ),
      subtitle: Text(
        '${asset.path} · rev ${asset.revision} · '
        'updated ${agoLabel(asset.updatedAt)}'
        '${asset.tags.isEmpty ? "" : " · ${asset.tags.join(", ")}"}',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: [
        _UsagePip(
          usage: _machineUsage[asset.id],
          known: _hasTraffic,
          days: _windowDays,
        ),
        const SizedBox(width: 8),
        StatusBadge(asset.status, tone: toneForStatus(asset.status)),
        const SizedBox(width: 8),
        RowAction(label: 'Open', onTap: () => _openEditor(assetId: asset.id)),
      ],
      onTap: () => _openEditor(assetId: asset.id),
    );
  }

  static int _byPath(AssetSummary a, AssetSummary b) =>
      a.path.compareTo(b.path);

  Widget _assetRow(AssetSummary asset) {
    return CumulusListRow(
      leading: Monogram(
        asset.authorityLevel.substring(0, 1).toUpperCase(),
        tone: toneForAuthority(asset.authorityLevel),
      ),
      title: Row(
        children: [
          Flexible(child: Text(asset.title, overflow: TextOverflow.ellipsis)),
          const SizedBox(width: 10),
          StatusBadge(
            asset.authorityLevel,
            tone: toneForAuthority(asset.authorityLevel),
            dense: true,
          ),
        ],
      ),
      // The revision beside the age, because a governed document is cited at
      // one — every ref in the console reads `knowledge:id@rev3` — and a row
      // that showed only "updated 4m ago" left the reader no way to tell a
      // freshly seeded first draft from a policy on its ninth edit.
      subtitle: Text(
        '${asset.path} · rev ${asset.revision} · '
        'updated ${agoLabel(asset.updatedAt)}'
        '${asset.owner.isEmpty ? "" : " · ${asset.owner}"}'
        '${asset.tags.isEmpty ? "" : " · ${asset.tags.join(", ")}"}',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: [
        _UsagePip(
          usage: _usage[asset.id],
          known: _hasTraffic,
          days: _windowDays,
        ),
        const SizedBox(width: 8),
        StatusBadge(asset.status, tone: toneForStatus(asset.status)),
        const SizedBox(width: 8),
        RowAction(label: 'Open', onTap: () => _openEditor(assetId: asset.id)),
      ],
      onTap: () => _openEditor(assetId: asset.id),
    );
  }

}


/// Compact usage figure for a taxonomy row.
///
/// Reads and citations are shown as separate numbers rather than summed. The
/// total hides the interesting case — opened repeatedly, never good enough to
/// cite — which is exactly the document worth rewriting.
class _UsagePip extends StatelessWidget {
  final AssetUsage? usage;

  /// False until the corpus usage call returns, so an asset is never labelled
  /// dead weight merely because the metrics have not loaded.
  final bool known;

  /// The window the server actually measured, not a literal — the tooltips
  /// state it in prose and would otherwise keep saying "30 days" whatever was
  /// asked for.
  final int days;

  const _UsagePip({
    required this.usage,
    required this.known,
    required this.days,
  });

  @override
  Widget build(BuildContext context) {
    if (!known) return const SizedBox.shrink();
    final reads = usage?.reads ?? 0;
    final citations = usage?.citations ?? 0;
    final impressions = usage?.impressions ?? 0;

    // The two ways a document fails read differently and need different
    // fixes, so the row says which one rather than a shared "unused".
    if (reads == 0 && citations == 0) {
      final ignored = impressions > 0;
      return Tooltip(
        message: ignored
            ? 'Surfaced in $impressions '
                  '${impressions == 1 ? "search" : "searches"} in $days days and '
                  'never opened — it ranks for questions it does not answer'
            : 'Never surfaced in a search in 30 days — nothing can read what '
                  'search does not return',
        child: Text(
          ignored ? 'ignored' : 'undiscovered',
          style: CumulusText.meta.copyWith(
            color: ignored ? CumulusColors.warn : CumulusColors.danger,
          ),
        ),
      );
    }

    return Tooltip(
      message:
          '$impressions surfaced · $reads '
          '${reads == 1 ? "read" : "reads"} · $citations '
          '${citations == 1 ? "citation" : "citations"} in $days days'
          '${usage?.lastUsedAt == null ? "" : " · last ${agoLabel(usage!.lastUsedAt!)}"}',
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(
            Icons.visibility_outlined,
            size: 13,
            color: CumulusColors.inkMuted,
          ),
          const SizedBox(width: 4),
          Text('$reads', style: CumulusText.meta),
          if (citations > 0) ...[
            const SizedBox(width: 8),
            const Icon(
              Icons.format_quote,
              size: 13,
              color: CumulusColors.accent,
            ),
            const SizedBox(width: 2),
            Text(
              '$citations',
              style: CumulusText.meta.copyWith(color: CumulusColors.accentInk),
            ),
          ],
        ],
      ),
    );
  }
}
