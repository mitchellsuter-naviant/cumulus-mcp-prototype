/// A stored PDF, rendered by the browser that is already sitting there.
///
/// The bytes cannot simply be pointed at: `/documents/{id}/content` wants the
/// caller's auth headers, and a bare `<iframe src>` sends none, so an iframe
/// aimed at the API would render a 401. So the bytes are fetched the way every
/// other call is, wrapped in a Blob, and the frame is pointed at the object
/// URL — the artifact never leaves the session that was allowed to read it.
///
/// No pub dependency. A PDF renderer is a large thing to carry for a page the
/// browser already knows how to draw, and the interop this needs is four
/// declarations against `Blob` and `URL`.
///
/// The fragment matters as much as the source. `view=Fit` scales the page to
/// the frame and the chrome is switched off, so the frame *is* the page — which
/// is what lets `regionRect`'s normalized boxes land on the values they name.
/// A toolbar inside the frame would push the page out of that coordinate space.
library;

import 'dart:js_interop';
import 'dart:typed_data';

import 'package:flutter/widgets.dart';

/// Whether [PdfPageView] can actually draw anything.
const bool canRenderPdfInline = true;

@JS('Blob')
extension type _Blob._(JSObject _) implements JSObject {
  external factory _Blob(JSArray<JSAny> parts, _BlobOptions options);
}

extension type _BlobOptions._(JSObject _) implements JSObject {
  external factory _BlobOptions({String type});
}

@JS('URL.createObjectURL')
external String _createObjectUrl(JSObject blob);

@JS('URL.revokeObjectURL')
external void _revokeObjectUrl(String url);

extension type _Frame._(JSObject _) implements JSObject {
  external set src(String value);

  external _Style get style;
}

extension type _Style._(JSObject _) implements JSObject {
  external set border(String value);

  external set width(String value);

  external set height(String value);

  external set backgroundColor(String value);
}

class PdfPageView extends StatefulWidget {
  final Uint8List bytes;
  final String mediaType;

  /// One-based, as `Region.page` is.
  final int page;

  const PdfPageView({
    super.key,
    required this.bytes,
    required this.mediaType,
    required this.page,
  });

  @override
  State<PdfPageView> createState() => _PdfPageViewState();
}

class _PdfPageViewState extends State<PdfPageView> {
  /// Held so it can be handed back. An object URL keeps its blob alive for the
  /// life of the document, so a drawer opened and closed a dozen times would
  /// otherwise pin a dozen invoices in memory.
  String? _url;

  @override
  void initState() {
    super.initState();
    _url = _objectUrl(widget.bytes, widget.mediaType);
  }

  @override
  void didUpdateWidget(PdfPageView old) {
    super.didUpdateWidget(old);
    if (identical(old.bytes, widget.bytes)) return;
    _release();
    _url = _objectUrl(widget.bytes, widget.mediaType);
  }

  @override
  void dispose() {
    _release();
    super.dispose();
  }

  void _release() {
    final url = _url;
    if (url != null) _revokeObjectUrl(url);
    _url = null;
  }

  static String _objectUrl(Uint8List bytes, String mediaType) => _createObjectUrl(
    _Blob(<JSAny>[bytes.toJS].toJS, _BlobOptions(type: mediaType)),
  );

  @override
  Widget build(BuildContext context) {
    final url = _url;
    if (url == null) return const SizedBox.shrink();
    final src =
        '$url#page=${widget.page}&toolbar=0&navpanes=0&scrollbar=0&view=Fit';
    return HtmlElementView.fromTagName(
      // A page change is a new source, and reusing the element would leave the
      // frame on the page it was built with.
      key: ValueKey(src),
      tagName: 'iframe',
      onElementCreated: (element) {
        final frame = element as _Frame;
        frame.style
          ..border = 'none'
          ..width = '100%'
          ..height = '100%'
          ..backgroundColor = 'transparent';
        frame.src = src;
      },
    );
  }
}
