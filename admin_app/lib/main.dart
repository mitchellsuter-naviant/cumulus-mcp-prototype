import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';

import 'firebase_options.dart';
import 'screens/home_shell.dart';
import 'screens/login_screen.dart';
import 'services/auth_service.dart';
import 'theme/cumulus_theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    await Firebase.initializeApp(
      options: DefaultFirebaseOptions.currentPlatform,
    );
  } catch (_) {
    // Placeholder firebase_options: run without auth against a dev server.
    AuthService.instance.devMode = true;
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
      builder: (context, signedIn, _) =>
          signedIn ? const HomeShell() : const LoginScreen(),
    );
  }
}
