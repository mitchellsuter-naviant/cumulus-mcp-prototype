class AssetSummary {
  final String id;
  final String path;
  final String title;
  final String contentType;

  /// Empty when the asset is not a document: an exemplar or a schema export
  /// carries no authority — its [kind] is the signal instead.
  final String authorityLevel;
  final String kind; // document | exemplar | schema-export | prompt | config
  final String origin; // authored | flywheel | derived | synced | flow-written
  final String scope;
  final String owner;
  final List<String> tags;
  final String status;
  final int revision;
  final DateTime updatedAt;

  AssetSummary({
    required this.id,
    required this.path,
    required this.title,
    required this.contentType,
    required this.authorityLevel,
    this.kind = 'document',
    this.origin = 'authored',
    required this.scope,
    required this.owner,
    required this.tags,
    required this.status,
    this.revision = 1,
    required this.updatedAt,
  });

  factory AssetSummary.fromJson(Map<String, dynamic> json) => AssetSummary(
    id: json['id'] as String,
    path: json['path'] as String,
    title: json['title'] as String,
    contentType: json['content_type'] as String,
    authorityLevel: json['authority_level'] as String? ?? '',
    kind: json['kind'] as String? ?? 'document',
    origin: json['origin'] as String? ?? 'authored',
    scope: json['scope'] as String? ?? 'org',
    owner: json['owner'] as String? ?? '',
    tags: (json['tags'] as List<dynamic>? ?? []).cast<String>(),
    status: json['status'] as String,
    revision: json['revision'] as int? ?? 1,
    updatedAt: DateTime.parse(json['updated_at'] as String),
  );
}

class Asset {
  final String id;
  String path;
  String title;
  String content;
  String contentType;
  String authorityLevel; // empty when kind != document
  String kind;
  String origin;
  String scope;
  String owner;
  List<String> tags;
  String? parent;
  List<String> related;
  String? supersedes;
  String? supersededBy;
  String status;

  Asset({
    required this.id,
    required this.path,
    required this.title,
    required this.content,
    this.contentType = 'text/markdown',
    this.authorityLevel = 'note',
    this.kind = 'document',
    this.origin = 'authored',
    this.scope = 'org',
    this.owner = '',
    this.tags = const [],
    this.parent,
    this.related = const [],
    this.supersedes,
    this.supersededBy,
    this.status = 'draft',
  });

  factory Asset.fromJson(Map<String, dynamic> json) => Asset(
    id: json['id'] as String,
    path: json['path'] as String,
    title: json['title'] as String,
    content: json['content'] as String,
    contentType: json['content_type'] as String,
    authorityLevel: json['authority_level'] as String? ?? '',
    kind: json['kind'] as String? ?? 'document',
    origin: json['origin'] as String? ?? 'authored',
    scope: json['scope'] as String? ?? 'org',
    owner: json['owner'] as String? ?? '',
    tags: (json['tags'] as List<dynamic>? ?? []).cast<String>(),
    parent: json['parent'] as String?,
    related: (json['related'] as List<dynamic>? ?? []).cast<String>(),
    supersedes: json['supersedes'] as String?,
    supersededBy: json['superseded_by'] as String?,
    status: json['status'] as String,
  );

  Map<String, dynamic> toJson() => {
    'id': id,
    'path': path,
    'title': title,
    'content': content,
    'content_type': contentType,
    'authority_level': authorityLevel.isEmpty ? null : authorityLevel,
    'kind': kind,
    'origin': origin,
    'scope': scope,
    'owner': owner,
    'tags': tags,
    'parent': parent,
    'related': related,
    'supersedes': supersedes,
    'superseded_by': supersededBy,
    'status': status,
  };
}

class SearchHit {
  final AssetSummary asset;
  final double score;
  final String snippet;

  /// Which retriever found this: `lexical`, `semantic`, or `hybrid`. The
  /// server has always sent it; surfacing it is what lets a result explain
  /// itself when the query shares no words with the document.
  final String retrieval;
  final int? lexicalRank;
  final int? semanticRank;

  /// Cosine against the query, when the semantic retriever scored it. Unlike
  /// [score] this is comparable across queries, so it is the one to read when
  /// deciding whether a hit is genuinely related or just the best of a bad set.
  final double? similarity;

  /// Nearest-neighbour noise rather than a real match, judged by the server
  /// against the rest of the ranking.
  ///
  /// This used to be computed here. It moved server-side because the console,
  /// the MCP tools and the usage counters were each free to draw the line
  /// somewhere different, and a document counted as "surfaced" for metrics
  /// while being hidden in the UI is a contradiction nobody would ever debug.
  final bool weak;

  SearchHit({
    required this.asset,
    required this.score,
    required this.snippet,
    this.retrieval = 'lexical',
    this.lexicalRank,
    this.semanticRank,
    this.similarity,
    this.weak = false,
  });

  /// True when only the vector index found this — no query term appears in the
  /// document. Worth labelling in the UI, because the match is otherwise
  /// invisible to the person reading it.
  bool get isSemanticOnly => retrieval == 'semantic';

  factory SearchHit.fromJson(Map<String, dynamic> json) => SearchHit(
    asset: AssetSummary.fromJson(json['asset'] as Map<String, dynamic>),
    score: (json['score'] as num).toDouble(),
    snippet: json['snippet'] as String,
    retrieval: json['retrieval'] as String? ?? 'lexical',
    lexicalRank: json['lexical_rank'] as int?,
    semanticRank: json['semantic_rank'] as int?,
    similarity: (json['similarity'] as num?)?.toDouble(),
    weak: json['weak'] as bool? ?? false,
  );
}

/// A metadata predicate over the corpus, distinct from the query text.
///
/// Typed into the search bar as `owner:hr authority:policy`, parsed out before
/// the rest of the text is sent as a query. The server applies these before
/// ranking, so a filtered search returns the best matching documents *within*
/// the filter rather than whatever survived a cut of the global top ten.
class SearchFilters {
  final String? owner;
  final String? authorityLevel;
  final String? status;
  final String? tag;
  final String? pathPrefix;

  const SearchFilters({
    this.owner,
    this.authorityLevel,
    this.status,
    this.tag,
    this.pathPrefix,
  });

  bool get isEmpty =>
      owner == null &&
      authorityLevel == null &&
      status == null &&
      tag == null &&
      pathPrefix == null;

  Map<String, String> toQuery() => {
    if (owner != null) 'owner': owner!,
    if (authorityLevel != null) 'authority_level': authorityLevel!,
    if (status != null) 'status': status!,
    if (tag != null) 'tag': tag!,
    if (pathPrefix != null) 'path_prefix': pathPrefix!,
  };

  /// Label/value pairs for rendering the active filters as chips.
  List<(String, String)> get chips => [
    if (owner != null) ('owner', owner!),
    if (authorityLevel != null) ('authority', authorityLevel!),
    if (status != null) ('status', status!),
    if (tag != null) ('tag', tag!),
    if (pathPrefix != null) ('path', pathPrefix!),
  ];
}

/// One document backing a claim in an answer.
class Citation {
  final String assetId;
  final String title;
  final String path;
  final String authorityLevel;
  final String status;
  final String snippet;

  Citation({
    required this.assetId,
    required this.title,
    required this.path,
    required this.authorityLevel,
    required this.status,
    required this.snippet,
  });

  factory Citation.fromJson(Map<String, dynamic> json) => Citation(
    assetId: json['asset_id'] as String,
    title: json['title'] as String,
    path: json['path'] as String,
    authorityLevel: json['authority_level'] as String,
    status: json['status'] as String,
    snippet: json['snippet'] as String? ?? '',
  );
}

/// One tool call the answer planner made, shown behind a disclosure so an
/// answer can be traced back to the queries that produced it.
class AskStep {
  final String tool;
  final Map<String, dynamic> arguments;
  final String summary;

  /// The graph ref the step landed on, when it landed on one — what lets the
  /// explorer replay the planner's walk instead of parsing prose.
  final String ref;

  AskStep({
    required this.tool,
    required this.arguments,
    required this.summary,
    this.ref = '',
  });

  factory AskStep.fromJson(Map<String, dynamic> json) => AskStep(
    tool: json['tool'] as String,
    arguments: (json['arguments'] as Map<String, dynamic>? ?? {}),
    summary: json['summary'] as String? ?? '',
    ref: json['ref'] as String? ?? '',
  );
}

class AskAnswer {
  final String answer;
  final List<Citation> citations;
  final List<AskStep> steps;

  /// `vertex` when a planner reasoned over the corpus, `retrieval` when the
  /// server degraded to ranked documents. Rendered differently on purpose:
  /// prose with no model behind it would misrepresent how it was reached.
  final String backend;
  final bool truncated;

  AskAnswer({
    required this.answer,
    required this.citations,
    required this.steps,
    required this.backend,
    required this.truncated,
  });

  bool get isGenerated => backend != 'retrieval';

  factory AskAnswer.fromJson(Map<String, dynamic> json) => AskAnswer(
    answer: json['answer'] as String,
    citations: (json['citations'] as List<dynamic>? ?? [])
        .map((j) => Citation.fromJson(j as Map<String, dynamic>))
        .toList(),
    steps: (json['steps'] as List<dynamic>? ?? [])
        .map((j) => AskStep.fromJson(j as Map<String, dynamic>))
        .toList(),
    backend: json['backend'] as String? ?? 'retrieval',
    truncated: json['truncated'] as bool? ?? false,
  );
}

/// One refinement option and how many documents it would leave.
class FacetValue {
  final String value;
  final String label;
  final int count;

  FacetValue({required this.value, required this.label, required this.count});

  factory FacetValue.fromJson(Map<String, dynamic> json) => FacetValue(
    value: json['value'] as String,
    label: json['label'] as String,
    count: json['count'] as int,
  );
}

/// Refinement counts for a query.
///
/// Counted over lexical matches rather than over every hit: a dense retriever
/// scores the whole corpus, so counting hits would put the same total behind
/// every value. Each dimension is counted with the other filters applied but
/// not its own, so picking one owner does not zero out the rest.
class SearchFacets {
  final int total;
  final List<FacetValue> owner;
  final List<FacetValue> authorityLevel;
  final List<FacetValue> status;
  final List<FacetValue> branch;

  const SearchFacets({
    this.total = 0,
    this.owner = const [],
    this.authorityLevel = const [],
    this.status = const [],
    this.branch = const [],
  });

  bool get isEmpty =>
      owner.isEmpty && authorityLevel.isEmpty && status.isEmpty && branch.isEmpty;

  static List<FacetValue> _list(dynamic raw) => (raw as List<dynamic>? ?? [])
      .map((j) => FacetValue.fromJson(j as Map<String, dynamic>))
      .toList();

  factory SearchFacets.fromJson(Map<String, dynamic> json) => SearchFacets(
    total: json['total'] as int? ?? 0,
    owner: _list(json['owner']),
    authorityLevel: _list(json['authority_level']),
    status: _list(json['status']),
    branch: _list(json['branch']),
  );
}

/// How a result page is ordered. Applied after ranking, so it reorders the
/// best matches rather than selecting different ones.
enum SearchSort {
  relevance('relevance', 'Relevance'),
  updated('updated', 'Recently updated'),
  authority('authority', 'Authority');

  final String wire;
  final String label;
  const SearchSort(this.wire, this.label);
}

/// One day of recorded usage for one asset.
class AssetUsageDay {
  final String day;
  final Map<String, int> events;
  final DateTime? lastUsedAt;

  AssetUsageDay({required this.day, required this.events, this.lastUsedAt});

  int get total => events.values.fold(0, (a, b) => a + b);

  factory AssetUsageDay.fromJson(Map<String, dynamic> json) => AssetUsageDay(
    day: json['day'] as String,
    events: (json['events'] as Map<String, dynamic>? ?? {})
        .map((k, v) => MapEntry(k, v as int)),
    lastUsedAt: json['last_used_at'] == null
        ? null
        : DateTime.parse(json['last_used_at'] as String),
  );
}

/// What a document is actually used for, over a window.
///
/// `reads` and `citations` are separate because the ratio is the signal: a
/// document read often but never cited is being opened and discarded, and one
/// with neither is dead weight regardless of how well it ranks.
class AssetUsage {
  final String assetId;
  final int reads;
  final int citations;

  /// Times it surfaced as a real match for a search. Weak on its own, but it
  /// is the denominator that tells a findability problem from a quality one.
  final int impressions;

  final int total;

  /// Which protocol drove the traffic: `rest`, `mcp`, or `a2a`.
  final Map<String, int> surfaces;

  /// Keyed by identity uid.
  final Map<String, int> actors;

  final DateTime? lastUsedAt;
  final List<AssetUsageDay> days;

  const AssetUsage({
    required this.assetId,
    this.reads = 0,
    this.citations = 0,
    this.impressions = 0,
    this.total = 0,
    this.surfaces = const {},
    this.actors = const {},
    this.lastUsedAt,
    this.days = const [],
  });

  /// Nothing consumed it. Surfacing in search does not count as use.
  bool get unused => reads == 0 && citations == 0;

  /// Found repeatedly, opened by nobody — a relevance problem. It ranks for
  /// queries it does not answer. Rewrite or retitle it.
  bool get ignored => impressions > 0 && unused;

  /// Never even surfaced — a findability problem. Nothing can read what search
  /// never returns, so better prose would change nothing; tags, title or
  /// taxonomy is the fix.
  bool get undiscovered => impressions == 0 && unused;

  static Map<String, int> _counts(dynamic raw) =>
      (raw as Map<String, dynamic>? ?? {}).map((k, v) => MapEntry(k, v as int));

  factory AssetUsage.fromJson(Map<String, dynamic> json) => AssetUsage(
    assetId: json['asset_id'] as String,
    reads: json['reads'] as int? ?? 0,
    citations: json['citations'] as int? ?? 0,
    impressions: json['impressions'] as int? ?? 0,
    total: json['total'] as int? ?? 0,
    surfaces: _counts(json['surfaces']),
    actors: _counts(json['actors']),
    lastUsedAt: json['last_used_at'] == null
        ? null
        : DateTime.parse(json['last_used_at'] as String),
    days: (json['days'] as List<dynamic>? ?? [])
        .map((j) => AssetUsageDay.fromJson(j as Map<String, dynamic>))
        .toList(),
  );
}
