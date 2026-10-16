import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';

import 'firebase_options.dart';
import 'screens/home_shell.dart';
import 'screens/login_screen.dart';
import 'services/api_client.dart';
import 'services/app_route.dart';
import 'services/url_strategy.dart';
import 'services/auth_service.dart';
import 'theme/cumulus_theme.dart';

/// Local development against a server running AUTH_BACKEND=none:
/// `flutter run --dart-define=DEV_AUTH=true` skips the Firebase gate the
/// committed firebase_options would otherwise impose. Compile-time only —
/// a hosted build never sets it.
const bool _devAuth = bool.fromEnvironment('DEV_AUTH');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  // Before the first frame, so the router never reads a hash-shaped location.
  useCleanUrls();
  if (_devAuth) {
    AuthService.instance.devMode = true;
  } else {
    try {
      await Firebase.initializeApp(
        options: DefaultFirebaseOptions.currentPlatform,
      );
    } catch (_) {
      // Placeholder firebase_options: run without auth against a dev server.
      AuthService.instance.devMode = true;
    }
  }
  AuthService.instance.bind();
  runApp(const CumulusAdminApp());
}

class CumulusAdminApp extends StatefulWidget {
  const CumulusAdminApp({super.key});

  @override
  State<CumulusAdminApp> createState() => _CumulusAdminAppState();
}

class _CumulusAdminAppState extends State<CumulusAdminApp> {
  final _delegate = CumulusRouterDelegate();
  final _parser = const CumulusRouteParser();

  @override
  void dispose() {
    _delegate.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'Cumulus Knowledge Admin',
      debugShowCheckedModeBanner: false,
      theme: buildCumulusTheme(),
      routerDelegate: _delegate,
      routeInformationParser: _parser,
    );
  }
}

/// The address bar, in both directions.
///
/// `MaterialApp.router` rather than a `home:` widget, because the console's
/// location used to live only in widget state: you could not send somebody a
/// link to the flow you were reading, and the browser's back button left the
/// page where it was. Both are the same missing piece — a location the app
/// and the browser agree on.
class CumulusRouteParser extends RouteInformationParser<AppRoute> {
  const CumulusRouteParser();

  @override
  Future<AppRoute> parseRouteInformation(
    RouteInformation routeInformation,
  ) async => AppRoute.parse(routeInformation.uri.path);

  @override
  RouteInformation restoreRouteInformation(AppRoute configuration) =>
      RouteInformation(uri: Uri.parse(configuration.location));
}

/// Renders the console and reports where it is.
///
/// The delegate holds no location of its own: [AppRouteState] is the one copy,
/// and this listens to it. A second copy here is how an address bar starts
/// disagreeing with the page under it.
class CumulusRouterDelegate extends RouterDelegate<AppRoute>
    with ChangeNotifier, PopNavigatorRouterDelegateMixin<AppRoute> {
  CumulusRouterDelegate() {
    AppRouteState.instance.addListener(notifyListeners);
  }

  @override
  final GlobalKey<NavigatorState> navigatorKey = GlobalKey<NavigatorState>();

  @override
  AppRoute get currentConfiguration => AppRouteState.instance.value;

  /// One page, always. The shell is not a stack — the rail swaps sections in
  /// place — so the Navigator exists for what is pushed *over* it: the run
  /// journal's slide-over, dialogs, the palette. Without one, those have
  /// nothing to attach to.
  @override
  Widget build(BuildContext context) => Navigator(
    key: navigatorKey,
    pages: const [MaterialPage(child: AuthGate(), name: 'cumulus')],
    onDidRemovePage: (_) {},
  );

  @override
  Future<void> setNewRoutePath(AppRoute configuration) async =>
      AppRouteState.instance.go(configuration);

  @override
  void dispose() {
    AppRouteState.instance.removeListener(notifyListeners);
    super.dispose();
  }
}

class AuthGate extends StatelessWidget {
  const AuthGate({super.key});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<bool>(
      valueListenable: AuthService.instance.signedIn,
      builder: (context, signedIn, _) {
        // A persona belongs to a session, not to the browser. Dropping it on
        // sign-out is what stops the next person who signs in from silently
        // inheriting the last impersonation; the shell then loads a session of
        // its own and the switcher starts from whoever they really are.
        if (!signedIn) ApiClient.persona = null;
        return signedIn ? const HomeShell() : const LoginScreen();
      },
    );
  }
}
