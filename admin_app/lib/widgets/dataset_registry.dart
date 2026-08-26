import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../models/payable.dart';
import '../models/reimbursement.dart';
import '../models/task.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'cumulus_shell.dart';
import 'ui_kit.dart';

/// The Data plane of the Knowledge layer: the registry of every Dataset the
/// map owns.
///
/// This plane answers "what does Cumulus hold, and where did it come from?" —
/// not "let me work the queue". Business views onto these rows are Workspaces
/// on the rail; a row here hands off to one rather than embedding it, which is
/// what the two dataset tabs that used to live here were doing wrong.
///
/// Every row carries the doctrine's vocabulary as tags, because that is what
/// makes the registry governance rather than a file browser:
///
/// - **Role** — the Dataset Role dial: `knowledge` makes agents smarter,
///   `data` is the record of what happened. Delete knowledge and the flows get
///   dumber; delete data and history is lost.
/// - **Kind** — what the container holds (documents, a table, an exemplar
///   shelf, a vector index, definitions).
/// - **Origin** — how rows get in: authored by a person, written by a Flow,
///   promoted by the flywheel, derived from another dataset, or synced from an
///   external system. Nothing carries `synced` yet, which is the honest state
///   of the Sources lens.
/// - **Authoritative** — the collection is the system of record for its
///   subject, so a flow may read it as canonical. This is the tag that makes
///   an exemplar shelf or a master-data table different from a scratch table.
class DatasetRegistryView extends StatefulWidget {
  final ApiClient api;
  final VoidCallback onOpenExpenseWorkspace;
  final VoidCallback onOpenPayablesWorkspace;
  final VoidCallback onOpenFlows;
  final VoidCallback onOpenLibrary;

  const DatasetRegistryView({
    super.key,
    required this.api,
    required this.onOpenExpenseWorkspace,
    required this.onOpenPayablesWorkspace,
    required this.onOpenFlows,
    required this.onOpenLibrary,
  });

  @override
  State<DatasetRegistryView> createState() => _DatasetRegistryViewState();
}

enum _Role { knowledge, data }

/// How rows get into a dataset. Ordered loosely by how much governance the
/// route carries.
enum _Origin { authored, flywheel, flowWritten, derived, synced }

extension on _Origin {
  String get label => switch (this) {
    _Origin.authored => 'authored',
    _Origin.flywheel => 'flywheel',
    _Origin.flowWritten => 'flow-written',
    _Origin.derived => 'derived',
    _Origin.synced => 'synced',
  };

  String get explanation => switch (this) {
    _Origin.authored => 'written by a person in the console',
    _Origin.flywheel => 'promoted from corrections by a curation Task',
    _Origin.flowWritten => 'written by a Flow through an Action',
    _Origin.derived => 'rebuilt from another dataset',
    _Origin.synced => 'landed from an external system',
  };
}

class _Dataset {
  /// Display name.
  final String name;

  /// The container it actually lives in — the thing a Tier-3 export names.
  final String container;
  final String description;
  final _Role role;
  final String kind;
  final _Origin origin;

  /// True when this collection is the system of record for its subject, so a
  /// flow may read it as canonical.
  final bool authoritative;

  /// The Flow that writes it, when one does.
  final String? writtenBy;

  /// Rendered count, or null when the console cannot count it honestly from
  /// here.
  final String? count;
  final String countCaption;

  final String? openLabel;
  final VoidCallback? onOpen;

  const _Dataset({
    required this.name,
    required this.container,
    required this.description,
    required this.role,
    required this.kind,
    required this.origin,
    required this.countCaption,
    this.authoritative = false,
    this.writtenBy,
    this.count,
    this.openLabel,
    this.onOpen,
  });
}

/// Counts pulled from the endpoints that already exist. Nullable throughout:
/// one failing request must leave the rest of the registry readable, and a
/// count the console cannot get is shown as `—` rather than as zero.
class _Counts {
  int? assets;
  int? exemplars;
  int? flows;
  int? reimbursements;
  int? payables;
  int? openTasks;
  int? identities;
  int? teams;
}

class _DatasetRegistryViewState extends State<DatasetRegistryView> {
  final _Counts _counts = _Counts();
  bool _loading = true;
  bool _sources = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    // Independent requests, each swallowing its own failure: the registry is a
    // survey, so a missing count must not blank the page.
    Future<T?> attempt<T>(Future<T> call) =>
        call.then<T?>((v) => v).catchError((_) => null);

    final results = await Future.wait([
      attempt(widget.api.listAssets()),
      attempt(widget.api.listFlows()),
      attempt(widget.api.reimbursementStats()),
      attempt(widget.api.payableStats()),
      attempt(widget.api.taskStats()),
      attempt(widget.api.listIdentities()),
      attempt(widget.api.listTeams()),
    ]);

    final assets = results[0] as List<AssetSummary>?;
    _counts
      ..assets = assets?.length
      ..exemplars = assets
          ?.where((a) => a.tags.contains('exemplar'))
          .length
      ..flows = (results[1] as List?)?.length
      ..reimbursements = (results[2] as ReimbursementStats?)?.total
      ..payables = (results[3] as PayableStats?)?.total
      ..openTasks = (results[4] as TaskStats?)?.open
      ..identities = (results[5] as List?)?.length
      ..teams = (results[6] as List?)?.length;

    if (mounted) setState(() => _loading = false);
  }

  String? _n(int? value) => value?.toString();

  /// The registry itself. Declared here rather than fetched, because the
  /// containers are a property of the deployment (see `server/app/config.py`)
  /// while only the counts are live. When this earns a `/api/v1/datasets`
  /// endpoint, this list is what it returns.
  Map<String, List<_Dataset>> get _groups => {
    'Governed knowledge': [
      _Dataset(
        name: 'Knowledge library',
        container: 'assets',
        description:
            'Policies, standards, guidelines and how-tos. Flows read these at '
            'run time — the expense policy\'s thresholds and the invoice '
            'posting limit are parsed out of documents in here, so editing one '
            'changes what a flow does with no code change.',
        role: _Role.knowledge,
        kind: 'documents',
        origin: _Origin.authored,
        authoritative: true,
        count: _n(_counts.assets),
        countCaption: 'documents',
        openLabel: 'Library',
        onOpen: widget.onOpenLibrary,
      ),
      _Dataset(
        name: 'AP vendor exemplars',
        container: 'assets · tag:exemplar',
        description:
            'One-shot extraction templates, one per vendor. A verified '
            'ensemble run writes the exemplar back through a curation Task, '
            'and the vendor\'s next invoice takes the single-pass path — this '
            'shelf is the flywheel\'s output and the AP flow\'s biggest '
            'cost lever.',
        role: _Role.knowledge,
        kind: 'exemplar shelf',
        origin: _Origin.flywheel,
        authoritative: true,
        count: _n(_counts.exemplars),
        countCaption: 'vendors covered',
      ),
      _Dataset(
        name: 'Flow definitions',
        container: 'flows',
        description:
            'The authored logic, as declarative Tier-2 map assets — steps, '
            'ModelCall configs, Rules and thresholds. Versioned, and the thing '
            'the customer keeps.',
        role: _Role.knowledge,
        kind: 'definitions',
        origin: _Origin.authored,
        authoritative: true,
        count: _n(_counts.flows),
        countCaption: 'flows',
        openLabel: 'Flows',
        onOpen: widget.onOpenFlows,
      ),
      const _Dataset(
        name: 'Golden sets & correction sets',
        container: 'flow_golden_sets',
        description:
            'Eval cases keyed to a FlowContract, and the raw→corrected pairs '
            'every human decision appends to. Per flow, so the counts live on '
            'the flow\'s Testing tab rather than here.',
        role: _Role.knowledge,
        kind: 'eval datasets',
        origin: _Origin.flywheel,
        countCaption: 'per flow',
      ),
    ],
    'Business data': [
      _Dataset(
        name: 'Reimbursements',
        container: 'reimbursements',
        description:
            'One row per expense request, each carrying the run journal that '
            'produced it: which policy revision was read, what the Rule said, '
            'who decided.',
        role: _Role.data,
        kind: 'table',
        origin: _Origin.flowWritten,
        writtenBy: 'expense-reimbursement',
        count: _n(_counts.reimbursements),
        countCaption: 'rows',
        openLabel: 'Workspace',
        onOpen: widget.onOpenExpenseWorkspace,
      ),
      _Dataset(
        name: 'AP invoices',
        container: 'payables',
        description:
            'One row per invoice, recording which extraction path ran — '
            'one-shot against a vendor exemplar, or a three-model ensemble '
            'adjudicated field by field and verified by a human.',
        role: _Role.data,
        kind: 'table',
        origin: _Origin.flowWritten,
        writtenBy: 'accounts-payable',
        count: _n(_counts.payables),
        countCaption: 'rows',
        openLabel: 'Workspace',
        onOpen: widget.onOpenPayablesWorkspace,
      ),
      _Dataset(
        name: 'Task queue',
        container: 'tasks',
        description:
            'Units of human judgment, routed through queues. Written by a Flow '
            'when it suspends; a decision resumes the run and lands in the '
            'curation ledger.',
        role: _Role.data,
        kind: 'work items',
        origin: _Origin.flowWritten,
        count: _n(_counts.openTasks),
        countCaption: 'open',
      ),
    ],
    'Platform & access': [
      _Dataset(
        name: 'Identities & keys',
        container: 'identities · api_keys',
        description:
            'Principals — people, agents and services — and the minted keys '
            'they call with. The control plane, not the map.',
        role: _Role.data,
        kind: 'table',
        origin: _Origin.authored,
        count: _n(_counts.identities),
        countCaption: 'identities',
      ),
      _Dataset(
        name: 'Teams',
        container: 'teams',
        description:
            'Owners of assets and the second axis of authorization on every '
            'read.',
        role: _Role.data,
        kind: 'table',
        origin: _Origin.authored,
        count: _n(_counts.teams),
        countCaption: 'teams',
      ),
      const _Dataset(
        name: 'Read attribution',
        container: 'asset_usage',
        description:
            'Which documents are actually retrieved and cited, rolled up per '
            'asset. The evidence behind the Library\'s ignored and '
            'undiscovered lenses.',
        role: _Role.data,
        kind: 'ledger',
        origin: _Origin.derived,
        countCaption: 'rollups',
      ),
    ],
    'Derived': [
      const _Dataset(
        name: 'Asset embeddings',
        container: 'asset_embeddings',
        description:
            'The vector index behind hybrid search. Rebuilt from the library '
            'whenever a document changes, and stamped with the encoder it came '
            'from — nothing here is authored, so nothing here is governed '
            'separately.',
        role: _Role.knowledge,
        kind: 'vector index',
        origin: _Origin.derived,
        countCaption: 'one per document',
      ),
    ],
  };

  @override
  Widget build(BuildContext context) {
    final groups = _groups;
    final all = groups.values.expand((g) => g).toList();
    final knowledge = all.where((d) => d.role == _Role.knowledge).length;
    final data = all.where((d) => d.role == _Role.data).length;
    final synced = all.where((d) => d.origin == _Origin.synced).length;

    return PageBody(
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      children: [
        PageHeading(
          eyebrow: 'Knowledge layer · data',
          title: 'Data',
          subtitle:
              'Every Dataset the map owns: what it holds, which Role it '
              'carries, how rows get in, and which Flow reads or writes it. '
              'The business views onto these rows live in Workspaces.',
          trailing: OutlinedButton.icon(
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('Refresh'),
          ),
        ),
        StatStrip(
          tiles: [
            StatTile(
              label: 'Datasets',
              value: '${all.length}',
              caption: 'in the registry',
            ),
            StatTile(
              label: 'Knowledge role',
              value: '$knowledge',
              caption: 'makes flows smarter',
            ),
            StatTile(
              label: 'Data role',
              value: '$data',
              caption: 'the record of what happened',
            ),
            StatTile(
              label: 'External sources',
              value: '$synced',
              caption: synced == 0 ? 'nothing synced yet' : 'landing datasets',
            ),
          ],
        ),
        Row(
          children: [
            LensChip(
              label: 'DATASETS',
              selected: !_sources,
              onTap: () => setState(() => _sources = false),
            ),
            const SizedBox(width: 8),
            LensChip(
              label: 'SOURCES',
              selected: _sources,
              onTap: () => setState(() => _sources = true),
            ),
          ],
        ),
        if (_sources)
          const _SourcesPanel()
        else
          for (final entry in groups.entries)
            ListCard(
              title: entry.key,
              subtitle: _groupSubtitle(entry.key),
              children: [
                for (final dataset in entry.value) _DatasetRow(dataset),
              ],
            ),
      ],
    );
  }

  String _groupSubtitle(String group) => switch (group) {
    'Governed knowledge' =>
      'the declarative middle — editable in the shell, versioned, read at run time',
    'Business data' => 'rows a Flow wrote, with the journal that produced them',
    'Platform & access' => 'the control plane: who may read and act',
    _ => 'rebuilt from another dataset, never authored',
  };
}

class _DatasetRow extends StatelessWidget {
  final _Dataset dataset;

  const _DatasetRow(this.dataset);

  @override
  Widget build(BuildContext context) {
    final roleTone = dataset.role == _Role.knowledge
        ? CumulusTone.accent
        : CumulusTone.teal;
    return CumulusListRow(
      onTap: dataset.onOpen,
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: roleTone.background,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Icon(_iconForKind(dataset.kind), size: 17, color: roleTone.foreground),
      ),
      title: Row(
        children: [
          Flexible(child: Text(dataset.name)),
          const SizedBox(width: 10),
          StatusBadge(
            dataset.role == _Role.knowledge ? 'knowledge' : 'data',
            tone: roleTone,
            dense: true,
            uppercase: true,
          ),
          const SizedBox(width: 6),
          StatusBadge(dataset.kind, dense: true),
          if (dataset.authoritative) ...[
            const SizedBox(width: 6),
            const Tooltip(
              message:
                  'System of record for its subject — a flow may read it as '
                  'canonical.',
              child: StatusBadge(
                'authoritative',
                tone: CumulusTone.violet,
                dense: true,
              ),
            ),
          ],
        ],
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(dataset.description),
          const SizedBox(height: 6),
          Row(
            children: [
              Text(dataset.container, style: CumulusText.mono),
              const SizedBox(width: 12),
              Tooltip(
                message: dataset.origin.explanation,
                child: Text(
                  '· ${dataset.origin.label}',
                  style: CumulusText.meta,
                ),
              ),
              if (dataset.writtenBy != null) ...[
                const SizedBox(width: 6),
                Text('· ${dataset.writtenBy}', style: CumulusText.meta),
              ],
            ],
          ),
        ],
      ),
      trailing: [
        SizedBox(
          width: 96,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                dataset.count ?? '—',
                style: const TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w600,
                  color: CumulusColors.ink,
                ),
              ),
              Text(dataset.countCaption, style: CumulusText.statCaption),
            ],
          ),
        ),
        // Fixed slot whether or not the row hands off, so the count column
        // right-aligns down the card instead of stepping in and out.
        SizedBox(
          width: 128,
          child: dataset.onOpen == null
              ? null
              : Align(
                  alignment: Alignment.centerRight,
                  child: RowAction(
                    label: dataset.openLabel ?? 'Open',
                    onTap: dataset.onOpen!,
                  ),
                ),
        ),
      ],
    );
  }
}

IconData _iconForKind(String kind) => switch (kind) {
  'documents' => Icons.description_outlined,
  'exemplar shelf' => Icons.auto_awesome_outlined,
  'definitions' => Icons.account_tree_outlined,
  'eval datasets' => Icons.science_outlined,
  'table' => Icons.table_rows_outlined,
  'work items' => Icons.inbox_outlined,
  'ledger' => Icons.receipt_long_outlined,
  'vector index' => Icons.scatter_plot_outlined,
  _ => Icons.dataset_outlined,
};

/// The Sources lens: what Cumulus pulls in from outside itself.
///
/// It is deliberately honest rather than decorative. Nothing is synced today —
/// the only external content that reaches the map arrives through the API as a
/// submitted document — and two steps of the AP flow are already declared as
/// placeholders waiting on exactly the feeds that would show up here.
class _SourcesPanel extends StatelessWidget {
  const _SourcesPanel();

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      spacing: 16,
      children: [
        ListCard(
          title: 'Inbound today',
          subtitle: 'the only routes external content takes into the map',
          children: const [
            _SourceRow(
              icon: Icons.upload_file_outlined,
              name: 'Submitted documents',
              transport: 'POST /api/v1/payables · POST /api/v1/reimbursements',
              detail:
                  'An invoice or a receipt arrives as bytes on the API and the '
                  'flow runs synchronously. Per-case, human- or client-'
                  'initiated — not a feed.',
              tone: CumulusTone.success,
              state: 'live',
            ),
            _SourceRow(
              icon: Icons.hub_outlined,
              name: 'Knowledge reads out',
              transport: 'MCP · A2A · REST',
              detail:
                  'The outbound direction, for completeness: external agents '
                  'read the governed corpus through the gateway, under the '
                  'same authority and attribution rules as the console.',
              tone: CumulusTone.accent,
              state: 'live',
            ),
          ],
        ),
        ListCard(
          title: 'Declared but not connected',
          subtitle:
              'steps that name a feed they do not have — the honest backlog '
              'for this lens',
          children: const [
            _SourceRow(
              icon: Icons.compare_arrows,
              name: 'Purchase orders & receiving',
              transport: 'ERP — no adapter',
              detail:
                  'The AP flow\'s three-way match step is a declared '
                  'placeholder and says so in every run journal. It needs a PO '
                  'dataset and a receiving dataset landed by an inbound Flow.',
              tone: CumulusTone.warn,
              state: 'placeholder',
            ),
            _SourceRow(
              icon: Icons.shield_outlined,
              name: 'Vendor master',
              transport: 'ERP / MDM — no adapter',
              detail:
                  'Canonical vendors, with cross-system identity Links back '
                  'to each source\'s id. The AP flow resolves vendors against '
                  'the exemplar shelf today, which is a knowledge shelf doing '
                  'a master-data job.',
              tone: CumulusTone.warn,
              state: 'placeholder',
            ),
            _SourceRow(
              icon: Icons.policy_outlined,
              name: 'Fraud & sanctions screening',
              transport: 'third-party service — no adapter',
              detail:
                  'The AP flow\'s fraud-screen step, likewise declared and '
                  'likewise a placeholder.',
              tone: CumulusTone.warn,
              state: 'placeholder',
            ),
          ],
        ),
        CumulusCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionHeader(
                title: 'What a connected source will show',
                subtitle:
                    'a source is not a primitive — it is a Transport plus an '
                    'inbound Flow',
              ),
              const SizedBox(height: 12),
              Text(
                'Per the doctrine, CDC, sync and MDM are Flows over sourced '
                'transport rather than mechanisms of their own. So a row in '
                'this lens will carry: the external system and its adapter; '
                'the direction, and for a bidirectional pair the echo-'
                'suppression and reconciliation policy; the landing Flow and '
                'the datasets it writes; freshness and lag against the last '
                'successful window; the claim precedence Rule when more than '
                'one system asserts the same field; and custody — whether the '
                'rows are copied into the map or referenced in place.',
                style: CumulusText.body,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _SourceRow extends StatelessWidget {
  final IconData icon;
  final String name;
  final String transport;
  final String detail;
  final CumulusTone tone;
  final String state;

  const _SourceRow({
    required this.icon,
    required this.name,
    required this.transport,
    required this.detail,
    required this.tone,
    required this.state,
  });

  @override
  Widget build(BuildContext context) {
    return CumulusListRow(
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: tone.background,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Icon(icon, size: 17, color: tone.foreground),
      ),
      title: Row(
        children: [
          Flexible(child: Text(name)),
          const SizedBox(width: 10),
          StatusBadge(state, tone: tone, dense: true, uppercase: true),
        ],
      ),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(detail),
          const SizedBox(height: 6),
          Text(transport, style: CumulusText.mono),
        ],
      ),
    );
  }
}
