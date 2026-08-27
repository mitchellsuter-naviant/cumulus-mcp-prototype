import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../models/data_layer.dart';
import '../models/eval.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'cumulus_shell.dart';
import 'ui_kit.dart';

/// The Learned plane: what the system figured out, as opposed to what people
/// wrote (Library) or what we declared (Ontology).
///
/// The doctrine's flywheel names three knowledge assets — the exemplar, the
/// model config, and the correction set — and this is where they surface,
/// together with the derived statistics (vendor payment profiles) and the
/// eval datasets built from promoted runs. Everything on this screen was
/// produced by runs and ratified by people; every row says what produced it
/// and which flow reads it, because provenance is the difference between
/// learned knowledge and a config file.
class LearnedPlaneView extends StatefulWidget {
  final ApiClient api;
  final VoidCallback onOpenFlows;

  const LearnedPlaneView({
    super.key,
    required this.api,
    required this.onOpenFlows,
  });

  @override
  State<LearnedPlaneView> createState() => _LearnedPlaneViewState();
}

class _LearnedPlaneViewState extends State<LearnedPlaneView> {
  List<AssetSummary>? _exemplars;
  List<VendorProfile>? _profiles;
  List<EvalSpecSummary>? _specs;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    Future<T?> attempt<T>(Future<T> call) =>
        call.then<T?>((v) => v).catchError((_) => null);
    final results = await Future.wait([
      attempt(widget.api.listAssets(kind: 'exemplar')),
      attempt(widget.api.listVendorProfiles()),
      attempt(widget.api.listEvalSpecs()),
    ]);
    if (!mounted) return;
    setState(() {
      _exemplars = results[0] as List<AssetSummary>?;
      _profiles = results[1] as List<VendorProfile>?;
      _specs = results[2] as List<EvalSpecSummary>?;
      _loading = false;
      if (_exemplars == null && _profiles == null && _specs == null) {
        _error = 'The knowledge layer did not answer.';
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final exemplars = _exemplars ?? const <AssetSummary>[];
    final profiles = _profiles ?? const <VendorProfile>[];
    final specs = _specs ?? const <EvalSpecSummary>[];
    return PageBody(
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      children: [
        PageHeading(
          // The section title above the tabs already says "Knowledge layer".
          eyebrow: 'emergent · flywheel',
          title: 'Learned',
          subtitle:
              'What the system figured out through use: exemplars the '
              'flywheel promoted, statistics derived from the graph, and the '
              'eval datasets built from real runs. Corrections land as '
              'structure — this is the compounding record of judgment.',
          trailing: OutlinedButton.icon(
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('Refresh'),
          ),
        ),
        if (_error != null) ErrorNotice(message: _error!, onRetry: _load),
        StatStrip(
          tiles: [
            StatTile(
              label: 'Exemplars',
              value: _loading && _exemplars == null
                  ? '—'
                  : '${exemplars.length}',
              caption: 'one-shot templates, human-vouched',
            ),
            StatTile(
              label: 'Vendor profiles',
              value:
                  _loading && _profiles == null ? '—' : '${profiles.length}',
              caption: 'derived; disposable and rebuildable',
            ),
            StatTile(
              label: 'Eval specs',
              value: _loading && _specs == null ? '—' : '${specs.length}',
              caption:
                  '${specs.fold<int>(0, (s, r) => s + r.cases)} cases over '
                  'the shared model',
            ),
          ],
        ),
        if (_loading && _exemplars == null)
          const CumulusLoading()
        else ...[
          ListCard(
            title: 'Exemplar shelf',
            subtitle:
                'a vendor graduates from unknown to known through use: the '
                'ensemble reads its first invoice, a clerk verifies, and the '
                'verified extraction is promoted here so the second invoice '
                'is the one-shot path. Promotion is a governed decision — '
                'never an inline side effect.',
            children: [
              if (exemplars.isEmpty)
                const CumulusEmptyState(
                  icon: Icons.auto_awesome_outlined,
                  title: 'Nothing promoted yet',
                  message:
                      'The first verified extraction for a new vendor lands '
                      'here from the accounts-payable flow.',
                ),
              for (final e in exemplars) _ExemplarRow(asset: e),
            ],
          ),
          ListCard(
            title: 'Vendor payment profiles',
            subtitle:
                'how each vendor\'s invoices normally look — the baseline the '
                'anomaly screen judges against. Derived statistics are data; '
                'the thresholds that judge them are governed knowledge '
                '(invoice-posting-policy).',
            children: [
              if (profiles.isEmpty)
                const CumulusEmptyState(
                  icon: Icons.scatter_plot_outlined,
                  title: 'No profiles computed yet',
                  message:
                      'POST /api/v1/vendor-profiles/refresh runs the windowed '
                      'profile Flow.',
                ),
              for (final p in profiles) _ProfileRow(profile: p),
            ],
          ),
          ListCard(
            title: 'Eval datasets',
            subtitle:
                'graph-shaped cases over the shared model — the doctrine\'s '
                'crown-jewel dataset, grown from runs. Run them from the '
                'flow\'s Evaluation tab.',
            children: [
              if (specs.isEmpty)
                const CumulusEmptyState(
                  icon: Icons.science_outlined,
                  title: 'No specs declared',
                  message: 'Eval specs are declared in server/app/eval_specs.py.',
                ),
              for (final spec in specs)
                CumulusListRow(
                  leading: Container(
                    width: 34,
                    height: 34,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: CumulusTone.teal.background,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: const Icon(Icons.science_outlined, size: 17,
                        color: CumulusColors.ink),
                  ),
                  title: Text(spec.id),
                  subtitle: Text(
                    '${spec.summary}\n${spec.cases} case(s) · '
                    '${spec.checkpoints} checkpoint(s) · grades '
                    '${spec.gradedFields} fields, ignores '
                    '${spec.ignoredFields} on purpose',
                  ),
                  trailing: [
                    RowAction(label: 'Open flow', onTap: widget.onOpenFlows),
                  ],
                ),
            ],
          ),
        ],
      ],
    );
  }
}

class _ExemplarRow extends StatelessWidget {
  final AssetSummary asset;

  const _ExemplarRow({required this.asset});

  @override
  Widget build(BuildContext context) {
    return CumulusListRow(
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: CumulusTone.accent.background,
          borderRadius: BorderRadius.circular(10),
        ),
        child: const Icon(Icons.auto_awesome_outlined, size: 17,
            color: CumulusColors.ink),
      ),
      title: Row(
        children: [
          Flexible(child: Text(asset.title)),
          const SizedBox(width: 10),
          StatusBadge('origin ${asset.origin}', dense: true),
          const SizedBox(width: 6),
          StatusBadge('rev ${asset.revision}', dense: true),
        ],
      ),
      subtitle: Wrap(
        spacing: 12,
        runSpacing: 4,
        children: [
          Text(asset.path, style: CumulusText.mono),
          Text('· read by accounts-payable at resolve time',
              style: CumulusText.meta),
          Text('· pinned as knowledge:${asset.id}@rev${asset.revision}',
              style: CumulusText.mono),
        ],
      ),
    );
  }
}

class _ProfileRow extends StatelessWidget {
  final VendorProfile profile;

  const _ProfileRow({required this.profile});

  @override
  Widget build(BuildContext context) {
    final p = profile;
    return CumulusListRow(
      leading: Container(
        width: 34,
        height: 34,
        alignment: Alignment.center,
        decoration: BoxDecoration(
          color: CumulusTone.violet.background,
          borderRadius: BorderRadius.circular(10),
        ),
        child: const Icon(Icons.scatter_plot_outlined, size: 17,
            color: CumulusColors.ink),
      ),
      title: Text(p.vendorLabel.isEmpty ? p.vendorId : p.vendorLabel),
      subtitle: Wrap(
        spacing: 12,
        runSpacing: 4,
        children: [
          Text('${p.invoices} invoice(s) in ${p.windowDays}d',
              style: CumulusText.meta),
          Text('· median \$${p.medianUsd.toStringAsFixed(2)}',
              style: CumulusText.meta),
          Text('· p95 \$${p.p95Usd.toStringAsFixed(2)}',
              style: CumulusText.meta),
          if (p.cadenceDays > 0)
            Text('· every ~${p.cadenceDays.toStringAsFixed(0)}d',
                style: CumulusText.meta),
          Text('· derived from the graph; rebuildable',
              style: CumulusText.meta),
        ],
      ),
    );
  }
}
