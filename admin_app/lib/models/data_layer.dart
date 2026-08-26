/// Collections, documents, sources, and the derived vendor profile.
/// Mirrors `server/app/data_collections.py`, `documents.py`, `sync.py`,
/// `anomaly.py`.
library;

/// A Collection: the unit governance hangs off. Retention, custody and
/// authoritativeness are properties of the *set*, not of a row.
class DataCollection {
  final String id;
  final String name;
  final String summary;
  final String role; // knowledge | data
  final String kind;
  final String origin; // authored | flywheel | flow-written | derived | synced
  final String container;
  final String custody; // copy | reference
  final String retention; // an ISO-8601 duration, or "indefinite"
  final String retentionNote;
  final bool authoritative;
  final bool legalHold;
  final List<String> objectTypes;
  final List<String> writtenBy;
  final List<String> readBy;

  /// Null when the console cannot count this honestly — which is a different
  /// thing from zero, and is shown differently.
  final int? rows;
  final String rowsCaption;
  final String assetId;

  const DataCollection({
    required this.id,
    required this.name,
    required this.summary,
    required this.role,
    required this.kind,
    required this.origin,
    required this.container,
    required this.custody,
    required this.retention,
    required this.retentionNote,
    required this.authoritative,
    required this.legalHold,
    required this.objectTypes,
    required this.writtenBy,
    required this.readBy,
    required this.rows,
    required this.rowsCaption,
    required this.assetId,
  });

  bool get isKnowledge => role == 'knowledge';

  factory DataCollection.fromJson(Map<String, dynamic> json) => DataCollection(
    id: json['id'] as String? ?? '',
    name: json['name'] as String? ?? '',
    summary: json['summary'] as String? ?? '',
    role: json['role'] as String? ?? 'data',
    kind: json['kind'] as String? ?? '',
    origin: json['origin'] as String? ?? '',
    container: json['container'] as String? ?? '',
    custody: json['custody'] as String? ?? 'copy',
    retention: json['retention'] as String? ?? 'indefinite',
    retentionNote: json['retention_note'] as String? ?? '',
    authoritative: json['authoritative'] as bool? ?? false,
    legalHold: json['legal_hold'] as bool? ?? false,
    objectTypes: (json['object_types'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    writtenBy: (json['written_by'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    readBy: (json['read_by'] as List<dynamic>? ?? const [])
        .map((e) => '$e')
        .toList(),
    rows: json['rows'] as int?,
    rowsCaption: json['rows_caption'] as String? ?? '',
    assetId: json['asset_id'] as String? ?? '',
  );
}

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

/// One inbound feed, assembled from its sync runs rather than a second store.
class SourceStatus {
  final String source;
  final String kind; // vendors | purchase-orders | receiving-reports
  final String flowId;
  final String lastRunId;
  final String lastRunAtIso;
  final String lastStatus;
  final String watermark;
  final int deliveries;
  final int recordsLanded;
  final int linked;
  final int created;
  final int awaitingSteward;
  final int unchanged;
  final String lastError;

  const SourceStatus({
    required this.source,
    required this.kind,
    required this.flowId,
    required this.lastRunId,
    required this.lastRunAtIso,
    required this.lastStatus,
    required this.watermark,
    required this.deliveries,
    required this.recordsLanded,
    required this.linked,
    required this.created,
    required this.awaitingSteward,
    required this.unchanged,
    required this.lastError,
  });

  factory SourceStatus.fromJson(Map<String, dynamic> json) => SourceStatus(
    source: json['source'] as String? ?? '',
    kind: json['kind'] as String? ?? '',
    flowId: json['flow_id'] as String? ?? '',
    lastRunId: json['last_run_id'] as String? ?? '',
    lastRunAtIso: json['last_run_at_iso'] as String? ?? '',
    lastStatus: json['last_status'] as String? ?? '',
    watermark: json['watermark'] as String? ?? '',
    deliveries: json['deliveries'] as int? ?? 0,
    recordsLanded: json['records_landed'] as int? ?? 0,
    linked: json['linked'] as int? ?? 0,
    created: json['created'] as int? ?? 0,
    awaitingSteward: json['awaiting_steward'] as int? ?? 0,
    unchanged: json['unchanged'] as int? ?? 0,
    lastError: json['last_error'] as String? ?? '',
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
