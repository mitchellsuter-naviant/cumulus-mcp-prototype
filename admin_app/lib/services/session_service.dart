import 'package:flutter/foundation.dart';

import '../models/session.dart';
import 'api_client.dart';

/// The acting persona, held once for the whole console.
///
/// A switch is not a filter the client applies. The server answers every
/// question differently once `X-Cumulus-Persona` changes, so nothing already
/// on screen survives one — which is what [generation] is for: it counts
/// switches, and the shell keys its sections off it so they are rebuilt from
/// scratch. That is cheaper and more honest than teaching every section how to
/// reload itself, and it cannot leave a stale pane behind.
class SessionController {
  SessionController(this._api);

  final ApiClient _api;

  /// Null until [load] lands. The shell paints without a persona control in
  /// the meantime rather than holding its first frame for one request.
  final ValueNotifier<Session?> session = ValueNotifier(null);

  int get generation => _generation;
  int _generation = 0;

  Future<void> load() async {
    session.value = await _api.session();
  }

  /// The header is set before the fetch because the new session has to be read
  /// *as* the persona being switched to — that is the only way to learn what
  /// it may see. It is put back if that read fails: a refused switch that left
  /// the rejected id in place would send it on every later request from every
  /// screen, so the session on screen and the session being served would
  /// silently disagree.
  Future<void> switchTo(String personaId) async {
    final previous = ApiClient.persona;
    ApiClient.persona = personaId;
    try {
      await load();
    } catch (_) {
      ApiClient.persona = previous;
      rethrow;
    }
    _generation++;
  }

  void dispose() => session.dispose();
}
