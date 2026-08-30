import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';

import 'firebase_options.dart';
import 'screens/home_shell.dart';
import 'screens/login_screen.dart';
import 'services/api_client.dart';
import 'services/auth_service.dart';
import 'theme/cumulus_theme.dart';

/// Local development against a server running AUTH_BACKEND=none:
/// `flutter run --dart-define=DEV_AUTH=true` skips the Firebase gate the
/// committed firebase_options would otherwise impose. Compile-time only —
/// a hosted build never sets it.
const bool _devAuth = bool.fromEnvironment('DEV_AUTH');

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
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

class CumulusAdminApp extends StatelessWidget {
  const CumulusAdminApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Cumulus Knowledge Admin',
      debugShowCheckedModeBanner: false,
      theme: buildCumulusTheme(),
      home: const AuthGate(),
    );
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
