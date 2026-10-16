import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import '../theme/domain_tones.dart';
import '../widgets/answer_view.dart';
import '../widgets/command_palette.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/ui_kit.dart';

/// The full search view: everything the dropdown deliberately leaves out.
///
/// The two surfaces answer different questions. The palette is for
/// recognition — you know roughly what you want, you type three letters and
/// jump. It has to stay short, which means it can only ever show the strongest
/// handful of results. This screen is for exploration — you do not know what
/// is in the corpus, you want to see the shape of it, narrow it down, and
/// understand why things matched.
///
/// So the split is not "small list" versus "big list". It is: the palette
/// hides the weak tail, the facets, the ranking controls and the provenance,
/// and this screen is where all four live. Handing off carries the query and
/// filters across unchanged, and ⌘K reopens the dropdown from here.
class SearchView extends StatefulWidget {
  final String query;
  final SearchFilters filters;

  /// Back to the taxonomy view.
  final VoidCallback onClose;
  final void Function(String assetId) onOpenAsset;

  /// Re-runs the search with a different filter set, by asking the owner of
  /// the query state to update it. Keeps one source of truth for what is being
  /// searched rather than forking it here.
  final void Function(String query, SearchFilters filters) onRefine;

  const SearchView({
    super.key,
    required this.query,
    required this.filters,
    required this.onClose,
    required this.onOpenAsset,
    required this.onRefine,
  });

  @override
  State<SearchView> createState() => _SearchViewState();
}

class _SearchViewState extends State<SearchView> {
  final ApiClient _api = ApiClient();

  List<SearchHit> _hits = const [];
  SearchFacets _facets = const SearchFacets();
  SearchSort _sort = SearchSort.relevance;
  String _mode = 'hybrid';

  /// Whether this deployment has a vector half at all, per
  /// `GET /api/v1/search/status`. Null until the probe lands.
  ///
  /// The retriever chips used to be drawn unconditionally, so on a deployment
  /// with no embedder a person could select `semantic`, watch the result list
  /// go empty, and have no way to learn that the mode they picked was never
  /// running — the server degrades to lexical internally and says so only
  /// here. A control that cannot do what its label says is worse than a
  /// missing control, so it is not drawn.
  bool? _hybrid;

  bool _loading = true;
  String? _error;
  bool _showWeak = false;

  AskAnswer? _answer;
  bool _asking = false;
  String? _askError;

  /// Discards responses from a superseded query, exactly as the palette does —
  /// refining twice quickly must not let the first response win.
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    _run();
    _probeRetrieval();
    if (looksLikeQuestion(widget.query)) _ask();
  }

  /// Asks what the retrieval stack is actually doing, once.
  ///
  /// Not awaited before the first paint and deliberately not re-running the
  /// search when it lands: a lexical-only server already answers a `hybrid`
  /// request lexically, so the hits on screen are correct either way. What
  /// changes is what the console is willing to *claim* about them.
  Future<void> _probeRetrieval() async {
    final hybrid = await _api.hybridSearchEnabled();
    if (!mounted) return;
    setState(() {
      _hybrid = hybrid;
      if (!hybrid) _mode = 'lexical';
    });
  }

  @override
  void didUpdateWidget(SearchView oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.query != widget.query ||
        !_sameFilters(oldWidget.filters, widget.filters)) {
      _run();
      // A new question deserves a new answer; a pure refinement does not.
      if (oldWidget.query != widget.query) {
        setState(() => _answer = null);
        if (looksLikeQuestion(widget.query)) _ask();
      }
    }
  }

  static bool _sameFilters(SearchFilters a, SearchFilters b) =>
      a.owner == b.owner &&
      a.authorityLevel == b.authorityLevel &&
      a.status == b.status &&
      a.tag == b.tag &&
      a.pathPrefix == b.pathPrefix;

  @override
  void dispose() {
    _api.close();
    super.dispose();
  }

  Future<void> _run() async {
    final generation = ++_generation;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      // Both in flight at once: the counts and the page are independent, and
      // waiting for one to start the other doubles the time to first paint.
      final results = await Future.wait([
        _api.search(
          widget.query,
          limit: 50,
          filters: widget.filters,
          sort: _sort,
          mode: _mode,
        ),
        _api.searchFacets(widget.query, filters: widget.filters),
      ]);
      if (generation != _generation || !mounted) return;
      setState(() {
        _hits = results[0] as List<SearchHit>;
        _facets = results[1] as SearchFacets;
        _loading = false;
      });
    } catch (e) {
      if (generation != _generation || !mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  Future<void> _ask() async {
    setState(() {
      _asking = true;
      _askError = null;
    });
    try {
      final answer = await _api.ask(widget.query);
      if (!mounted) return;
      setState(() {
        _answer = answer;
        _asking = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _askError = '$e';
        _asking = false;
      });
    }
  }

  /// Toggling a facet value off means clearing that dimension, not setting it
  /// to something else — which is why each of these passes null.
  void _toggleFacet(String dimension, String value) {
    final f = widget.filters;
    final selected = switch (dimension) {
      'owner' => f.owner,
      'authority' => f.authorityLevel,
      'status' => f.status,
      _ => f.pathPrefix,
    };
    final next = selected == value ? null : value;
    widget.onRefine(
      widget.query,
      SearchFilters(
        owner: dimension == 'owner' ? next : f.owner,
        authorityLevel: dimension == 'authority' ? next : f.authorityLevel,
        status: dimension == 'status' ? next : f.status,
        tag: f.tag,
        pathPrefix: dimension == 'branch' ? next : f.pathPrefix,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final strong = [
      for (final h in _hits)
        if (!isWeakSemanticHit(h)) h,
    ];
    final weak = [
      for (final h in _hits)
        if (isWeakSemanticHit(h)) h,
    ];

    return PageBody(
      children: [
        _heading(strong.length, weak.length),
        if (_error != null) ErrorNotice(message: _error!, onRetry: _run),
        if (_answer != null || _asking || _askError != null) _answerCard(),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(width: 244, child: _facetColumn()),
            const SizedBox(width: 16),
            Expanded(child: _results(strong, weak)),
          ],
        ),
      ],
    );
  }

  Widget _heading(int strong, int weak) {
    final label = [
      if (widget.query.isNotEmpty) '"${widget.query}"',
      for (final (name, value) in widget.filters.chips) '$name: $value',
    ].join(' · ');
    return PageHeading(
      eyebrow: 'Knowledge layer · search',
      title: label.isEmpty ? 'Search' : label,
      pills: [
        MetaPill('$strong ${strong == 1 ? "result" : "results"}'),
        if (weak > 0) MetaPill('$weak weaker', tone: CumulusTone.neutral),
        MetaPill(_mode, tone: CumulusTone.accent),
      ],
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (_answer == null && !_asking)
            OutlinedButton.icon(
              onPressed: _ask,
              icon: const Icon(Icons.auto_awesome_outlined, size: 16),
              label: const Text('Ask Cumulus'),
            ),
          const SizedBox(width: 10),
          OutlinedButton.icon(
            onPressed: widget.onClose,
            icon: const Icon(Icons.arrow_back, size: 16),
            label: const Text('Back to corpus'),
          ),
        ],
      ),
    );
  }

  Widget _answerCard() {
    if (_asking) {
      return const CumulusCard(
        child: Row(
          children: [
            SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(strokeWidth: 1.8),
            ),
            SizedBox(width: 12),
            Text('Reading the corpus…', style: CumulusText.body),
          ],
        ),
      );
    }
    if (_askError != null) {
      return ErrorNotice(message: _askError!, onRetry: _ask);
    }
    // Same renderer as the palette dropdown: bold, inline code, lists, and
    // citations as chips rather than raw markdown on the canvas.
    return AnswerPanel(answer: _answer!, onOpenAsset: widget.onOpenAsset);
  }

  Widget _facetColumn() {
    if (_facets.isEmpty && !_loading) {
      return const SizedBox.shrink();
    }
    return CumulusCard(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Text('REFINE', style: CumulusText.statLabel),
              const Spacer(),
              if (!widget.filters.isEmpty)
                RowAction(
                  label: 'Clear',
                  onTap: () =>
                      widget.onRefine(widget.query, const SearchFilters()),
                ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            '${_facets.total} '
            '${_facets.total == 1 ? "document" : "documents"} match the query',
            style: CumulusText.meta,
          ),
          const SizedBox(height: 12),
          _facetGroup('Owner', 'owner', _facets.owner, widget.filters.owner),
          _facetGroup(
            'Authority',
            'authority',
            _facets.authorityLevel,
            widget.filters.authorityLevel,
          ),
          _facetGroup('Status', 'status', _facets.status, widget.filters.status),
          _facetGroup(
            'Taxonomy',
            'branch',
            _facets.branch,
            widget.filters.pathPrefix,
          ),
        ],
      ),
    );
  }

  Widget _facetGroup(
    String title,
    String dimension,
    List<FacetValue> values,
    String? selected,
  ) {
    if (values.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(title, style: CumulusText.sectionSubtitle),
          const SizedBox(height: 6),
          for (final value in values)
            _FacetRow(
              value: value,
              selected: selected == value.value,
              onTap: () => _toggleFacet(dimension, value.value),
            ),
        ],
      ),
    );
  }

  Widget _results(List<SearchHit> strong, List<SearchHit> weak) {
    if (_loading) return const CumulusCard(child: CumulusLoading());
    if (strong.isEmpty && weak.isEmpty) {
      return CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.search_off,
          title: 'Nothing matches',
          message:
              'No document in the governed corpus matches this query and '
              'these filters. Try clearing a filter, or ask Cumulus the '
              'question directly.',
          action: OutlinedButton(
            onPressed: () => widget.onRefine(widget.query, const SearchFilters()),
            child: const Text('Clear filters'),
          ),
        ),
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _controls(),
        const SizedBox(height: 12),
        for (final hit in strong) ...[
          _ResultCard(hit: hit, onOpen: () => widget.onOpenAsset(hit.asset.id)),
          const SizedBox(height: 10),
        ],
        if (weak.isNotEmpty) _weakSection(weak),
      ],
    );
  }

  Widget _controls() {
    return CumulusCard(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Row(
        children: [
          const Icon(Icons.swap_vert, size: 15, color: CumulusColors.inkMuted),
          const SizedBox(width: 8),
          DropdownButton<SearchSort>(
            value: _sort,
            underline: const SizedBox.shrink(),
            isDense: true,
            style: CumulusText.body,
            items: [
              for (final sort in SearchSort.values)
                DropdownMenuItem(value: sort, child: Text(sort.label)),
            ],
            onChanged: (value) {
              if (value == null) return;
              setState(() => _sort = value);
              _run();
            },
          ),
          const Spacer(),
          // Lexical only, and said out loud rather than left as one chip that
          // looks like a choice. A deployment with no embedder is a normal
          // deployment, not a broken one, and the honest thing is to name what
          // is ranking the results.
          if (_hybrid == false)
            Text(
              'lexical only — this deployment has no embedder',
              style: CumulusText.meta,
            ),
          // The API has always supported forcing one retriever. Exposing it is
          // the fastest way to answer "why did this match?" — flip to lexical
          // and a result that vanishes was never a keyword match. Drawn only
          // once the server has confirmed both retrievers exist: a chip that
          // returns an empty list and cannot say why is a dead end.
          if (_hybrid == true)
            for (final mode in const ['hybrid', 'lexical', 'semantic'])
              Padding(
                padding: const EdgeInsets.only(left: 8),
                child: LensChip(
                  label: mode,
                  selected: _mode == mode,
                  tone: mode == 'semantic'
                      ? CumulusTone.violet
                      : CumulusTone.accent,
                  onTap: () {
                    if (_mode == mode) return;
                    setState(() => _mode = mode);
                    _run();
                  },
                ),
              ),
        ],
      ),
    );
  }

  Widget _weakSection(List<SearchHit> weak) {
    return CumulusCard(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          InkWell(
            onTap: () => setState(() => _showWeak = !_showWeak),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  Icon(
                    _showWeak ? Icons.expand_less : Icons.expand_more,
                    size: 18,
                    color: CumulusColors.inkMuted,
                  ),
                  const SizedBox(width: 8),
                  Text(
                    '${weak.length} weaker '
                    '${weak.length == 1 ? "match" : "matches"}',
                    style: CumulusText.rowTitle,
                  ),
                  const SizedBox(width: 10),
                  const Expanded(
                    child: Text(
                      'nearest neighbours — the vector index scores every '
                      'document, so these share no term with the query',
                      style: CumulusText.meta,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ),
          ),
          if (_showWeak) ...[
            const SizedBox(height: 10),
            for (final hit in weak) ...[
              _ResultCard(
                hit: hit,
                dimmed: true,
                onOpen: () => widget.onOpenAsset(hit.asset.id),
              ),
              const SizedBox(height: 10),
            ],
          ],
        ],
      ),
    );
  }
}

class _FacetRow extends StatelessWidget {
  final FacetValue value;
  final bool selected;
  final VoidCallback onTap;

  const _FacetRow({
    required this.value,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(7),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 4, horizontal: 4),
        child: Row(
          children: [
            Icon(
              selected ? Icons.check_box : Icons.check_box_outline_blank,
              size: 15,
              color: selected ? CumulusColors.accent : CumulusColors.inkMuted,
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                value.label,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 12.5,
                  color: selected
                      ? CumulusColors.accentInk
                      : CumulusColors.inkSecondary,
                  fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                ),
              ),
            ),
            Text('${value.count}', style: CumulusText.meta),
          ],
        ),
      ),
    );
  }
}

/// One result, with the provenance the dropdown has no room for.
class _ResultCard extends StatelessWidget {
  final SearchHit hit;
  final bool dimmed;
  final VoidCallback onOpen;

  const _ResultCard({
    required this.hit,
    required this.onOpen,
    this.dimmed = false,
  });

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: dimmed ? 0.72 : 1,
      child: CumulusCard(
        padding: const EdgeInsets.fromLTRB(18, 16, 18, 14),
        child: InkWell(
          onTap: onOpen,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Monogram(
                    hit.asset.authorityLevel.substring(0, 1).toUpperCase(),
                    tone: toneForAuthority(hit.asset.authorityLevel),
                    size: 26,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      hit.asset.title,
                      style: CumulusText.sectionTitle,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  StatusBadge(
                    hit.asset.authorityLevel,
                    tone: toneForAuthority(hit.asset.authorityLevel),
                    dense: true,
                  ),
                  const SizedBox(width: 6),
                  StatusBadge(
                    hit.asset.status,
                    tone: toneForStatus(hit.asset.status),
                    dense: true,
                  ),
                ],
              ),
              const SizedBox(height: 10),
              Text(hit.snippet, style: CumulusText.body),
              const SizedBox(height: 12),
              _provenance(),
            ],
          ),
        ),
      ),
    );
  }

  /// Why this result is here and how confident the ranking is.
  ///
  /// The server has always reported all of this; nothing else in the console
  /// showed it. Two retrievers agreeing is a much stronger signal than either
  /// alone, and a semantic-only hit with a mediocre cosine is worth treating
  /// with suspicion — which you cannot do if the interface hides the number.
  Widget _provenance() {
    final tone = switch (hit.retrieval) {
      'semantic' => CumulusTone.violet,
      'hybrid' => CumulusTone.accent,
      _ => CumulusTone.neutral,
    };
    return Wrap(
      spacing: 8,
      runSpacing: 6,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        StatusBadge(hit.retrieval, tone: tone, dense: true),
        Text(hit.asset.path, style: CumulusText.mono.copyWith(fontSize: 11)),
        if (hit.asset.owner.isNotEmpty)
          Text('owner ${hit.asset.owner}', style: CumulusText.meta),
        if (hit.lexicalRank != null)
          Text('keyword #${hit.lexicalRank}', style: CumulusText.meta),
        if (hit.semanticRank != null)
          Text('vector #${hit.semanticRank}', style: CumulusText.meta),
        if (hit.similarity != null)
          Text(
            'cos ${hit.similarity!.toStringAsFixed(3)}',
            style: CumulusText.meta,
          ),
        Text('updated ${agoLabel(hit.asset.updatedAt)}',
            style: CumulusText.meta),
      ],
    );
  }
}
