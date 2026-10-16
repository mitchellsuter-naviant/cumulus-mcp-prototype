import 'package:flutter/material.dart';

import '../models/api_key.dart';
import '../models/identity.dart';
import '../models/team.dart';
import '../services/api_client.dart';
import '../services/auth_service.dart';
import '../theme/cumulus_theme.dart';
import '../theme/domain_tones.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/skeleton.dart';
import '../widgets/ui_kit.dart';
import 'identity_detail_screen.dart';
import 'team_detail_screen.dart';

const _roles = ['owner', 'admin', 'user'];

const _roleBlurbs = {
  'owner':
      'Full control, including other owners and admin-scoped keys. '
      'Cannot hold an API key.',
  'admin': 'Authors and publishes knowledge; manages plain users.',
  'user': 'Reads the governed corpus.',
};

/// Everything that can hold a credential, in one place: people who sign in,
/// and the agents and service clients that only ever present an API key.
///
/// Keys are not a separate concern with their own screen — they belong to a
/// holder, so they are managed by clicking into that holder.
class IdentitiesSection extends StatefulWidget {
  const IdentitiesSection({super.key});

  @override
  State<IdentitiesSection> createState() => _IdentitiesSectionState();
}

class _IdentitiesSectionState extends State<IdentitiesSection> {
  final ApiClient _api = ApiClient();
  List<Identity> _identities = [];

  /// The roster split and ordered once per load. Two lists rather than two
  /// getters, because a getter that sorts is a sort per build.
  List<Identity> _people = [];
  List<Identity> _machines = [];
  List<ApiKey> _keys = [];
  List<Team> _teams = [];
  String _myRole = 'user';
  bool _loading = true;
  String? _error;

  /// The identity currently drilled into, if any.
  String? _detailUid;

  /// The team currently drilled into, if any. Only one detail is ever open.
  String? _detailTeam;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  @override
  void dispose() {
    _api.close();
    super.dispose();
  }

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    // Four independent reads. Awaited one after another they cost the sum of
    // four round trips to say one page; issued together they cost the slowest
    // of them.
    final Future<Map<String, dynamic>> meRead = _api.whoami();
    final Future<List<Identity>> identitiesRead = _api.listIdentities();
    final Future<List<ApiKey>> keysRead = _api.listApiKeys();
    final Future<List<Team>> teamsRead = _api.listTeams();
    try {
      // Waited on as a group rather than one `await` after another: awaiting
      // them in a row means the first failure escapes while the other three
      // are still in flight, and their errors then land with nobody listening.
      await Future.wait<void>([meRead, identitiesRead, keysRead, teamsRead]);
      final me = await meRead;
      final identities = await identitiesRead;
      final keys = await keysRead;
      final teams = await teamsRead;
      if (!mounted) return;
      setState(() {
        _myRole = me['role'] as String? ?? 'user';
        _identities = identities;
        // Sorted once, here, rather than twice per build: the order is a
        // property of the roster, and the roster changes only on a refresh.
        _people = identities.where((i) => i.isPerson).toList()
          ..sort(_byRoleThenName);
        _machines = identities.where((i) => !i.isPerson).toList()
          ..sort(_byRoleThenName);
        _keys = keys;
        _teams = teams;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  bool get _isOwner => _myRole == 'owner';

  /// Platform-wide authority. Creating people and teams stays here; a team
  /// admin's powers are scoped to the teams they administer.
  bool get _isPlatformAdmin => _myRole == 'owner' || _myRole == 'admin';

  /// Teams this viewer may act on. The server decides this and reports it, so
  /// the console never re-derives the permission rules.
  List<Team> get _myTeams => _teams.where((t) => t.canAdminister).toList();

  /// Admins may manage plain identities; anything holding admin-or-higher
  /// authority is an owner's business. Nobody manages themselves.
  ///
  /// Team admins reach this screen but hold no platform authority, so they get
  /// no role picker and no delete button — offering either would only produce
  /// a 403.
  bool _canTouch(Identity identity) =>
      _isPlatformAdmin &&
      identity.uid != AuthService.instance.uid &&
      (_isOwner || identity.role == 'user');

  List<String> _assignableRoles(Identity identity) {
    final roles = _isOwner ? [..._roles] : <String>['user'];
    // Machine clients are capped below owner: a credential file must never be
    // able to grant roles or delete accounts.
    if (!identity.isPerson) roles.remove('owner');
    return roles;
  }

  /// Keys minted before identities existed. They authenticate nothing, but they
  /// have no holder to be reached through, so they surface here or nowhere.
  List<ApiKey> get _orphanedKeys =>
      _keys.where((k) => k.isUnattributed).toList();

  List<ApiKey> _keysFor(Identity identity) =>
      _keys.where((k) => k.identityUid == identity.uid).toList();

  static int _byRoleThenName(Identity a, Identity b) {
    const rank = {'owner': 0, 'admin': 1, 'user': 2};
    final byRole = (rank[a.role] ?? 9).compareTo(rank[b.role] ?? 9);
    return byRole != 0
        ? byRole
        : a.label.toLowerCase().compareTo(b.label.toLowerCase());
  }

  /// Teams this identity belongs to, with the role it holds on each.
  List<Team> _teamsFor(String identityUid) => _teams
      .where((t) => t.members.any((m) => m.identityUid == identityUid))
      .toList();

  // ---- navigation ----

  void _openDetail(Identity identity) => setState(() {
    _detailTeam = null;
    _detailUid = identity.uid;
  });

  void _openIdentityByUid(String uid) => setState(() {
    _detailTeam = null;
    _detailUid = uid;
  });

  void _openTeam(String slug) => setState(() {
    _detailUid = null;
    _detailTeam = slug;
  });

  void _closeDetail({required bool changed}) {
    setState(() {
      _detailUid = null;
      _detailTeam = null;
    });
    if (changed) _refresh();
  }

  // ---- mutations ----

  Future<void> _changeRole(Identity identity, String role) async {
    try {
      await _api.changeIdentityRole(identity.uid, role);
      _refresh();
    } catch (e) {
      _showError(e);
    }
  }

  /// Returns true only once the identity is gone *and* the roster reflects it,
  /// so a caller standing on that identity's page knows when to leave.
  Future<bool> _delete(Identity identity) async {
    final held = _keysFor(identity).where((k) => k.isActive).length;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(identity.isPerson ? 'Remove person?' : 'Delete identity?'),
        content: Text(
          identity.isPerson
              ? 'This permanently deletes ${identity.email} and revokes every '
                    'API key they hold${held == 0 ? "" : " ($held active)"}.'
              : 'This permanently deletes "${identity.label}" and revokes every '
                    'API key it holds${held == 0 ? "" : " ($held active)"}. '
                    'Any client using one starts getting 401s.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: CumulusColors.danger,
            ),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true) return false;
    try {
      await _api.deleteIdentity(identity.uid);
      await _refresh();
      return true;
    } catch (e) {
      _showError(e);
      return false;
    }
  }

  /// Same confirmation as the roster, but the page we are standing on is about
  /// to describe something that no longer exists, so leave it on success.
  Future<void> _deleteFromDetail(Identity identity) async {
    if (await _delete(identity) && mounted) {
      setState(() => _detailUid = null);
    }
  }

  Future<void> _createTeamDialog() async {
    final slugController = TextEditingController();
    final nameController = TextEditingController();
    final descriptionController = TextEditingController();
    void disposeControllers() {
      slugController.dispose();
      nameController.dispose();
      descriptionController.dispose();
    }

    final formKey = GlobalKey<FormState>();
    var slugEdited = false;

    final submitted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('New team'),
        content: SizedBox(
          width: 460,
          child: StatefulBuilder(
            builder: (context, setDialogState) => Form(
              key: formKey,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  LabeledField(
                    label: 'Name',
                    child: TextFormField(
                      controller: nameController,
                      autofocus: true,
                      decoration: const InputDecoration(
                        hintText: 'e.g. Platform Engineering',
                      ),
                      // The slug is what assets point at and cannot be changed
                      // later, so derive it until the user takes it over.
                      onChanged: (v) {
                        if (slugEdited) return;
                        setDialogState(() => slugController.text = _slugify(v));
                      },
                      validator: (v) => (v == null || v.trim().isEmpty)
                          ? 'Give the team a name'
                          : null,
                    ),
                  ),
                  const SizedBox(height: 16),
                  LabeledField(
                    label: 'Slug',
                    hint: 'permanent — assets point at this',
                    child: TextFormField(
                      controller: slugController,
                      onChanged: (_) => slugEdited = true,
                      decoration: const InputDecoration(
                        hintText: 'platform-eng',
                      ),
                      validator: (v) =>
                          (v == null ||
                              !RegExp(r'^[a-z0-9][a-z0-9-]*$').hasMatch(v))
                          ? 'Lowercase letters, digits, and hyphens'
                          : null,
                    ),
                  ),
                  const SizedBox(height: 16),
                  LabeledField(
                    label: 'What it owns',
                    child: TextFormField(
                      controller: descriptionController,
                      maxLines: 2,
                      decoration: const InputDecoration(
                        hintText: 'Platform reliability, CI, and the runbooks…',
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  const _Notice(
                    icon: Icons.groups_outlined,
                    message:
                        'A team owns documents and runs its own membership. '
                        'Give it an admin once it exists, and they can manage '
                        'it without platform-wide rights.',
                  ),
                ],
              ),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () {
              if (formKey.currentState!.validate()) {
                Navigator.pop(context, true);
              }
            },
            child: const Text('Create team'),
          ),
        ],
      ),
    );
    if (submitted != true) {
      disposeControllers();
      return;
    }
    final slug = slugController.text.trim();
    final name = nameController.text.trim();
    final description = descriptionController.text.trim();
    disposeControllers();
    try {
      await _api.createTeam(slug: slug, name: name, description: description);
      await _refresh();
      if (!mounted) return;
      // A team with no members does nothing; land where members are added.
      _openTeam(slug);
    } catch (e) {
      _showError(e);
    }
  }

  Future<void> _revokeOrphan(ApiKey key) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Revoke orphaned key?'),
        content: Text(
          '"${key.name}" (${key.display}) has no identity behind it, so it '
          'already authenticates nothing. Revoking clears it from the list for '
          'good.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: CumulusColors.danger,
            ),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Revoke'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await _api.revokeApiKey(key.id);
      _refresh();
    } catch (e) {
      _showError(e);
    }
  }

  Future<void> _addPersonDialog() async {
    final emailController = TextEditingController();
    final passwordController = TextEditingController();
    final nameController = TextEditingController();
    void disposeControllers() {
      emailController.dispose();
      passwordController.dispose();
      nameController.dispose();
    }

    String role = 'user';
    final formKey = GlobalKey<FormState>();
    final assignable = _isOwner ? _roles : const ['user'];

    final submitted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Add person'),
        content: SizedBox(
          width: 440,
          child: StatefulBuilder(
            builder: (context, setDialogState) => Form(
              key: formKey,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  LabeledField(
                    label: 'Email',
                    child: TextFormField(
                      controller: emailController,
                      autofocus: true,
                      decoration: const InputDecoration(
                        hintText: 'name@carrier.com',
                      ),
                      validator: (v) => (v == null || !v.contains('@'))
                          ? 'Enter a valid email'
                          : null,
                    ),
                  ),
                  const SizedBox(height: 16),
                  LabeledField(
                    label: 'Display name',
                    child: TextFormField(
                      controller: nameController,
                      decoration: const InputDecoration(hintText: 'Optional'),
                    ),
                  ),
                  const SizedBox(height: 16),
                  LabeledField(
                    label: 'Temporary password',
                    hint: 'min 8 characters',
                    child: TextFormField(
                      controller: passwordController,
                      obscureText: true,
                      validator: (v) => (v == null || v.length < 8)
                          ? 'Min 8 characters'
                          : null,
                    ),
                  ),
                  const SizedBox(height: 16),
                  LabeledField(
                    label: 'Role',
                    child: _Dropdown(
                      value: role,
                      choices: {
                        for (final r in assignable) r: '$r — ${_roleBlurbs[r]}',
                      },
                      onChanged: (v) => setDialogState(() => role = v),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () {
              if (formKey.currentState!.validate()) {
                Navigator.pop(context, true);
              }
            },
            child: const Text('Add person'),
          ),
        ],
      ),
    );
    if (submitted != true) {
      disposeControllers();
      return;
    }
    // Read the fields before freeing the controllers.
    final email = emailController.text.trim();
    final password = passwordController.text;
    final displayName = nameController.text.trim();
    disposeControllers();
    try {
      await _api.createUser(
        email: email,
        password: password,
        displayName: displayName,
        role: role,
      );
      _refresh();
    } catch (e) {
      _showError(e);
    }
  }

  Future<void> _addMachineDialog() async {
    final nameController = TextEditingController();
    final descriptionController = TextEditingController();
    final ownerController = TextEditingController();
    void disposeControllers() {
      nameController.dispose();
      descriptionController.dispose();
      ownerController.dispose();
    }

    String kind = 'agent';
    String role = 'user';
    // A team admin may only register clients for their own teams, so the team
    // is part of creating one rather than a later step.
    final teams = _myTeams;
    String team = teams.isEmpty ? '' : teams.first.slug;
    final formKey = GlobalKey<FormState>();

    final submitted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('New agent or service'),
        content: SizedBox(
          width: 460,
          child: StatefulBuilder(
            builder: (context, setDialogState) => Form(
              key: formKey,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  LabeledField(
                    label: 'Name',
                    child: TextFormField(
                      controller: nameController,
                      autofocus: true,
                      decoration: const InputDecoration(
                        hintText: 'e.g. Renewal Brief Agent',
                      ),
                      validator: (v) => (v == null || v.trim().isEmpty)
                          ? 'Give it a recognizable name'
                          : null,
                    ),
                  ),
                  const SizedBox(height: 16),
                  LabeledField(
                    label: 'Kind',
                    child: _Dropdown(
                      value: kind,
                      choices: const {
                        'agent': 'Agent — reasons over the knowledge layer',
                        'service': 'Service — a machine client or integration',
                      },
                      onChanged: (v) => setDialogState(() => kind = v),
                    ),
                  ),
                  const SizedBox(height: 16),
                  LabeledField(
                    label: 'Team',
                    hint: 'who answers for it',
                    child: _Dropdown(
                      value: team,
                      choices: {
                        for (final t in teams) t.slug: '${t.name} · ${t.slug}',
                        // Only a platform admin can create a client that
                        // belongs to nobody; for a team admin the team is the
                        // basis of their authority to create it at all.
                        if (_isPlatformAdmin) '': 'No team',
                      },
                      onChanged: (v) => setDialogState(() => team = v),
                    ),
                  ),
                  const SizedBox(height: 16),
                  LabeledField(
                    label: 'Authority',
                    child: _Dropdown(
                      value: role,
                      choices: {
                        for (final r in _isOwner ? ['user', 'admin'] : ['user'])
                          r: '$r — ${_roleBlurbs[r]}',
                      },
                      onChanged: (v) => setDialogState(() => role = v),
                    ),
                  ),
                  const SizedBox(height: 16),
                  LabeledField(
                    label: 'What it does',
                    child: TextFormField(
                      controller: descriptionController,
                      maxLines: 2,
                      decoration: const InputDecoration(
                        hintText: 'Assembles renewal briefs from policy admin…',
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  LabeledField(
                    label: 'Accountable person',
                    hint: 'who answers for it',
                    child: TextFormField(
                      controller: ownerController,
                      decoration: const InputDecoration(
                        hintText: 'e.g. underwriting-ops',
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  const _Notice(
                    icon: Icons.key_outlined,
                    message:
                        'Agents and services cannot sign in. They act only '
                        'through API keys, which you mint by opening the '
                        'identity once it exists.',
                  ),
                ],
              ),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () {
              if (formKey.currentState!.validate()) {
                Navigator.pop(context, true);
              }
            },
            child: const Text('Create identity'),
          ),
        ],
      ),
    );
    if (submitted != true) {
      disposeControllers();
      return;
    }
    final displayName = nameController.text.trim();
    final description = descriptionController.text.trim();
    final owner = ownerController.text.trim();
    disposeControllers();
    try {
      final created = await _api.createMachineIdentity(
        displayName: displayName,
        kind: kind,
        role: role,
        description: description,
        owner: owner,
        team: team,
      );
      await _refresh();
      if (!mounted) return;
      // A machine identity is useless until it holds a key, so land the user
      // where minting one is the obvious next move.
      setState(() => _detailUid = created.uid);
    } catch (e) {
      _showError(e);
    }
  }

  void _showError(Object e) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
  }

  // ---- build ----

  @override
  Widget build(BuildContext context) {
    if (_detailTeam != null) {
      return TeamDetailView(
        key: ValueKey(_detailTeam),
        slug: _detailTeam!,
        identities: _identities,
        onClose: _closeDetail,
        onOpenIdentity: _openIdentityByUid,
      );
    }
    final detail = _detailUid == null
        ? null
        : _identities.where((i) => i.uid == _detailUid).firstOrNull;
    if (detail != null) {
      final manageable = _canTouch(detail);
      return IdentityDetailView(
        // Rebuild the detail view from scratch when the holder changes, so its
        // key list and usage never carry over from the previous identity.
        key: ValueKey(detail.uid),
        identity: detail,
        myRole: _myRole,
        isMe: detail.uid == AuthService.instance.uid,
        onClose: _closeDetail,
        onChangeRole: manageable ? (role) => _changeRole(detail, role) : null,
        onDelete: manageable ? () => _deleteFromDetail(detail) : null,
        assignableRoles: manageable ? _assignableRoles(detail) : const [],
        teams: _teamsFor(detail.uid),
        onOpenTeam: _openTeam,
      );
    }

    return PageBody(
      children: [
        PageHeading(
          eyebrow: 'Access · identities & credentials',
          title: 'Identities',
          subtitle:
              'Everything that can reach the knowledge layer, and the keys it '
              'reaches through. Open an identity to mint, inspect, or revoke '
              'its credentials.',
          pills: [
            MetaPill('${_identities.length} identity(s)'),
            MetaPill('you are $_myRole', tone: toneForRole(_myRole)),
          ],
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              OutlinedButton.icon(
                onPressed: _loading ? null : _refresh,
                icon: const Icon(Icons.refresh, size: 16),
                label: const Text('Refresh'),
              ),
              if (_isPlatformAdmin) ...[
                const SizedBox(width: 10),
                OutlinedButton.icon(
                  onPressed: _addPersonDialog,
                  icon: const Icon(Icons.person_add_alt, size: 16),
                  label: const Text('Add person'),
                ),
              ],
              if (_myTeams.isNotEmpty) ...[
                const SizedBox(width: 10),
                OutlinedButton.icon(
                  onPressed: _addMachineDialog,
                  icon: const Icon(Icons.add, size: 16),
                  label: const Text('New agent'),
                ),
              ],
              // "New team" lives on the Teams card, beside the list it adds
              // to, rather than a fourth button up here competing with three
              // others for a reader who has not decided what they are doing
              // yet.
            ],
          ),
        ),
        _statStrip(),
        if (_error != null) ErrorNotice(message: _error!, onRetry: _refresh),
        if (_loading)
          // The heading and the strip above are already real; only the three
          // rosters are unknown, so only they turn to geometry.
          const SkeletonPulse(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              spacing: 16,
              children: [
                SkeletonListCard(rows: 3),
                SkeletonListCard(rows: 4),
              ],
            ),
          )
        else ...[
          _teamsCard(),
          ListCard(
            title: 'People',
            subtitle: 'sign in to the console; may also hold keys',
            children: [
              if (_people.isEmpty)
                const _EmptyRow('No people yet')
              else
                for (final identity in _people) _identityRow(identity),
            ],
          ),
          ListCard(
            title: 'Agents & services',
            subtitle: 'no sign-in — they act only through the keys they hold',
            children: [
              if (_machines.isEmpty)
                const _EmptyRow(
                  'No agents or service clients yet. Create one, then mint its '
                  'key from inside it.',
                )
              else
                for (final identity in _machines) _identityRow(identity),
            ],
          ),
          if (_orphanedKeys.isNotEmpty) _orphanedCard(),
          _rolesLegend(),
        ],
      ],
    );
  }

  Widget _statStrip() {
    int countKind(String kind) =>
        _identities.where((i) => i.kind == kind).length;
    final activeKeys = _keys.where((k) => k.isActive).length;
    final requests = _keys.fold<int>(0, (sum, k) => sum + k.requestCount);
    return StatStrip(
      tiles: [
        StatTile(
          label: 'Teams',
          value: '${_teams.length}',
          caption: 'own the corpus',
          valueColor: CumulusColors.accent,
        ),
        StatTile(
          label: 'People',
          value: '${countKind("person")}',
          caption: 'can sign in',
        ),
        StatTile(
          label: 'Agents',
          value: '${countKind("agent")}',
          caption: 'reason over the corpus',
          valueColor: CumulusColors.teal,
        ),
        StatTile(
          label: 'Active keys',
          value: '$activeKeys',
          caption: '${_keys.length} issued in total',
          valueColor: activeKeys == 0 ? null : CumulusColors.success,
        ),
        StatTile(
          label: 'Requests',
          value: compactCount(requests),
          caption: 'across every credential',
          valueColor: CumulusColors.accent,
        ),
      ],
    );
  }

  /// `2 of 3 keys active · 1.2K requests` — enough to know which identity is
  /// worth opening without opening it.
  String _keySummary(Identity identity) {
    final keys = _keysFor(identity);
    if (keys.isEmpty) return 'no keys';
    final active = keys.where((k) => k.isActive).length;
    final requests = keys.fold<int>(0, (sum, k) => sum + k.requestCount);
    final lastUsed = keys
        .map((k) => k.lastUsedAt)
        .whereType<DateTime>()
        .fold<DateTime?>(
          null,
          (latest, when) =>
              latest == null || when.isAfter(latest) ? when : latest,
        );
    return '$active of ${keys.length} ${keys.length == 1 ? "key" : "keys"} active · '
        '${compactCount(requests)} requests'
        '${lastUsed == null ? "" : " · last used ${agoLabel(lastUsed)}"}';
  }

  Widget _identityRow(Identity identity) {
    final isMe = identity.uid == AuthService.instance.uid;
    final descriptor = identity.isPerson
        ? identity.email
        : [
            if (identity.description.isNotEmpty) identity.description,
            if (identity.owner.isNotEmpty) 'owner: ${identity.owner}',
          ].join(' · ');
    final subtitle = [
      if (descriptor.isNotEmpty) descriptor,
      _keySummary(identity),
    ].join(' · ');

    return CumulusListRow(
      onTap: () => _openDetail(identity),
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: identity.isPerson
              ? toneForRole(identity.role).background
              : toneForKind(identity.kind).background,
          shape: BoxShape.circle,
        ),
        child: identity.isPerson
            ? Text(
                (identity.label.isNotEmpty ? identity.label[0] : '?')
                    .toUpperCase(),
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                  color: toneForRole(identity.role).foreground,
                ),
              )
            : Icon(
                iconForKind(identity.kind),
                size: 16,
                color: toneForKind(identity.kind).foreground,
              ),
      ),
      title: Row(
        children: [
          Flexible(
            child: Text(identity.label, overflow: TextOverflow.ellipsis),
          ),
          if (!identity.isPerson) ...[
            const SizedBox(width: 8),
            StatusBadge(
              labelForKind(identity.kind).toLowerCase(),
              tone: toneForKind(identity.kind),
              dense: true,
            ),
          ],
          if (isMe) ...[
            const SizedBox(width: 8),
            const StatusBadge('you', dense: true),
          ],
          if (identity.disabled) ...[
            const SizedBox(width: 8),
            const StatusBadge(
              'disabled',
              tone: CumulusTone.danger,
              dense: true,
            ),
          ],
        ],
      ),
      subtitle: Text(subtitle, maxLines: 1, overflow: TextOverflow.ellipsis),
      trailing: [
        if (_canTouch(identity))
          _RolePicker(
            value: identity.role,
            options: {..._assignableRoles(identity), identity.role}.toList(),
            onChanged: (role) => _changeRole(identity, role),
          )
        else
          StatusBadge(identity.role, tone: toneForRole(identity.role)),
        const SizedBox(width: 8),
        if (_canTouch(identity))
          IconButton(
            icon: const Icon(Icons.delete_outline, size: 18),
            tooltip: identity.isPerson ? 'Remove person' : 'Delete identity',
            onPressed: () => _delete(identity),
          )
        else
          const SizedBox(width: 40),
        const SizedBox(width: 4),
        RowAction(label: 'Open', onTap: () => _openDetail(identity)),
      ],
    );
  }

  Widget _teamsCard() {
    if (_teams.isEmpty) {
      return CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.groups_outlined,
          title: 'No teams yet',
          message: _isPlatformAdmin
              ? 'A team owns documents and runs its own membership, agents, '
                    'and keys. Create one and give it an admin.'
              : 'Teams own documents and run their own access. A platform '
                    'admin creates them.',
          action: _isPlatformAdmin
              ? FilledButton.icon(
                  onPressed: _createTeamDialog,
                  icon: const Icon(Icons.groups_outlined, size: 17),
                  label: const Text('New team'),
                )
              : null,
        ),
      );
    }
    return ListCard(
      title: 'Teams',
      subtitle: 'own the knowledge, and administer themselves',
      headerTrailing: _isPlatformAdmin
          ? RowAction(
              label: 'New team',
              icon: Icons.add,
              onTap: _createTeamDialog,
            )
          : null,
      children: [
        for (final team in _teams)
          CumulusListRow(
            onTap: () => _openTeam(team.slug),
            leading: Container(
              width: 34,
              height: 34,
              alignment: Alignment.center,
              decoration: const BoxDecoration(
                color: CumulusColors.accentSoft,
                shape: BoxShape.circle,
              ),
              child: const Icon(
                Icons.groups_outlined,
                size: 17,
                color: CumulusColors.accent,
              ),
            ),
            title: Row(
              children: [
                Flexible(
                  child: Text(team.name, overflow: TextOverflow.ellipsis),
                ),
                const SizedBox(width: 8),
                StatusBadge(team.slug, dense: true),
                if (team.myTeamRole == 'admin') ...[
                  const SizedBox(width: 8),
                  const StatusBadge(
                    'you administer',
                    tone: CumulusTone.accent,
                    dense: true,
                  ),
                ] else if (team.myTeamRole != null) ...[
                  const SizedBox(width: 8),
                  const StatusBadge('you are a member', dense: true),
                ],
                if (team.admins.isEmpty) ...[
                  const SizedBox(width: 8),
                  const StatusBadge(
                    'no admin',
                    tone: CumulusTone.warn,
                    dense: true,
                  ),
                ],
              ],
            ),
            subtitle: Text(
              [
                if (team.description.isNotEmpty) team.description,
                '${team.members.length} '
                    '${team.members.length == 1 ? "member" : "members"}',
                '${team.assetCount} '
                    '${team.assetCount == 1 ? "asset" : "assets"} owned',
              ].join(' · '),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            trailing: [
              RowAction(label: 'Open', onTap: () => _openTeam(team.slug)),
            ],
          ),
      ],
    );
  }

  Widget _orphanedCard() {
    return ListCard(
      title: 'Orphaned keys',
      subtitle: 'no identity behind them — they authenticate nothing',
      children: [
        for (final key in _orphanedKeys)
          CumulusListRow(
            leading: Container(
              width: 34,
              height: 34,
              alignment: Alignment.center,
              decoration: const BoxDecoration(
                color: CumulusColors.dangerSoft,
                shape: BoxShape.circle,
              ),
              child: const Icon(
                Icons.key_off,
                size: 16,
                color: CumulusColors.danger,
              ),
            ),
            title: Row(
              children: [
                Flexible(
                  child: Text(key.name, overflow: TextOverflow.ellipsis),
                ),
                const SizedBox(width: 10),
                const StatusBadge(
                  'no identity',
                  tone: CumulusTone.danger,
                  dense: true,
                ),
              ],
            ),
            subtitle: Text(
              '${key.display} · minted before identities existed. Mint a '
              'replacement against the identity that should hold it.',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            trailing: [
              if (key.isActive)
                IconButton(
                  icon: const Icon(Icons.block, size: 18),
                  tooltip: 'Revoke',
                  onPressed: () => _revokeOrphan(key),
                )
              else
                const SizedBox(width: 40),
            ],
          ),
      ],
    );
  }

  Widget _rolesLegend() {
    return SectionCard(
      title: 'What the roles mean',
      subtitle: 'authority is granted per identity, and inherited by its keys',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        spacing: 12,
        children: [
          for (final role in _roles)
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 92,
                  child: StatusBadge(role, tone: toneForRole(role)),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(_roleBlurbs[role]!, style: CumulusText.body),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

/// Compact role dropdown that reads as a badge until it is opened.
/// Mirrors the server's `slugify`, so the suggested slug matches what the
/// server would accept.
String _slugify(String name) => name
    .trim()
    .toLowerCase()
    .replaceAll(RegExp(r'[^a-z0-9]+'), '-')
    .replaceAll(RegExp(r'^-+|-+$'), '');

class _RolePicker extends StatelessWidget {
  final String value;
  final List<String> options;
  final ValueChanged<String> onChanged;

  const _RolePicker({
    required this.value,
    required this.options,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    final tone = toneForRole(value);
    return PopupMenuButton<String>(
      tooltip: 'Change authority',
      offset: const Offset(0, 34),
      onSelected: onChanged,
      itemBuilder: (context) => [
        for (final option in options)
          PopupMenuItem<String>(
            value: option,
            height: 38,
            child: Row(
              children: [
                StatusBadge(option, tone: toneForRole(option), dense: true),
                const SizedBox(width: 10),
                if (option == value)
                  const Icon(
                    Icons.check,
                    size: 15,
                    color: CumulusColors.inkMuted,
                  ),
              ],
            ),
          ),
      ],
      child: Container(
        padding: const EdgeInsets.fromLTRB(10, 5, 6, 5),
        decoration: BoxDecoration(
          color: tone.background,
          borderRadius: BorderRadius.circular(CumulusRadius.pill),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              value,
              style: TextStyle(
                fontSize: 11.5,
                fontWeight: FontWeight.w600,
                color: tone.foreground,
              ),
            ),
            Icon(Icons.keyboard_arrow_down, size: 15, color: tone.foreground),
          ],
        ),
      ),
    );
  }
}

/// Dropdown styled for dialogs, where there is no field theme to inherit.
class _Dropdown extends StatelessWidget {
  final String value;
  final Map<String, String> choices;
  final ValueChanged<String> onChanged;

  const _Dropdown({
    required this.value,
    required this.choices,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return DropdownButtonFormField<String>(
      initialValue: value,
      isExpanded: true,
      icon: const Icon(
        Icons.keyboard_arrow_down,
        size: 18,
        color: CumulusColors.inkMuted,
      ),
      style: const TextStyle(fontSize: 13.5, color: CumulusColors.ink),
      borderRadius: BorderRadius.circular(CumulusRadius.control),
      items: [
        for (final entry in choices.entries)
          DropdownMenuItem(
            value: entry.key,
            child: Text(entry.value, overflow: TextOverflow.ellipsis),
          ),
      ],
      onChanged: (v) => onChanged(v!),
    );
  }
}

class _Notice extends StatelessWidget {
  final IconData icon;
  final String message;

  const _Notice({required this.icon, required this.message});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: CumulusColors.surfaceMuted,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 16, color: CumulusColors.inkMuted),
          const SizedBox(width: 10),
          Expanded(child: Text(message, style: CumulusText.meta)),
        ],
      ),
    );
  }
}

class _EmptyRow extends StatelessWidget {
  final String message;

  const _EmptyRow(this.message);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 6, 20, 18),
      child: Text(message, style: CumulusText.meta),
    );
  }
}
