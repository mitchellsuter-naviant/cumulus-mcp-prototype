import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../models/api_key.dart';
import '../models/asset.dart';
import '../models/calendar.dart';
import '../models/dashboard.dart';
import '../models/identity.dart';
import '../models/platform_user.dart';
import '../models/team.dart';
import '../models/employee.dart';
import '../models/flow.dart';
import '../models/data_layer.dart';
import '../models/eval.dart';
import '../models/graph.dart';
import '../models/identity_screen.dart';
import '../models/ontology_screen.dart';
import '../models/payable.dart';
import '../models/reimbursement.dart';
import '../models/run.dart';
import '../models/session.dart';
import '../models/task.dart';
import '../models/world.dart';
import 'auth_service.dart';

/// Configure at build/run time:
/// flutter run -d chrome --dart-define=API_BASE_URL=https://your-service.run.app --dart-define=API_KEY=...
const apiBaseUrl = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: 'http://localhost:8080',
);
const apiKey = String.fromEnvironment('API_KEY', defaultValue: '');

/// Cloud Run cold starts can take a few seconds; past this the UI gets an error
/// it can show rather than an indefinite spinner.
const _timeout = Duration(seconds: 20);

/// Answering runs a multi-turn planner with tool calls between turns, so it is
/// legitimately slower than any other call in this client. Timing it out at 20s
/// would abandon answers that were about to arrive.
const _askTimeout = Duration(seconds: 90);

/// A pull reads every list route in a feed and delivers through the door
/// before it answers, so it is bounded by the estate rather than by us.
const _syncNowTimeout = Duration(seconds: 120);

class ApiException implements Exception {
  final int statusCode;
  final String message;
  ApiException(this.statusCode, this.message);

  @override
  String toString() => 'API error $statusCode: $message';
}

class ApiClient {
  final http.Client _http = http.Client();

  /// The persona every request acts as, or null to be whoever signed in.
  ///
  /// Static because the acting persona belongs to the session, not to a
  /// connection: the console holds a client per screen that fetches, and a
  /// switch that moved only one of them would leave half the app answering as
  /// the persona you just left.
  static String? persona;

  /// Release the underlying connection pool. Call from `State.dispose()`.
  void close() => _http.close();

  Uri _uri(String path, [Map<String, String>? query]) =>
      Uri.parse('$apiBaseUrl$path').replace(queryParameters: query);

  Future<Map<String, String>> _headers() async {
    final token = await AuthService.instance.idToken();
    return {
      'Content-Type': 'application/json',
      if (token != null) 'Authorization': 'Bearer $token',
      if (apiKey.isNotEmpty) 'X-API-Key': apiKey,
      if (persona != null) 'X-Cumulus-Persona': persona!,
    };
  }

  /// Every request goes through here so the timeout and the timeout-to-
  /// ApiException translation are applied uniformly.
  Future<http.Response> _send(
    Future<http.Response> request, {
    Duration? timeout,
  }) async {
    final limit = timeout ?? _timeout;
    try {
      return await request.timeout(limit);
    } on TimeoutException {
      throw ApiException(
        408,
        'The server did not respond within ${limit.inSeconds}s.',
      );
    }
  }

  // ---- Session ----

  /// The shell's first call, and the only one that reports on the persona
  /// itself rather than through it.
  Future<Session> session() async {
    final resp = await _send(
      _http.get(_uri('/api/ui/v1/session'), headers: await _headers()),
    );
    _check(resp);
    return Session.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<List<AssetSummary>> listAssets({
    String? prefix,
    String? status,
    String? kind,
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/assets', {
          if (prefix != null && prefix.isNotEmpty) 'prefix': prefix,
          if (status != null && status.isNotEmpty) 'status': status,
          if (kind != null && kind.isNotEmpty) 'kind': kind,
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => AssetSummary.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<Asset> getAsset(String id) async {
    final resp = await _send(
      _http.get(_uri('/api/v1/assets/$id'), headers: await _headers()),
    );
    _check(resp);
    return Asset.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Dereference a read-set reference — the `knowledge:{id}@rev{n}` strings
  /// run journals and tasks carry — back into the document it names.
  ///
  /// The ref goes into the path verbatim. Its colon and `@` are both legal
  /// path characters, so they survive [_uri] untouched and the `{ref:path}`
  /// route receives exactly what the journal recorded. Percent-encoding them
  /// happens to work as well, but only because Starlette decodes before it
  /// matches, and a call should not lean on that.
  Future<RefResolution> resolveRef(String ref) async {
    final resp = await _send(
      _http.get(_uri('/api/v1/refs/$ref'), headers: await _headers()),
    );
    _check(resp);
    return RefResolution.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<List<SearchHit>> search(
    String query, {
    int limit = 20,
    SearchFilters filters = const SearchFilters(),
    SearchSort sort = SearchSort.relevance,
    String mode = 'hybrid',
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/search', {
          'q': query,
          'limit': '$limit',
          'include_drafts': 'true',
          'sort': sort.wire,
          'mode': mode,
          ...filters.toQuery(),
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => SearchHit.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// Refinement counts for the current query and filters.
  Future<SearchFacets> searchFacets(
    String query, {
    SearchFilters filters = const SearchFilters(),
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/search/facets', {
          'q': query,
          'include_drafts': 'true',
          ...filters.toQuery(),
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return SearchFacets.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// Ask a question rather than search for a document.
  ///
  /// The server plans over both the document text and the corpus metadata, so
  /// this answers things retrieval cannot — "which assets does the HR team
  /// manage" lives in the owner field, not in any document's prose. It always
  /// returns something: with no generative backend configured it degrades to
  /// ranked documents and says so in [AskAnswer.backend].
  ///
  /// A planner runs several model turns, so this can take far longer than a
  /// search; [_askTimeout] rather than [_timeout] applies.
  Future<AskAnswer> ask(String question, {String? scopeAssetId}) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/ask'),
        headers: await _headers(),
        body: jsonEncode({
          'question': question,
          if (scopeAssetId != null) 'scope_asset_id': scopeAssetId,
          'include_drafts': true,
        }),
      ),
      timeout: _askTimeout,
    );
    _check(resp);
    return AskAnswer.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Reads and citations for one document.
  Future<AssetUsage> assetUsage(String id, {int days = 30}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/assets/$id/usage', {'days': '$days'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return AssetUsage.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// The corpus and its traffic, joined — the whole Library screen in one
  /// call. Zero-filled, because the documents with no traffic are precisely
  /// what the dead-weight lenses exist to find.
  ///
  /// Returned as the two shapes the screen already holds rather than a third
  /// model: a [LibraryRow] would be an [AssetSummary] and an [AssetUsage] with
  /// a new name, and a name is a concept someone has to learn.
  Future<({List<AssetSummary> assets, Map<String, AssetUsage> usage})>
  libraryScreen({int days = 30}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/ui/v1/library', {'days': '$days'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    final body = jsonDecode(resp.body) as Map<String, dynamic>;
    final rows = (body['assets'] as List<dynamic>? ?? const [])
        .cast<Map<String, dynamic>>();
    return (
      assets: [
        for (final row in rows)
          AssetSummary(
            id: row['id'] as String? ?? '',
            path: row['path'] as String? ?? '',
            title: row['title'] as String? ?? '',
            // Not carried by this route: the list renders a taxonomy, and no
            // row of it has ever asked how the bytes are encoded.
            contentType: '',
            authorityLevel: row['authority_level'] as String? ?? '',
            kind: row['kind'] as String? ?? 'document',
            origin: row['origin'] as String? ?? 'authored',
            scope: row['scope'] as String? ?? 'org',
            owner: row['owner'] as String? ?? '',
            tags: (row['tags'] as List<dynamic>? ?? const [])
                .map((e) => '$e')
                .toList(),
            status: row['status'] as String? ?? '',
            revision: row['revision'] as int? ?? 1,
            updatedAt:
                DateTime.tryParse(row['updated_at_iso'] as String? ?? '') ??
                DateTime.fromMillisecondsSinceEpoch(0),
          ),
      ],
      usage: {
        for (final row in rows)
          row['id'] as String: AssetUsage(
            assetId: row['id'] as String? ?? '',
            reads: row['reads'] as int? ?? 0,
            citations: row['citations'] as int? ?? 0,
            impressions: row['impressions'] as int? ?? 0,
            lastUsedAt: DateTime.tryParse(
              row['last_used_at_iso'] as String? ?? '',
            ),
          ),
      },
    );
  }

  Future<Asset> createAsset(Asset asset) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/assets'),
        headers: await _headers(),
        body: jsonEncode(asset.toJson()),
      ),
    );
    _check(resp);
    return Asset.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<Asset> updateAsset(Asset asset) async {
    final body = asset.toJson()..remove('id');
    final resp = await _send(
      _http.put(
        _uri('/api/v1/assets/${asset.id}'),
        headers: await _headers(),
        body: jsonEncode(body),
      ),
    );
    _check(resp);
    return Asset.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<void> deleteAsset(String id) async {
    final resp = await _send(
      _http.delete(_uri('/api/v1/assets/$id'), headers: await _headers()),
    );
    _check(resp);
  }

  // ---- User management ----

  Future<Map<String, dynamic>> whoami() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/users/me'), headers: await _headers()),
    );
    _check(resp);
    return jsonDecode(resp.body) as Map<String, dynamic>;
  }

  Future<PlatformUser> createUser({
    required String email,
    required String password,
    String displayName = '',
    String role = 'user',
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/users'),
        headers: await _headers(),
        body: jsonEncode({
          'email': email,
          'password': password,
          'display_name': displayName,
          'role': role,
        }),
      ),
    );
    _check(resp);
    return PlatformUser.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  // ---- Identities ----

  /// People and machine clients in one list, as the server merges them.
  Future<List<Identity>> listIdentities() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/identities'), headers: await _headers()),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => Identity.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// Agents and services only — people are created through [createUser],
  /// because they need a password.
  Future<Identity> createMachineIdentity({
    required String displayName,
    String kind = 'agent',
    String role = 'user',
    String description = '',
    String owner = '',
    String team = '',
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/identities'),
        headers: await _headers(),
        body: jsonEncode({
          'display_name': displayName,
          'kind': kind,
          'role': role,
          'description': description,
          'owner': owner,
          if (team.isNotEmpty) 'team': team,
        }),
      ),
    );
    _check(resp);
    return Identity.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<Identity> changeIdentityRole(String uid, String role) async {
    final resp = await _send(
      _http.put(
        _uri('/api/v1/identities/$uid/role'),
        headers: await _headers(),
        body: jsonEncode({'role': role}),
      ),
    );
    _check(resp);
    return Identity.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Deletes the identity and revokes every key it holds.
  Future<void> deleteIdentity(String uid) async {
    final resp = await _send(
      _http.delete(_uri('/api/v1/identities/$uid'), headers: await _headers()),
    );
    _check(resp);
  }

  // ---- teams ----

  Future<List<Team>> listTeams() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/teams'), headers: await _headers()),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => Team.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<Team> getTeam(String slug) async {
    final resp = await _send(
      _http.get(_uri('/api/v1/teams/$slug'), headers: await _headers()),
    );
    _check(resp);
    return Team.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<Team> createTeam({
    required String slug,
    required String name,
    String description = '',
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/teams'),
        headers: await _headers(),
        body: jsonEncode({
          'slug': slug,
          'name': name,
          'description': description,
        }),
      ),
    );
    _check(resp);
    return Team.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<Team> updateTeam(
    String slug, {
    String? name,
    String? description,
  }) async {
    final resp = await _send(
      _http.patch(
        _uri('/api/v1/teams/$slug'),
        headers: await _headers(),
        body: jsonEncode({
          if (name != null) 'name': name,
          if (description != null) 'description': description,
        }),
      ),
    );
    _check(resp);
    return Team.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }


  /// Adds a member, or changes the team role of one already on the team. This
  /// grants team authority only — platform roles are changed elsewhere.
  Future<Team> setTeamMember(
    String slug,
    String identityUid, {
    String teamRole = 'member',
  }) async {
    final resp = await _send(
      _http.put(
        _uri('/api/v1/teams/$slug/members/$identityUid'),
        headers: await _headers(),
        body: jsonEncode({'team_role': teamRole}),
      ),
    );
    _check(resp);
    return Team.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<Team> removeTeamMember(String slug, String identityUid) async {
    final resp = await _send(
      _http.delete(
        _uri('/api/v1/teams/$slug/members/$identityUid'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return Team.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<List<AssetSummary>> teamAssets(String slug) async {
    final resp = await _send(
      _http.get(_uri('/api/v1/teams/$slug/assets'), headers: await _headers()),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => AssetSummary.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  // ---- reimbursements ----

  /// `mine: true` narrows to the acting persona's own rows server-side. The
  /// hub used to fetch every submitter's receipts and discard the rest in the
  /// browser, which sent a table across the wire to throw most of it away.
  Future<List<Reimbursement>> listReimbursements({
    String? state,
    bool mine = false,
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/reimbursements', {
          if (state != null && state.isNotEmpty) 'state': state,
          if (mine) 'mine': 'true',
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => Reimbursement.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<ReimbursementStats> reimbursementStats() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/reimbursements/stats'), headers: await _headers()),
    );
    _check(resp);
    return ReimbursementStats.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// Triggers one live run of the expense-reimbursement flow. The image is
  /// optional; the note doubles as extraction input for the local stub reader.
  Future<Reimbursement> submitReimbursement({
    String? imageBase64,
    String? filename,
    String? note,
    List<Map<String, String>> documents = const [],
    String commentary = '',
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/reimbursements'),
        headers: await _headers(),
        body: jsonEncode({
          if (imageBase64 != null) 'image_base64': imageBase64,
          if (filename != null) 'filename': filename,
          if (note != null) 'note': note,
          if (documents.isNotEmpty) 'documents': documents,
          if (commentary.isNotEmpty) 'commentary': commentary,
        }),
      ),
    );
    return Reimbursement.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<Reimbursement> getReimbursement(String id) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/reimbursements/$id'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return Reimbursement.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<PayableInvoice> getPayable(String id) async {
    final resp = await _send(
      _http.get(_uri('/api/v1/payables/$id'), headers: await _headers()),
    );
    _check(resp);
    return PayableInvoice.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<List<PayableInvoice>> listPayables({String? state}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/payables', {
          if (state != null && state.isNotEmpty) 'state': state,
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => PayableInvoice.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<PayableStats> payableStats() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/payables/stats'), headers: await _headers()),
    );
    _check(resp);
    return PayableStats.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// The whole AP workspace in one call: the rows this reader is accountable
  /// for, the strip's counts over exactly that slice, and the composer —
  /// the vendor master, its orders and their lines, and the governed values a
  /// run will read.
  Future<PayablesScreen> payablesScreen() async {
    final resp = await _send(
      _http.get(_uri('/api/ui/v1/payables'), headers: await _headers()),
    );
    _check(resp);
    return PayablesScreen.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// One live run of the accounts-payable flow: extract → resolve the vendor
  /// → three-way match → anomaly screen → post or review.
  ///
  /// The *scenario* is posted rather than the hint string it prints, so the
  /// string the run receives is serialized in one place — the server's
  /// `InvoiceScenario.to_hint` — and comes back on the outcome as sent.
  Future<PayableOutcome> submitInvoice({
    InvoiceScenario? scenario,
    String imageBase64 = '',
    String filename = '',
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/ui/v1/payables/submissions'),
        headers: await _headers(),
        body: jsonEncode({
          if (scenario != null) 'scenario': scenario.toJson(),
          if (imageBase64.isNotEmpty) 'image_base64': imageBase64,
          if (filename.isNotEmpty) 'filename': filename,
        }),
      ),
      // A vision ModelCall plus resolution, matching and adjudication.
      timeout: const Duration(seconds: 90),
    );
    _check(resp);
    return PayableOutcome.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  // ---- employee hub ----

  Future<EmployeeProfile> employeeProfile() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/employee/me'), headers: await _headers()),
    );
    _check(resp);
    return EmployeeProfile.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<List<PtoRequest>> listPtoRequests() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/employee/pto'), headers: await _headers()),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => PtoRequest.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<PtoRequest> requestPto({
    required String startDate,
    required String endDate,
    required double days,
    String note = '',
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/employee/pto'),
        headers: await _headers(),
        body: jsonEncode({
          'start_date': startDate,
          'end_date': endDate,
          'days': days,
          if (note.isNotEmpty) 'note': note,
        }),
      ),
    );
    _check(resp);
    return PtoRequest.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<HrChatReply> hrChat(String message) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/employee/hr-chat'),
        headers: await _headers(),
        body: jsonEncode({'message': message}),
      ),
      // A planner-backed answer can take a while.
      timeout: const Duration(seconds: 60),
    );
    _check(resp);
    return HrChatReply.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  // ---- calendars ----

  /// Your week, mirrored from the calendar provider. `start` is a Monday-ish
  /// ISO date; omitting it asks the server for the current week.
  ///
  /// When the signed-in principal is not in the workforce — every local-dev
  /// session — the server answers with a demo persona's calendar and says so
  /// in `personaNote`. The UI shows that note rather than pretending.
  Future<Agenda> calendarAgenda({String? start, int days = 7}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/calendar/me', {
          if (start != null && start.isNotEmpty) 'start': start,
          'days': '$days',
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return Agenda.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Every mirrored calendar in the system, one per person.
  Future<List<CalendarSummary>> calendars() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/calendar/calendars'), headers: await _headers()),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => CalendarSummary.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  // ---- tasks ----

  /// The Task Queue: open judgments first, high priority first among them.
  Future<List<QueueTask>> listTasks({
    String? status,
    String? queue,
    String? kind,
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/tasks', {
          if (status != null && status.isNotEmpty) 'status': status,
          if (queue != null && queue.isNotEmpty) 'queue': queue,
          if (kind != null && kind.isNotEmpty) 'kind': kind,
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => QueueTask.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// Open counts for the top-bar badge; only open tasks are counted.
  Future<TaskStats> taskStats() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/tasks/stats'), headers: await _headers()),
    );
    _check(resp);
    return TaskStats.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Record a human judgment. The server refuses API keys (403) and a second
  /// decision on the same task (409).
  /// Decide a task. `payload` is the structured half of a `corrected`
  /// verdict — edited values, added or removed lines, a chosen entity id.
  /// Prose alone cannot say "split this receipt into two lines".
  /// One Task by id — what the outcome's gate points at.
  Future<QueueTask> getTask(String id) async {
    final resp = await _send(
      _http.get(_uri('/api/v1/tasks/$id'), headers: await _headers()),
    );
    _check(resp);
    return QueueTask.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<QueueTask> decideTask(
    String id, {
    required String verdict,
    String note = '',
    String correction = '',
    Map<String, dynamic> payload = const {},
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/tasks/$id/decide'),
        headers: await _headers(),
        body: jsonEncode({
          'verdict': verdict,
          if (note.isNotEmpty) 'note': note,
          if (correction.isNotEmpty) 'correction': correction,
          if (payload.isNotEmpty) 'payload': payload,
        }),
      ),
    );
    _check(resp);
    return QueueTask.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  // ---- authored ABoxes ----

  Future<List<AuthoredWorld>> listWorlds() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/worlds'), headers: await _headers()),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => AuthoredWorld.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// Runs the fragment into a throwaway world and returns what it asserts.
  /// Not free — the sync flows really run — so it is a separate call from the
  /// catalog.
  Future<MaterializedWorld> materializeWorld(String worldId) async {
    final resp = await _send(
      _http.get(_uri('/api/v1/worlds/$worldId'), headers: await _headers()),
      timeout: _askTimeout,
    );
    _check(resp);
    return MaterializedWorld.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  // ---- graph-shaped evals ----

  Future<List<EvalSpecSummary>> listEvalSpecs({String? flowId}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/evals', {
          if (flowId != null && flowId.isNotEmpty) 'flow_id': flowId,
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => EvalSpecSummary.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<EvalSpec> getEvalSpec(String specId) async {
    final resp = await _send(
      _http.get(_uri('/api/v1/evals/$specId'), headers: await _headers()),
    );
    _check(resp);
    return EvalSpec.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<List<EvalCase>> listEvalCases(String specId) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/evals/$specId/cases'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => EvalCase.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// The most recent result in this server process. Results are not persisted:
  /// a pass rate only means something against the knowledge revisions it read.
  Future<EvalRunResult?> lastEvalResult(String specId) async {
    final resp = await _http.get(
      _uri('/api/v1/evals/$specId/result'),
      headers: await _headers(),
    );
    if (resp.statusCode == 404) return null;
    _check(resp);
    return EvalRunResult.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<EvalRunResult> runEval(String specId, {List<String> caseIds = const []}) async {
    final query = <String, String>{};
    if (caseIds.length == 1) query['case_id'] = caseIds.first;
    final resp = await _send(
      _http.post(
        _uri('/api/v1/evals/$specId/run', query.isEmpty ? null : query),
        headers: await _headers(),
      ),
      timeout: _askTimeout,
    );
    _check(resp);
    return EvalRunResult.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  // ---- runs: the durable journal a workspace's run list reads ----

  Future<List<FlowRunSummary>> listRuns({
    String? flowId,
    String? status,
    int limit = 50,
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/runs', {
          if (flowId != null && flowId.isNotEmpty) 'flow_id': flowId,
          if (status != null && status.isNotEmpty) 'status': status,
          'limit': '$limit',
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => FlowRunSummary.fromJson(j as Map<String, dynamic>))
        .toList();
  }


  Future<FlowRun> getRun(String runId) async {
    final resp = await _send(
      _http.get(_uri('/api/v1/runs/$runId'), headers: await _headers()),
    );
    _check(resp);
    return FlowRun.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  // ---- the entity layer ----

  Future<Ontology> ontology() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/graph/ontology'), headers: await _headers()),
    );
    _check(resp);
    return Ontology.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Everything the model diagram draws, in one call: the types, the links,
  /// the Collections it clusters by, and — when [flow] names one — that
  /// flow's observed footprint beside the one it declared.
  Future<OntologyScreen> ontologyScreen({String? flow}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/ui/v1/ontology', {
          if (flow != null && flow.isNotEmpty) 'flow': flow,
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return OntologyScreen.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// The live ABox in the same shape an authored fragment comes back in, so
  /// one renderer serves both.
  Future<MaterializedWorld> graphInstances({String? type, int limit = 400}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/graph/instances', {
          if (type != null && type.isNotEmpty) 'type': type,
          'limit': '$limit',
        }),
        headers: await _headers(),
      ),
      timeout: _askTimeout,
    );
    _check(resp);
    return MaterializedWorld.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<List<EntityHit>> searchEntities(String q, {int limit = 20}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/graph/search', {'q': q, 'limit': '$limit'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => EntityHit.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<List<LinkRow>> listLinks({String? type, int limit = 200}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/graph/links', {
          if (type != null) 'type': type,
          'limit': '$limit',
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => LinkRow.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<List<GraphObject>> listObjects({
    String? type,
    String q = '',
    int limit = 100,
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/graph/objects', {
          if (type != null && type.isNotEmpty) 'type': type,
          if (q.isNotEmpty) 'q': q,
          'limit': '$limit',
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => GraphObject.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<GraphObjectDetail> getObject(String objectId) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/graph/objects/$objectId'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return GraphObjectDetail.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// The analytical question: two hops and a sum, with the path attached.
  Future<ExpenseRollup> employeeExpenses(String email, {int days = 180}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/graph/employees/$email/expenses', {'days': '$days'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return ExpenseRollup.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  // ---- the data layer's governance views ----

  /// The current state of the whole system, in one call.
  ///
  /// Four questions — what the map holds, whether the outside world is
  /// arriving, whether judgment is flowing, whether governance is holding —
  /// composed server-side from the surfaces that already own their numbers.
  /// One call because fetching them separately would put the join in the
  /// client, where each panel could show a different instant and disagree
  /// about a figure they were both reading off the same store.
  Future<Dashboard> dashboard() async {
    final resp = await _send(
      _http.get(_uri('/api/ui/v1/dashboard'), headers: await _headers()),
      timeout: _askTimeout,
    );
    _check(resp);
    return Dashboard.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Every inbound feed against the contract it lands under, plus the
  /// artifacts its runs left — the whole Sources screen in one call.
  ///
  /// Health and contract in one row is the argument for the screen: a feed
  /// delivering cleanly under a mapping nobody declared is a different problem
  /// from one that is failing, and reading them off two lists side by side is
  /// how the second kind goes unnoticed.
  Future<SourcesScreen> sourcesScreen() async {
    final resp = await _send(
      _http.get(_uri('/api/ui/v1/sources'), headers: await _headers()),
    );
    _check(resp);
    return SourcesScreen.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// Pull one feed now, and get back the row the screen redraws.
  ///
  /// Synchronous on purpose: the operator pressed it, so the answer is what
  /// landed rather than "accepted". A pull reads every list route in the feed
  /// and delivers through the door, which is legitimately slower than a read —
  /// timing it out at 20s would abandon a sync that was about to finish.
  Future<SyncNowOutcome> syncSourceNow(String source) async {
    final resp = await _send(
      _http.post(
        _uri('/api/ui/v1/sources/$source/sync-now'),
        headers: await _headers(),
      ),
      timeout: _syncNowTimeout,
    );
    _check(resp);
    return SyncNowOutcome.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// The identity workbench: every proposed merge, the evidence under it, the
  /// two records it joins, and what has been ruled on — in one call.
  ///
  /// One call because the four are one question. A proposal is an edge, its
  /// evidence is on the edge, the records are the objects it joins, and
  /// whether you may answer it is a property of a Task somewhere else
  /// entirely. Fetching those separately would assemble the argument here,
  /// where it could drift from what the server thinks it means.
  ///
  /// Throws 403 for a persona that owns neither stewardship desk and reads
  /// only its own work — the refusal carries the sentence the screen prints.
  Future<IdentityScreen> identityScreen() async {
    final resp = await _send(
      _http.get(_uri('/api/ui/v1/identity'), headers: await _headers()),
    );
    _check(resp);
    return IdentityScreen.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<StoredDocument> getDocument(String documentId) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/documents/$documentId'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return StoredDocument.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// The artifact's bytes, for a review screen to draw boxes over. Returns
  /// null when the run was hint-driven and carried no file.
  Future<Uint8List?> documentContent(String documentId) async {
    final resp = await _http.get(
      _uri('/api/v1/documents/$documentId/content'),
      headers: await _headers(),
    );
    if (resp.statusCode == 404) return null;
    _check(resp);
    return resp.bodyBytes;
  }

  Future<List<VendorProfile>> listVendorProfiles() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/vendor-profiles'), headers: await _headers()),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => VendorProfile.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<Map<String, dynamic>> refreshVendorProfiles({int windowDays = 365}) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/vendor-profiles/refresh', {'window_days': '$windowDays'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return jsonDecode(resp.body) as Map<String, dynamic>;
  }

  // ---- flows ----

  /// The fleet: every Flow and DataFlow definition with 30-day rollups,
  /// sorted by model spend.
  Future<List<FlowSummary>> listFlows({
    String q = '',
    String? kind,
    String? status,
    String? team,
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows', {
          if (q.isNotEmpty) 'q': q,
          if (kind != null && kind.isNotEmpty) 'kind': kind,
          if (status != null && status.isNotEmpty) 'status': status,
          if (team != null && team.isNotEmpty) 'team': team,
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => FlowSummary.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<FleetStats> flowFleetStats({int days = 30}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/stats', {'days': '$days'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return FleetStats.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<FlowDetail> getFlow(String id) async {
    final resp = await _send(
      _http.get(_uri('/api/v1/flows/$id'), headers: await _headers()),
    );
    _check(resp);
    return FlowDetail.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// Lifecycle and governed-knowledge edits (status, ROI assumptions, budget).
  Future<FlowDetail> updateFlow(
    String id, {
    String? status,
    String? description,
    double? roiMinutesPerRun,
    double? roiHourlyRateUsd,
    double? modelBudgetUsdPerRun,
  }) async {
    final resp = await _send(
      _http.patch(
        _uri('/api/v1/flows/$id'),
        headers: await _headers(),
        body: jsonEncode({
          if (status != null) 'status': status,
          if (description != null) 'description': description,
          if (roiMinutesPerRun != null) 'roi_minutes_per_run': roiMinutesPerRun,
          if (roiHourlyRateUsd != null) 'roi_hourly_rate_usd': roiHourlyRateUsd,
          if (modelBudgetUsdPerRun != null)
            'model_budget_usd_per_run': modelBudgetUsdPerRun,
        }),
      ),
    );
    _check(resp);
    return FlowDetail.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<FlowUsage> flowUsage(String id, {int days = 30}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/usage', {'days': '$days'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return FlowUsage.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// Published history plus any open draft, newest first.
  Future<List<FlowVersion>> flowVersions(String id) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/versions'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => FlowVersion.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// Steps of [version] annotated added / removed / modified against
  /// [against] (the active version when omitted).
  Future<List<StepDiff>> flowVersionDiff(
    String id,
    int version, {
    int? against,
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/versions/$version/diff', {
          if (against != null) 'against': '$against',
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => StepDiff.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<List<Recommendation>> flowRecommendations(
    String id, {
    int days = 30,
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/recommendations', {'days': '$days'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => Recommendation.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// Stages a recommendation into the draft version — never the live one.
  /// Measuring it is the eval spec's job, run deliberately.
  Future<AppliedRecommendation> applyRecommendation(
    String id,
    String recommendationId,
  ) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/flows/$id/recommendations/$recommendationId/apply'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return AppliedRecommendation.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  // ---- API keys ----

  Future<List<ApiKey>> listApiKeys() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/api-keys'), headers: await _headers()),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => ApiKey.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// The response carries the secret; it is not retrievable afterwards.
  Future<MintedApiKey> createApiKey({
    required String name,
    required String identityUid,
    int? expiresInDays,
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/api-keys'),
        headers: await _headers(),
        body: jsonEncode({
          'name': name,
          'identity_uid': identityUid,
          if (expiresInDays != null) 'expires_in_days': expiresInDays,
        }),
      ),
    );
    _check(resp);
    return MintedApiKey.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<void> revokeApiKey(String keyId) async {
    final resp = await _send(
      _http.delete(_uri('/api/v1/api-keys/$keyId'), headers: await _headers()),
    );
    _check(resp);
  }

  Future<List<ApiKeyUsageDay>> apiKeyUsage(String keyId, {int days = 30}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/api-keys/$keyId/usage', {'days': '$days'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => ApiKeyUsageDay.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  void _check(http.Response resp) {
    if (resp.statusCode >= 400) {
      String message = resp.body;
      try {
        message = (jsonDecode(resp.body) as Map<String, dynamic>)['detail']
            .toString();
      } catch (_) {}
      throw ApiException(resp.statusCode, message);
    }
  }
}
