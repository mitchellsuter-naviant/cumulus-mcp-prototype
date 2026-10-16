/// The non-web build's answer: there is no address bar to configure.
///
/// The web implementation lives beside this file and is selected by the
/// conditional export in `url_strategy.dart`. Splitting it this way is what
/// keeps `flutter test` — which runs on the VM — from importing a web-only
/// library it cannot compile.
library;

void useCleanUrls() {}
