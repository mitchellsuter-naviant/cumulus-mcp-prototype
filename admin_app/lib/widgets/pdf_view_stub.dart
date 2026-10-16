/// The non-web build's answer: there is no browser here to render a PDF.
///
/// The web implementation lives beside this file and is selected by the
/// conditional export in `pdf_view.dart` — the same split `url_strategy.dart`
/// uses, and for the same reason: `flutter test` runs on the VM and must never
/// import a web-only library.
library;

import 'dart:typed_data';

import 'package:flutter/widgets.dart';

/// Whether [PdfPageView] can actually draw anything. Callers check this and
/// say what is held instead — an empty frame pretending to be a viewer is the
/// state this whole widget exists to replace.
const bool canRenderPdfInline = false;

class PdfPageView extends StatelessWidget {
  final Uint8List bytes;
  final String mediaType;
  final int page;

  const PdfPageView({
    super.key,
    required this.bytes,
    required this.mediaType,
    required this.page,
  });

  @override
  Widget build(BuildContext context) => const SizedBox.shrink();
}
