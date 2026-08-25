import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart';

/// Wraps FirebaseAuth so the rest of the app has one place to ask about
/// auth state. When Firebase isn't configured (placeholder firebase_options),
/// [devMode] is true and the app runs unauthenticated against a dev server.
class AuthService {
  static final AuthService instance = AuthService._();
  AuthService._();

  bool devMode = false;

  ValueListenable<bool> get signedIn => _signedIn;
  final ValueNotifier<bool> _signedIn = ValueNotifier(false);

  void bind() {
    if (devMode) {
      _signedIn.value = true;
      return;
    }
    FirebaseAuth.instance.authStateChanges().listen((user) {
      _signedIn.value = user != null;
    });
  }

  String? get email =>
      devMode ? 'dev@localhost' : FirebaseAuth.instance.currentUser?.email;

  String? get uid => devMode ? 'dev' : FirebaseAuth.instance.currentUser?.uid;

  Future<void> signIn(String email, String password) async {
    await FirebaseAuth.instance.signInWithEmailAndPassword(
      email: email,
      password: password,
    );
  }

  Future<void> signOut() async {
    if (!devMode) await FirebaseAuth.instance.signOut();
  }

  Future<String?> idToken() async {
    if (devMode) return null;
    return FirebaseAuth.instance.currentUser?.getIdToken();
  }
}
