import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/api_key.dart';
import '../models/identity.dart';
import '../models/team.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import '../theme/domain_tones.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/ui_kit.dart';

/// Expiry choices offered when minting. `null` means no expiry.
const _expiryOptions = <String, int?>{
  'Never': null,
  '30 days': 30,
  '90 days': 90,
  '1 year': 365,
};

/// Usage windows offered on the detail page.
const _usageWindows = <String, int>{'7 days': 7, '30 days': 30, '90 days': 90};

/// One identity, end to end: who or what it is, the keys it holds, and the
/// traffic those keys have actually driven.
///
/// Keys are only ever reached through their holder — a credential with no
/// identity behind it is not a thing this console will help you create.
class IdentityDetailView extends StatefulWidget {
  final Identity identity;

  /// The signed-in viewer's role, which decides what may be minted or revoked.
  final String myRole;

  /// True when the viewer is looking at their own identity.
  final bool isMe;

  /// Called on the way out; `changed` is true if anything here needs the list
  /// behind us to reload.
  final void Function({required bool changed}) onClose;

  /// Identity-level actions stay owned by the list, which holds the roster and
  /// knows how to reload it. Null when the viewer may not manage this identity.
  final ValueChanged<String>? onChangeRole;
  final VoidCallback? onDelete;

  /// Roles this viewer may assign to this identity.
  final List<String> assignableRoles;

  /// Teams this identity belongs to. Shown as links, not as a copy of the
  /// team's contents — the team page owns its roster and its assets, and one
  /// fact living in two places is one fact that can disagree with itself.
  final List<Team> teams;
  final ValueChanged<String>? onOpenTeam;

  const IdentityDetailView({
    super.key,
    required this.identity,
    required this.myRole,
    required this.isMe,
    required this.onClose,
    this.onChangeRole,
    this.onDelete,
    this.assignableRoles = const [],
    this.teams = const [],
    this.onOpenTeam,
  });

  @override
  State<IdentityDetailView> createState() => _IdentityDetailViewState();
}

class _IdentityDetailViewState extends State<IdentityDetailView> {
  final ApiClient _api = ApiClient();

  List<ApiKey> _keys = [];
  bool _loading = true;
  String? _error;

  /// Set once anything is minted or revoked, so the list behind us reloads.
  bool _dirty = false;

  int _usageDays = 30;
  Future<_AggregateUsage>? _usage;

  Identity get _identity => widget.identity;

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
    try {
      final all = await _api.listApiKeys();
      if (!mounted) return;
      setState(() {
        _keys = all.where((k) => k.identityUid == _identity.uid).toList()
          ..sort((a, b) {
            if (a.isActive != b.isActive) return a.isActive ? -1 : 1;
            return b.createdAt.compareTo(a.createdAt);
          });
        _loading = false;
      });
      _reloadUsage();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  /// Usage is per key server-side, so an identity's traffic is the sum of its
  /// keys' traffic — including keys that have since been revoked, which is the
  /// whole point of keeping the history attached to the holder.
  void _reloadUsage() {
    setState(() => _usage = _fetchUsage(_keys, _usageDays));
  }

  Future<_AggregateUsage> _fetchUsage(List<ApiKey> keys, int days) async {
    if (keys.isEmpty) return _AggregateUsage.empty();
    final perKey = await Future.wait(
      keys.map((k) => _api.apiKeyUsage(k.id, days: days)),
    );
    final byDay = <String, int>{};
    final byEndpoint = <String, int>{};
    final byKey = <String, int>{};
    for (var i = 0; i < keys.length; i++) {
      for (final day in perKey[i]) {
        byDay[day.day] = (byDay[day.day] ?? 0) + day.count;
        byKey[keys[i].name] = (byKey[keys[i].name] ?? 0) + day.count;
        day.endpoints.forEach(
          (path, n) => byEndpoint[path] = (byEndpoint[path] ?? 0) + n,
        );
      }
    }
    return _AggregateUsage(
      byDay: byDay,
      byEndpoint: byEndpoint,
      byKey: byKey,
      days: days,
    );
  }

  // ---- permissions ----

  bool get _isOwner => widget.myRole == 'owner';

  /// Mirrors the server: an owner identity holds no keys at all, a disabled one
  /// cannot be minted for, and admin-scoped credentials are an owner's business.
  String? get _mintBlockedReason {
    if (_identity.role == 'owner') {
      return 'Owner identities cannot hold API keys — owner authority is a '
          'human sign-in privilege, not something a file on disk should carry. '
          'Mint against an admin identity instead.';
    }
    if (_identity.disabled) {
      return 'This identity is disabled, so it cannot be given a new key.';
    }
    if (_identity.role == 'admin' && !_isOwner) {
      return 'Only an owner may mint a key that carries admin authority.';
    }
    return null;
  }

  bool get _canMint => _mintBlockedReason == null;

  bool _canRevoke(ApiKey key) => _isOwner || key.role != 'admin';

  void _showError(Object e) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
  }

  Future<void> _copy(String text, String label) async {
    await Clipboard.setData(ClipboardData(text: text));
    if (!mounted) return;
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text('$label copied to clipboard')));
  }

  // ---- minting ----

  /// The holder is fixed — that is what folding keys into the identity buys:
  /// there is no "acts as" picker to get wrong.
  Future<void> _mintDialog() async {
    final nameController = TextEditingController();
    String expiryLabel = 'Never';
    final formKey = GlobalKey<FormState>();

    final submitted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Mint key for ${_identity.label}'),
        content: SizedBox(
          width: 460,
          child: StatefulBuilder(
            builder: (context, setDialogState) => Form(
              key: formKey,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _HolderCard(identity: _identity),
                  const SizedBox(height: 18),
                  LabeledField(
                    label: 'Name',
                    hint: 'where this key will live',
                    child: TextFormField(
                      controller: nameController,
                      autofocus: true,
                      decoration: const InputDecoration(
                        hintText: 'e.g. Claude Desktop — laptop',
                      ),
                      validator: (v) => (v == null || v.trim().isEmpty)
                          ? 'Give the key a recognizable name'
                          : null,
                    ),
                  ),
                  const SizedBox(height: 16),
                  LabeledField(
                    label: 'Expires',
                    child: _DialogDropdown(
                      value: expiryLabel,
                      choices: {
                        for (final label in _expiryOptions.keys) label: label,
                      },
                      onChanged: (v) => setDialogState(() => expiryLabel = v),
                    ),
                  ),
                  const SizedBox(height: 16),
                  _Notice(
                    icon: Icons.shield_outlined,
                    message:
                        'This key inherits ${_identity.label}\'s authority and '
                        'nothing more. It can never manage identities or other '
                        'keys. Withdraw the identity and every key it holds '
                        'follows.',
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
            child: const Text('Mint key'),
          ),
        ],
      ),
    );

    final name = nameController.text.trim();
    nameController.dispose();
    if (submitted != true) return;

    try {
      final minted = await _api.createApiKey(
        name: name,
        identityUid: _identity.uid,
        expiresInDays: _expiryOptions[expiryLabel],
      );
      _dirty = true;
      await _refresh();
      if (!mounted) return;
      await _showSecretDialog(minted);
    } catch (e) {
      _showError(e);
    }
  }

  /// The only place the secret is ever visible. Deliberately modal and
  /// dismiss-once, so it reads as something to capture now.
  Future<void> _showSecretDialog(MintedApiKey minted) async {
    final configJson = const JsonEncoder.withIndent(
      '  ',
    ).convert(minted.mcpConfig);

    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        title: Row(
          children: [
            Container(
              width: 30,
              height: 30,
              alignment: Alignment.center,
              decoration: const BoxDecoration(
                color: CumulusColors.accentSoft,
                shape: BoxShape.circle,
              ),
              child: const Icon(Icons.key, size: 15, color: CumulusColors.accent),
            ),
            const SizedBox(width: 12),
            Expanded(child: Text('Key minted — ${minted.key.name}')),
          ],
        ),
        content: SizedBox(
          width: 580,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 12,
                  ),
                  decoration: BoxDecoration(
                    color: CumulusColors.warnSoft,
                    borderRadius: BorderRadius.circular(CumulusRadius.control),
                  ),
                  child: const Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(
                        Icons.warning_amber_rounded,
                        size: 17,
                        color: CumulusColors.warn,
                      ),
                      SizedBox(width: 10),
                      Expanded(
                        child: Text(
                          'Copy this now — it is not stored and cannot be '
                          'shown again. Only a hash is kept.',
                          style: TextStyle(
                            fontSize: 12.5,
                            color: CumulusColors.warn,
                            height: 1.4,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 20),
                _CopyableBlock(
                  label: 'Token',
                  content: minted.token,
                  onCopy: () => _copy(minted.token, 'Token'),
                ),
                const SizedBox(height: 20),
                _CopyableBlock(
                  label: 'MCP client configuration',
                  content: configJson,
                  onCopy: () => _copy(configJson, 'MCP configuration'),
                ),
              ],
            ),
          ),
        ),
        actions: [
          FilledButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Done'),
          ),
        ],
      ),
    );
  }

  // ---- revoking ----

  Future<void> _revoke(ApiKey key) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Revoke key?'),
        content: Text(
          '"${key.name}" (${key.display}) stops working immediately. '
          'Any MCP client using it will start getting 401s. '
          'This cannot be undone.',
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
      _dirty = true;
      await _refresh();
    } catch (e) {
      _showError(e);
    }
  }

  // ---- per-key usage ----

  Future<void> _showKeyUsage(ApiKey key) async {
    // Built once, outside the dialog builder: a future created inside `builder`
    // is re-issued on every rebuild of the route.
    final usage = _api.apiKeyUsage(key.id, days: _usageDays);
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Usage — ${key.name}'),
        content: SizedBox(
          width: 540,
          child: FutureBuilder<List<ApiKeyUsageDay>>(
            future: usage,
            builder: (context, snapshot) {
              if (snapshot.connectionState != ConnectionState.done) {
                return const SizedBox(height: 120, child: CumulusLoading());
              }
              if (snapshot.hasError) {
                return ErrorNotice(message: '${snapshot.error}');
              }
              final days = snapshot.data ?? const [];
              if (days.isEmpty) {
                return const CumulusEmptyState(
                  icon: Icons.bar_chart,
                  title: 'No requests recorded yet',
                  message:
                      'Once an MCP client authenticates with this key, its '
                      'traffic shows up here by day and by endpoint.',
                );
              }
              final total = days.fold<int>(0, (sum, d) => sum + d.count);
              final byEndpoint = <String, int>{};
              for (final day in days) {
                day.endpoints.forEach(
                  (path, n) => byEndpoint[path] = (byEndpoint[path] ?? 0) + n,
                );
              }
              final endpoints = byEndpoint.entries.toList()
                ..sort((a, b) => b.value.compareTo(a.value));
              final busiest = days
                  .map((d) => d.count)
                  .reduce((a, b) => a > b ? a : b);

              return SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _UsageHeadline(total: total, days: days.length),
                    const SizedBox(height: 22),
                    const FieldLabel('By endpoint'),
                    for (final e in endpoints)
                      UsageBar(
                        label: e.key,
                        count: e.value,
                        max: endpoints.first.value,
                        mono: true,
                      ),
                    const SizedBox(height: 20),
                    const FieldLabel('By day'),
                    for (final day in days.reversed)
                      UsageBar(
                        label: day.day,
                        count: day.count,
                        max: busiest,
                      ),
                  ],
                ),
              );
            },
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  // ---- build ----

  @override
  Widget build(BuildContext context) {
    return PageBody(
      maxWidth: 1080,
      children: [
        BackLink(
          label: 'All identities',
          onTap: () => widget.onClose(changed: _dirty),
        ),
        _heading(),
        if (_error != null) ErrorNotice(message: _error!, onRetry: _refresh),
        if (_loading)
          const CumulusCard(child: CumulusLoading())
        else ...[
          _statStrip(),
          _profileCard(),
          _teamsCard(),
          _keysCard(),
          _usageCard(),
        ],
      ],
    );
  }

  Widget _heading() {
    return PageHeading(
      eyebrow: 'Access · ${labelForKind(_identity.kind).toLowerCase()}',
      title: _identity.label,
      pills: [
        MetaPill(
          labelForKind(_identity.kind),
          tone: toneForKind(_identity.kind),
        ),
        MetaPill(_identity.role, tone: toneForRole(_identity.role)),
        if (widget.isMe) const MetaPill('you'),
        if (_identity.disabled)
          const MetaPill('disabled', tone: CumulusTone.danger),
      ],
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          OutlinedButton.icon(
            onPressed: _loading ? null : _refresh,
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('Refresh'),
          ),
          if (widget.onDelete != null) ...[
            const SizedBox(width: 10),
            OutlinedButton.icon(
              onPressed: widget.onDelete,
              icon: const Icon(Icons.delete_outline, size: 16),
              label: Text(_identity.isPerson ? 'Remove' : 'Delete'),
              style: OutlinedButton.styleFrom(
                foregroundColor: CumulusColors.danger,
                side: const BorderSide(color: Color(0xFFF3C9C9)),
              ),
            ),
          ],
          const SizedBox(width: 10),
          FilledButton.icon(
            onPressed: _canMint ? _mintDialog : null,
            icon: const Icon(Icons.add, size: 17),
            label: const Text('Mint key'),
          ),
        ],
      ),
    );
  }

  Widget _statStrip() {
    final active = _keys.where((k) => k.isActive).length;
    final revoked = _keys.where((k) => k.status == 'revoked').length;
    final expired = _keys.where((k) => k.status == 'expired').length;
    final requests = _keys.fold<int>(0, (sum, k) => sum + k.requestCount);
    final lastUsed = _keys
        .map((k) => k.lastUsedAt)
        .whereType<DateTime>()
        .fold<DateTime?>(
          null,
          (latest, when) =>
              latest == null || when.isAfter(latest) ? when : latest,
        );

    return StatStrip(
      tiles: [
        StatTile(
          label: 'Active keys',
          value: '$active',
          caption: '${_keys.length} issued in total',
          valueColor: active == 0 ? null : CumulusColors.success,
        ),
        StatTile(
          label: 'Requests',
          value: compactCount(requests),
          caption: 'lifetime, across every key',
        ),
        StatTile(
          label: 'Last call',
          value: lastUsed == null ? 'never' : agoLabel(lastUsed),
          caption: 'most recent request',
        ),
        StatTile(
          label: 'Revoked',
          value: '$revoked',
          caption: 'cut off permanently',
          valueColor: revoked == 0 ? null : CumulusColors.danger,
        ),
        StatTile(
          label: 'Expired',
          value: '$expired',
          caption: 'aged out of validity',
          valueColor: expired == 0 ? null : CumulusColors.warn,
        ),
        StatTile(
          label: 'Reaches',
          value: '${widget.teams.fold<int>(0, (n, t) => n + t.assetCount)}',
          caption:
              'assets, via ${widget.teams.length} '
              '${widget.teams.length == 1 ? "team" : "teams"}',
          valueColor: CumulusColors.accent,
        ),
      ],
    );
  }

  /// Memberships as links. The team page owns the roster and the asset list;
  /// restating them here would create a second copy that can disagree.
  Widget _teamsCard() {
    if (widget.teams.isEmpty) {
      return SectionCard(
        title: 'Teams',
        subtitle: 'ownership and access run through teams',
        child: _Notice(
          icon: Icons.groups_outlined,
          message:
              '${_identity.label} is not on any team, so it owns nothing and '
              'inherits no team authority. Add it from a team\'s page.',
        ),
      );
    }
    return ListCard(
      title: 'Teams',
      subtitle: 'what this identity owns, it owns through these',
      children: [
        for (final team in widget.teams)
          CumulusListRow(
            onTap: widget.onOpenTeam == null
                ? null
                : () => widget.onOpenTeam!(team.slug),
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
              ],
            ),
            subtitle: Text(
              '${team.assetCount} ${team.assetCount == 1 ? "asset" : "assets"} '
              'owned by this team · ${team.members.length} members',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            trailing: [
              StatusBadge(
                _teamRoleOf(team),
                tone: _teamRoleOf(team) == 'admin'
                    ? CumulusTone.accent
                    : CumulusTone.neutral,
              ),
              const SizedBox(width: 8),
              RowAction(
                label: 'Open',
                onTap: () => widget.onOpenTeam?.call(team.slug),
              ),
            ],
          ),
      ],
    );
  }

  /// Safe because the roster only passes teams this identity is on.
  String _teamRoleOf(Team team) => team.members
      .firstWhere((m) => m.identityUid == _identity.uid)
      .teamRole;

  Widget _profileCard() {
    final rows = <(String, String)>[
      if (_identity.isPerson && _identity.email.isNotEmpty)
        ('Email', _identity.email),
      if (!_identity.isPerson && _identity.description.isNotEmpty)
        ('What it does', _identity.description),
      if (!_identity.isPerson && _identity.owner.isNotEmpty)
        ('Accountable person', _identity.owner),
      ('Identity ID', _identity.uid),
      if (_identity.createdAt != null)
        (
          'Created',
          '${_identity.createdAt!.toLocal().toString().split(" ").first}'
              '${_identity.createdBy.isEmpty ? "" : " by ${_identity.createdBy}"}',
        ),
    ];

    return SectionCard(
      title: 'Identity',
      subtitle: _identity.isPerson
          ? 'signs in to the console; may also hold keys'
          : 'cannot sign in — acts only through the keys below',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        spacing: 12,
        children: [
          for (final (label, value) in rows)
            _ProfileRow(
              label: label,
              child: SelectableText(value, style: CumulusText.body),
            ),
          _ProfileRow(
            label: 'Authority',
            child: Row(
              children: [
                if (widget.onChangeRole != null)
                  _RoleMenu(
                    value: _identity.role,
                    options: {
                      ...widget.assignableRoles,
                      _identity.role,
                    }.toList(),
                    onChanged: widget.onChangeRole!,
                  )
                else
                  StatusBadge(
                    _identity.role,
                    tone: toneForRole(_identity.role),
                  ),
                const SizedBox(width: 12),
                Expanded(
                  child: Text(
                    'every key below inherits this — '
                    '${_roleBlurb(_identity.role)}',
                    style: CumulusText.meta,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _keysCard() {
    if (_keys.isEmpty) {
      return CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.key_outlined,
          title: 'No keys yet',
          message: _canMint
              ? 'Mint a key here and paste the generated configuration into '
                    'the MCP client that will act as ${_identity.label}.'
              : _mintBlockedReason!,
          action: _canMint
              ? FilledButton.icon(
                  onPressed: _mintDialog,
                  icon: const Icon(Icons.add, size: 17),
                  label: const Text('Mint key'),
                )
              : null,
        ),
      );
    }

    return ListCard(
      title: 'API keys',
      subtitle: 'every credential this identity acts through',
      headerTrailing: _canMint
          ? RowAction(
              label: 'Mint key',
              icon: Icons.add,
              onTap: _mintDialog,
            )
          : null,
      children: [
        for (final key in _keys) _keyRow(key),
        if (!_canMint)
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 14, 20, 8),
            child: _Notice(
              icon: Icons.lock_outline,
              message: _mintBlockedReason!,
            ),
          ),
      ],
    );
  }

  Widget _keyRow(ApiKey key) {
    return CumulusListRow(
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: toneForStatus(key.status).background,
          shape: BoxShape.circle,
        ),
        child: Icon(
          key.isActive ? Icons.key : Icons.key_off,
          size: 16,
          color: toneForStatus(key.status).foreground,
        ),
      ),
      title: Row(
        children: [
          Flexible(child: Text(key.name, overflow: TextOverflow.ellipsis)),
          if (!key.isActive) ...[
            const SizedBox(width: 8),
            StatusBadge(
              key.status,
              tone: toneForStatus(key.status),
              dense: true,
            ),
          ],
        ],
      ),
      subtitle: Text(
        '${key.display} · ${compactCount(key.requestCount)} requests · '
        '${key.lastUsedAt == null ? "never used" : "last used ${agoLabel(key.lastUsedAt!)}"} · '
        'created by ${key.createdBy}'
        '${key.expiresAt != null ? " · expires ${key.expiresAt!.toLocal().toString().split(" ").first}" : ""}',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: [
        IconButton(
          icon: const Icon(Icons.bar_chart, size: 18),
          tooltip: 'Usage',
          onPressed: () => _showKeyUsage(key),
        ),
        if (key.isActive && _canRevoke(key))
          IconButton(
            icon: const Icon(Icons.block, size: 18),
            tooltip: 'Revoke',
            onPressed: () => _revoke(key),
          )
        else
          const SizedBox(width: 40),
      ],
    );
  }

  Widget _usageCard() {
    return SectionCard(
      title: 'Usage',
      subtitle: 'what this identity has actually asked the knowledge layer',
      headerTrailing: SegmentedTabs(
        labels: _usageWindows.keys.toList(),
        selectedIndex: _usageWindows.values.toList().indexOf(_usageDays),
        onChanged: (i) {
          setState(() => _usageDays = _usageWindows.values.toList()[i]);
          _reloadUsage();
        },
      ),
      child: FutureBuilder<_AggregateUsage>(
        future: _usage,
        builder: (context, snapshot) {
          if (snapshot.connectionState != ConnectionState.done) {
            return const SizedBox(height: 140, child: CumulusLoading());
          }
          if (snapshot.hasError) {
            return ErrorNotice(message: '${snapshot.error}');
          }
          final usage = snapshot.data ?? _AggregateUsage.empty();
          if (usage.total == 0) {
            return CumulusEmptyState(
              icon: Icons.bar_chart,
              title: 'No requests in this window',
              message: _keys.isEmpty
                  ? 'This identity holds no keys yet, so it has never reached '
                        'the knowledge layer.'
                  : 'Nothing in the last $_usageDays days. Traffic appears here '
                        'as soon as a client authenticates with one of the '
                        'keys above.',
            );
          }

          final endpoints = usage.sorted(usage.byEndpoint);
          final byKey = usage.sorted(usage.byKey);
          final days = usage.byDay.keys.toList()..sort();
          final busiestDay = usage.byDay.values.reduce((a, b) => a > b ? a : b);

          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _UsageHeadline(total: usage.total, days: usage.byDay.length),
              const SizedBox(height: 22),
              if (byKey.length > 1) ...[
                const FieldLabel('By key'),
                for (final e in byKey)
                  UsageBar(
                    label: e.key,
                    count: e.value,
                    max: byKey.first.value,
                  ),
                const SizedBox(height: 20),
              ],
              const FieldLabel('By endpoint'),
              for (final e in endpoints)
                UsageBar(
                  label: e.key,
                  count: e.value,
                  max: endpoints.first.value,
                  mono: true,
                ),
              const SizedBox(height: 20),
              const FieldLabel('By day'),
              for (final day in days.reversed)
                UsageBar(
                  label: day,
                  count: usage.byDay[day]!,
                  max: busiestDay,
                ),
            ],
          );
        },
      ),
    );
  }
}

String _roleBlurb(String role) => switch (role) {
  'owner' => 'full control, including other owners',
  'admin' => 'authors and publishes knowledge',
  _ => 'reads the governed corpus',
};

/// Usage for one identity, summed across every key it holds.
class _AggregateUsage {
  final Map<String, int> byDay;
  final Map<String, int> byEndpoint;
  final Map<String, int> byKey;
  final int days;

  _AggregateUsage({
    required this.byDay,
    required this.byEndpoint,
    required this.byKey,
    required this.days,
  });

  factory _AggregateUsage.empty() =>
      _AggregateUsage(byDay: {}, byEndpoint: {}, byKey: {}, days: 0);

  int get total => byDay.values.fold(0, (sum, n) => sum + n);

  List<MapEntry<String, int>> sorted(Map<String, int> source) =>
      source.entries.toList()..sort((a, b) => b.value.compareTo(a.value));
}

/// `1,204 requests over 12 days` — the one number the card leads with.
class _UsageHeadline extends StatelessWidget {
  final int total;
  final int days;

  const _UsageHeadline({required this.total, required this.days});

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Text('$total', style: CumulusText.statValue),
        const SizedBox(width: 10),
        Padding(
          padding: const EdgeInsets.only(bottom: 4),
          child: Text(
            'requests over $days ${days == 1 ? "day" : "days"}',
            style: CumulusText.statCaption,
          ),
        ),
      ],
    );
  }
}

/// Restates, at mint time, exactly whose authority the new key will carry.
class _HolderCard extends StatelessWidget {
  final Identity identity;

  const _HolderCard({required this.identity});

  @override
  Widget build(BuildContext context) {
    final tone = toneForKind(identity.kind);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: CumulusColors.surfaceMuted,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Row(
        children: [
          Container(
            width: 30,
            height: 30,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: tone.background,
              shape: BoxShape.circle,
            ),
            child: Icon(
              iconForKind(identity.kind),
              size: 15,
              color: tone.foreground,
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Acts as ${identity.label}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                    color: CumulusColors.ink,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  'inherits ${identity.role} authority — '
                  '${_roleBlurb(identity.role)}',
                  style: CumulusText.meta,
                ),
              ],
            ),
          ),
          StatusBadge(identity.role, tone: toneForRole(identity.role), dense: true),
        ],
      ),
    );
  }
}

/// Dropdown styled for use inside dialogs, where there is no [LabeledField]
/// theme to inherit from.
class _DialogDropdown extends StatelessWidget {
  final String value;
  final Map<String, String> choices;
  final ValueChanged<String> onChanged;

  const _DialogDropdown({
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

/// Monospace block with a copy button — used for the token and the MCP config.
class _CopyableBlock extends StatelessWidget {
  final String label;
  final String content;
  final VoidCallback onCopy;

  const _CopyableBlock({
    required this.label,
    required this.content,
    required this.onCopy,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            FieldLabel(label),
            const Spacer(),
            TextButton.icon(
              onPressed: onCopy,
              icon: const Icon(Icons.copy, size: 14),
              label: const Text('Copy'),
            ),
          ],
        ),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: CumulusColors.surfaceMuted,
            borderRadius: BorderRadius.circular(CumulusRadius.control),
            border: Border.all(color: CumulusColors.border),
          ),
          child: SelectableText(content, style: CumulusText.mono),
        ),
      ],
    );
  }
}

/// Quiet inline explanation — used where an action is unavailable and the
/// reason matters more than the button.
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

/// `LABEL   value` — one line of the identity summary.
class _ProfileRow extends StatelessWidget {
  final String label;
  final Widget child;

  const _ProfileRow({required this.label, required this.child});

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 150,
          child: Text(label.toUpperCase(), style: CumulusText.statLabel),
        ),
        const SizedBox(width: 12),
        Expanded(child: child),
      ],
    );
  }
}

/// Role badge that opens into a picker — the same affordance the roster uses,
/// so authority is changed the same way wherever it is shown.
class _RoleMenu extends StatelessWidget {
  final String value;
  final List<String> options;
  final ValueChanged<String> onChanged;

  const _RoleMenu({
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
