/// The artifact a run actually read, rendered.
///
/// The bytes have been stored since the document store was written — under the
/// `audit-documents` Collection's retention, keyed by their own content hash —
/// and `/documents/{id}/content` has served them the whole time. Nothing in
/// the console ever called it. So a run could tell you it read
/// `document:doc_28fee8830bfcf03c` and there was no way, anywhere, to look at
/// the receipt: the evidence was held, addressable, and invisible.
///
/// Then it was fetched and still not drawn: an image rendered, a PDF got a
/// paragraph explaining that PDFs do not render, and the regions — which have
/// always carried normalized 0–1 geometry precisely so a viewer could draw
/// them — were printed as a list of numbers nobody could check against the
/// page. Both halves are here now.
///
/// Three things this is careful about:
///
/// - **A document is evidence, never a fact.** The regions are what the
///   extractor *claims* it read and where, so they are drawn as claims with
///   their confidence, not as the answer.
/// - **A run with no bytes is a real state**, not a broken one. A composed or
///   hint-driven run stores a placeholder the journal can still point at, and
///   saying so is better than an empty frame.
/// - **Stored bytes that are not what they say they are** get named as such.
///   A `.pdf` holding plain text is a fact about the estate, and rendering an
///   empty viewer over it would hide the one thing worth knowing.
library;

import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../models/data_layer.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'pdf_view.dart';
import 'side_drawer.dart';
import 'ui_kit.dart';

/// Opens the drawer for `document:<id>` or a bare id.
void openDocumentDrawer(BuildContext context, ApiClient api, String ref) {
  final id = ref.startsWith('document:') ? ref.substring(9) : ref;
  // Kicked off before the drawer builds, so the fetch happens once rather than
  // once per rebuild — the same reason `RefChips` resolves eagerly.
  final pending = _load(api, id);
  showSideDrawer<void>(
    context: context,
    width: 760,
    title: id,
    subtitle:
        'The artifact this run read, held under its Collection’s retention. '
        'Its regions are where the extractor claims it found each value.',
    builder: (context) => DocumentBody(pending: pending),
  );
}

Future<(StoredDocument, Uint8List?)> _load(ApiClient api, String id) async {
  final document = await api.getDocument(id);
  if (!document.hasBytes) return (document, null);
  try {
    return (document, await api.documentContent(id));
  } on ApiException {
    // The metadata is worth showing even when the bytes will not come: the
    // custody story is the half that survives a storage adapter being down.
    return (document, null);
  }
}

// ---------------------------------------------------------------------------
// Region geometry
// ---------------------------------------------------------------------------

/// A box the extractor reported as a point is still a claim about somewhere on
/// the page, so it is drawn at a size a person can see and click.
const double _minRegionSide = 5;

/// The rectangle a normalized region occupies on a page rendered at [page].
///
/// Regions are stored 0–1 (`documents.py`), which is the whole reason drawing
/// them is arithmetic rather than a dependency: multiply by the rendered page,
/// then clamp back onto it. A box the model reports running off the edge is
/// still a claim about this page, and letting it spill would paint it over the
/// drawer's chrome as if the page continued.
Rect regionRect(DocumentRegion region, Size page) {
  final (left, right) = _side(region.x, region.width, page.width);
  final (top, bottom) = _side(region.y, region.height, page.height);
  return Rect.fromLTRB(left, top, right, bottom);
}

/// One axis of [regionRect]. A negative extent is read as the box the two
/// coordinates describe rather than discarded — the geometry is a claim, and
/// the honest rendering of a backwards claim is the span it names.
(double, double) _side(double start, double extent, double whole) {
  final lo = start.clamp(0.0, 1.0) * whole;
  final hi = (start + extent).clamp(0.0, 1.0) * whole;
  var a = math.min(lo, hi);
  var b = math.max(lo, hi);
  final floor = math.min(_minRegionSide, whole);
  if (b - a < floor) {
    b = a + floor;
    if (b > whole) {
      b = whole;
      a = whole - floor;
    }
  }
  return (a, b);
}

/// The regions that belong on [page], one-based.
///
/// A record written before pages were tracked says `page: 1`, and anything
/// below one is not a page number — both read as the first page rather than as
/// nothing, because dropping a region silently is how a viewer comes to claim
/// the extractor found less than it did.
List<DocumentRegion> regionsOnPage(List<DocumentRegion> regions, int page) => [
  for (final region in regions)
    if ((region.page < 1 ? 1 : region.page) == page) region,
];

/// Every PDF begins `%PDF-`. The estate currently files invoice *text* under a
/// `.pdf` name, and a viewer that renders an empty frame over that says the
/// document is broken when what is true is that the bytes are not a PDF.
bool looksLikePdf(Uint8List bytes) {
  if (bytes.length < 5) return false;
  const magic = [0x25, 0x50, 0x44, 0x46, 0x2d]; // %PDF-
  for (var i = 0; i < magic.length; i++) {
    if (bytes[i] != magic[i]) return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// The page, with boxes on it
// ---------------------------------------------------------------------------

/// The page is capped rather than filling its column. A letter page drawn at
/// the full width would stand two and a half screens tall, and the point of
/// the frame is to be looked at whole with the boxes on it.
const double _maxPageWidth = 520;

/// US Letter. A stored document says how many pages it has and not how large
/// they are, so the frame assumes the proportions every artifact this system
/// files actually has. `view=Fit` then makes the page fill the frame, which is
/// what puts a normalized box on the value it names — and a page with other
/// proportions renders letterboxed, with the boxes on the frame rather than on
/// the page. A limit worth stating; the alternative is guessing silently.
const double _pageAspect = 8.5 / 11;

/// One page of a stored artifact, with the boxes the caller asked for on it.
///
/// Public and caller-fed rather than a private half of the drawer, because two
/// screens want the same page and want different boxes on it. The drawer draws
/// everything the extractor claimed. The invoice review screen draws **only
/// the fields in doubt** — a clerk asked about three fields is not helped by
/// thirty boxes — and the way to keep those from being two viewers that drift
/// is for the boxes to be an argument.
///
/// [regions] is drawn as given: filtering to [page] is the caller's business
/// (see [regionsOnPage]), so a caller with one region per *field* rather than
/// one per page is not forced to pretend otherwise.
///
/// Every state where there is nothing to render is a sentence rather than an
/// empty frame — no bytes, bytes the store would not hand over, a `.pdf` that
/// does not begin `%PDF-`, a build with no page renderer. Those are the states
/// this widget mostly exists for.
class DocumentPageView extends StatelessWidget {
  final StoredDocument document;
  final Uint8List? bytes;

  /// One-based, and already clamped to the document's page count.
  final int page;

  /// The boxes to draw on this page.
  final List<DocumentRegion> regions;

  /// The field path currently highlighted, in [regions]' vocabulary.
  final String? active;

  /// The page asking for a field to become active: a path on hover-enter or on
  /// a click, and null when the pointer leaves the box that was active. What
  /// that *means* is the caller's decision — the drawer treats it as hover, a
  /// review screen keeps a clicked selection and ignores the null.
  final ValueChanged<String?>? onActive;

  final double maxWidth;

  const DocumentPageView({
    super.key,
    required this.document,
    required this.bytes,
    required this.page,
    this.regions = const [],
    this.active,
    this.onActive,
    this.maxWidth = _maxPageWidth,
  });

  @override
  Widget build(BuildContext context) {
    if (!document.hasBytes) {
      return _note(
        'No bytes. The map holds a reference to this document and nobody has '
        'read it — which is what a `storage_ref` with no content hash means. '
        'The custody record above is the whole of what is known about it.',
      );
    }
    final content = bytes;
    if (content == null) {
      return _note(
        'The document is stored but its bytes could not be loaded just now. '
        'The custody record above is unaffected.',
      );
    }
    if (document.mediaType.startsWith('image/')) {
      // The image sizes the Stack, so the overlay covers exactly the pixels
      // that were painted — no assumption about proportions is needed here.
      return _framed(
        Stack(
          children: [
            Image.memory(
              content,
              fit: BoxFit.fitWidth,
              errorBuilder: (_, _, _) => _note(
                'The bytes are held but this browser cannot render '
                '${document.mediaType}.',
              ),
            ),
            Positioned.fill(child: _overlay()),
          ],
        ),
      );
    }
    final isPdf = document.mediaType.contains('pdf');
    if (isPdf && !looksLikePdf(content)) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _note(
            'These bytes are filed as ${document.mediaType} but do not begin '
            '“%PDF-”, so no viewer will draw them. That is a fact about what '
            'was filed, not about the store: the extractor read exactly what '
            'is below.',
          ),
          const SizedBox(height: 12),
          _text(content),
        ],
      );
    }
    if (isPdf && canRenderPdfInline) {
      return _framed(
        AspectRatio(
          aspectRatio: _pageAspect,
          child: Stack(
            children: [
              Positioned.fill(
                child: PdfPageView(
                  bytes: content,
                  mediaType: document.mediaType,
                  page: page,
                ),
              ),
              Positioned.fill(child: _overlay()),
            ],
          ),
        ),
      );
    }
    if (isPdf) {
      return _note(
        '${document.byteSize} bytes of ${document.mediaType}, held in full. '
        'This build has no page renderer — the boxes below are what the '
        'extractor found, and the browser build draws them on the page.',
      );
    }
    return _note(
      '${document.byteSize} bytes of ${document.mediaType}, held in full. '
      'The console draws pages and images; the extractor reads this type '
      'natively, which is the part that matters.',
    );
  }

  /// The boxes, in the coordinate space the page was drawn in.
  Widget _overlay() {
    if (regions.isEmpty) return const SizedBox.shrink();
    return LayoutBuilder(
      builder: (context, constraints) {
        final size = Size(constraints.maxWidth, constraints.maxHeight);
        return Stack(
          clipBehavior: Clip.none,
          children: [
            for (final region in regions) ...[
              Positioned.fromRect(
                rect: regionRect(region, size),
                child: _RegionBox(
                  region: region,
                  selected: region.field == active,
                  onEnter: () => onActive?.call(region.field),
                  onExit: () {
                    if (active == region.field) onActive?.call(null);
                  },
                  onTap: () => onActive?.call(region.field),
                ),
              ),
              // The label sits above its box rather than inside it: a value's
              // box is the size of the value, and a field path is longer.
              if (region.field == active)
                Positioned(
                  left: regionRect(region, size).left,
                  top: math.max(0, regionRect(region, size).top - 18),
                  child: _RegionLabel(region: region),
                ),
            ],
          ],
        );
      },
    );
  }

  /// One rule around the page, so the artifact reads as a thing being looked
  /// at rather than as part of the surface it sits on.
  Widget _framed(Widget child) => Center(
    child: ConstrainedBox(
      constraints: BoxConstraints(maxWidth: maxWidth),
      child: Container(
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          border: Border.all(color: CumulusColors.borderStrong),
        ),
        clipBehavior: Clip.antiAlias,
        child: child,
      ),
    ),
  );

  Widget _note(String message) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(16),
    decoration: BoxDecoration(
      color: CumulusColors.surfaceMuted,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
    ),
    child: Text(message, style: CumulusText.body),
  );

  /// Bytes that decode as text, shown as text. Nothing is claimed about them
  /// beyond that they are what the store holds.
  Widget _text(Uint8List raw) {
    final String content;
    try {
      content = utf8.decode(raw);
    } on FormatException {
      return _note('The bytes are not UTF-8 text either — they are held as '
          'they arrived and can only be read by whatever wrote them.');
    }
    return PayloadBlock(label: 'The stored bytes', content: content);
  }
}

// ---------------------------------------------------------------------------
// The drawer
// ---------------------------------------------------------------------------

/// The drawer's body, taking its document and bytes as one settled future.
///
/// Public so it can be pumped with a fixture: what it renders when the bytes
/// are absent, or are not the type they claim, is a set of statements about
/// the estate, and those are worth holding still.
class DocumentBody extends StatefulWidget {
  final Future<(StoredDocument, Uint8List?)> pending;

  const DocumentBody({super.key, required this.pending});

  @override
  State<DocumentBody> createState() => _DocumentBodyState();
}

class _DocumentBodyState extends State<DocumentBody> {
  int _page = 1;

  /// The canonical field path of the region under the cursor or under the
  /// reader's last click. One selection drives both the box on the page and
  /// the row in the list, which is the whole point of having both.
  String? _active;

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<(StoredDocument, Uint8List?)>(
      future: widget.pending,
      builder: (context, snapshot) {
        if (snapshot.hasError) {
          return Padding(
            padding: const EdgeInsets.all(20),
            child: ErrorNotice(message: '${snapshot.error}'),
          );
        }
        if (!snapshot.hasData) {
          return const Padding(
            padding: EdgeInsets.all(40),
            child: Center(child: CircularProgressIndicator()),
          );
        }
        final (document, bytes) = snapshot.data!;
        final pages = math.max(1, document.pages);
        final page = _page.clamp(1, pages);
        final here = regionsOnPage(document.regions, page);
        return ListView(
          padding: const EdgeInsets.all(20),
          children: [
            Text(
              document.filename.isEmpty ? '(no filename)' : document.filename,
              style: CumulusText.sectionTitle,
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              children: [
                MetaPill(document.mediaType),
                MetaPill(_bytes(document.byteSize)),
                MetaPill('$pages page${pages == 1 ? '' : 's'}'),
                MetaPill(document.collectionId),
                if (document.retentionUntilIso.isNotEmpty)
                  MetaPill(
                    'retained until '
                    '${document.retentionUntilIso.split("T").first}',
                  ),
              ],
            ),
            if (pages > 1) ...[
              const SizedBox(height: 14),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  for (var n = 1; n <= pages; n++)
                    LensChip(
                      label: 'PAGE $n',
                      tone: CumulusTone.accent,
                      selected: n == page,
                      onTap: () => setState(() {
                        _page = n;
                        _active = null;
                      }),
                    ),
                ],
              ),
            ],
            const SizedBox(height: 18),
            DocumentPageView(
              document: document,
              bytes: bytes,
              page: page,
              // The drawer's subject is the whole extraction, so it draws
              // every claim on this page. A review screen draws a subset.
              regions: here,
              active: _active,
              onActive: (field) => setState(() => _active = field),
            ),
            if (document.regions.isNotEmpty) ...[
              const SizedBox(height: 20),
              FieldLabel(
                'What the extractor read',
                hint: '${document.regions.length} field(s), with where it '
                    'claims each came from',
              ),
              for (final region in document.regions)
                _RegionRow(
                  region: region,
                  selected: region.field == _active,
                  onTap: () => setState(() {
                    _page = region.page < 1 ? 1 : region.page;
                    _active = region.field == _active ? null : region.field;
                  }),
                ),
            ],
            const SizedBox(height: 20),
            FieldLabel('Custody', hint: 'who submitted it, and what read it'),
            Text(
              'Submitted by ${document.uploadedBy.isEmpty ? "an unattributed "
                  "caller" : document.uploadedBy}. Identity is the content '
              'hash, so resubmitting the same file adds a run reference '
              'rather than a second copy — which is what makes a replayed '
              'step idempotent and duplicate detection free.',
              style: CumulusText.body,
            ),
            if (document.runIds.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text('Read by: ${document.runIds.join("  ·  ")}',
                  style: CumulusText.mono),
            ],
          ],
        );
      },
    );
  }

  static String _bytes(int size) => size < 1024
      ? '$size B'
      : size < 1024 * 1024
      ? '${(size / 1024).toStringAsFixed(1)} KB'
      : '${(size / 1024 / 1024).toStringAsFixed(1)} MB';
}

/// One claim, drawn where it was claimed.
///
/// Deliberately an outline over a wash rather than a filled shape: the value
/// underneath is the evidence and the box is the machine pointing at it, so
/// the box must never be the thing you can read.
///
/// Clickable as well as hoverable. Hover is the right gesture for browsing an
/// extraction, and the wrong one for working: a clerk who moves the pointer
/// off a box to type into its input has lost the highlight they were using.
class _RegionBox extends StatelessWidget {
  final DocumentRegion region;
  final bool selected;
  final VoidCallback onEnter;
  final VoidCallback onExit;
  final VoidCallback onTap;

  const _RegionBox({
    required this.region,
    required this.selected,
    required this.onEnter,
    required this.onExit,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => onEnter(),
      onExit: (_) => onExit(),
      child: GestureDetector(
        onTap: onTap,
        child: Tooltip(
          message: '${region.field}\n${region.text}\n'
              '${(region.confidence * 100).round()}% confident',
          child: Container(
            decoration: BoxDecoration(
              color:
                  CumulusColors.accent.withValues(alpha: selected ? 0.2 : 0.08),
              border: Border.all(
                color: CumulusColors.accent.withValues(
                  alpha: selected ? 1 : 0.65,
                ),
                width: selected ? 2 : 1,
              ),
              borderRadius: BorderRadius.circular(2),
            ),
          ),
        ),
      ),
    );
  }
}

/// The field path, over the box it belongs to.
class _RegionLabel extends StatelessWidget {
  final DocumentRegion region;

  const _RegionLabel({required this.region});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
      decoration: BoxDecoration(
        color: CumulusColors.accent,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        region.field,
        style: CumulusText.mono.copyWith(fontSize: 10, color: Colors.white),
      ),
    );
  }
}

/// One row of what the extractor read, and the way onto its box.
class _RegionRow extends StatelessWidget {
  final DocumentRegion region;
  final bool selected;
  final VoidCallback onTap;

  const _RegionRow({
    required this.region,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Material(
        color: selected ? CumulusColors.accentSoft : Colors.transparent,
        borderRadius: BorderRadius.circular(6),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(6),
          hoverColor: CumulusColors.surfaceMuted,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
            child: Row(
              children: [
                SizedBox(
                  width: 220,
                  child: Text(region.field, style: CumulusText.mono),
                ),
                Expanded(
                  child: Text(region.text, style: CumulusText.body),
                ),
                Text(
                  'p${region.page < 1 ? 1 : region.page} · '
                  '${(region.confidence * 100).round()}%',
                  style: CumulusText.meta,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
