/// Where the console is, as a value you can put in a URL.
///
/// The shell has always kept its location in widget state — a section index in
/// `home_shell`, a drilled-into flow id in `FlowsSection` — which works
/// perfectly until somebody wants to send a colleague a link to the thing they
/// are looking at. This is that same location, named, so the browser can hold
/// it and hand it back.
///
/// Deliberately a *slug*, not an index. The rail's section constants live in
/// `home_shell` and move whenever a stop is added; a URL that encoded their
/// numbering would silently point somewhere else the next time one was
/// inserted. The shell owns the slug-to-index mapping, and this file never
/// needs to know it.
library;

import 'package:flutter/foundation.dart';

/// The four views of one flow. The slug is what appears in the path; the index
/// is what `FlowDetailScreen` selects on.
enum FlowTab {
  overview('overview'),
  canvas('canvas'),
  runs('runs'),
  evaluation('evaluation');

  final String slug;

  const FlowTab(this.slug);

  static FlowTab fromIndex(int index) =>
      index >= 0 && index < values.length ? values[index] : overview;

  /// An unrecognised slug reads as the flow's front page rather than as a
  /// failure: a stale link with a renamed tab should still land on the flow.
  static FlowTab fromSlug(String slug) =>
      values.where((t) => t.slug == slug).firstOrNull ?? overview;
}

/// One location. Immutable, compared by value, so a Router can tell whether
/// anything actually changed.
@immutable
class AppRoute {
  /// The rail section's slug. Empty is home.
  final String section;

  /// The flow drilled into, or empty for the fleet list. Only meaningful
  /// while [section] is `flows`.
  final String flowId;

  final FlowTab flowTab;

  const AppRoute({this.section = '', this.flowId = '', this.flowTab = FlowTab.overview});

  static const home = AppRoute();

  bool get isHome => section.isEmpty;

  AppRoute copyWith({String? section, String? flowId, FlowTab? flowTab}) =>
      AppRoute(
        section: section ?? this.section,
        flowId: flowId ?? this.flowId,
        flowTab: flowTab ?? this.flowTab,
      );

  /// The path this location writes into the address bar.
  ///
  /// A flow's front page is the bare `/flows/<id>`, not `/flows/<id>/overview`:
  /// the tab that opens by default should not have to be named, or every link
  /// anybody copies carries a segment that means "no choice was made".
  String get location {
    if (section.isEmpty) return '/';
    if (section != 'flows' || flowId.isEmpty) return '/$section';
    if (flowTab == FlowTab.overview) return '/flows/$flowId';
    return '/flows/$flowId/${flowTab.slug}';
  }

  /// Read a location back. Tolerant by design — a URL is user input, and the
  /// worst thing a bad one can do is land somewhere sensible.
  factory AppRoute.parse(String? path) {
    final segments = (path ?? '/')
        .split('?')
        .first
        .split('/')
        .where((s) => s.isNotEmpty)
        .toList();
    if (segments.isEmpty) return home;
    final section = segments.first.toLowerCase();
    if (section != 'flows' || segments.length < 2) {
      return AppRoute(section: section);
    }
    return AppRoute(
      section: 'flows',
      flowId: segments[1],
      flowTab: segments.length > 2 ? FlowTab.fromSlug(segments[2]) : FlowTab.overview,
    );
  }

  @override
  bool operator ==(Object other) =>
      other is AppRoute &&
      other.section == section &&
      other.flowId == flowId &&
      other.flowTab == flowTab;

  @override
  int get hashCode => Object.hash(section, flowId, flowTab);

  @override
  String toString() => 'AppRoute($location)';
}

/// The one place the console's location lives.
///
/// A notifier rather than props threaded through the shell: the location is
/// read and written by widgets at three different depths — the rail, the fleet
/// list, a flow's tab bar — and passing it down through all of them would put
/// a callback on every constructor in between for the sake of a value none of
/// them care about.
///
/// Writes are idempotent: setting the location it already holds notifies
/// nobody, which is what keeps a widget that syncs on build from looping.
class AppRouteState extends ValueNotifier<AppRoute> {
  AppRouteState._() : super(AppRoute.home);

  static final AppRouteState instance = AppRouteState._();

  /// Go somewhere. No-op when we are already there.
  void go(AppRoute route) {
    if (value == route) return;
    value = route;
  }

  /// Open the fleet list.
  void openFlows() => go(const AppRoute(section: 'flows'));

  /// Drill into one flow, on a named tab.
  void openFlow(String flowId, {FlowTab tab = FlowTab.overview}) =>
      go(AppRoute(section: 'flows', flowId: flowId, flowTab: tab));

  /// Change tab without leaving the flow. Does nothing if no flow is open,
  /// so a tab change can never invent a location.
  void setFlowTab(FlowTab tab) {
    if (value.flowId.isEmpty) return;
    go(value.copyWith(flowTab: tab));
  }

  /// A rail section by slug. Clears any drilled-into flow, because leaving
  /// the section is leaving the flow.
  void openSection(String slug) => go(AppRoute(section: slug));
}
