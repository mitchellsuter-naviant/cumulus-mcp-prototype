/// Render a stored PDF page, on whichever platform this is.
library;

export 'pdf_view_stub.dart'
    if (dart.library.js_interop) 'pdf_view_web.dart';
