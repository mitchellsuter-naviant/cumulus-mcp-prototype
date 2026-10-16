/// Real paths in the address bar, rather than `/#/`.
///
/// Flutter web defaults to the hash strategy, which would make every link
/// somebody copies read `/#/flows/ap-invoice/runs`. Hosting already rewrites
/// unmatched paths to `index.html` (the `"**"` rule in `firebase.json`, after
/// the API routes), so the clean form resolves on a cold load too.
library;

import 'package:flutter_web_plugins/url_strategy.dart';

void useCleanUrls() => usePathUrlStrategy();
