import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../models/identity.dart';
import '../models/team.dart';
import '../services/api_client.dart';
import '../services/auth_service.dart';
import '../theme/cumulus_theme.dart';
import '../theme/domain_tones.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/skeleton.dart';
import '../widgets/ui_kit.dart';

/// One team: who is on it, what it owns, and who may change either.
///
/// This is where team-scoped authority is exercised. A team admin manages this
/// page without any platform-wide rights; the server decides that and reports
/// it back as [Team.canAdminister], so the console never replays the rules.
class TeamDetailView extends StatefulWidget {
  final String slug;

  /// Every identity, for the member picker. Owned by the roster behind us.
  final List<Identity> identities;

  /// Called on the way out; `changed` is true when the roster needs reloading.
  final void Function({required bool changed}) onClose;

  /// Open another identity's page — memberships are links, not dead text.
  final void Function(String identityUid)? onOpenIdentity;

  const TeamDetailView({
    super.key,
    required this.slug,
    required this.identities,
    required this.onClose,
    this.onOpenIdentity,
  });

  @override
  State<TeamDetailView> createState() => _TeamDetailViewState();
}

class _TeamDetailViewState extends State<TeamDetailView> {
  final ApiClient _api = ApiClient();

  Team? _team;
  List<AssetSummary> _assets = [];
  bool _loading = true;
  String? _error;
  bool _dirty = false;

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
    // The team and what it owns are independent reads of the same slug, so the
    // page waits for the slower of the two rather than for their sum.
    final Future<Team> teamRead = _api.getTeam(widget.slug);
    final Future<List<AssetSummary>> assetsRead = _api.teamAssets(widget.slug);
    try {
      // Grouped rather than awaited in a row: a first failure would otherwise
      // escape while the second read is still in flight, and its error would
      // land with nobody listening.
      await Future.wait<void>([teamRead, assetsRead]);
      final team = await teamRead;
      final assets = await assetsRead;
      if (!mounted) return;
      setState(() {
        _team = team;
        _assets = assets;
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

  void _showError(Object e) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
  }

  /// Every mutation here returns the updated team, so the page repaints from
  /// the server's answer rather than a guess about what changed.
  Future<void> _apply(Future<Team> action) async {
    try {
      final team = await action;
      if (!mounted) return;
      setState(() {
        _team = team;
        _dirty = true;
      });
      // Membership does not move assets, but renaming or removing might have
      // been queued behind it; keep the owned list honest.
      final assets = await _api.teamAssets(widget.slug);
      if (mounted) setState(() => _assets = assets);
    } catch (e) {
      _showError(e);
    }
  }

  // ---- membership ----

  Future<void> _addMemberDialog() async {
    final team = _team!;
    final onTeam = team.members.map((m) => m.identityUid).toSet();
    final candidates =
        widget.identities.where((i) => !onTeam.contains(i.uid)).toList()
          ..sort((a, b) {
            if (a.isPerson != b.isPerson) return a.isPerson ? -1 : 1;
            return a.label.toLowerCase().compareTo(b.label.toLowerCase());
          });

    if (candidates.isEmpty) {
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Everyone is already here'),
          content: const SizedBox(
            width: 420,
            child: Text(
              'Every identity in the console is already on this team. Create a '
              'new person, agent, or service first.',
            ),
          ),
          actions: [
            FilledButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Got it'),
            ),
          ],
        ),
      );
      return;
    }

    Identity picked = candidates.first;
    String teamRole = 'member';

    final submitted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Add to ${team.name}'),
        content: SizedBox(
          width: 460,
          child: StatefulBuilder(
            builder: (context, setDialogState) => Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                LabeledField(
                  label: 'Identity',
                  hint: 'a person, agent, or service',
                  child: _DialogDropdown(
                    value: picked.uid,
                    choices: {
                      for (final i in candidates)
                        i.uid: '${labelForKind(i.kind)} · ${i.label}',
                    },
                    onChanged: (v) => setDialogState(() {
                      picked = candidates.firstWhere((i) => i.uid == v);
                      // Machines cannot administer a team, so the option
                      // disappears — drop the selection with it rather than
                      // leaving a value the picker no longer offers.
                      if (!picked.isPerson) teamRole = 'member';
                    }),
                  ),
                ),
                const SizedBox(height: 16),
                LabeledField(
                  label: 'Team role',
                  child: _DialogDropdown(
                    value: teamRole,
                    choices: {
                      'member': 'Member — belongs to the team',
                      if (picked.isPerson)
                        'admin':
                            'Team admin — runs membership, agents, and keys',
                    },
                    onChanged: (v) => setDialogState(() => teamRole = v),
                  ),
                ),
                const SizedBox(height: 16),
                _Notice(
                  icon: Icons.shield_outlined,
                  message: picked.isPerson
                      ? 'Team roles are separate from platform roles. Making '
                            'someone a team admin lets them run this team, and '
                            'grants nothing outside it.'
                      : 'Agents and services can belong to a team but never '
                            'administer one — a credential must not be able to '
                            'grant access.',
                ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Add'),
          ),
        ],
      ),
    );
    if (submitted != true) return;
    // A machine picked while 'admin' was selected must not be sent as admin.
    final role = picked.isPerson ? teamRole : 'member';
    await _apply(
      _api.setTeamMember(widget.slug, picked.uid, teamRole: role),
    );
  }

  Future<void> _setRole(TeamMember member, String role) =>
      _apply(_api.setTeamMember(widget.slug, member.identityUid, teamRole: role));

  Future<void> _removeMember(TeamMember member) async {
    final team = _team!;
    final lastAdmin = member.isTeamAdmin && team.admins.length == 1;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Remove from team?'),
        content: Text(
          lastAdmin
              ? '${member.label} is the only admin of ${team.name}. Removing '
                    'them leaves nobody able to run this team except a platform '
                    'admin.'
              : '${member.label} loses access to everything this team '
                    'administers. Their identity and any keys they hold are '
                    'untouched.',
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
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    await _apply(_api.removeTeamMember(widget.slug, member.identityUid));
  }

  Future<void> _editDetailsDialog() async {
    final team = _team!;
    final nameController = TextEditingController(text: team.name);
    final descriptionController = TextEditingController(text: team.description);

    final submitted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Edit team'),
        content: SizedBox(
          width: 460,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              LabeledField(
                label: 'Name',
                child: TextFormField(
                  controller: nameController,
                  autofocus: true,
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
              _Notice(
                icon: Icons.link,
                message:
                    'The slug "${team.slug}" is what assets point at, so it '
                    'cannot be renamed here.',
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    final name = nameController.text.trim();
    final description = descriptionController.text.trim();
    nameController.dispose();
    descriptionController.dispose();
    if (submitted != true) return;
    await _apply(
      _api.updateTeam(widget.slug, name: name, description: description),
    );
  }

  // ---- build ----

  @override
  Widget build(BuildContext context) {
    final team = _team;
    return PageBody(
      maxWidth: 1080,
      children: [
        BackLink(
          label: 'All identities',
          onTap: () => widget.onClose(changed: _dirty),
        ),
        if (_error != null) ErrorNotice(message: _error!, onRetry: _refresh),
        if (_loading || team == null)
          // The back link above is real — the way out of a page owes the page
          // nothing. The team's own name, counts, members and assets are what
          // the read is for, so they stand in as geometry.
          const SkeletonPulse(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              spacing: 16,
              children: [
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SkeletonBar(width: 132, height: 10),
                    SizedBox(height: 12),
                    SkeletonBar(width: 260, height: 26),
                    SizedBox(height: 14),
                    Row(
                      spacing: 8,
                      children: [
                        SkeletonPill(width: 88),
                        SkeletonPill(width: 140),
                      ],
                    ),
                  ],
                ),
                SkeletonStatStrip(count: 4),
                SkeletonListCard(rows: 4),
                SkeletonListCard(rows: 4, leadingCircle: false),
              ],
            ),
          )
        else ...[
          _heading(team),
          _statStrip(team),
          _membersCard(team),
          _assetsCard(team),
        ],
      ],
    );
  }

  Widget _heading(Team team) {
    return PageHeading(
      eyebrow: 'Access · team',
      title: team.name,
      subtitle: team.description.isEmpty ? null : team.description,
      pills: [
        MetaPill(team.slug),
        if (team.myTeamRole != null)
          MetaPill(
            team.myTeamRole == 'admin' ? 'you administer this' : 'you are a member',
            tone: team.myTeamRole == 'admin'
                ? CumulusTone.accent
                : CumulusTone.neutral,
          ),
      ],
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          OutlinedButton.icon(
            onPressed: _loading ? null : _refresh,
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('Refresh'),
          ),
          if (team.canAdminister) ...[
            const SizedBox(width: 10),
            OutlinedButton.icon(
              onPressed: _editDetailsDialog,
              icon: const Icon(Icons.edit_outlined, size: 16),
              label: const Text('Edit'),
            ),
            const SizedBox(width: 10),
            FilledButton.icon(
              onPressed: _addMemberDialog,
              icon: const Icon(Icons.person_add_alt, size: 17),
              label: const Text('Add member'),
            ),
          ],
        ],
      ),
    );
  }

  Widget _statStrip(Team team) {
    final published = _assets.where((a) => a.status == 'published').length;
    final governing = _assets
        .where(
          (a) => a.authorityLevel == 'policy' || a.authorityLevel == 'standard',
        )
        .length;
    return StatStrip(
      tiles: [
        StatTile(
          label: 'Members',
          value: '${team.members.length}',
          caption: '${team.people.length} people · ${team.machines.length} machine',
        ),
        StatTile(
          label: 'Team admins',
          value: '${team.admins.length}',
          caption: 'can run this team',
          valueColor: team.admins.isEmpty
              ? CumulusColors.danger
              : CumulusColors.accent,
        ),
        StatTile(
          label: 'Assets owned',
          value: '${team.assetCount}',
          caption: '$published published',
        ),
        StatTile(
          label: 'Governing',
          value: '$governing',
          caption: 'policy + standard',
          valueColor: CumulusColors.accent,
        ),
      ],
    );
  }

  Widget _membersCard(Team team) {
    if (team.members.isEmpty) {
      return CumulusCard(
        child: CumulusEmptyState(
          icon: Icons.groups_outlined,
          title: 'Nobody on this team yet',
          message: team.canAdminister
              ? 'Add the people who answer for this team\'s knowledge, and the '
                    'agents that act on its behalf.'
              : 'A platform admin or an admin of this team can add members.',
          action: team.canAdminister
              ? FilledButton.icon(
                  onPressed: _addMemberDialog,
                  icon: const Icon(Icons.person_add_alt, size: 17),
                  label: const Text('Add member'),
                )
              : null,
        ),
      );
    }

    return ListCard(
      title: 'Members',
      subtitle: 'team roles are separate from platform roles',
      headerTrailing: team.canAdminister
          ? RowAction(
              label: 'Add member',
              icon: Icons.person_add_alt,
              onTap: _addMemberDialog,
            )
          : null,
      children: [for (final member in team.members) _memberRow(team, member)],
    );
  }

  Widget _memberRow(Team team, TeamMember member) {
    final isMe = member.identityUid == AuthService.instance.uid;
    final tone = member.isPerson
        ? toneForRole(member.globalRole)
        : toneForKind(member.kind);

    return CumulusListRow(
      onTap: widget.onOpenIdentity == null || member.missing
          ? null
          : () => widget.onOpenIdentity!(member.identityUid),
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: tone.background,
          shape: BoxShape.circle,
        ),
        child: member.isPerson
            ? Text(
                (member.label.isNotEmpty ? member.label[0] : '?').toUpperCase(),
                style: TextStyle(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                  color: tone.foreground,
                ),
              )
            : Icon(iconForKind(member.kind), size: 16, color: tone.foreground),
      ),
      title: Row(
        children: [
          Flexible(child: Text(member.label, overflow: TextOverflow.ellipsis)),
          if (member.isTeamAdmin) ...[
            const SizedBox(width: 8),
            const StatusBadge(
              'team admin',
              tone: CumulusTone.accent,
              dense: true,
            ),
          ],
          if (!member.isPerson) ...[
            const SizedBox(width: 8),
            StatusBadge(
              labelForKind(member.kind).toLowerCase(),
              tone: toneForKind(member.kind),
              dense: true,
            ),
          ],
          if (isMe) ...[
            const SizedBox(width: 8),
            const StatusBadge('you', dense: true),
          ],
          if (member.missing) ...[
            const SizedBox(width: 8),
            const StatusBadge(
              'identity missing',
              tone: CumulusTone.danger,
              dense: true,
            ),
          ],
        ],
      ),
      subtitle: Text(
        [
          if (member.email.isNotEmpty) member.email,
          'platform role: ${member.globalRole}',
          if (member.addedAt != null) 'joined ${agoLabel(member.addedAt!)}',
        ].join(' · '),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: [
        if (team.canAdminister && member.isPerson)
          _TeamRolePicker(
            value: member.teamRole,
            onChanged: (role) => _setRole(member, role),
          )
        else
          StatusBadge(
            member.teamRole,
            tone: member.isTeamAdmin
                ? CumulusTone.accent
                : CumulusTone.neutral,
          ),
        const SizedBox(width: 8),
        if (team.canAdminister)
          IconButton(
            icon: const Icon(Icons.person_remove_outlined, size: 18),
            tooltip: 'Remove from team',
            onPressed: () => _removeMember(member),
          )
        else
          const SizedBox(width: 40),
      ],
    );
  }

  Widget _assetsCard(Team team) {
    if (_assets.isEmpty) {
      return CumulusCard(
        child: const CumulusEmptyState(
          icon: Icons.hub_outlined,
          title: 'This team owns nothing yet',
          message:
              'Set a document\'s owner to this team from the knowledge layer, '
              'and it appears here — along with the right to edit it.',
        ),
      );
    }
    return ListCard(
      title: 'Assets owned',
      subtitle: 'this team answers for these documents, and may edit them',
      children: [
        for (final asset in _assets)
          CumulusListRow(
            leading: Monogram(
              asset.authorityLevel.substring(0, 1).toUpperCase(),
              tone: toneForAuthority(asset.authorityLevel),
            ),
            title: Row(
              children: [
                Flexible(
                  child: Text(asset.title, overflow: TextOverflow.ellipsis),
                ),
                const SizedBox(width: 10),
                StatusBadge(
                  asset.authorityLevel,
                  tone: toneForAuthority(asset.authorityLevel),
                  dense: true,
                ),
              ],
            ),
            subtitle: Text(
              '${asset.path} · updated ${agoLabel(asset.updatedAt)}'
              '${asset.tags.isEmpty ? "" : " · ${asset.tags.join(", ")}"}',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            trailing: [
              StatusBadge(asset.status, tone: toneForStatus(asset.status)),
            ],
          ),
      ],
    );
  }
}

/// Member / team admin, as a badge that opens into a picker.
class _TeamRolePicker extends StatelessWidget {
  final String value;
  final ValueChanged<String> onChanged;

  const _TeamRolePicker({required this.value, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    final tone = value == 'admin' ? CumulusTone.accent : CumulusTone.neutral;
    return PopupMenuButton<String>(
      tooltip: 'Change team role',
      offset: const Offset(0, 34),
      onSelected: onChanged,
      itemBuilder: (context) => [
        for (final option in const ['admin', 'member'])
          PopupMenuItem<String>(
            value: option,
            height: 38,
            child: Row(
              children: [
                StatusBadge(
                  option,
                  tone: option == 'admin'
                      ? CumulusTone.accent
                      : CumulusTone.neutral,
                  dense: true,
                ),
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
      // A FormField reads `initialValue` once, in initState, so changing it on
      // rebuild does nothing. The team-role choices shrink when a machine is
      // picked, which would strand the field on a value its items no longer
      // contain — keying on the choice set rebuilds the field instead.
      key: ValueKey('${choices.keys.join("|")}:$value'),
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
