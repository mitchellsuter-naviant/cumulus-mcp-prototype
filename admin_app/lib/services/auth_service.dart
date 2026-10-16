import 'dart:convert';

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
      // Whoever is signed in has changed, so a token minted for the previous
      // one must not survive into the next session's requests.
      _forgetToken();
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
    _forgetToken();
    if (!devMode) await FirebaseAuth.instance.signOut();
  }

  /// The last token minted, who it was minted for, and when it dies.
  ///
  /// Every API request used to await `getIdToken()`, which is a plugin channel
  /// round trip even when the token in hand is good for another hour — and a
  /// screen that fires six requests paid it six times. A JWT carries its own
  /// expiry, so the question is answerable here.
  String? _token;
  String? _tokenUid;
  DateTime? _tokenExpiry;

  /// How long before the real expiry the cache stops trusting itself, so a
  /// request that takes a moment to reach the server never arrives carrying a
  /// token that died in flight.
  static const Duration _refreshWindow = Duration(minutes: 5);

  void _forgetToken() {
    _token = null;
    _tokenUid = null;
    _tokenExpiry = null;
  }

  /// Drop the cached token so the next request mints a fresh one.
  ///
  /// The cache trusts the `exp` claim it read, and the claim can be right while
  /// the token is not: a revoked session, or a clock a few minutes apart from
  /// Google's, produces a 401 from the server while this still believes it
  /// holds something good for another hour. The API client calls this on that
  /// 401 and asks once more — the server is the authority on whether a token
  /// is live, and nothing else here can find out.
  void invalidateToken() => _forgetToken();

  Future<String?> idToken() async {
    if (devMode) return null;
    final user = FirebaseAuth.instance.currentUser;
    if (user == null) {
      _forgetToken();
      return null;
    }
    final cached = _token;
    final expiry = _tokenExpiry;
    if (cached != null &&
        expiry != null &&
        _tokenUid == user.uid &&
        DateTime.now().isBefore(expiry.subtract(_refreshWindow))) {
      return cached;
    }
    final token = await user.getIdToken();
    final freshExpiry = token == null ? null : _expiryOf(token);
    // An expiry we could not read means no cache: better to pay the round trip
    // every request, as this did before, than to hold a token past its life.
    _token = freshExpiry == null ? null : token;
    _tokenUid = freshExpiry == null ? null : user.uid;
    _tokenExpiry = freshExpiry;
    return token;
  }

  /// The `exp` claim of a JWT, or null if it is not one we can read.
  static DateTime? _expiryOf(String token) {
    final parts = token.split('.');
    if (parts.length != 3) return null;
    try {
      final payload = jsonDecode(
        utf8.decode(base64Url.decode(base64Url.normalize(parts[1]))),
      );
      if (payload is! Map) return null;
      final exp = payload['exp'];
      if (exp is! int) return null;
      return DateTime.fromMillisecondsSinceEpoch(exp * 1000, isUtc: true);
    } on FormatException {
      return null;
    }
  }
}
