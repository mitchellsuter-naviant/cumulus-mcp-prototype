/// The endpoint catalog behind the API reference page.
///
/// Kept as data rather than widgets so the reference stays close to the server
/// it describes: adding a route here is one const entry, and the rendering in
/// [api_docs_screen.dart] never has to change. When you change a route in
/// `server/app/*_api.py`, change its entry here in the same commit — a
/// reference that has drifted is worse than no reference, because people code
/// against it.
library;

/// Who may call an endpoint. `keysRefused` marks the routes that reject minted
/// API keys outright: managing credentials and team membership is treated as a
/// human action, so a key can never escalate itself.
class ApiAuth {
  final String label;
  final bool keysRefused;

  const ApiAuth(this.label, {this.keysRefused = false});

  static const reader = ApiAuth('user+');
  static const admin = ApiAuth('admin+');
  static const owner = ApiAuth('owner');
  static const open = ApiAuth('open');
  static const assetWrite = ApiAuth('admin+, or admin of the owning team');
  static const humanAdmin = ApiAuth('admin+', keysRefused: true);
  static const humanTeamAdmin = ApiAuth(
    'platform admin, or admin of that team',
    keysRefused: true,
  );
  static const anyTeamAdmin = ApiAuth('platform admin, or admin of any team');
  static const keyManager = ApiAuth(
    'admin+, or admin of a team owning the agent',
    keysRefused: true,
  );
}

class ApiParam {
  final String name;
  final String type;
  final String where; // 'path' | 'query' | 'body'
  final bool required;
  final String? defaultValue;
  final String description;

  const ApiParam(
    this.name,
    this.type, {
    this.where = 'body',
    this.required = false,
    this.defaultValue,
    this.description = '',
  });
}

class ApiError {
  final int status;
  final String detail;

  const ApiError(this.status, this.detail);
}

class Endpoint {
  final String method;
  final String path;
  final int status;
  final String summary;
  final ApiAuth auth;
  final String? notes;
  final List<ApiParam> params;
  final String? requestExample;
  final String? responseExample;
  final List<ApiError> errors;

  const Endpoint({
    required this.method,
    required this.path,
    required this.summary,
    required this.auth,
    this.status = 200,
    this.notes,
    this.params = const [],
    this.requestExample,
    this.responseExample,
    this.errors = const [],
  });
}

class EndpointGroup {
  final String title;
  final String blurb;
  final List<Endpoint> endpoints;

  const EndpointGroup({
    required this.title,
    required this.blurb,
    required this.endpoints,
  });
}

// ---- Knowledge -------------------------------------------------------------

const _assetSummaryShape = '''{
  "id": "expense-approval-policy",
  "path": "finance/policies/expense-approval",
  "title": "Expense Approval Policy",
  "content_type": "text/markdown",
  "authority_level": "policy",
  "scope": "org",
  "owner": "finance",
  "tags": ["expenses", "approval"],
  "status": "published",
  "updated_at": "2026-08-22T14:03:11.482Z"
}''';

const knowledgeGroup = EndpointGroup(
  title: 'Knowledge',
  blurb:
      'The corpus itself. Reads are open to any authenticated caller; writes '
      'need platform admin or admin of the team that owns the asset.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/assets',
      summary: 'List assets as summaries, without their content.',
      auth: ApiAuth.reader,
      notes:
          'Returns every match with no pagination, so filter with `prefix` on '
          'a large corpus rather than listing everything and filtering client '
          'side.',
      params: [
        ApiParam('prefix', 'string', where: 'query',
            description:
                'Taxonomy path prefix, e.g. `finance/policies`. Matches the '
                'path itself and everything beneath it.'),
        ApiParam('status', 'string', where: 'query',
            description: 'One of `draft`, `published`, `deprecated`.'),
      ],
      responseExample: '[$_assetSummaryShape]',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/assets/tree',
      summary: 'The taxonomy as a nested tree, for browsing.',
      auth: ApiAuth.reader,
      notes: 'Includes drafts — it is a navigation aid, not a published view.',
      responseExample: '''{
  "name": "",
  "assets": [],
  "children": [
    {"name": "finance", "assets": [], "children": [
      {"name": "policies", "assets": [ { "id": "expense-approval-policy", "…": "…" } ], "children": []}
    ]}
  ]
}''',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/assets/{asset_id}',
      summary: 'One asset with its full content and metadata.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('asset_id', 'string', where: 'path', required: true),
      ],
      errors: [ApiError(404, 'Asset not found: {asset_id}')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/assets/{asset_id}/context',
      summary: "An asset's place in the hierarchy.",
      auth: ApiAuth.reader,
      notes:
          'Ancestors follow the `parent` chain; siblings share a path prefix; '
          '`related` follows explicit cross-links.',
      params: [
        ApiParam('asset_id', 'string', where: 'path', required: true),
      ],
      responseExample: '''{
  "asset": { "…": "AssetSummary" },
  "ancestors": [], "children": [], "siblings": [], "related": [],
  "superseded_by": null
}''',
      errors: [ApiError(404, 'Asset not found: {asset_id}')],
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/assets',
      status: 201,
      summary: 'Create an asset.',
      auth: ApiAuth.assetWrite,
      notes:
          'The new asset is indexed for keyword search immediately, and '
          'embedded for semantic search on the same request when embeddings '
          'are enabled. An embedding failure is logged but does not fail the '
          'write — the document is simply keyword-only until the next reindex.',
      params: [
        ApiParam('id', 'string',
            required: true,
            description: 'Lowercase letters, digits, and hyphens.'),
        ApiParam('path', 'string',
            required: true, description: 'Taxonomy path; may not be empty.'),
        ApiParam('title', 'string', required: true),
        ApiParam('content', 'string', required: true),
        ApiParam('content_type', 'string',
            defaultValue: 'text/markdown',
            description: '`text/markdown` or `text/plain`.'),
        ApiParam('authority_level', 'string',
            defaultValue: 'note',
            description:
                '`policy`, `standard`, `guideline`, `how-to`, or `note`. '
                'Agents prefer higher authority when sources disagree.'),
        ApiParam('scope', 'string', defaultValue: 'org'),
        ApiParam('owner', 'string',
            defaultValue: '""',
            description: 'Team slug. Must name an existing team, or be empty.'),
        ApiParam('tags', 'string[]', defaultValue: '[]'),
        ApiParam('parent', 'string | null', defaultValue: 'null'),
        ApiParam('related', 'string[]', defaultValue: '[]'),
        ApiParam('supersedes', 'string | null', defaultValue: 'null'),
        ApiParam('superseded_by', 'string | null', defaultValue: 'null'),
        ApiParam('status', 'string',
            defaultValue: 'draft',
            description: 'Only `published` assets are returned by search.'),
      ],
      requestExample: '''{
  "id": "vendor-onboarding",
  "path": "finance/policies/vendor-onboarding",
  "title": "Vendor Onboarding Policy",
  "content": "# Vendor Onboarding\\n\\nAll new vendors…",
  "authority_level": "policy",
  "owner": "finance",
  "tags": ["vendors", "procurement"],
  "status": "published"
}''',
      errors: [
        ApiError(403, 'Requires admin role, or admin of the team that owns this asset'),
        ApiError(409, 'Asset already exists: {id}'),
        ApiError(422, "Unknown owning team: '{owner}'"),
      ],
    ),
    Endpoint(
      method: 'PUT',
      path: '/api/v1/assets/{asset_id}',
      summary: 'Update an asset. Send only the fields you are changing.',
      auth: ApiAuth.assetWrite,
      notes:
          'Moving an asset between teams is gated at both ends: you must be '
          'able to write it where it is and where it is going. Editing '
          '`content` re-embeds it; editing only metadata does not.',
      params: [
        ApiParam('asset_id', 'string', where: 'path', required: true),
        ApiParam('…', 'partial Asset',
            description:
                'Any field from POST except `id`. Omitted fields are left '
                'alone.'),
      ],
      requestExample: '{\n  "title": "Vendor Onboarding Policy (2026)",\n  "status": "published"\n}',
      errors: [
        ApiError(403, 'Requires admin role, or admin of the team that owns this asset'),
        ApiError(404, 'Asset not found: {asset_id}'),
        ApiError(422, 'Validation error, e.g. an empty path'),
      ],
    ),
    Endpoint(
      method: 'DELETE',
      path: '/api/v1/assets/{asset_id}',
      status: 204,
      summary: 'Delete an asset and drop it from both search indexes.',
      auth: ApiAuth.assetWrite,
      params: [
        ApiParam('asset_id', 'string', where: 'path', required: true),
      ],
      errors: [ApiError(404, 'Asset not found: {asset_id}')],
    ),
  ],
);

// ---- Search ----------------------------------------------------------------

const searchGroup = EndpointGroup(
  title: 'Search',
  blurb:
      'Hybrid retrieval: a BM25 keyword ranking and a vector-similarity '
      'ranking are computed independently and fused with reciprocal rank '
      'fusion. Exact terms and paraphrases both work, and a result both '
      'retrievers agree on outranks one that only a single retriever liked.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/search',
      summary: 'Search the corpus.',
      auth: ApiAuth.reader,
      notes:
          'Every hit reports which retriever found it and where it ranked, so '
          'you can tell a literal match from a semantic one. When embeddings '
          'are disabled or the encoder is unreachable, hybrid silently '
          'degrades to lexical rather than failing — check `retrieval` on the '
          'hits, or `/api/v1/search/status`, if that distinction matters.',
      params: [
        ApiParam('q', 'string',
            where: 'query', required: true, description: 'Minimum length 1.'),
        ApiParam('limit', 'int',
            where: 'query', defaultValue: '10', description: 'Between 1 and 50.'),
        ApiParam('include_drafts', 'bool',
            where: 'query',
            defaultValue: 'false',
            description: 'Drafts and deprecated assets are excluded by default.'),
        ApiParam('mode', 'string',
            where: 'query',
            defaultValue: 'hybrid',
            description:
                '`hybrid` fuses both retrievers. `lexical` is exact-term only '
                '— the right choice for an acronym, client name, or error '
                'code. `semantic` ignores vocabulary entirely and will always '
                'return something, so read `similarity` before trusting it.'),
      ],
      responseExample: '''[
  {
    "asset": { "…": "AssetSummary" },
    "score": 0.030675,
    "snippet": "…approvals above \$5,000 require the CFO…",
    "retrieval": "hybrid",
    "lexical_rank": 1,
    "semantic_rank": 3,
    "similarity": 0.7412
  }
]''',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/search/status',
      summary: 'What the retrieval stack is doing right now.',
      auth: ApiAuth.reader,
      notes:
          'Use `embedded` against `assets` to tell "hybrid, still warming" '
          'from "lexical, misconfigured". After a cold start the two converge '
          'as the backfill runs.',
      responseExample: '''{
  "mode": "hybrid",
  "model": "text-embedding-005",
  "dimensions": 768,
  "assets": 1247,
  "embedded": 1247,
  "ready": true,
  "lexical_weight": 1.0,
  "semantic_weight": 0.8,
  "min_similarity": 0.0
}''',
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/search/reindex',
      summary: 'Re-embed whatever has drifted from its stored vector.',
      auth: ApiAuth.admin,
      notes:
          'Synchronous and idempotent. Only assets whose embedded text or '
          'model changed are sent to the encoder, so calling it when nothing '
          'has changed costs nothing. Reach for it after changing '
          'EMBEDDING_MODEL, or to retry documents that failed to embed.',
      responseExample:
          '{\n  "status": "ok",\n  "embedded": 12,\n  "failed": 0,\n  "total": 1247\n}',
    ),
  ],
);

// ---- Identity and access ---------------------------------------------------

const usersGroup = EndpointGroup(
  title: 'Users',
  blurb:
      'People who sign in. Admins manage plain users; anything touching an '
      'admin or owner account needs owner. Nobody may delete themselves or '
      'change their own role.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/users/me',
      summary: 'The calling principal — the cheapest way to test a credential.',
      auth: ApiAuth.open,
      notes:
          'Any authenticated caller. `key_id` is set when a minted API key was '
          'used, and null for a Firebase token or the legacy admin key.',
      responseExample:
          '{\n  "uid": "…",\n  "email": "you@example.com",\n  "role": "owner",\n  "key_id": null\n}',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/users',
      summary: 'List people.',
      auth: ApiAuth.admin,
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/users',
      status: 201,
      summary: 'Create a person with a password.',
      auth: ApiAuth.admin,
      notes: 'Creating an admin or owner requires owner.',
      params: [
        ApiParam('email', 'string', required: true),
        ApiParam('password', 'string',
            required: true, description: 'Minimum 8 characters.'),
        ApiParam('display_name', 'string', defaultValue: '""'),
        ApiParam('role', 'string',
            defaultValue: 'user', description: '`owner`, `admin`, or `user`.'),
      ],
      errors: [
        ApiError(403, 'Requires owner role or higher'),
        ApiError(409, 'User already exists: {email}'),
      ],
    ),
    Endpoint(
      method: 'PUT',
      path: '/api/v1/users/{uid}/role',
      summary: "Change a person's platform role.",
      auth: ApiAuth.admin,
      notes:
          'Owner is required when the current role or the new role is admin '
          'or owner.',
      params: [
        ApiParam('uid', 'string', where: 'path', required: true),
        ApiParam('role', 'string',
            required: true, description: '`owner`, `admin`, or `user`.'),
      ],
      errors: [
        ApiError(400, 'You cannot change your own role'),
        ApiError(404, 'User not found: {uid}'),
      ],
    ),
    Endpoint(
      method: 'DELETE',
      path: '/api/v1/users/{uid}',
      status: 204,
      summary: 'Delete a person, revoke their keys, drop their memberships.',
      auth: ApiAuth.admin,
      params: [ApiParam('uid', 'string', where: 'path', required: true)],
      errors: [
        ApiError(400, 'You cannot delete yourself'),
        ApiError(404, 'User not found: {uid}'),
      ],
    ),
  ],
);

const identitiesGroup = EndpointGroup(
  title: 'Identities',
  blurb:
      'Everything that can hold a credential, in one list: people, plus the '
      'agents and services that never sign in. A minted key acts as its '
      'identity and inherits that identity\'s role on every request, so '
      'withdrawing authority takes effect without touching the key.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/identities',
      summary: 'People and machine clients, merged.',
      auth: ApiAuth.anyTeamAdmin,
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/identities',
      status: 201,
      summary: 'Register an agent or service. People are created via /users.',
      auth: ApiAuth.humanAdmin,
      notes:
          'Naming a `team` switches this to team self-service: you need admin '
          'of that team rather than platform admin, and the new identity joins '
          'it as a member. Creating an identity with `role: admin` needs owner '
          'either way. UIDs are generated as `agt_…` or `svc_…`.',
      params: [
        ApiParam('display_name', 'string',
            required: true, description: '1–120 characters.'),
        ApiParam('kind', 'string',
            defaultValue: 'agent', description: '`agent` or `service`.'),
        ApiParam('role', 'string',
            defaultValue: 'user',
            description: '`user` or `admin`. Machines may not be owner.'),
        ApiParam('description', 'string', defaultValue: '""'),
        ApiParam('owner', 'string',
            defaultValue: '""',
            description: 'Person accountable for this client.'),
        ApiParam('team', 'string',
            defaultValue: '""',
            description: 'Required for the team-admin self-service path.'),
      ],
      errors: [
        ApiError(403, 'API keys cannot manage identities; sign in as an admin.'),
        ApiError(404, 'Team not found: {team}'),
        ApiError(409, "An identity named '{display_name}' already exists"),
      ],
    ),
    Endpoint(
      method: 'PUT',
      path: '/api/v1/identities/{uid}/role',
      summary: "Change an identity's platform role.",
      auth: ApiAuth.humanAdmin,
      params: [
        ApiParam('uid', 'string', where: 'path', required: true),
        ApiParam('role', 'string', required: true),
      ],
      errors: [
        ApiError(400, 'You cannot change your own role'),
        ApiError(404, 'Identity not found: {uid}'),
      ],
    ),
    Endpoint(
      method: 'DELETE',
      path: '/api/v1/identities/{uid}',
      status: 204,
      summary: 'Delete an identity and revoke every key it holds.',
      auth: ApiAuth.humanAdmin,
      params: [ApiParam('uid', 'string', where: 'path', required: true)],
      errors: [
        ApiError(400, 'You cannot delete yourself'),
        ApiError(404, 'Identity not found: {uid}'),
      ],
    ),
  ],
);

const teamsGroup = EndpointGroup(
  title: 'Teams',
  blurb:
      'Teams own assets and form the second axis of authority: a plain user '
      'who administers a team may write that team\'s documents without any '
      'platform-wide rights. Non-members see a team without its roster.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/teams',
      summary: 'List teams.',
      auth: ApiAuth.reader,
      notes:
          'For a team you are not on and are not a platform admin of, '
          '`members` comes back empty and `my_team_role` null, while '
          '`member_count` still reports the real size.',
      responseExample: '''[{
  "slug": "finance", "name": "Finance", "description": "",
  "members": [{"identity_uid": "…", "label": "Ada", "kind": "person",
               "team_role": "admin", "global_role": "user", "missing": false}],
  "member_count": 1, "asset_count": 12,
  "my_team_role": "admin", "can_administer": true
}]''',
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/teams',
      status: 201,
      summary: 'Create a team.',
      auth: ApiAuth.humanAdmin,
      params: [
        ApiParam('slug', 'string',
            required: true,
            description: 'Lowercase letters, digits, hyphens. 1–60 chars.'),
        ApiParam('name', 'string',
            defaultValue: '""', description: 'Defaults to the slug.'),
        ApiParam('description', 'string', defaultValue: '""'),
      ],
      errors: [ApiError(409, "A team named '{slug}' already exists")],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/teams/{slug}',
      summary: 'One team.',
      auth: ApiAuth.reader,
      params: [ApiParam('slug', 'string', where: 'path', required: true)],
      errors: [ApiError(404, 'Team not found: {slug}')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/teams/{slug}/assets',
      summary: 'Assets this team owns.',
      auth: ApiAuth.reader,
      params: [ApiParam('slug', 'string', where: 'path', required: true)],
      errors: [ApiError(404, 'Team not found: {slug}')],
    ),
    Endpoint(
      method: 'PATCH',
      path: '/api/v1/teams/{slug}',
      summary: 'Rename a team or change its description.',
      auth: ApiAuth.humanTeamAdmin,
      notes:
          'A blank `name` is ignored; a blank `description` clears it. The '
          'slug is immutable — assets reference it.',
      params: [
        ApiParam('slug', 'string', where: 'path', required: true),
        ApiParam('name', 'string | null', defaultValue: 'null'),
        ApiParam('description', 'string | null', defaultValue: 'null'),
      ],
      errors: [ApiError(404, 'Team not found: {slug}')],
    ),
    Endpoint(
      method: 'DELETE',
      path: '/api/v1/teams/{slug}',
      status: 204,
      summary: 'Delete a team.',
      auth: ApiAuth.humanAdmin,
      notes:
          'Refused while the team still owns assets, so ownership is never '
          'lost by accident. Platform admin only — a team cannot delete '
          'itself.',
      params: [ApiParam('slug', 'string', where: 'path', required: true)],
      errors: [
        ApiError(409, "'{slug}' still owns N assets. Reassign them first."),
        ApiError(404, 'Team not found: {slug}'),
      ],
    ),
    Endpoint(
      method: 'PUT',
      path: '/api/v1/teams/{slug}/members/{identity_uid}',
      summary: 'Add a member, or change their team role.',
      auth: ApiAuth.humanTeamAdmin,
      notes:
          'Grants team authority only; platform roles are unchanged. Only a '
          'person may be a team admin — agents and services can only be '
          'members. Demoting the last admin is refused unless you are a '
          'platform admin.',
      params: [
        ApiParam('slug', 'string', where: 'path', required: true),
        ApiParam('identity_uid', 'string', where: 'path', required: true),
        ApiParam('team_role', 'string',
            defaultValue: 'member', description: '`admin` or `member`.'),
      ],
      errors: [
        ApiError(400, 'Only a person can administer a team'),
        ApiError(404, 'Identity not found: {identity_uid}'),
        ApiError(409, "'{slug}' would be left with no admin"),
      ],
    ),
    Endpoint(
      method: 'DELETE',
      path: '/api/v1/teams/{slug}/members/{identity_uid}',
      summary: 'Remove a member. Returns the team.',
      auth: ApiAuth.humanTeamAdmin,
      notes: 'Returns 200 with the updated team, not 204.',
      params: [
        ApiParam('slug', 'string', where: 'path', required: true),
        ApiParam('identity_uid', 'string', where: 'path', required: true),
      ],
      errors: [
        ApiError(404, "'{identity_uid}' is not on team '{slug}'"),
        ApiError(409, "'{slug}' would be left with no admin"),
      ],
    ),
  ],
);

const apiKeysGroup = EndpointGroup(
  title: 'API keys',
  blurb:
      'Minted keys look like `cumk_<id>_<secret>`. They are per-client, '
      'revocable, and attributed on every request. A key never manages keys, '
      'identities, or teams — those routes refuse it outright.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/api-keys',
      summary: 'List keys, newest first. Secrets are never returned.',
      auth: ApiAuth.keyManager,
      notes:
          'A team admin sees only keys held by their team\'s agents and '
          'services. Keys held by a person, and orphaned keys, are visible to '
          'platform admins only.',
      responseExample: '''[{
  "id": "k_9f2a…", "name": "Claude Desktop",
  "identity_uid": "agt_1c4b…", "identity_name": "Research Agent",
  "identity_kind": "agent", "role": "user",
  "display": "cumk_k_9f2a…_ab12…",
  "created_at": "…", "created_by": "you@example.com",
  "expires_at": null, "revoked_at": null, "revoked_by": null,
  "last_used_at": "…", "request_count": 412, "status": "active"
}]''',
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/api-keys',
      status: 201,
      summary: 'Mint a key. The only response that carries the secret.',
      auth: ApiAuth.keyManager,
      notes:
          'The token is shown once and is not recoverable. Minting for an '
          'identity whose role is admin requires owner. Owner identities '
          'cannot hold keys at all.',
      params: [
        ApiParam('name', 'string',
            required: true, description: 'What this credential is. 1–120 chars.'),
        ApiParam('identity_uid', 'string',
            required: true,
            description: 'Who the key acts as; supplies its role.'),
        ApiParam('expires_in_days', 'int | null',
            defaultValue: 'null',
            description: '1–3650. Null never expires.'),
      ],
      responseExample: '''{
  "key": { "…": "ApiKeyPublic" },
  "token": "cumk_k_9f2a…_<secret>",
  "mcp_config": {
    "servers": {
      "cumulus-knowledge": {
        "type": "http",
        "url": "<base>/mcp/",
        "headers": {"X-API-Key": "cumk_…"}
      }
    }
  }
}''',
      errors: [
        ApiError(400, "'{label}' is disabled and cannot hold a key"),
        ApiError(400, 'Owner identities cannot hold API keys'),
        ApiError(404, 'Identity not found: {identity_uid}'),
      ],
    ),
    Endpoint(
      method: 'DELETE',
      path: '/api/v1/api-keys/{key_id}',
      status: 204,
      summary: 'Revoke a key. Idempotent.',
      auth: ApiAuth.keyManager,
      notes: 'Revoking a key held by an admin identity requires owner.',
      params: [ApiParam('key_id', 'string', where: 'path', required: true)],
      errors: [ApiError(404, 'API key not found: {key_id}')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/api-keys/{key_id}/usage',
      summary: 'Daily request counts and endpoint breakdown.',
      auth: ApiAuth.keyManager,
      notes:
          'Counts are buffered in memory and flushed periodically, so the last '
          'few requests before an instance dies can be lost. Treat this as an '
          'activity signal, not a billing ledger. Endpoint keys are '
          'normalised, e.g. `/api/v1/assets/:id`.',
      params: [
        ApiParam('key_id', 'string', where: 'path', required: true),
        ApiParam('days', 'int',
            where: 'query',
            defaultValue: '30',
            description: '1–365, inclusive of today.'),
      ],
      responseExample: '''[{
  "key_id": "k_9f2a…", "day": "2026-08-22", "count": 118,
  "endpoints": {"/api/v1/search": 96, "/api/v1/assets/:id": 22},
  "last_used_at": "2026-08-22T19:41:02Z"
}]''',
    ),
  ],
);

// ---- Flows ---------------------------------------------------------

const _flowSummaryShape = '''{
  "id": "invoice-intake",
  "name": "Invoice intake",
  "status": "active",
  "description": "Extracts, validates and routes vendor invoices…",
  "team": "finance-ops",
  "tags": ["ap", "extraction"],
  "version": 14,
  "updated_at": "2026-08-12T09:14:02Z",
  "runs_30d": 1893,
  "cost_30d_usd": 411.5,
  "success_30d": 0.978,
  "tasks_open": 3,
  "minutes_saved_30d": 14801.0,
  "spark": [52, 58, 55, 61, 66, 63, 58, 71, 69, 74, 72, 78, 81, 86]
}''';

const flowsGroup = EndpointGroup(
  title: 'Flows',
  blurb:
      'The registry and observability plane for authored logic. There is one '
      'kind of authored logic — a Flow — carrying its declarative steps, run '
      'telemetry and ModelCall costs, audit trail, golden set, and test '
      'harness. (The former Workflow/DataFlow split is retired: what differs '
      'between flows is derived from their shape, such as whether the trigger '
      'is a window or a business event.) Note: in this prototype the run '
      'telemetry is synthesized deterministically per definition (no '
      'execution engine yet); definitions, golden sets and test runs are '
      'real stored state.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows',
      summary: 'The fleet, with 30-day rollups, sorted by model spend.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('q', 'string', where: 'query',
            description: 'Substring match over id, name, description, team, tags.'),
        ApiParam('status', 'string', where: 'query',
            description: 'One of `draft`, `active`, `paused`, `deprecated`.'),
        ApiParam('team', 'string', where: 'query',
            description: 'Exact owning-team slug.'),
      ],
      responseExample: '[$_flowSummaryShape]',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/stats',
      summary: 'Fleet-wide stats: runs, spend, ROI, tasks, a daily series.',
      auth: ApiAuth.reader,
      notes:
          'Spend rolls up from audited ModelCall records; `roi_usd` and '
          '`hours_returned` derive from each definition\'s governed ROI '
          'assumptions. `attention` lists definitions below the success bar '
          'or missing a golden set.',
      params: [
        ApiParam('days', 'int', where: 'query', defaultValue: '30',
            description: 'Window length, 1–365.'),
      ],
      responseExample: '''{
  "days": 30, "definitions": 6, "active": 5, "paused": 0, "draft": 1,
  "runs": 4770, "completed": 4601, "success_rate": 0.9646,
  "model_spend_usd": 1284.2, "tokens": 24810000, "model_calls": 7120,
  "hours_returned": 642.1, "roi_usd": 19263.0,
  "tasks_open": 23, "escalated": 19,
  "series": [{"date": "2026-07-26", "runs": 122, "cost_usd": 31.4, "tasks_raised": 9}],
  "attention": [{"flow_id": "contract-review", "reason": "success 91.2% over the last 30d"}]
}''',
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/flows',
      status: 201,
      summary: 'Register a new definition (created as a draft).',
      auth: ApiAuth.humanAdmin,
      params: [
        ApiParam('id', 'string', required: true,
            description: 'Slug: lowercase letters, digits, hyphens.'),
        ApiParam('name', 'string'),
        ApiParam('description', 'string'),
        ApiParam('team', 'string'),
        ApiParam('steps', 'list',
            description:
                'Declarative steps: {id, type, name, summary, config, next, '
                'branch_labels}. Types: trigger, modelcall, rule, task, '
                'action, event, transform.'),
        ApiParam('roi_minutes_per_run', 'number', defaultValue: '0'),
        ApiParam('model_budget_usd_per_run', 'number', defaultValue: '0.50'),
      ],
      errors: [ApiError(409, 'A flow with that id already exists.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/{id}',
      summary: 'One definition in full: steps, queues, budgets, rollups.',
      auth: ApiAuth.reader,
      errors: [ApiError(404, 'Flow not found.')],
    ),
    Endpoint(
      method: 'PATCH',
      path: '/api/v1/flows/{id}',
      summary: 'Lifecycle and governed-knowledge edits.',
      auth: ApiAuth.humanAdmin,
      notes:
          'Status changes (pause, activate, deprecate) and edits to the ROI '
          'assumptions or model budget — governed knowledge, so every change '
          'is logged with its actor.',
      params: [
        ApiParam('status', 'string',
            description: '`draft`, `active`, `paused`, or `deprecated`.'),
        ApiParam('description', 'string'),
        ApiParam('roi_minutes_per_run', 'number'),
        ApiParam('roi_hourly_rate_usd', 'number'),
        ApiParam('model_budget_usd_per_run', 'number'),
      ],
      errors: [ApiError(404, 'Flow not found.')],
    ),
    Endpoint(
      method: 'DELETE',
      path: '/api/v1/flows/{id}',
      status: 204,
      summary: 'Delete a non-active definition and its golden set.',
      auth: ApiAuth.humanAdmin,
      errors: [
        ApiError(404, 'Flow not found.'),
        ApiError(409,
            'The flow is active — pause or deprecate it first; '
            'definitions are map assets.'),
      ],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/{id}/usage',
      summary: 'Daily usage series plus totals: runs, cost, tokens, ROI.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('days', 'int', where: 'query', defaultValue: '30'),
      ],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/{id}/runs',
      summary: 'Recent runs, newest first.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('limit', 'int', where: 'query', defaultValue: '25'),
      ],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/{id}/runs/{run_id}',
      summary: 'One run\'s full journal: steps, ModelCalls, decisions.',
      auth: ApiAuth.reader,
      notes:
          'Every ModelCall records model, tokens and cost; Task entries name '
          'the decider; Action entries carry their ledger reference.',
      errors: [ApiError(404, 'Run not found (journals are kept 45 days).')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/{id}/audit',
      summary: 'The ledger view: decisions, gateway commits, resolutions, '
          'knowledge changes.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('limit', 'int', where: 'query', defaultValue: '40'),
      ],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/{id}/versions',
      summary: 'Published history plus any open draft, newest first.',
      auth: ApiAuth.reader,
      notes:
          'A version is a full snapshot of the declarative export, never a '
          'delta — a chain of deltas is exactly the opaque artifact the '
          'transparency bar rules out. At most one version is `draft`, and '
          'exactly one is `active`.',
      responseExample: '''[
  {"version": 15, "label": "v15-draft", "status": "draft",
   "steps": [], "notes": "Adds a deterministic duplicate guard…",
   "authored_by": "fde:m.suter", "created_at": "2026-08-23T09:00:00Z"},
  {"version": 14, "label": "v14", "status": "active", "steps": []}
]''',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/{id}/versions/{version}',
      summary: 'One version with its full step list.',
      auth: ApiAuth.reader,
      errors: [ApiError(404, 'Version not found.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/{id}/versions/{version}/diff',
      summary: 'Steps annotated added / removed / modified.',
      auth: ApiAuth.reader,
      notes:
          'Removed steps are returned last carrying the *baseline* step, so a '
          'canvas can render what a draft deletes rather than silently '
          'dropping it. `changed_fields` names which of type, name, summary, '
          'config, next, or branch_labels moved.',
      params: [
        ApiParam('against', 'int', where: 'query',
            description: 'Version to compare with. Defaults to the active one.'),
      ],
      responseExample: '''[
  {"step": {"id": "x0", "type": "transform", "name": "duplicate-check"},
   "change": "added", "changed_fields": []},
  {"step": {"id": "t1", "type": "trigger", "name": "InvoiceReceived"},
   "change": "modified", "changed_fields": ["next"]}
]''',
      errors: [ApiError(404, 'Version not found.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/{id}/recommendations',
      summary: 'Cost and performance insights from this definition\'s telemetry.',
      auth: ApiAuth.reader,
      notes:
          'Each card carries the evidence it was derived from. A model '
          'downgrade is proposed one rung at a time and only when the '
          'flow is already succeeding — spending less on something that '
          'is failing is not a saving. Cards with a null `proposed_change` '
          'are advisory and cannot be applied.',
      params: [
        ApiParam('days', 'int', where: 'query', defaultValue: '30',
            description: 'Telemetry window, 7–365.'),
      ],
      responseExample: '''[
  {
    "id": "invoice-intake:downgrade:m1",
    "kind": "model-downgrade",
    "title": "Downgrade opportunity: extract-invoice",
    "detail": "extract-invoice runs on claude-sonnet-5…",
    "evidence": "1,916 runs / 30d · \$442.49 attributed to this step · success 99.4%",
    "step_id": "m1", "step_name": "extract-invoice",
    "estimated_monthly_savings_usd": 268.41, "confidence": 0.95,
    "proposed_change": {"step_id": "m1", "field": "model",
                        "from": "claude-sonnet-5", "to": "claude-haiku-4-5"}
  }
]''',
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/flows/{id}/recommendations/{recommendation_id}/apply',
      summary: 'Stage a recommendation into the draft version.',
      auth: ApiAuth.humanAdmin,
      notes:
          'Never edits the live version: the change lands in a draft (opened '
          'from the active version if none exists) precisely so it has to '
          'survive the golden set before it becomes what runs. Applying edits '
          'governed knowledge, so it is logged with its actor.',
      errors: [
        ApiError(404, 'Recommendation not found — insights are derived from live telemetry.'),
        ApiError(409, 'The insight is advisory; there is no mechanical change to apply.'),
      ],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/{id}/golden-set',
      summary: 'The golden set, plus where its seeded half came from.',
      auth: ApiAuth.reader,
      notes:
          'FDEs author golden cases in code as well as in the console, so the '
          'set names the repository file it was seeded from rather than '
          'implying the UI is the only source.',
      responseExample: '''{
  "flow_id": "invoice-intake",
  "synced_from": "server/tests/golden/invoice-intake.json",
  "synced_at": "2026-08-25T07:12:00Z",
  "cases": [
    {"id": "gc-001", "name": "Standard invoice · single PO",
     "expected": "amount \$2,340 · automatic path",
     "source": "correction-set", "run_id": "", "payload": ""}
  ]
}''',
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/flows/{id}/golden-set',
      status: 201,
      summary: 'Add a golden case; returns the updated set.',
      auth: ApiAuth.humanAdmin,
      notes:
          'Passing `run_id` promotes a real execution: the server tags the '
          'case `promoted-from-run` and keeps the pointer back to that run, '
          'so a future failure on it is traceable to the execution it came '
          'from. This is the flywheel in miniature.',
      params: [
        ApiParam('name', 'string', required: true),
        ApiParam('expected', 'string',
            description: 'What a passing run must produce.'),
        ApiParam('input_ref', 'string',
            description: 'The pinned input document or fixture.'),
        ApiParam('source', 'string', defaultValue: 'human-authored',
            description:
                '`correction-set`, `exemplar`, `human-authored`, or '
                '`promoted-from-run`.'),
        ApiParam('run_id', 'string',
            description: 'The run being promoted, if any.'),
        ApiParam('payload', 'string',
            description:
                'A JSON fixture, stored verbatim — re-serializing it would '
                'quietly change what is under test.'),
      ],
    ),
    Endpoint(
      method: 'DELETE',
      path: '/api/v1/flows/{id}/golden-set/{case_id}',
      summary: 'Remove a golden case; returns the updated set.',
      auth: ApiAuth.humanAdmin,
      errors: [ApiError(404, 'Golden case not found.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/{id}/test-runs',
      summary: 'Past and in-flight test runs, newest first.',
      auth: ApiAuth.reader,
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/flows/{id}/test-runs',
      status: 202,
      summary: 'Launch a golden-set evaluation. Writes suppressed.',
      auth: ApiAuth.admin,
      notes:
          'Every case is graded against BOTH the candidate and the baseline, '
          'so the comparison is like-for-like rather than candidate-versus-'
          'remembered-numbers, and `regressions` counts cases the baseline '
          'passed and the candidate did not. Executes case-by-case in the '
          'background — poll the returned run for progress. Admin but not '
          'human-gated: launching an eval from CI with a minted key is a '
          'legitimate machine act. No Action reaches the gateway from a test.',
      params: [
        ApiParam('candidate_version', 'string',
            description: 'Defaults to the open draft.'),
        ApiParam('baseline_version', 'string',
            description: 'Defaults to the active version.'),
      ],
      responseExample: '''{
  "id": "test_9f21c4a0bd", "candidate_version": "v15-draft",
  "baseline_version": "v14", "status": "completed",
  "total_cases": 8, "passed": 7, "baseline_passed": 7,
  "cost_usd": 0.71, "baseline_cost_usd": 1.42,
  "cost_delta_usd": -0.71, "latency_delta_ms": -310,
  "regressions": 0, "writes_suppressed": true,
  "results": [{"case_id": "gc-001", "passed": true,
               "baseline_passed": true, "cost_usd": 0.09,
               "baseline_cost_usd": 0.18}]
}''',
      errors: [ApiError(409, 'No golden set: add cases first.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/flows/{id}/test-runs/{test_id}',
      summary: 'One test run with per-case verdicts.',
      auth: ApiAuth.reader,
      errors: [ApiError(404, 'Test run not found.')],
    ),
  ],
);

const healthGroup = EndpointGroup(
  title: 'Health',
  blurb: 'Unauthenticated on purpose — Cloud Run and uptime probes need it.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/health',
      summary: 'Liveness, asset count, and whether retrieval is warm.',
      auth: ApiAuth.open,
      notes:
          'Also served at `/healthz`, though Google\'s frontend intercepts '
          'that path on `*.run.app` — use `/health` publicly. Because this is '
          'unauthenticated it reports only a count and the retrieval mode, '
          'never document contents and never the model, dimensions, or fusion '
          'weights — those are on `/api/v1/search/status`, behind auth.',
      responseExample: '''{
  "status": "ok",
  "assets": 1247,
  "retrieval": {"mode": "hybrid", "ready": true}
}''',
    ),
  ],
);

const restGroups = <EndpointGroup>[
  knowledgeGroup,
  searchGroup,
  usersGroup,
  identitiesGroup,
  teamsGroup,
  flowsGroup,
  apiKeysGroup,
  healthGroup,
];

// ---- MCP -------------------------------------------------------------------

class McpTool {
  final String name;
  final String signature;
  final String description;

  const McpTool(this.name, this.signature, this.description);
}

const mcpTools = <McpTool>[
  McpTool(
    'search_docs',
    'search_docs(query: str, limit: int = 10, mode: str = "hybrid")',
    'Hybrid keyword-plus-semantic search over published documents. Each hit '
        'reports which retriever found it, so a semantic-only match can be '
        'treated more cautiously than one both retrievers agreed on. Set mode '
        'to "lexical" for exact terms, "semantic" to ignore vocabulary.',
  ),
  McpTool(
    'list_docs',
    'list_docs(prefix: str = "")',
    'List published documents, optionally under a taxonomy path prefix. '
        'Returns summaries without content.',
  ),
  McpTool(
    'get_doc',
    'get_doc(doc_id: str)',
    'Full content and metadata for one document. Warns in the response when '
        'the document has been superseded, naming its replacement.',
  ),
  McpTool(
    'get_context',
    'get_context(doc_id: str)',
    "A document's ancestors, children, siblings, and related documents — how "
        'to navigate the taxonomy without listing all of it.',
  ),
];

// ---- A2A -------------------------------------------------------------------

const a2aGroup = EndpointGroup(
  title: 'A2A',
  blurb:
      'A minimal Agent-to-Agent front door. Synthesis is retrieval-based '
      'rather than model-based: the agent answers with the best-matching '
      'documents and their citations.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/.well-known/agent-card.json',
      summary: 'Agent discovery metadata.',
      auth: ApiAuth.open,
      notes:
          'Open by design — it contains no secrets and advertises the accepted '
          'credential schemes. Also served at `/.well-known/agent.json`.',
    ),
    Endpoint(
      method: 'POST',
      path: '/a2a',
      summary: 'JSON-RPC 2.0. Only `message/send` is supported.',
      auth: ApiAuth.reader,
      notes:
          'Protocol errors come back as JSON-RPC error objects with HTTP 200, '
          'per the spec. Authentication failures are a real 401.',
      requestExample: '''{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "message/send",
  "params": {
    "message": {
      "kind": "message",
      "role": "user",
      "parts": [{"kind": "text", "text": "What is our expense approval policy?"}]
    }
  }
}''',
      responseExample: '''{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "kind": "message", "role": "agent",
    "messageId": "…", "contextId": "…",
    "parts": [{"kind": "text", "text": "Top 3 knowledge document(s)…"}]
  }
}''',
    ),
  ],
);
