import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../models/team.dart';
import '../services/api_client.dart';
import '../widgets/command_palette.dart';
import '../theme/cumulus_theme.dart';
import '../theme/domain_tones.dart';
import '../widgets/cumulus_shell.dart';
import '../widgets/skeleton.dart';
import '../widgets/ui_kit.dart';

const _authorityChoices = {
  'policy': 'Policy — binding',
  'standard': 'Standard — expected practice',
  'guideline': 'Guideline — recommended',
  'how-to': 'How-to — procedure',
  'note': 'Note — context',
};

const _statusChoices = {
  'draft': 'Draft',
  'published': 'Published',
  'deprecated': 'Deprecated',
};

const _typeChoices = {'text/markdown': 'Markdown', 'text/plain': 'Plain text'};

/// Full-page editor rendered inside the console shell. Fields are grouped the
/// way the asset is reasoned about: what it is, how much authority it carries,
/// what it hangs off, and finally the prose itself.
class AssetEditorView extends StatefulWidget {
  final String? assetId; // null = create
  final void Function({required bool changed}) onClose;

  /// Publishes the open document to the command palette, so ⌘K can search
  /// inside it and so navigating away can ask before discarding edits. Called
  /// with null when the editor closes.
  final void Function(DocumentScope? scope)? onScopeChanged;

  const AssetEditorView({
    super.key,
    this.assetId,
    required this.onClose,
    this.onScopeChanged,
  });

  @override
  State<AssetEditorView> createState() => _AssetEditorViewState();
}

class _AssetEditorViewState extends State<AssetEditorView> {
  final ApiClient _api = ApiClient();
  final _formKey = GlobalKey<FormState>();

  final _idController = TextEditingController();
  final _pathController = TextEditingController();
  final _titleController = TextEditingController();
  final _contentController = TextEditingController();
  final _tagsController = TextEditingController();
  final _parentController = TextEditingController();
  final _relatedController = TextEditingController();

  String _contentType = 'text/markdown';
  String _authorityLevel = 'note';
  String _kind = 'document';
  String _origin = 'authored';

  /// Generated from Python and republished on every startup — the server
  /// refuses edits (409), so the editor says so up front instead of letting
  /// someone type into a page that cannot take it.
  bool get _generated => _kind == 'schema-export';
  String _status = 'draft';

  /// The owning team's slug. Empty means unowned, which the server allows and
  /// which reads as "nobody answers for this yet".
  String _owner = '';
  List<Team> _teams = [];

  /// Read by the MCP fetch, which refuses a superseded document. Nothing here
  /// writes it, and a PUT sends the whole asset, so it is carried through the
  /// editor or saving a document would quietly un-supersede it.
  ///
  /// Its former other half, `supersedes`, is gone from both sides: this editor
  /// was the only thing that ever wrote it and nothing ever read it.
  String? _supersededBy;

  bool _loading = false;
  bool _saving = false;
  String? _error;

  /// Every field joined, captured after load and after each save. Comparing
  /// against it is cheaper to keep correct than a dirty flag maintained by
  /// hand across a dozen `onChanged` callbacks.
  String _saved = '';

  final _contentFocus = FocusNode();

  /// Null until loaded, and left null for a document that does not exist yet.
  AssetUsage? _usage;

  bool get _isEdit => widget.assetId != null;

  @override
  void initState() {
    super.initState();
    _loadTeams();
    if (_isEdit) {
      _load();
      _loadUsage();
    } else {
      _saved = _fingerprint();
    }
    _publishScope();
  }

  /// Usage never blocks the editor: a metrics outage should cost the panel,
  /// not the ability to edit the document.
  Future<void> _loadUsage() async {
    try {
      final usage = await _api.assetUsage(widget.assetId!);
      if (mounted) setState(() => _usage = usage);
    } catch (_) {}
  }

  /// Identifies the current state of every editable field.
  String _fingerprint() => [
    _idController.text,
    _pathController.text,
    _titleController.text,
    _contentController.text,
    _tagsController.text,
    _parentController.text,
    _relatedController.text,
    _contentType,
    _authorityLevel,
    _status,
    _owner,
  ].join('\u0000');

  bool get _isDirty => _fingerprint() != _saved;

  void _publishScope() {
    final id = widget.assetId;
    // A document that does not exist yet cannot be scoped to: there is nothing
    // on the server to ask questions against, and no id to cite.
    if (id == null) {
      widget.onScopeChanged?.call(null);
      return;
    }
    widget.onScopeChanged?.call(
      DocumentScope(
        assetId: id,
        title: _titleController.text.isEmpty ? id : _titleController.text,
        readContent: () => _contentController.text,
        reveal: _revealInContent,
        isDirty: () => _isDirty,
      ),
    );
  }

  /// Selects a range in the content field and scrolls it into view, which is
  /// what makes the palette's in-document lane actually navigate.
  void _revealInContent(int start, int end) {
    final length = _contentController.text.length;
    _contentController.selection = TextSelection(
      baseOffset: start.clamp(0, length),
      extentOffset: end.clamp(0, length),
    );
    _contentFocus.requestFocus();
  }

  /// Ownership is a reference to a real team, so the picker has to know which
  /// teams exist before it can offer them.
  Future<void> _loadTeams() async {
    try {
      final teams = await _api.listTeams();
      if (mounted) setState(() => _teams = teams);
    } catch (_) {
      // Non-fatal: the field falls back to showing the current owner only, so
      // an unrelated failure here never blocks editing the document itself.
    }
  }

  @override
  void dispose() {
    for (final c in [
      _idController,
      _pathController,
      _titleController,
      _contentController,
      _tagsController,
      _parentController,
      _relatedController,
    ]) {
      c.dispose();
    }
    _contentFocus.dispose();
    // The palette must not keep pointing at an editor that no longer exists.
    widget.onScopeChanged?.call(null);
    _api.close();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final asset = await _api.getAsset(widget.assetId!);
      if (!mounted) return;
      _idController.text = asset.id;
      _pathController.text = asset.path;
      _titleController.text = asset.title;
      _contentController.text = asset.content;
      _tagsController.text = asset.tags.join(', ');
      _parentController.text = asset.parent ?? '';
      _relatedController.text = asset.related.join(', ');
      setState(() {
        _contentType = asset.contentType;
        // Empty for non-documents (a prompt carries no authority); the
        // dropdown still needs a resolvable value.
        _authorityLevel =
            asset.authorityLevel.isEmpty ? 'note' : asset.authorityLevel;
        _kind = asset.kind;
        _origin = asset.origin;
        _status = asset.status;
        _owner = asset.owner;
        _supersededBy = asset.supersededBy;
        _loading = false;
      });
      _saved = _fingerprint();
      // The title only becomes known here, and the palette shows it.
      _publishScope();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _loading = false;
      });
    }
  }

  List<String> _splitList(String text) =>
      text.split(',').map((s) => s.trim()).where((s) => s.isNotEmpty).toList();

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    final asset = Asset(
      id: _idController.text.trim(),
      path: _pathController.text.trim(),
      title: _titleController.text.trim(),
      content: _contentController.text,
      contentType: _contentType,
      authorityLevel: _authorityLevel,
      kind: _kind,
      origin: _origin,
      owner: _owner,
      supersededBy: _supersededBy,
      tags: _splitList(_tagsController.text),
      parent: _parentController.text.trim().isEmpty
          ? null
          : _parentController.text.trim(),
      related: _splitList(_relatedController.text),
      status: _status,
    );
    try {
      if (_isEdit) {
        await _api.updateAsset(asset);
      } else {
        await _api.createAsset(asset);
      }
      // Saved state is the new baseline, so closing right after a save never
      // prompts about discarding work that is already persisted.
      _saved = _fingerprint();
      if (mounted) widget.onClose(changed: true);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = '$e';
        _saving = false;
      });
    }
  }

  Future<void> _delete() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Delete asset?'),
        content: Text(
          'This permanently removes "${_titleController.text}" from the '
          'knowledge layer. Agents citing it will stop finding it.',
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
    if (confirmed != true) return;
    try {
      await _api.deleteAsset(widget.assetId!);
      if (mounted) widget.onClose(changed: true);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      // The way out is chrome, not data: dropping the back link for a spinner
      // stranded the reader on a page with no exit. The heading's own words —
      // this asset's path, title and status — are the thing being fetched, so
      // they stand in as geometry until they arrive.
      return PageBody(
        maxWidth: 1080,
        children: [
          BackLink(
            label: 'All assets',
            onTap: () => widget.onClose(changed: false),
          ),
          const SkeletonPulse(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              spacing: 20,
              children: [
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SkeletonBar(width: 190, height: 10),
                    SizedBox(height: 12),
                    SkeletonBar(width: 320, height: 26),
                    SizedBox(height: 14),
                    Row(
                      spacing: 8,
                      children: [
                        SkeletonPill(width: 84),
                        SkeletonPill(width: 96),
                      ],
                    ),
                  ],
                ),
                SkeletonCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SkeletonBar(width: 96, height: 10),
                      SizedBox(height: 10),
                      SkeletonBar(height: 38, radius: CumulusRadius.control),
                      SizedBox(height: 20),
                      SkeletonBar(width: 72, height: 10),
                      SizedBox(height: 10),
                      SkeletonBar(height: 38, radius: CumulusRadius.control),
                      SizedBox(height: 20),
                      SkeletonBar(width: 84, height: 10),
                      SizedBox(height: 10),
                      SkeletonBar(height: 260, radius: CumulusRadius.control),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      );
    }
    return Form(
      key: _formKey,
      child: PageBody(
        maxWidth: 1080,
        children: [
          BackLink(
            label: 'All assets',
            onTap: () => widget.onClose(changed: false),
          ),
          PageHeading(
            eyebrow: _isEdit
                ? (_pathController.text.isEmpty
                      ? 'Knowledge layer'
                      : _pathController.text)
                : 'Knowledge layer · new document',
            title: _isEdit
                ? (_titleController.text.isEmpty
                      ? 'Untitled asset'
                      : _titleController.text)
                : 'New asset',
            pills: [
              if (_kind == 'document')
                MetaPill(
                  _authorityChoices[_authorityLevel]!.split(' — ').first,
                  tone: toneForAuthority(_authorityLevel),
                )
              else
                MetaPill(_kind, tone: CumulusTone.violet),
              if (_origin != 'authored')
                MetaPill('origin $_origin', tone: CumulusTone.teal),
              MetaPill(
                _statusChoices[_status]!,
                tone: toneForStatus(_status),
              ),
            ],
            trailing: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (_isEdit) ...[
                  OutlinedButton.icon(
                    onPressed: (_saving || _generated) ? null : _delete,
                    icon: const Icon(Icons.delete_outline, size: 16),
                    label: const Text('Delete'),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: CumulusColors.danger,
                      side: const BorderSide(color: Color(0xFFF3C9C9)),
                    ),
                  ),
                  const SizedBox(width: 10),
                ],
                FilledButton.icon(
                  onPressed: (_saving || _generated) ? null : _save,
                  icon: _saving
                      ? const SizedBox(
                          width: 15,
                          height: 15,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: Colors.white,
                          ),
                        )
                      : const Icon(Icons.check, size: 17),
                  label: Text(_isEdit ? 'Save changes' : 'Create asset'),
                ),
              ],
            ),
          ),
          if (_generated)
            const CumulusCard(
              child: Row(
                children: [
                  Icon(Icons.sync_outlined, size: 18,
                      color: CumulusColors.inkSecondary),
                  SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      'Generated from code and republished on every startup — '
                      'the declaration in server/app/ is the source of truth, '
                      'and the server refuses edits to this page. Read it '
                      'here; change it in Python.',
                      style: CumulusText.body,
                    ),
                  ),
                ],
              ),
            ),
          if (_error != null) ErrorNotice(message: _error!),
          SectionCard(
            title: 'Identity',
            subtitle: 'how the asset is addressed and found',
            child: Column(
              children: [
                _Grid(
                  children: [
                    LabeledField(
                      label: 'ID',
                      hint: _isEdit ? 'slug · fixed at creation' : 'slug',
                      child: TextFormField(
                        controller: _idController,
                        enabled: !_isEdit,
                        // Once minted, the id is what everything cites, so it
                        // is shown locked rather than editable-looking.
                        style: TextStyle(
                          fontSize: 13.5,
                          color: _isEdit
                              ? CumulusColors.inkSecondary
                              : CumulusColors.ink,
                        ),
                        decoration: InputDecoration(
                          hintText: 'renewal-referral-rules',
                          fillColor: _isEdit
                              ? CumulusColors.surfaceMuted
                              : CumulusColors.surface,
                          suffixIcon: _isEdit
                              ? const Icon(
                                  Icons.lock_outline,
                                  size: 15,
                                  color: CumulusColors.inkMuted,
                                )
                              : null,
                        ),
                        validator: (v) =>
                            (v == null ||
                                !RegExp(
                                  r'^[a-z0-9][a-z0-9\-]*$',
                                ).hasMatch(v.trim()))
                            ? 'Lowercase letters, digits, hyphens'
                            : null,
                      ),
                    ),
                    LabeledField(
                      label: 'Taxonomy path',
                      hint: 'a/b/c',
                      child: TextFormField(
                        controller: _pathController,
                        decoration: const InputDecoration(
                          hintText: 'underwriting/appetite',
                        ),
                        onChanged: (_) => setState(() {}),
                        validator: (v) =>
                            (v == null || v.trim().isEmpty) ? 'Required' : null,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                LabeledField(
                  label: 'Title',
                  child: TextFormField(
                    controller: _titleController,
                    decoration: const InputDecoration(
                      hintText: 'What an agent will see when it cites this',
                    ),
                    onChanged: (_) => setState(() {}),
                    validator: (v) =>
                        (v == null || v.trim().isEmpty) ? 'Required' : null,
                  ),
                ),
              ],
            ),
          ),
          SectionCard(
            title: 'Governance',
            subtitle: 'how much force this document carries, and for whom',
            child: Column(
              children: [
                _Grid(
                  columns: 3,
                  children: [
                    LabeledField(
                      label: 'Authority',
                      child: _dropdown(
                        value: _authorityLevel,
                        choices: _authorityChoices,
                        onChanged: (v) => setState(() => _authorityLevel = v),
                      ),
                    ),
                    LabeledField(
                      label: 'Status',
                      child: _dropdown(
                        value: _status,
                        choices: _statusChoices,
                        onChanged: (v) => setState(() => _status = v),
                      ),
                    ),
                    LabeledField(
                      label: 'Content type',
                      child: _dropdown(
                        value: _contentType,
                        choices: _typeChoices,
                        onChanged: (v) => setState(() => _contentType = v),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                // A "Scope" dropdown — Org, Department, Team — used to lead
                // this row. It was "Org" on every document in the corpus, and
                // nothing anywhere read it: reach is decided by the
                // Principal's own scope and the path prefixes the callers
                // check. A control a person can turn that changes nothing is
                // worse than no control, because it tells them the system
                // works a way it does not.
                _Grid(
                  columns: 3,
                  children: [
                    LabeledField(
                      label: 'Owning team',
                      hint: 'who answers for this',
                      child: _dropdown(
                        value: _owner,
                        choices: {
                          '': 'Unowned — nobody answers for it',
                          // Keep the current owner selectable even if the team
                          // list failed to load or the team has since gone.
                          if (_owner.isNotEmpty &&
                              !_teams.any((t) => t.slug == _owner))
                            _owner: _owner,
                          for (final team in _teams)
                            team.slug: '${team.name} · ${team.slug}',
                        },
                        onChanged: (v) => setState(() => _owner = v),
                      ),
                    ),
                    LabeledField(
                      label: 'Tags',
                      hint: 'comma-separated',
                      child: TextFormField(
                        controller: _tagsController,
                        decoration: const InputDecoration(
                          hintText: 'renewal, appetite',
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          SectionCard(
            title: 'Relationships',
            subtitle: 'what this document hangs off, and what it points to',
            child: _Grid(
              children: [
                LabeledField(
                  label: 'Parent asset',
                  child: TextFormField(
                    controller: _parentController,
                    decoration: const InputDecoration(hintText: 'asset id'),
                  ),
                ),
                LabeledField(
                  label: 'Related assets',
                  hint: 'comma-separated',
                  child: TextFormField(
                    controller: _relatedController,
                    decoration: const InputDecoration(hintText: 'asset ids'),
                  ),
                ),
              ],
            ),
          ),
          if (_usage != null) _usagePanel(_usage!),
          SectionCard(
            title: 'Content',
            subtitle: 'the text agents retrieve and cite',
            headerTrailing: Text(
              '${_contentController.text.length} characters',
              style: CumulusText.sectionSubtitle,
            ),
            child: TextFormField(
              controller: _contentController,
              focusNode: _contentFocus,
              maxLines: 22,
              onChanged: (_) => setState(() {}),
              style: CumulusText.mono,
              decoration: InputDecoration(
                alignLabelWithHint: true,
                fillColor: CumulusColors.surfaceMuted,
                hintText: '# Heading\n\nWrite the governed document here…',
                hintStyle: CumulusText.mono.copyWith(
                  color: CumulusColors.inkMuted,
                ),
                contentPadding: const EdgeInsets.all(16),
              ),
              validator: (v) =>
                  (v == null || v.trim().isEmpty) ? 'Required' : null,
            ),
          ),
        ],
      ),
    );
  }

  /// What this document is actually used for.
  ///
  /// Sits above the content rather than below it: the decision it informs —
  /// is this worth editing, or should it be retired — comes before the edit,
  /// not after.
  Widget _usagePanel(AssetUsage usage) {
    return SectionCard(
      title: 'Usage',
      subtitle: 'reads and citations over the last 30 days',
      headerTrailing: usage.lastUsedAt == null
          ? const MetaPill('never used', tone: CumulusTone.warn)
          : Text('last used ${agoLabel(usage.lastUsedAt!)}',
              style: CumulusText.sectionSubtitle),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              _usageStat('Surfaced', usage.impressions, 'returned by search'),
              _usageStat('Reads', usage.reads, 'full content fetched'),
              _usageStat(
                'Citations',
                usage.citations,
                'backed an answer',
                tone: usage.citations > 0 ? CumulusColors.accent : null,
              ),
              _usageStat('Active days', usage.days.length, 'of the last 30'),
            ],
          ),
          if (usage.ignored) ...[
            const SizedBox(height: 12),
            Text(
              'Search returned this ${usage.impressions} '
              '${usage.impressions == 1 ? "time" : "times"} in 30 days and '
              'nobody opened it. That is a relevance problem, not a reach '
              'one: it ranks for questions it does not answer. The title and '
              'the opening lines are what people judge it on.',
              style: CumulusText.meta,
            ),
          ] else if (usage.undiscovered) ...[
            const SizedBox(height: 12),
            Text(
              'Search has not returned this document once in 30 days, so '
              'nothing has had the chance to read it. Rewriting the body '
              'would change nothing — the fix is the title, the tags, or '
              'where it sits in the taxonomy.',
              style: CumulusText.meta,
            ),
          ],
          if (usage.surfaces.isNotEmpty) ...[
            const SizedBox(height: 14),
            const Text('BY SURFACE', style: CumulusText.statLabel),
            const SizedBox(height: 6),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final entry in usage.surfaces.entries)
                  MetaPill('${entry.key} · ${entry.value}'),
              ],
            ),
          ],
          if (usage.actors.isNotEmpty) ...[
            const SizedBox(height: 14),
            const Text('BY CALLER', style: CumulusText.statLabel),
            const SizedBox(height: 6),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final entry in usage.actors.entries)
                  MetaPill('${entry.key} · ${entry.value}'),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _usageStat(String label, int value, String caption, {Color? tone}) {
    return Expanded(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label.toUpperCase(), style: CumulusText.statLabel),
          const SizedBox(height: 4),
          Text(
            '$value',
            style: CumulusText.statValue.copyWith(color: tone),
          ),
          Text(caption, style: CumulusText.statCaption),
        ],
      ),
    );
  }

  Widget _dropdown({
    required String value,
    required Map<String, String> choices,
    required ValueChanged<String> onChanged,
  }) {
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

/// Equal-width field columns that collapse to a single column when the window
/// gets tight.
class _Grid extends StatelessWidget {
  final List<Widget> children;
  final int columns;

  const _Grid({required this.children, this.columns = 2});

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        const gap = 16.0;
        final perRow = constraints.maxWidth < 620 ? 1 : columns;
        final width = (constraints.maxWidth - gap * (perRow - 1)) / perRow;
        return Wrap(
          spacing: gap,
          runSpacing: gap,
          children: [
            for (final child in children)
              SizedBox(width: width, child: child),
          ],
        );
      },
    );
  }
}
