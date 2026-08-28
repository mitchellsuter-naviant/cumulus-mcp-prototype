import 'package:flutter/material.dart';

import '../models/data_layer.dart';
import '../models/eval.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import 'cumulus_shell.dart';
import 'ui_kit.dart';

/// The Learned plane: what the system figured out, as opposed to what people
/// wrote (Library) or what we declared (Ontology).
///
/// Two kinds of thing surface here: statistics derived from the graph (the
/// vendor payment profiles the anomaly screen judges against) and the eval
/// datasets grown from real runs. Everything on this screen was produced by
/// runs and ratified by people; every row says what produced it and which
/// flow reads it, because provenance is the difference between learned
/// knowledge and a config file.
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
      attempt(widget.api.listVendorProfiles()),
      attempt(widget.api.listEvalSpecs()),
    ]);
    if (!mounted) return;
    setState(() {
      _profiles = results[0] as List<VendorProfile>?;
      _specs = results[1] as List<EvalSpecSummary>?;
      _loading = false;
      if (_profiles == null && _specs == null) {
        _error = 'The knowledge layer did not answer.';
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final profiles = _profiles ?? const <VendorProfile>[];
    final specs = _specs ?? const <EvalSpecSummary>[];
    return PageBody(
      padding: const EdgeInsets.fromLTRB(28, 20, 28, 40),
      children: [
        PlaneHeader(
          summary:
              'What the system figured out through use: statistics derived '
              'from the graph, and the eval datasets built from real runs. '
              'Corrections land as structure — this is the compounding '
              'record of judgment.',
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
        if (_loading && _profiles == null)
          const CumulusLoading()
        else ...[
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
