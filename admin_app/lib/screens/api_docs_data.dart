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
        ApiParam('kind', 'string', where: 'query',
            description:
                'One of `document`, `schema-export`, `prompt`, `config`. '
                'The Library is `kind=document`; generated exports are '
                '`kind=schema-export`. Assets carry `origin` too — who put '
                'them here: authored, flywheel, derived.'),
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
      path: '/api/v1/refs/{ref}',
      summary: 'Resolve a read-set reference like knowledge:{id}@rev{n}.',
      auth: ApiAuth.reader,
      notes:
          'The seam between the two halves of the prototype: pinned read-sets, '
          'task refs, and run journals speak in these strings, and this '
          'endpoint dereferences them. Every asset carries a `revision` that '
          'bumps on write; a stale pin resolves to the current revision with '
          '`pinned_available: false` (retained history is a later step). '
          'Namespaces without a store yet (`graph:`, `vendor:`, `po:`) answer '
          '`resolved: false` honestly.',
      params: [
        ApiParam('ref', 'string', where: 'path', required: true,
            description: 'e.g. knowledge:expense-approval-policy@rev5'),
      ],
      responseExample: '''{
  "ref": "knowledge:expense-approval-policy@rev5",
  "namespace": "knowledge",
  "id": "expense-approval-policy",
  "resolved": true,
  "requested_revision": 5,
  "current_revision": 5,
  "pinned_available": true,
  "asset": {"id": "expense-approval-policy", "title": "Expense Approval Policy"}
}''',
      errors: [
        ApiError(404, 'Unknown knowledge ref.'),
        ApiError(422, 'Unparseable ref.'),
      ],
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
        ApiError(409,
            'kind=schema-export: generated from code and republished on '
            'every startup — edit the declaration in server/app/ instead'),
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
      errors: [
        ApiError(404, 'Asset not found: {asset_id}'),
        ApiError(409,
            'kind=schema-export: deleting only lasts until the next startup '
            'republishes it'),
      ],
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
        ApiParam('kind', 'string',
            where: 'query',
            description:
                'Restrict by asset kind, so governed-context retrieval and '
                'generated-export listing cannot cross.'),
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
                'branch_labels}. Types: trigger, modelcall, transform, rule, '
                'task, action, event, sync, check, resolve.'),
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
  ],
);

// ---- Reimbursements ----------------------------------------------------------

const reimbursementsGroup = EndpointGroup(
  title: 'Reimbursements',
  blurb:
      'The expense-reimbursement flow\'s live run and its output table. '
      'Submitting a receipt runs extraction → policy check → rule '
      'synchronously; an elevated request suspends into a Task and the '
      'human\'s decision resumes it. Every row carries its run journal.',
  endpoints: [
    Endpoint(
      method: 'POST',
      path: '/api/v1/reimbursements',
      status: 201,
      summary: 'Submit a receipt — one live run of the flow.',
      auth: ApiAuth('user+', keysRefused: true),
      notes:
          'With MODEL_BACKEND=vertex the image is read by a multimodal '
          'ModelCall; locally a deterministic stub honors hints like '
          '`total=64.20 category=meals vendor=corner-bistro` (and the word '
          '`blurry` drops confidence) so the pipeline is drivable without a '
          'credential. The rule\'s limit, confidence floor, and restricted '
          'categories are parsed from the expense-approval-policy asset at '
          'run time — edit the document and the flow behaves differently.',
      params: [
        ApiParam('documents', 'array', description:
            'One entry per artifact: `{content_base64, filename, note, hint}`. '
            'The note is a real input to the run — the projection reads a '
            'stated claim out of it ("only requesting \$75 of the \$110") and '
            'the journal quotes it.'),
        ApiParam('commentary', 'string', description:
            'The submitter\'s framing of the whole request. A stated purpose '
            'here is what keeps a line above the governed threshold from '
            'needing a reviewer.'),
        ApiParam('image_base64', 'string', description:
            'Single-document shorthand, still accepted.'),
        ApiParam('filename', 'string'),
        ApiParam('note', 'string',
            description: 'Single-document shorthand for the note above.'),
      ],
      responseExample: '''{
  "id": "reimb_18bcbface3",
  "employee": "dev",
  "vendor": "harbor-hotel",
  "total_usd": 480.0,
  "category": "lodging",
  "confidence": 0.9,
  "state": "pending",
  "verdict_reason": "\$480.00 exceeds the \$200.00 auto-approval limit",
  "policy_ref": "knowledge:expense-approval-policy",
  "task_id": "task_reimb_18bcbface3",
  "journal": [{"step_id": "t1", "name": "ReceiptSubmitted", "kind": "trigger",
               "summary": "receipt submitted by dev"}]
}''',
      errors: [
        ApiError(403, 'Minted API keys cannot submit reimbursements.'),
        ApiError(413,
            'A document exceeds this deployment\'s document-store limit.'),
      ],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/reimbursements',
      summary: 'The table, newest first.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('state', 'string', where: 'query',
            description: '`approved`, `pending`, or `denied`.'),
        ApiParam('mine', 'bool', where: 'query',
            description: 'Only the caller\'s own requests.'),
      ],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/reimbursements/stats',
      summary: 'Counts by state, approved spend, auto-approval share.',
      auth: ApiAuth.reader,
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/reimbursements/{id}',
      summary: 'One request with its full run journal.',
      auth: ApiAuth.reader,
      errors: [ApiError(404, 'Reimbursement not found.')],
    ),
  ],
);

// ---- Accounts payable ---------------------------------------------------------

const payablesGroup = EndpointGroup(
  title: 'Accounts payable',
  blurb:
      'The accounts-payable flow\'s live run and its table. One extraction '
      'pass reads the document; the resolver turns the printed vendor name '
      'into a canonical Vendor id against the master; the billed lines are '
      'matched against the purchase order and the receiving report, screened '
      'against the vendor\'s own history, and posted under governed policy.',
  endpoints: [
    Endpoint(
      method: 'POST',
      path: '/api/v1/payables',
      status: 201,
      summary: 'Submit an invoice — one live run of the flow.',
      auth: ApiAuth('user+', keysRefused: true),
      notes:
          'Locally the stub readers honor hints (`vendor=acme-industrial '
          'total=1840 invoice=ACME-INV-20881 po=PO-7741`); an unrecognized '
          'vendor is minted `onboarding` by the resolver. The auto-post limit '
          'and extraction-confidence floor parse from the '
          'invoice-posting-policy asset at run time.',
      params: [
        ApiParam('image_base64', 'string'),
        ApiParam('filename', 'string'),
        ApiParam('note', 'string', description: 'Hint text / reviewer note.'),
      ],
      errors: [ApiError(403, 'Minted API keys cannot submit invoices.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/payables',
      summary: 'The table, newest first.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('state', 'string', where: 'query',
            description: '`posted`, `pending`, or `denied`.'),
      ],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/payables/stats',
      summary: 'Counts by state and by extraction path, posted spend.',
      auth: ApiAuth.reader,
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/payables/{id}',
      summary: 'One invoice with candidates, conflicts, and its run journal.',
      auth: ApiAuth.reader,
      errors: [ApiError(404, 'Invoice not found.')],
    ),
  ],
);

// ---- Employee Hub ------------------------------------------------------------

const employeeGroup = EndpointGroup(
  title: 'Employee Hub',
  blurb:
      'Personal surface: profile, PTO, and the HR agent. PTO follows the flow '
      'pattern in miniature — requests over the governed auto-approval window '
      '(parsed from the pto-policy asset) raise a Task to the hr queue, and '
      'the decision resumes the request.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/employee/me',
      summary: 'The caller\'s profile: PTO balance, accrual, benefits.',
      auth: ApiAuth.reader,
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/employee/pto',
      summary: 'The caller\'s PTO requests, newest first.',
      auth: ApiAuth.reader,
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/employee/pto',
      status: 201,
      summary: 'Request time off — auto-approves within the governed window.',
      auth: ApiAuth('user+', keysRefused: true),
      params: [
        ApiParam('start_date', 'string', required: true,
            description: 'ISO date.'),
        ApiParam('end_date', 'string', required: true, description: 'ISO date.'),
        ApiParam('days', 'number', required: true,
            description: 'Working days requested.'),
        ApiParam('note', 'string'),
      ],
      errors: [ApiError(403, 'Minted API keys cannot request PTO.')],
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/employee/hr-chat',
      summary: 'Ask the HR Agent — grounded on people/ knowledge, cited.',
      auth: ApiAuth.reader,
      notes:
          'With MODEL_BACKEND=vertex the agent plans with tools over the '
          'knowledge layer; locally it degrades to retrieval plus the '
          'caller\'s structured facts, honestly labelled '
          '`"backend": "retrieval"`. Citations name real asset ids.',
      params: [
        ApiParam('message', 'string', required: true),
      ],
      responseExample: '''{
  "reply": "PTO balance: 17.5 days (accruing 1.67/month)…",
  "citations": [{"id": "pto-policy", "title": "Paid Time Off Policy"}],
  "backend": "retrieval"
}''',
    ),
  ],
);

// ---- Calendars ---------------------------------------------------------------

const _agendaShape = '''{
  "email": "dana.prentiss@naviant.com",
  "name": "Dana Prentiss",
  "title": "Engagement Director",
  "calendar": {
    "id": "calendar_9f71ad7259af",
    "ref": "graph:Calendar/calendar_9f71ad7259af@rev2",
    "address": "dana.prentiss@naviant.com",
    "provider": "microsoft-365",
    "timezone": "America/New_York",
    "events": 46
  },
  "start_date": "2026-08-24",
  "days": 7,
  "events": [
    {
      "id": "calendarevent_171d00c49333",
      "ref": "graph:CalendarEvent/calendarevent_171d00c49333@rev2",
      "title": "Acme Phase 1 — daily standup",
      "kind": "meeting",
      "starts_at": "2026-08-28T08:45:00",
      "ends_at": "2026-08-28T09:00:00",
      "all_day": false,
      "location": "Teams",
      "organizer_email": "dana.prentiss@naviant.com",
      "agenda_ref": "engagement:acme-phase-1",
      "attendees": [
        {"email": "dana.prentiss@naviant.com", "name": "Dana Prentiss",
         "required": true, "response": "accepted", "is_owner": true}
      ]
    }
  ],
  "persona_note": ""
}''';

const calendarGroup = EndpointGroup(
  title: 'Calendars',
  blurb:
      'Schedules, mirrored from the calendar provider into the graph: a '
      'Calendar per person, and CalendarEvents as parts of it — one row per '
      'attendee\'s copy, which is what the provider actually stores. Read-only '
      'on purpose: the provider is the system of record, so a block created '
      'here would be a fact the mirror could not defend and the next sync '
      'would delete it. Times are wall-clock local in the calendar\'s own '
      'zone, never UTC instants.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/calendar/me',
      summary: 'The caller\'s window of time, current week by default.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('start', 'string',
            description: 'ISO date the window starts on. Defaults to Monday '
                'of the current week.'),
        ApiParam('days', 'number', description: '1–31; defaults to 7.'),
      ],
      notes:
          'A principal with no Employee record — every local-dev session — '
          'gets a demo persona\'s calendar, and `persona_note` says whose it '
          'is rather than substituting silently.',
      responseExample: _agendaShape,
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/calendar/calendars',
      summary: 'Every mirrored calendar, with its owner, count and next block.',
      auth: ApiAuth.reader,
    ),
  ],
);

// ---- Tasks -------------------------------------------------------------------

const _taskShape = '''{
  "id": "task_mdm_0071",
  "kind": "match_review",
  "title": "Low-confidence vendor match: “Acme Ind. LLC”",
  "queue": "data-steward",
  "priority": "normal",
  "status": "open",
  "flow_id": "vendor-master-sync",
  "case_ref": "erp-vend-88410",
  "refs": ["vendor:acme-industrial@rev12"],
  "evidence": [{"label": "Method", "value": "fuzzy_name · confidence 0.71"}],
  "proposal": {
    "action": "confirm-identity-link",
    "target": "graph",
    "summary": "Link erp-vend-88410 → vendor:acme-industrial (reversible)"
  },
  "resumes": "vendor-master-sync window closes with the match recorded",
  "created_at_iso": "2026-08-25T09:14:02.000Z",
  "decision": null
}''';

const tasksGroup = EndpointGroup(
  title: 'Tasks',
  blurb:
      'The Task Queue — the unit of human judgment, routed through Queues. '
      'An agent proposes, a person decides, the system resumes. Reads are '
      'user+; deciding is human-only, so minted keys are refused: a key '
      'deciding a task would be an agent approving its own proposal.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/tasks',
      summary:
          'Every task, open before done, high priority first among open, '
          'oldest first within a band.',
      auth: ApiAuth.reader,
      params: [
        ApiParam(
          'status',
          'string',
          where: 'query',
          description: '`open` or `done`.',
        ),
        ApiParam(
          'queue',
          'string',
          where: 'query',
          description: 'Filter to one queue, e.g. `data-steward`.',
        ),
        ApiParam(
          'kind',
          'string',
          where: 'query',
          description:
              '`decision`, `match_review`, `curation`, `eval_review`, '
              'or `exception`.',
        ),
      ],
      responseExample: '[$_taskShape]',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/tasks/stats',
      summary: 'Open counts for the top-bar badge; only open tasks count.',
      auth: ApiAuth.reader,
      responseExample: '''{
  "open": 9,
  "high_priority": 3,
  "by_queue": {"ap-clerk": 2, "data-steward": 2, "knowledge-curation": 2},
  "by_kind": {"decision": 3, "match_review": 2, "curation": 2}
}''',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/tasks/{id}',
      summary: 'One task with its evidence, refs, and proposed action.',
      auth: ApiAuth.reader,
      responseExample: _taskShape,
      errors: [ApiError(404, 'Task not found.')],
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/tasks/{id}/decide',
      summary: 'Record a human judgment; the task is closed and attributed.',
      auth: ApiAuth('user+', keysRefused: true),
      notes:
          'Once-only: a second decision returns 409 so the second decider '
          'learns the judgment already happened. The decision is durable, '
          'attributed state — the prototype\'s stand-in for the curation '
          'ledger entry every human decision must land as.',
      params: [
        ApiParam(
          'verdict',
          'string',
          required: true,
          description: '`approved`, `rejected`, or `corrected`.',
        ),
        ApiParam(
          'note',
          'string',
          description: 'Recorded alongside the verdict.',
        ),
        ApiParam(
          'correction',
          'string',
          description: 'What the human supplied in place of the proposal.',
        ),
      ],
      requestExample:
          '{"verdict": "approved", "note": "Matches the PO; posting."}',
      errors: [
        ApiError(403, 'Minted API keys cannot decide tasks.'),
        ApiError(404, 'Task not found.'),
        ApiError(409, 'Task already decided.'),
      ],
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

// ---- Runs ---------------------------------------------------------------------

const runsGroup = EndpointGroup(
  title: 'Flow runs',
  blurb:
      'The durable journal. A run is stored state whose completed steps *are* '
      'the run: re-entering the body replays them instead of re-executing, '
      'which is what lets a flow suspend for a person, be corrected, re-run '
      'its checks and drive to finished — across a restart. Reads only: runs '
      'are started by triggers and advanced by Task decisions, never by a '
      'POST from outside.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/runs',
      summary: 'Runs, newest first — the list a workspace shows.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('flow_id', 'string', where: 'query'),
        ApiParam('status', 'string', where: 'query',
            description: '`running`, `suspended`, `succeeded`, `failed`.'),
        ApiParam('limit', 'int', where: 'query', defaultValue: '50'),
      ],
      responseExample: '''[{
  "id": "run_er_seed11",
  "flow_id": "expense-reimbursement",
  "status": "suspended",
  "case_ref": "run_er_seed11",
  "steps": 7,
  "model_calls": 1,
  "open_task_id": "task_run_er_seed11_k1",
  "suspended_at_step": "k1",
  "resume_count": 0,
  "refs": ["knowledge:expense-approval-policy@rev1"]
}]''',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/runs/stats',
      summary:
          'Counts by status, audited ModelCall spend, and how often a person '
          'was needed.',
      auth: ApiAuth.reader,
      params: [ApiParam('flow_id', 'string', where: 'query')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/runs/{id}',
      summary:
          'One run: every step, its output, the read-set it used, and the '
          'decision that released each gate.',
      auth: ApiAuth.reader,
      errors: [ApiError(404, 'Run not found.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/runs/{id}/steps',
      summary: 'Just the steps, for a trace view.',
      auth: ApiAuth.reader,
      errors: [ApiError(404, 'Run not found.')],
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/runs/{id}/resume',
      summary:
          'Drive a run whose gate was decided but which was interrupted before '
          'it could resume.',
      auth: ApiAuth.admin,
      notes:
          'Idempotent by construction: completed steps replay, so a run that '
          'is already finished — or still genuinely waiting — comes back '
          'unchanged. This exists for the narrow case where the process died '
          'between the decision write and the resume.',
      errors: [ApiError(404, 'Run not found.')],
    ),
  ],
);

// ---- The entity layer ---------------------------------------------------------

const graphGroup = EndpointGroup(
  title: 'Entity layer',
  blurb:
      'Canonical Objects, typed Links, and retained Claims. An Object\'s id is '
      'derived from a declared natural key, so the same real-world thing seen '
      'twice resolves to one entity; its field values are the *resolution* of '
      'claims from every source, under the ontology\'s precedence order, with '
      'the losing claims kept. Read-only over HTTP on purpose: the graph is '
      'written by Flows, through Actions.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/graph/ontology',
      summary:
          'The declared ontology, with live instance counts beside each type.',
      auth: ApiAuth.reader,
      notes:
          'Declaration and reality in one payload — a declared type with zero '
          'instances is a promise nothing has kept yet. Links carry a '
          '`nature` (association | composition | identity); each type carries '
          'its derived place in the hierarchy (`owner_type`, `part_types`) '
          'and its filter `tags` (freeform facets, unregistered; a part '
          'inherits its owner\'s). Fields may be `ref:<Type>` — an '
          'observation awaiting promotion to a Link. Object ids are minted; '
          'the deterministic (type, key) hash is a lookup alias, so a key '
          'change (an employee\'s email) re-keys without breaking one '
          'reference. Generated from `server/app/ontology.py`, which is also '
          'exported to the corpus as the `enterprise-ontology` document.',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/graph/stats',
      summary:
          'Object and link counts by type, and how many objects have sources '
          'that disagree.',
      auth: ApiAuth.reader,
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/graph/search',
      summary:
          'Find entities by any handle a person holds: label, natural key, '
          'id — or an external system\'s id.',
      auth: ApiAuth.reader,
      notes:
          'When the match is a SourceRecord, the canonical entity it is '
          '`identified_as` is surfaced too, with `via` naming the row that '
          'carried the id — typing "V-10041" finds the Vendor it means here. '
          'That is the MDM mapping made searchable.',
      params: [
        ApiParam('q', 'string', where: 'query', required: true),
        ApiParam('limit', 'int', where: 'query', defaultValue: '20'),
      ],
      responseExample: '''[{
  "entity": {"id": "vendor_2f39…", "type": "Vendor",
             "label": "Acme Industrial Inc.", "…": "…"},
  "match": "external id",
  "via": "erp-vendors:V-10041"
}]''',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/graph/links',
      summary:
          'Links as rows — the edge table for one Link type, with identity '
          'metadata per pair.',
      auth: ApiAuth.reader,
      notes:
          'Where an identity Link becomes auditable: confidence, method, '
          'liveness, reversal reason, and the run/step that wrote it — per '
          'pair, instead of a dashed line on a diagram.',
      params: [
        ApiParam('type', 'string', where: 'query',
            description: '`identified_as`, `issued`, …'),
        ApiParam('include_reversed', 'bool', where: 'query',
            defaultValue: 'false'),
        ApiParam('limit', 'int', where: 'query', defaultValue: '200'),
      ],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/graph/objects',
      summary: 'Browse objects of one type.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('type', 'string', where: 'query',
            description: '`Vendor`, `Invoice`, `ExpenseLine`, …'),
        ApiParam('q', 'string', where: 'query',
            description: 'Substring over the label and natural key.'),
        ApiParam('limit', 'int', where: 'query', defaultValue: '100'),
      ],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/graph/objects/{id}',
      summary:
          'One object: resolved fields, every claim, the fields where sources '
          'disagree, and its edges.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('link_type', 'string', where: 'query',
            description: 'Restrict the edges to one type.'),
      ],
      errors: [ApiError(404, 'Object not found.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/graph/vendors',
      summary:
          'The vendor master: one row per canonical vendor, carrying every '
          'source system\'s id for it.',
      auth: ApiAuth.reader,
      notes:
          'The column that matters is `source_records`. When a vendor was a '
          'slug on a row, the same company in two systems was two vendors and '
          'merging them merged nothing.',
      responseExample: '''[{
  "id": "vendor_5c1b0f9a3e21",
  "label": "Acme Industrial Inc.",
  "status": "active",
  "tax_id": "94-2711883",
  "source_records": [
    {"source": "erp-vendors", "external_id": "V-10041", "confidence": 1.0,
     "method": "exact", "live": true},
    {"source": "procurement-coupa", "external_id": "C-5501",
     "confidence": 1.0, "method": "exact", "live": true}
  ],
  "invoices": 3,
  "conflict_fields": ["payment_terms"]
}]''',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/graph/employees/{email}/expenses',
      summary:
          'How much this employee has submitted — a traversal, not a search.',
      auth: ApiAuth.reader,
      notes:
          'Two hops and a sum: `Employee --submitted--> ExpenseRequest '
          '--has_expense_line--> ExpenseLine`. No document contains this '
          'number, so no amount of retrieval would find it; the answer carries '
          'the path that produced it. An unknown employee is an empty rollup, '
          'not a 404.',
      params: [
        ApiParam('days', 'int', where: 'query', defaultValue: '180'),
      ],
    ),
  ],
);

// ---- Collections, documents, sources ------------------------------------------

// ---- Mappings ----------------------------------------------------------------

const mappingsGroup = EndpointGroup(
  title: 'Mappings',
  blurb:
      'The declared seams between external systems and the graph — the '
      'doctrine\'s Mapping primitive, one contract per feed: what arrives, '
      'what it binds to via which keys, under which match policy and '
      'precedence, and whether rows are **reified** into the graph (so a '
      'steward can adjudicate them) or **column-bound** and resolved lazily '
      '(so a high-volume feed never mints objects per row). Declared in '
      '`server/app/mappings.py` and exported to the corpus like the ontology; '
      'read-only over HTTP for the same reason the graph is.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/mappings',
      summary: 'Every declared contract, plus registry conformance.',
      auth: ApiAuth.reader,
      notes:
          '`problems` lists structural failures — a mapping naming a type, '
          'Collection or precedence source that does not exist. Empty means '
          'the registry conforms, checked the same way the TBox invariant '
          'sweep checks the graph.',
      responseExample: '''{
  "mappings": [{
    "id": "erp-vendors",
    "source": "erp-vendors",
    "strategy": "reify",
    "lands_as": "SourceRecord",
    "resolves_to": ["Vendor"],
    "identity": [{"column": "name", "object_type": "Vendor",
                  "key": "normalized legal name"}],
    "precedence_source": "erp",
    "match_policy": "knowledge:entity-resolution-policy",
    "custody": "copy",
    "collection": "source-landing",
    "escalation": "per-row task"
  }],
  "problems": []
}''',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/mappings/{mapping_id}',
      summary: 'One contract.',
      auth: ApiAuth.reader,
      errors: [ApiError(404, 'No such mapping.')],
    ),
  ],
);

const dataGroup = EndpointGroup(
  title: 'Data governance',
  blurb:
      'Collections are the unit governance hangs off: retention, custody and '
      'authoritativeness are properties of a *set*, not of a row, so they are '
      'declared in `server/app/data_collections.py`, exported to the corpus, '
      'and stamped on every row at write time. Documents are the artifacts a '
      'decision was made from. Sources are the inbound feeds, assembled from '
      'the sync runs themselves.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/collections',
      summary:
          'The registry, with live counts. A count the server cannot get '
          'honestly is null, not zero.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('role', 'string', where: 'query',
            description: '`knowledge` or `data`.'),
      ],
      responseExample: '''[{
  "id": "audit-documents",
  "name": "Audit documents",
  "role": "data",
  "kind": "documents",
  "origin": "flow-written",
  "custody": "copy",
  "retention": "P2Y",
  "authoritative": true,
  "container": "documents",
  "rows": 12,
  "rows_caption": "artifacts"
}]''',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/collections/{id}',
      summary: 'One Collection.',
      auth: ApiAuth.reader,
      errors: [ApiError(404, 'Collection not found.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/documents',
      summary: 'Stored artifacts, without their bytes.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('kind', 'string', where: 'query',
            description: '`receipt`, `invoice`, …'),
        ApiParam('run_id', 'string', where: 'query'),
      ],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/documents/expiring',
      summary: 'Artifacts past their Collection\'s retention horizon.',
      auth: ApiAuth.reader,
      notes:
          'Reported, not swept. The policy is declared and the date computed; '
          'the purge pass is not built, and saying so is better than implying '
          'otherwise.',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/documents/{id}',
      summary:
          'One artifact with the regions an extraction read out of it.',
      auth: ApiAuth.reader,
      params: [
        ApiParam('include_bytes', 'bool', where: 'query', defaultValue: 'false',
            description: 'Bytes are opt-in; the regions are the useful part.'),
      ],
      errors: [ApiError(404, 'Document not found.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/documents/{id}/content',
      summary: 'The artifact itself, for a review screen to draw boxes over.',
      auth: ApiAuth.reader,
      errors: [
        ApiError(404,
            'No stored bytes — the run was hint-driven and carried no file.'),
      ],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/sources',
      summary: 'One row per inbound feed and kind.',
      auth: ApiAuth.reader,
      notes:
          'Assembled from the sync runs, not from a second bookkeeping store — '
          'inventing one would guarantee the two disagree. A feed that has '
          'never run does not appear.',
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/sources/deliver',
      status: 202,
      summary: 'A push from an external system: vendors, POs, or receipts.',
      auth: ApiAuth.admin,
      notes:
          'The one write here that an API key *should* make — a feed is a '
          'machine act, the opposite of a Task decision. Pass '
          '`idempotency_key` and a retried delivery re-enters the same run, '
          'replays its steps and changes nothing, which is what makes an '
          'at-least-once transport safe to point at this. Rows land exactly as '
          'delivered before resolution, so changing the matching rules '
          're-resolves without re-fetching; an ambiguous row raises a steward '
          'Task and the feed keeps going.',
      params: [
        ApiParam('source', 'string', required: true,
            description: 'e.g. `erp-vendors`. Maps to a precedence bucket.'),
        ApiParam('watermark', 'string',
            description: 'The source\'s own cursor, echoed onto the run.'),
        ApiParam('idempotency_key', 'string'),
        ApiParam('vendors', 'array'),
        ApiParam('purchase_orders', 'array'),
        ApiParam('receiving_reports', 'array'),
      ],
      requestExample: '''{
  "source": "erp-vendors",
  "watermark": "2026-08-01T00:00:00Z",
  "idempotency_key": "erp-vendors-001",
  "vendors": [
    {"external_id": "V-10041", "name": "Acme Industrial Inc.",
     "tax_id": "94-2711883", "status": "active", "payment_terms": "NET30"}
  ]
}''',
      responseExample: '''{
  "source": "erp-vendors",
  "runs": [{"flow_id": "vendor-sync", "run_id": "run_sync_1f2c3d4e5a",
            "status": "succeeded", "replayed": false,
            "landed": 1, "unchanged": 0, "linked": 0, "created": 1,
            "awaiting_steward": 0}]
}''',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/vendor-profiles',
      summary:
          'The derived anomaly baseline: how each vendor\'s invoices normally '
          'look.',
      auth: ApiAuth.reader,
      notes:
          'Derived data, and nothing here is a source of truth — delete the '
          'collection and the profile Flow rebuilds it from the invoice graph. '
          'The *thresholds* that judge these numbers are governed knowledge in '
          'the invoice-posting-policy document, which is the reusable split: '
          'derived statistics are data, the thresholds that judge them are '
          'knowledge.',
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/vendor-profiles/refresh',
      summary: 'Run the windowed profile Flow now.',
      auth: ApiAuth.admin,
      params: [
        ApiParam('window_days', 'int', where: 'query', defaultValue: '365'),
      ],
    ),
  ],
);

// ---- Evals --------------------------------------------------------------------

const evalsGroup = EndpointGroup(
  title: 'Evals',
  blurb:
      'A per-flow eval spec is an **overlay on the enterprise model**: the '
      'ontology says what an Invoice is; the spec says which parts of it a '
      'given flow establishes, how tightly to compare each field, which edges '
      'matter, and — through checkpoints — at which point in a run to look. '
      'Cases supply only the values, so the match policy is decided once. '
      'Every case runs in its own isolated in-memory world with the corpus '
      'copied in: object ids derive from natural keys, so an unisolated run '
      'would append claims to real entities and feed eval invoices into the '
      'anomaly baseline real invoices are judged against.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/evals',
      summary: 'The specs, with how much of the model each one grades.',
      auth: ApiAuth.reader,
      params: [ApiParam('flow_id', 'string', where: 'query')],
      responseExample: '''[{
  "id": "accounts-payable-core",
  "flow_id": "accounts-payable",
  "version": 1,
  "checkpoints": 3,
  "cases": 5,
  "graded_fields": 18,
  "ignored_fields": 8,
  "graded_links": 6
}]''',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/evals/{spec_id}',
      summary:
          'The whole contract: checkpoints, per-field match rules, graded '
          'edges, trajectory rules.',
      auth: ApiAuth.reader,
      notes:
          'Match rules are `exact` (numbers compare as numbers), `approx` with '
          'a tolerance, `fuzzy` with a similarity floor for values a model '
          'reads off a page, `one_of`, `present`, `absent`, and `ignore`. '
          '`ignore` is used deliberately — a due date is the vendor\'s payment '
          'terms, so grading it would be grading the wrong system. Generated '
          'from `server/app/eval_specs.py`, which is also exported to the '
          'corpus as `flow-eval-specs`.',
      errors: [ApiError(404, 'No such spec.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/evals/{spec_id}/cases',
      summary: 'The cases, with their fixtures and scripted gate decisions.',
      auth: ApiAuth.reader,
      notes:
          'A fixture is expressed as the deliveries the real feeds would have '
          'made, so the precondition is established the way production '
          'established it — and in natural keys, never ids, so a case promoted '
          'from a real run needs no rewriting.',
      errors: [ApiError(404, 'No such spec.')],
    ),
    Endpoint(
      method: 'POST',
      path: '/api/v1/evals/{spec_id}/run',
      summary: 'Run the cases and grade them against the spec.',
      auth: ApiAuth.admin,
      notes:
          'Admin, but **not** human-gated: a CI key launching an eval is the '
          'point of having keys, and it is the one write in this API where a '
          'credential doing the work is correct. Each case gets a fresh world; '
          'the TBox invariant sweep runs per case at no authoring cost, so a '
          'structural bug fails the case even when no expectation named it.',
      params: [
        ApiParam('case_id', 'string', where: 'query',
            description: 'Run one case instead of the set.'),
      ],
      responseExample: '''{
  "spec_id": "accounts-payable-core",
  "passed": 5,
  "failed": 0,
  "assertions": 74,
  "failed_assertions": 0,
  "invariant_violations": 0,
  "result": {"cases": [{"case_id": "ap-known-vendor-clean", "passed": true,
                        "run_status": "succeeded", "assertions": []}]}
}''',
      errors: [ApiError(404, 'No such spec.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/evals/{spec_id}/result',
      summary: 'The most recent result in this server process.',
      auth: ApiAuth.reader,
      notes:
          'Not persisted, on purpose: a pass rate only means something against '
          'the knowledge revisions it read, and each result records them. '
          'Storing numbers nobody can compare would be worse than storing '
          'none.',
      errors: [ApiError(404, 'Nothing has been run in this process yet.')],
    ),
  ],
);

// ---- Authored ABoxes ---------------------------------------------------------

const worldsGroup = EndpointGroup(
  title: 'Authored ABoxes',
  blurb:
      'The ontology is a TBox and the console draws it. A good deal of *ABox* '
      'is authored too — every eval fixture, and the demo seed\'s feed '
      'deliveries — and was only ever observable through its effects. These '
      'endpoints make those fragments viewable, by **materializing** them: '
      'running each one into a throwaway in-memory world and reading the graph '
      'back. That is not the same as expanding the payload, and the difference '
      'is the value — what comes back includes the resolution the sync flow '
      'performed (which vendor it matched, at what score, by what method) and '
      'the Tasks it escalated. Nothing real is touched, which is why looking is '
      'a plain read.',
  endpoints: [
    Endpoint(
      method: 'GET',
      path: '/api/v1/worlds',
      summary: 'The ABox fragments declared in code, with where they live.',
      auth: ApiAuth.reader,
      responseExample: '''[{
  "id": "seed:demo-world",
  "name": "Demo world",
  "kind": "seed-world",
  "declared_in": "server/app/payables.py",
  "owner": "seed_payables_if_empty",
  "deliveries": 6,
  "direct_objects": 0
}]''',
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/worlds/{world_id}',
      summary:
          'Materialize one fragment: its instances, edges, claims, and what '
          'establishing it did.',
      auth: ApiAuth.reader,
      notes:
          'Every object carries both halves of the question a natural key '
          'raises — the key as written and the id it resolves to — and every '
          'claim says whether it is the one that won under precedence. The '
          'TBox invariant sweep runs over the result, so a fragment that would '
          'violate the ontology says so before any case uses it.',
      errors: [ApiError(404, 'No such world.')],
    ),
    Endpoint(
      method: 'GET',
      path: '/api/v1/graph/instances',
      summary:
          'The live ABox, in the same shape an authored fragment comes back in.',
      auth: ApiAuth.reader,
      notes:
          'One renderer for both, so the running graph and a declared fixture '
          'are peers in the console rather than two views that merely resemble '
          'each other. A `type` filter pulls in that type\'s one-hop '
          'neighbours too: a screen of boxes with no edges answers less than a '
          'smaller one that shows how they join.',
      params: [
        ApiParam('type', 'string', where: 'query'),
        ApiParam('limit', 'int', where: 'query', defaultValue: '400'),
      ],
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
  tasksGroup,
  reimbursementsGroup,
  payablesGroup,
  employeeGroup,
  calendarGroup,
  runsGroup,
  graphGroup,
  worldsGroup,
  mappingsGroup,
  evalsGroup,
  dataGroup,
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
