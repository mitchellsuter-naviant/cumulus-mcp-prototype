/// The artifact a run actually read, rendered.
///
/// The bytes have been stored since the document store was written — under the
/// `audit-documents` Collection's retention, keyed by their own content hash —
/// and `/documents/{id}/content` has served them the whole time. Nothing in
/// the console ever called it. So a run could tell you it read
/// `document:doc_28fee8830bfcf03c` and there was no way, anywhere, to look at
/// the receipt: the evidence was held, addressable, and invisible.
///
/// Two things this is careful about:
///
/// - **A document is evidence, never a fact.** The regions are what the
///   extractor *claims* it read and where, so they are drawn as claims with
///   their confidence, not as the answer.
/// - **A run with no bytes is a real state**, not a broken one. A composed or
///   hint-driven run stores a placeholder the journal can still point at, and
///   saying so is better than an empty frame.
library;

import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../models/data_layer.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
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
    width: 720,
    title: id,
    subtitle:
        'The artifact this run read, held under its Collection’s retention. '
        'Its regions are where the extractor claims it found each value.',
    builder: (context) => _DocumentBody(pending: pending),
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

class _DocumentBody extends StatelessWidget {
  final Future<(StoredDocument, Uint8List?)> pending;

  const _DocumentBody({required this.pending});

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<(StoredDocument, Uint8List?)>(
      future: pending,
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
                MetaPill('${document.pages} page'
                    '${document.pages == 1 ? '' : 's'}'),
                MetaPill(document.collectionId),
                if (document.retentionUntilIso.isNotEmpty)
                  MetaPill(
                    'retained until '
                    '${document.retentionUntilIso.split("T").first}',
                  ),
              ],
            ),
            const SizedBox(height: 18),
            _preview(document, bytes),
            if (document.regions.isNotEmpty) ...[
              const SizedBox(height: 20),
              FieldLabel(
                'What the extractor read',
                hint: '${document.regions.length} field(s), with where it '
                    'claims each came from',
              ),
              for (final region in document.regions)
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
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
                        'p${region.page} · '
                        '${(region.confidence * 100).round()}%',
                        style: CumulusText.meta,
                      ),
                    ],
                  ),
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

  Widget _preview(StoredDocument document, Uint8List? bytes) {
    if (!document.hasBytes) {
      return _note(
        'No bytes. This run was composed rather than uploaded, so the '
        'artifact is a placeholder the journal can still point at. Everything '
        'downstream of extraction ran on the real machinery either way.',
      );
    }
    if (bytes == null) {
      return _note(
        'The document is stored but its bytes could not be loaded just now. '
        'The custody record above is unaffected.',
      );
    }
    if (document.mediaType.startsWith('image/')) {
      return ClipRRect(
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        child: Image.memory(
          bytes,
          fit: BoxFit.contain,
          errorBuilder: (_, _, _) => _note(
            'The bytes are held but this browser cannot render '
            '${document.mediaType}.',
          ),
        ),
      );
    }
    // A PDF is the common case and there is no in-app renderer, so say what is
    // held rather than showing an empty frame pretending to be a viewer.
    return _note(
      '${document.byteSize} bytes of ${document.mediaType}, held in full. '
      'The console has no inline viewer for this type — the extractor reads '
      'it natively, which is the part that matters, and the regions below are '
      'what it found.',
    );
  }

  Widget _note(String message) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(16),
    decoration: BoxDecoration(
      color: CumulusColors.surfaceMuted,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
    ),
    child: Text(message, style: CumulusText.body),
  );

  static String _bytes(int size) => size < 1024
      ? '$size B'
      : size < 1024 * 1024
      ? '${(size / 1024).toStringAsFixed(1)} KB'
      : '${(size / 1024 / 1024).toStringAsFixed(1)} MB';
}
