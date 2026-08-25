import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/api_key.dart';
import '../models/asset.dart';
import '../models/identity.dart';
import '../models/platform_user.dart';
import '../models/team.dart';
import '../models/workflow.dart';
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
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/assets', {
          if (prefix != null && prefix.isNotEmpty) 'prefix': prefix,
          if (status != null && status.isNotEmpty) 'status': status,
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

  // ---- workflows ----

  /// The fleet: every Workflow and DataFlow definition with 30-day rollups,
  /// sorted by model spend.
  Future<List<WorkflowSummary>> listWorkflows({
    String q = '',
    String? kind,
    String? status,
    String? team,
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/workflows', {
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
        .map((j) => WorkflowSummary.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  Future<FleetStats> workflowFleetStats({int days = 30}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/workflows/stats', {'days': '$days'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return FleetStats.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<WorkflowDetail> getWorkflow(String id) async {
    final resp = await _send(
      _http.get(_uri('/api/v1/workflows/$id'), headers: await _headers()),
    );
    _check(resp);
    return WorkflowDetail.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  /// Lifecycle and governed-knowledge edits (status, ROI assumptions, budget).
  Future<WorkflowDetail> updateWorkflow(
    String id, {
    String? status,
    String? description,
    double? roiMinutesPerRun,
    double? roiHourlyRateUsd,
    double? modelBudgetUsdPerRun,
  }) async {
    final resp = await _send(
      _http.patch(
        _uri('/api/v1/workflows/$id'),
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
    return WorkflowDetail.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<WorkflowUsage> workflowUsage(String id, {int days = 30}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/workflows/$id/usage', {'days': '$days'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return WorkflowUsage.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<List<RunSummary>> workflowRuns(String id, {int limit = 25}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/workflows/$id/runs', {'limit': '$limit'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => RunSummary.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// The full journal of one run: every step, ModelCall, verdict and commit.
  Future<RunDetail> workflowRun(String id, String runId) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/workflows/$id/runs/$runId'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return RunDetail.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<List<AuditEntry>> workflowAudit(String id, {int limit = 60}) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/workflows/$id/audit', {'limit': '$limit'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => AuditEntry.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// Published history plus any open draft, newest first.
  Future<List<WorkflowVersion>> workflowVersions(String id) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/workflows/$id/versions'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => WorkflowVersion.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// Steps of [version] annotated added / removed / modified against
  /// [against] (the active version when omitted).
  Future<List<StepDiff>> workflowVersionDiff(
    String id,
    int version, {
    int? against,
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/workflows/$id/versions/$version/diff', {
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

  Future<List<Recommendation>> workflowRecommendations(
    String id, {
    int days = 30,
  }) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/workflows/$id/recommendations', {'days': '$days'}),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => Recommendation.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// Stages a recommendation into the draft version — never the live one.
  /// Returns the draft it landed in.
  Future<WorkflowVersion> applyRecommendation(
    String id,
    String recommendationId,
  ) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/workflows/$id/recommendations/$recommendationId/apply'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    final body = jsonDecode(resp.body) as Map<String, dynamic>;
    return WorkflowVersion.fromJson(body['draft'] as Map<String, dynamic>);
  }

  Future<GoldenSetInfo> workflowGoldenSet(String id) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/workflows/$id/golden-set'),
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
        _uri('/api/v1/workflows/$id/golden-set'),
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
        _uri('/api/v1/workflows/$id/golden-set/$caseId'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return GoldenSetInfo.fromJson(
      jsonDecode(resp.body) as Map<String, dynamic>,
    );
  }

  Future<List<TestRun>> workflowTestRuns(String id) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/workflows/$id/test-runs'),
        headers: await _headers(),
      ),
    );
    _check(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((j) => TestRun.fromJson(j as Map<String, dynamic>))
        .toList();
  }

  /// Kicks off a golden-set evaluation; the harness runs case-by-case in the
  /// background, so poll [workflowTestRun] for progress. Writes suppressed.
  Future<TestRun> launchTestRun(
    String id, {
    String candidateVersion = '',
    String baselineVersion = '',
  }) async {
    final resp = await _send(
      _http.post(
        _uri('/api/v1/workflows/$id/test-runs'),
        headers: await _headers(),
        body: jsonEncode({
          'candidate_version': candidateVersion,
          'baseline_version': baselineVersion,
        }),
      ),
    );
    _check(resp);
    return TestRun.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<TestRun> workflowTestRun(String id, String testId) async {
    final resp = await _send(
      _http.get(
        _uri('/api/v1/workflows/$id/test-runs/$testId'),
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
