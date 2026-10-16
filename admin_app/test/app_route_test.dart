import 'package:flutter_test/flutter_test.dart';

import 'package:cumulus_admin/services/app_route.dart';

/// The console's location, as a value that survives a round trip through the
/// address bar.
///
/// The properties worth holding:
///
/// - **A link round-trips.** Whatever the address bar shows, parsing it back
///   has to give the location that wrote it — otherwise a copied link opens
///   somewhere other than where it was copied from, which is the one failure
///   this whole feature exists to prevent.
/// - **A bad URL lands somewhere sensible.** A path is user input. A renamed
///   tab, a trailing slash or a section that no longer exists should still put
///   the reader on a real page rather than on an error.
/// - **The default tab is not named.** `/flows/ap-invoice` is the front page;
///   a link that says `/overview` records a choice nobody made.
/// - **Writing the location you already hold notifies nobody**, which is what
///   keeps a widget that syncs on build from looping.
void main() {
  group('a location round-trips through its path', () {
    test('every shape survives being written and read back', () {
      const routes = [
        AppRoute.home,
        AppRoute(section: 'flows'),
        AppRoute(section: 'knowledge'),
        AppRoute(section: 'flows', flowId: 'ap-invoice'),
        AppRoute(
          section: 'flows',
          flowId: 'ap-invoice',
          flowTab: FlowTab.canvas,
        ),
        AppRoute(
          section: 'flows',
          flowId: 'disagreement-sweep',
          flowTab: FlowTab.evaluation,
        ),
      ];
      for (final route in routes) {
        expect(
          AppRoute.parse(route.location),
          route,
          reason: '${route.location} did not survive the round trip',
        );
      }
    });

    test('the paths are the ones a person would expect to see', () {
      expect(AppRoute.home.location, '/');
      expect(const AppRoute(section: 'flows').location, '/flows');
      expect(
        const AppRoute(section: 'flows', flowId: 'ap-invoice').location,
        '/flows/ap-invoice',
      );
      expect(
        const AppRoute(
          section: 'flows',
          flowId: 'ap-invoice',
          flowTab: FlowTab.runs,
        ).location,
        '/flows/ap-invoice/runs',
      );
    });

    test('the default tab is not named in the path', () {
      // `/flows/x/overview` would record a choice the reader never made, and
      // every copied link would carry it.
      expect(
        const AppRoute(
          section: 'flows',
          flowId: 'x',
          flowTab: FlowTab.overview,
        ).location,
        '/flows/x',
      );
    });

    test('a flow id is only carried while the section is flows', () {
      // Nothing else in the console addresses a flow, so a stray id must not
      // leak into another section's path.
      expect(
        const AppRoute(section: 'tasks', flowId: 'ap-invoice').location,
        '/tasks',
      );
    });
  });

  group('a bad path lands somewhere real', () {
    test('empty, root and null all read as home', () {
      for (final path in ['', '/', '//', null]) {
        expect(AppRoute.parse(path), AppRoute.home, reason: 'path: $path');
      }
    });

    test('a trailing slash changes nothing', () {
      expect(
        AppRoute.parse('/flows/ap-invoice/'),
        const AppRoute(section: 'flows', flowId: 'ap-invoice'),
      );
    });

    test('a renamed tab still lands on the flow', () {
      // A stale link is worth honouring as far as it can be: the flow is
      // still there even when the tab it named is not.
      final route = AppRoute.parse('/flows/ap-invoice/telemetry');
      expect(route.flowId, 'ap-invoice');
      expect(route.flowTab, FlowTab.overview);
    });

    test('a query string is not part of the location', () {
      expect(
        AppRoute.parse('/flows/ap-invoice?from=slack'),
        const AppRoute(section: 'flows', flowId: 'ap-invoice'),
      );
    });

    test('an unknown section is carried, not swallowed', () {
      // The shell decides what an unmapped slug means. Parsing must not
      // silently rewrite it to home, or the shell can never say "no such
      // page" about anything.
      expect(AppRoute.parse('/nowhere').section, 'nowhere');
    });

    test('segments past the tab are ignored', () {
      expect(
        AppRoute.parse('/flows/ap-invoice/runs/extra/junk'),
        const AppRoute(
          section: 'flows',
          flowId: 'ap-invoice',
          flowTab: FlowTab.runs,
        ),
      );
    });
  });

  group('tab slugs and indices agree', () {
    test('an index maps to the tab the detail screen selects on', () {
      expect(FlowTab.fromIndex(0), FlowTab.overview);
      expect(FlowTab.fromIndex(1), FlowTab.canvas);
      expect(FlowTab.fromIndex(2), FlowTab.runs);
      expect(FlowTab.fromIndex(3), FlowTab.evaluation);
      // The order is the tab bar's order, and a link is written from an index.
      for (final tab in FlowTab.values) {
        expect(FlowTab.fromIndex(tab.index), tab);
      }
    });

    test('an index off the end reads as the front page', () {
      expect(FlowTab.fromIndex(-1), FlowTab.overview);
      expect(FlowTab.fromIndex(99), FlowTab.overview);
    });
  });

  group('the notifier', () {
    tearDown(() => AppRouteState.instance.go(AppRoute.home));

    test('going where you already are notifies nobody', () {
      final state = AppRouteState.instance;
      state.go(const AppRoute(section: 'flows'));
      var notifications = 0;
      void count() => notifications++;
      state.addListener(count);
      addTearDown(() => state.removeListener(count));

      state.go(const AppRoute(section: 'flows'));
      state.openFlows();
      expect(
        notifications,
        0,
        reason: 'a widget syncing on build would loop on this',
      );

      state.openFlow('ap-invoice');
      expect(notifications, 1);
    });

    test('a tab change cannot invent a location', () {
      final state = AppRouteState.instance;
      state.openFlows();
      state.setFlowTab(FlowTab.runs);
      // No flow is open, so there is no tab to be on.
      expect(state.value, const AppRoute(section: 'flows'));

      state.openFlow('ap-invoice');
      state.setFlowTab(FlowTab.runs);
      expect(state.value.location, '/flows/ap-invoice/runs');
    });

    test('leaving the section leaves the flow', () {
      final state = AppRouteState.instance;
      state.openFlow('ap-invoice', tab: FlowTab.canvas);
      state.openSection('tasks');
      expect(state.value.flowId, isEmpty);
      expect(state.value.location, '/tasks');
    });
  });
}
