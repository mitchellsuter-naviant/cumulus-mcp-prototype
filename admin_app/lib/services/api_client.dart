import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../models/api_key.dart';
import '../models/asset.dart';
import '../models/identity.dart';
import '../models/platform_user.dart';
import '../models/team.dart';
import '../models/employee.dart';
import '../models/flow.dart';
import '../models/data_layer.dart';
import '../models/eval.dart';
import '../models/graph.dart';
import '../models/mapping.dart';
import '../models/payable.dart';
import '../models/reimbursement.dart';
import '../models/run.dart';
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

class ApiException implements Exception {
  final int statusCode;
  final String message;
  ApiException(this.statusCode, this.message);

  @override
  String toString() => 'API error $statusCode: $message';
}

class ApiClient {
  final http.Client _http = http.Client();

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

  /// Usage for every asset, zero-filled.
  ///
  /// One call rather than one per row: it backs the list column, the corpus
  /// stats and the unused report at once. Assets with no traffic are included
  /// precisely because their absence is what the dead-weight view looks for.
  Future<Map<String, AssetUsage>> corpusUsage({int days = 30}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/usage/assets', {'days': '$days'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return {
      for (final row in jsonDecode(resp.body) as List<dynamic>)
        (row as Map<String, dynamic>)['asset_id'] as String:
            AssetUsage.fromJson(row),
    };
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

  /// Refused with 409 while the team still owns assets, so ownership is never
  /// lost by accident.
  Future<void> deleteTeam(String slug) async {
    final resp = await _send(
      _http.delete(_uri('/api/v1/teams/$slug'), headers: await _headers()),
    );
    _check(resp);
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

  Future<List<Reimbursement>> listReimbursements({String? state}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/reimbursements', {
          if (state != null && state.isNotEmpty) 'state': state,
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

  /// One live run of the accounts-payable flow: classify → exemplar one-shot
  /// or three-model ensemble → placeholders → post or review.
  Future<PayableInvoice> submitInvoice({
    String imageBase64 = '',
    String filename = '',
    String note = '',
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/payables'),
        headers: await _headers(),
        body: jsonEncode({
          if (imageBase64.isNotEmpty) 'image_base64': imageBase64,
          if (filename.isNotEmpty) 'filename': filename,
          if (note.isNotEmpty) 'note': note,
        }),
      ),
      // Up to three vision ModelCalls plus adjudication.
      timeout: const Duration(seconds: 90),
    );
    _check(resp);
    return PayableInvoice.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
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

  Future<RunStats> runStats({String? flowId}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/runs/stats', {
          if (flowId != null && flowId.isNotEmpty) 'flow_id': flowId,
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return RunStats.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<FlowRun> getRun(String runId) async {
    final resp = await _send(
      _http.get(_uri('/api/v1/runs/$runId'), headers: await _headers()),
    );
    _check(resp);
    return FlowRun.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  // ---- the entity layer ----

  Future<Map<String, dynamic>> graphStats() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/graph/stats'), headers: await _headers()),
    );
    _check(resp);
    return jsonDecode(resp.body) as Map<String, dynamic>;
  }

  Future<Ontology> ontology() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/graph/ontology'), headers: await _headers()),
    );
    _check(resp);
    return Ontology.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<InvariantReport> graphInvariants() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/graph/invariants'), headers: await _headers()),
    );
    _check(resp);
    return InvariantReport.fromJson(
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

  Future<List<VendorRecord>> listVendors() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/graph/vendors'), headers: await _headers()),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => VendorRecord.fromJson(j as Map<String, dynamic>))
        .toList();
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

  Future<List<DataCollection>> listCollections({String? role}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/collections', {
          if (role != null && role.isNotEmpty) 'role': role,
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => DataCollection.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<List<SourceStatus>> listSources() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/sources'), headers: await _headers()),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => SourceStatus.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<MappingRegistry> listMappings() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/mappings'), headers: await _headers()),
    );
    _check(resp);
    return MappingRegistry.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<List<StoredDocument>> listDocuments({String? kind, String? runId}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/documents', {
          if (kind != null && kind.isNotEmpty) 'kind': kind,
          if (runId != null && runId.isNotEmpty) 'run_id': runId,
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => StoredDocument.fromJson(j as Map<String, dynamic>))
        .toList();
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

  Future<Map<String, dynamic>> documentStats() async {
    final resp = await _send(
      _http.get(_uri('/api/v1/documents/stats'), headers: await _headers()),
    );
    _check(resp);
    return jsonDecode(resp.body) as Map<String, dynamic>;
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

  Future<List<RunSummary>> flowRuns(String id, {int limit = 25}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/runs', {'limit': '$limit'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => RunSummary.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// The full journal of one run: every step, ModelCall, verdict and commit.
  Future<RunDetail> flowRun(String id, String runId) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/runs/$runId'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return RunDetail.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<List<AuditEntry>> flowAudit(String id, {int limit = 60}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/audit', {'limit': '$limit'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => AuditEntry.fromJson(j as Map<String, dynamic>))
        .toList();
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

  /// Stages a recommendation into the draft version — never the live one —
  /// and reports the experiment auto-launched to measure it.
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

  /// The declared canonical facts plus the derived producer map for one
  /// version (the active one when [version] is null).
  Future<FlowContractInfo> flowContract(String id, {int? version}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/contract', {
          if (version != null) 'version': '$version',
        }),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return FlowContractInfo.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<List<CorrectionSet>> flowCorrectionSets(String id) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/correction-sets'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => CorrectionSet.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<GoldenSetInfo> flowGoldenSet(String id) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/golden-set'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return GoldenSetInfo.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// Adds a case. Pass [runId] to promote a real execution — the server tags
  /// it `promoted-from-run` and keeps the pointer back to that run.
  Future<GoldenSetInfo> addGoldenCase(
    String id, {
    required String name,
    String inputRef = '',
    String expected = '',
    String source = 'human-authored',
    String runId = '',
    String payload = '',
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/flows/$id/golden-set'),
        headers: await _headers(),
        body: jsonEncode({
          'name': name,
          'input_ref': inputRef,
          'expected': expected,
          'source': source,
          if (runId.isNotEmpty) 'run_id': runId,
          if (payload.isNotEmpty) 'payload': payload,
        }),
      ),
    );
    _check(resp);
    return GoldenSetInfo.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<GoldenSetInfo> removeGoldenCase(String id, String caseId) async {
    final resp = await _send(
      _http.delete(
        _uri('/api/v1/flows/$id/golden-set/$caseId'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return GoldenSetInfo.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// Bulk upload: JSONL, one case per line — many cases, one dataset revision.
  Future<GoldenSetInfo> importGoldenCases(String id, String jsonl) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/flows/$id/golden-set/import'),
        headers: await _headers(),
        body: jsonEncode({'jsonl': jsonl}),
      ),
    );
    _check(resp);
    return GoldenSetInfo.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// The fixture document behind a case, with grounded field regions.
  Future<CaseDocument> goldenCaseDocument(String id, String caseId) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/golden-set/$caseId/document'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return CaseDocument.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Records a human's pass over the case's labels. An empty [corrections]
  /// map confirms the case as labelled; either way the dataset version moves.
  Future<GoldenSetInfo> reviewGoldenCase(
    String id,
    String caseId, {
    Map<String, String> corrections = const {},
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/flows/$id/golden-set/$caseId/review'),
        headers: await _headers(),
        body: jsonEncode({'corrections': corrections}),
      ),
    );
    _check(resp);
    return GoldenSetInfo.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<List<TestRun>> flowTestRuns(String id) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/test-runs'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => TestRun.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// Kicks off a golden-set evaluation; the harness runs case-by-case in the
  /// background, so poll [flowTestRun] for progress. Writes suppressed.
  Future<TestRun> launchTestRun(
    String id, {
    String candidateVersion = '',
    String baselineVersion = '',
    String reads = 'pinned',
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/flows/$id/test-runs'),
        headers: await _headers(),
        body: jsonEncode({
          'candidate_version': candidateVersion,
          'baseline_version': baselineVersion,
          'reads': reads,
        }),
      ),
    );
    _check(resp);
    return TestRun.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<TestRun> flowTestRun(String id, String testId) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/flows/$id/test-runs/$testId'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return TestRun.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
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
