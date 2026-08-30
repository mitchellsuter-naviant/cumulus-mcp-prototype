/// The Sources screen, the artifact store, and the derived vendor profile.
/// Mirrors `SourcesScreen` in `server/app/ui_api.py`, plus `documents.py`
/// and `anomaly.py`.
library;

/// Where on the page a value was read from. Normalized 0–1 coordinates, so a
/// viewer draws the box at any render size.
class DocumentRegion {
  final String field;
  final int page;
  final double x;
  final double y;
  final double width;
  final double height;
  final String text;
  final double confidence;

  const DocumentRegion({
    required this.field,
    required this.page,
    required this.x,
    required this.y,
    required this.width,
    required this.height,
    required this.text,
    required this.confidence,
  });

  factory DocumentRegion.fromJson(Map<String, dynamic> json) => DocumentRegion(
    field: json['field'] as String? ?? '',
    page: json['page'] as int? ?? 1,
    x: (json['x'] as num? ?? 0).toDouble(),
    y: (json['y'] as num? ?? 0).toDouble(),
    width: (json['width'] as num? ?? 0).toDouble(),
    height: (json['height'] as num? ?? 0).toDouble(),
    text: json['text'] as String? ?? '',
    confidence: (json['confidence'] as num? ?? 0).toDouble(),
  );
}

class StoredDocument {
  final String id;
  final String sha256;
  final String filename;
  final String mediaType;
  final int byteSize;
  final String kind;
  final int pages;
  final int regionCount;
  final String collectionId;
  final String retentionUntilIso;
  final String uploadedBy;
  final String uploadedAtIso;
  final List<String> runIds;
  final bool hasBytes;
  final List<DocumentRegion> regions;

  const StoredDocument({
    required this.id,
    required this.sha256,
    required this.filename,
    required this.mediaType,
    required this.byteSize,
    required this.kind,
    required this.pages,
    required this.regionCount,
    required this.collectionId,
    required this.retentionUntilIso,
    required this.uploadedBy,
    required this.uploadedAtIso,
    required this.runIds,
    required this.hasBytes,
    required this.regions,
  });

  factory StoredDocument.fromJson(Map<String, dynamic> json) => StoredDocument(
    id: json['id'] as String? ?? '',
    sha256: json['sha256'] as String? ?? '',
    filename: json['filename'] as String? ?? '',
    mediaType: json['media_type'] as String? ?? '',
    byteSize: json['byte_size'] as int? ?? 0,
    kind: json['kind'] as String? ?? '',
    pages: json['pages'] as int? ?? 1,
    regionCount: json['region_count'] as int? ??
        (json['regions'] as List<dynamic>? ?? const []).length,
    collectionId: json['collection_id'] as String? ?? '',
    retentionUntilIso: json['retention_until_iso'] as String? ?? '',
    uploadedBy: json['uploaded_by'] as String? ?? '',
    uploadedAtIso: json['uploaded_at_iso'] as String? ?? '',
    runIds: (json['run_ids'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    hasBytes: json['has_bytes'] as bool? ??
        ((json['content_b64'] as String? ?? '').isNotEmpty),
    regions: (json['regions'] as List<dynamic>? ?? const [])
        .map((e) => DocumentRegion.fromJson(e as Map<String, dynamic>))
        .toList(),
  );
}

/// The Mapping a feed lands under — the declared half of the arrangement,
/// carried on the feed itself so health and contract are never read off two
/// lists side by side.
class SourceContract {
  final String id;
  final String system;
  final String transport;
  final String payload;

  /// `reify` mints an object per row; `bind` declares that a column carries
  /// identifiers of a type and resolves them lazily. A million readings must
  /// never become a million objects, and this is where that is decided.
  final String strategy;
  final String landsAs;
  final List<String> resolvesTo;
  final String matchPolicy;
  final String precedenceSource;
  final String freshness;
  final String custody; // copy | reference
  final String collection;
  final String escalation;

  const SourceContract({
    required this.id,
    required this.system,
    required this.transport,
    required this.payload,
    required this.strategy,
    required this.landsAs,
    required this.resolvesTo,
    required this.matchPolicy,
    required this.precedenceSource,
    required this.freshness,
    required this.custody,
    required this.collection,
    required this.escalation,
  });

  factory SourceContract.fromJson(Map<String, dynamic> json) => SourceContract(
    id: json['id'] as String? ?? '',
    system: json['system'] as String? ?? '',
    transport: json['transport'] as String? ?? '',
    payload: json['payload'] as String? ?? '',
    strategy: json['strategy'] as String? ?? '',
    landsAs: json['lands_as'] as String? ?? '',
    resolvesTo: (json['resolves_to'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    matchPolicy: json['match_policy'] as String? ?? '',
    precedenceSource: json['precedence_source'] as String? ?? '',
    freshness: json['freshness'] as String? ?? '',
    custody: json['custody'] as String? ?? 'copy',
    collection: json['collection'] as String? ?? '',
    escalation: json['escalation'] as String? ?? '',
  );
}

/// An artifact a feed's run left behind, attributed through the run rather
/// than the flow — three feeds share `vendor-sync`, so grouping by flow would
/// credit each of them with all three feeds' documents.
class SourceArtifact {
  final String id;
  final String filename;
  final String kind;
  final String uploadedAtIso;
  final int regionCount;
  final String retentionUntilIso;

  const SourceArtifact({
    required this.id,
    required this.filename,
    required this.kind,
    required this.uploadedAtIso,
    required this.regionCount,
    required this.retentionUntilIso,
  });

  factory SourceArtifact.fromJson(Map<String, dynamic> json) => SourceArtifact(
    id: json['id'] as String? ?? '',
    filename: json['filename'] as String? ?? '',
    kind: json['kind'] as String? ?? '',
    uploadedAtIso: json['uploaded_at_iso'] as String? ?? '',
    regionCount: json['region_count'] as int? ?? 0,
    retentionUntilIso: json['retention_until_iso'] as String? ?? '',
  );
}

/// One inbound feed: how it is running, what it promised, what it left behind.
///
/// The connector's half of a feed Cumulus *pulls* — its cursor, and how the
/// last pull went.
///
/// Null on a feed that pushes through the door on its own cadence: that feed
/// has no cursor here and no Sync now, and the null is that fact rather than
/// missing data. The state cannot be derived from the runs the way the rest of
/// the row is — a pull that found nothing starts no run, and a pull that
/// failed has no run at all.
class PullState {
  /// Whether discovery says the namespace actually runs this feed's app. A
  /// registry row for an app the customer does not run is inert, not broken.
  final bool active;
  final String estateApp;
  final String namespace;

  /// The list routes this feed pulls together, in delivery order.
  final List<String> resources;

  /// Per resource, because they move independently — an employee change must
  /// not re-pull the jobs catalogue.
  final Map<String, String> watermarks;

  final String lastPullAt;
  final String lastOutcome;
  final String lastError;

  /// Only the dev interval loop knows when the next pull is due; under a
  /// managed scheduler the cadence belongs to the scheduler and this is empty.
  final String nextPullAt;

  final bool syncNowAllowed;

  /// Why not, when not. An access boundary is a sentence the screen prints
  /// next to a control it does not draw — never a button that fails.
  final String syncNowRefusal;

  const PullState({
    required this.active,
    required this.estateApp,
    required this.namespace,
    required this.resources,
    required this.watermarks,
    required this.lastPullAt,
    required this.lastOutcome,
    required this.lastError,
    required this.nextPullAt,
    required this.syncNowAllowed,
    required this.syncNowRefusal,
  });

  bool get everPulled => lastPullAt.isNotEmpty;

  bool get failed => lastOutcome == 'failed' || lastError.isNotEmpty;

  /// The furthest point any of this feed's cursors has reached — what the next
  /// pull will ask the estate for. The largest, not the first: the resources
  /// advance independently and the row states one number.
  String get watermark {
    if (watermarks.isEmpty) return '';
    return watermarks.values.reduce((a, b) => a.compareTo(b) >= 0 ? a : b);
  }

  factory PullState.fromJson(Map<String, dynamic> json) => PullState(
    active: json['active'] as bool? ?? false,
    estateApp: json['estate_app'] as String? ?? '',
    namespace: json['namespace'] as String? ?? '',
    resources: (json['resources'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    watermarks: (json['watermarks'] as Map<String, dynamic>? ?? const {})
        .map((k, v) => MapEntry(k, '$v')),
    lastPullAt: json['last_pull_at'] as String? ?? '',
    lastOutcome: json['last_outcome'] as String? ?? '',
    lastError: json['last_error'] as String? ?? '',
    nextPullAt: json['next_pull_at'] as String? ?? '',
    syncNowAllowed: json['sync_now_allowed'] as bool? ?? false,
    syncNowRefusal: json['sync_now_refusal'] as String? ?? '',
  );
}

/// The registered estate, above the feeds it explains.
///
/// Screen-level rather than per-row because registration is one fact for the
/// whole connector: five feeds idle because no estate is registered is one
/// finding, and repeating it down a list buries it.
class EstateCard {
  final bool registered;
  final String project;
  final String namespace;
  final String discoveryUrl;

  /// Whether a credential is configured — never which one.
  final bool authenticated;

  final int pullIntervalSeconds;

  /// Set when discovery itself failed. Its own state: an estate that could not
  /// be reached is not an estate that runs no apps.
  final String discoveryError;

  final String industry;

  /// What discovery says this namespace runs. The feed registry proposes and
  /// this disposes, so a feed whose app is absent renders as inactive.
  final List<String> apps;

  final List<String> processes;

  /// Null when the discovery payload carries no pulse block — an estate that
  /// reports no simulation is not one whose simulation is stopped.
  final bool? pulseEnabled;

  final String pulseIntensity;

  /// Feeds in the connector's registry, and how many of them this namespace
  /// runs. Both counts are answered by rows in the list below.
  final int feedsRegistered;
  final int feedsActive;

  const EstateCard({
    required this.registered,
    required this.project,
    required this.namespace,
    required this.discoveryUrl,
    required this.authenticated,
    required this.pullIntervalSeconds,
    required this.discoveryError,
    required this.industry,
    required this.apps,
    required this.processes,
    required this.pulseEnabled,
    required this.pulseIntensity,
    required this.feedsRegistered,
    required this.feedsActive,
  });

  static const none = EstateCard(
    registered: false,
    project: '',
    namespace: '',
    discoveryUrl: '',
    authenticated: false,
    pullIntervalSeconds: 0,
    discoveryError: '',
    industry: '',
    apps: [],
    processes: [],
    pulseEnabled: null,
    pulseIntensity: '',
    feedsRegistered: 0,
    feedsActive: 0,
  );

  factory EstateCard.fromJson(Map<String, dynamic> json) => EstateCard(
    registered: json['registered'] as bool? ?? false,
    project: json['project'] as String? ?? '',
    namespace: json['namespace'] as String? ?? '',
    discoveryUrl: json['discovery_url'] as String? ?? '',
    authenticated: json['authenticated'] as bool? ?? false,
    pullIntervalSeconds: json['pull_interval_seconds'] as int? ?? 0,
    discoveryError: json['discovery_error'] as String? ?? '',
    industry: json['industry'] as String? ?? '',
    apps: (json['apps'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    processes: (json['processes'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    pulseEnabled: json['pulse_enabled'] as bool?,
    pulseIntensity: json['pulse_intensity'] as String? ?? '',
    feedsRegistered: json['feeds_registered'] as int? ?? 0,
    feedsActive: json['feeds_active'] as int? ?? 0,
  );
}

/// Assembled from the sync runs themselves rather than a second bookkeeping
/// store, so a feed with no run has never delivered and this cannot pretend
/// otherwise.
class SourceFeed {
  final String source;
  final String kind;
  final String flowId;
  final String lastRunId;
  final String lastRunAtIso;
  final String lastStatus;
  final String lastError;
  final String watermark;
  final int deliveries;
  final int recordsLanded;
  final int linked;
  final int created;
  final int awaitingSteward;
  final int unchanged;

  /// Null when no Mapping claims this feed — rows arriving under no declared
  /// contract, which is exactly the finding the screen is for.
  final SourceContract? contract;

  /// Null for a feed that pushes. See [PullState].
  final PullState? pull;

  final List<SourceArtifact> artifacts;

  /// What [artifacts] is a window onto: a feed that dropped a thousand scanned
  /// reports should not turn a list screen into a megabyte.
  final int artifactsTotal;

  const SourceFeed({
    required this.source,
    required this.kind,
    required this.flowId,
    required this.lastRunId,
    required this.lastRunAtIso,
    required this.lastStatus,
    required this.lastError,
    required this.watermark,
    required this.deliveries,
    required this.recordsLanded,
    required this.linked,
    required this.created,
    required this.awaitingSteward,
    required this.unchanged,
    required this.contract,
    required this.pull,
    required this.artifacts,
    required this.artifactsTotal,
  });

  bool get failed =>
      lastStatus == 'failed' || lastError.isNotEmpty || (pull?.failed ?? false);

  /// True for a registered feed that has never delivered: the row exists so
  /// the pull that would start it has somewhere to be pressed.
  bool get neverDelivered => deliveries == 0 && lastRunAtIso.isEmpty;

  factory SourceFeed.fromJson(Map<String, dynamic> json) => SourceFeed(
    source: json['source'] as String? ?? '',
    kind: json['kind'] as String? ?? '',
    flowId: json['flow_id'] as String? ?? '',
    lastRunId: json['last_run_id'] as String? ?? '',
    lastRunAtIso: json['last_run_at_iso'] as String? ?? '',
    lastStatus: json['last_status'] as String? ?? '',
    lastError: json['last_error'] as String? ?? '',
    watermark: json['watermark'] as String? ?? '',
    deliveries: json['deliveries'] as int? ?? 0,
    recordsLanded: json['records_landed'] as int? ?? 0,
    linked: json['linked'] as int? ?? 0,
    created: json['created'] as int? ?? 0,
    awaitingSteward: json['awaiting_steward'] as int? ?? 0,
    unchanged: json['unchanged'] as int? ?? 0,
    contract: json['contract'] == null
        ? null
        : SourceContract.fromJson(json['contract'] as Map<String, dynamic>),
    pull: json['pull'] == null
        ? null
        : PullState.fromJson(json['pull'] as Map<String, dynamic>),
    artifacts: (json['artifacts'] as List<dynamic>? ?? const [])
        .map((e) => SourceArtifact.fromJson(e as Map<String, dynamic>))
        .toList(),
    artifactsTotal: json['artifacts_total'] as int? ?? 0,
  );
}

/// Everything the Sources screen draws, in one round trip.
class SourcesScreen {
  final List<SourceFeed> sources;

  /// The estate the pulled feeds below are pulled from.
  final EstateCard estate;

  /// Mappings naming a type or Collection that does not exist. Null — not
  /// empty — for a tenant-scoped persona: a registry bug is a Naviant bug,
  /// and an alarm a customer can take no action on is worse than no alarm.
  final List<String>? problems;

  const SourcesScreen({
    required this.sources,
    required this.estate,
    required this.problems,
  });

  /// The same screen with one feed's row replaced.
  ///
  /// A sync answers with the row it left behind, so the screen redraws that
  /// feed from the same read that reported the result rather than re-fetching
  /// and hoping the two agree.
  SourcesScreen replacing(SourceFeed feed) => SourcesScreen(
    sources: [
      for (final f in sources)
        f.source == feed.source && f.kind == feed.kind ? feed : f,
    ],
    estate: estate,
    problems: problems,
  );

  factory SourcesScreen.fromJson(Map<String, dynamic> json) => SourcesScreen(
    sources: (json['sources'] as List<dynamic>? ?? const [])
        .map((e) => SourceFeed.fromJson(e as Map<String, dynamic>))
        .toList(),
    estate: json['estate'] == null
        ? EstateCard.none
        : EstateCard.fromJson(json['estate'] as Map<String, dynamic>),
    problems: (json['problems'] as List<dynamic>?)
        ?.map((e) => '$e')
        .toList(),
  );
}

/// What one Sync now did, and the row it leaves behind.
///
/// The row travels with the outcome so a count in the banner and the count in
/// the list under it come from one read and cannot disagree.
class SyncNowOutcome {
  final String source;

  /// `delivered` · `unchanged` · `failed`.
  final String outcome;

  /// Records read from the estate, per resource.
  final Map<String, int> pulled;

  /// Records handed to the door, across every chunk of this pull.
  final int delivered;

  final int chunks;
  final String error;

  /// Null only if the feed left the registry mid-request; the screen then
  /// keeps the row it already has.
  final SourceFeed? row;

  const SyncNowOutcome({
    required this.source,
    required this.outcome,
    required this.pulled,
    required this.delivered,
    required this.chunks,
    required this.error,
    required this.row,
  });

  int get recordsPulled => pulled.values.fold(0, (sum, n) => sum + n);

  factory SyncNowOutcome.fromJson(Map<String, dynamic> json) => SyncNowOutcome(
    source: json['source'] as String? ?? '',
    outcome: json['outcome'] as String? ?? '',
    pulled: (json['pulled'] as Map<String, dynamic>? ?? const {})
        .map((k, v) => MapEntry(k, (v as num? ?? 0).toInt())),
    delivered: json['delivered'] as int? ?? 0,
    chunks: json['chunks'] as int? ?? 0,
    error: json['error'] as String? ?? '',
    row: json['row'] == null
        ? null
        : SourceFeed.fromJson(json['row'] as Map<String, dynamic>),
  );
}

/// The derived anomaly baseline: how this vendor's invoices normally look.
/// Disposable by design — delete it and the profile Flow rebuilds it.
class VendorProfile {
  final String vendorId;
  final String vendorLabel;
  final int invoices;
  final double totalUsd;
  final double meanUsd;
  final double medianUsd;
  final double p95Usd;
  final double maxUsd;
  final double stdevUsd;
  final double medianLineCount;
  final double cadenceDays;
  final double roundNumberRate;
  final String firstInvoiceIso;
  final String lastInvoiceIso;
  final int windowDays;
  final String computedAtIso;

  const VendorProfile({
    required this.vendorId,
    required this.vendorLabel,
    required this.invoices,
    required this.totalUsd,
    required this.meanUsd,
    required this.medianUsd,
    required this.p95Usd,
    required this.maxUsd,
    required this.stdevUsd,
    required this.medianLineCount,
    required this.cadenceDays,
    required this.roundNumberRate,
    required this.firstInvoiceIso,
    required this.lastInvoiceIso,
    required this.windowDays,
    required this.computedAtIso,
  });

  factory VendorProfile.fromJson(Map<String, dynamic> json) => VendorProfile(
    vendorId: json['vendor_id'] as String? ?? '',
    vendorLabel: json['vendor_label'] as String? ?? '',
    invoices: json['invoices'] as int? ?? 0,
    totalUsd: (json['total_usd'] as num? ?? 0).toDouble(),
    meanUsd: (json['mean_usd'] as num? ?? 0).toDouble(),
    medianUsd: (json['median_usd'] as num? ?? 0).toDouble(),
    p95Usd: (json['p95_usd'] as num? ?? 0).toDouble(),
    maxUsd: (json['max_usd'] as num? ?? 0).toDouble(),
    stdevUsd: (json['stdev_usd'] as num? ?? 0).toDouble(),
    medianLineCount: (json['median_line_count'] as num? ?? 0).toDouble(),
    cadenceDays: (json['cadence_days'] as num? ?? 0).toDouble(),
    roundNumberRate: (json['round_number_rate'] as num? ?? 0).toDouble(),
    firstInvoiceIso: json['first_invoice_iso'] as String? ?? '',
    lastInvoiceIso: json['last_invoice_iso'] as String? ?? '',
    windowDays: json['window_days'] as int? ?? 365,
    computedAtIso: json['computed_at_iso'] as String? ?? '',
  );
}
