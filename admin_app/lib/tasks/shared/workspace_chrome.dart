/// The chrome the three plant workspaces share.
///
/// Two pieces, and they are here rather than in whichever workspace happened
/// to be written first for the ordinary reason: a lateral import between two
/// sibling screens makes one of them a library it never meant to be, and the
/// next person adding a fourth workspace has to guess which of the three to
/// reach into. `tasks/shared/` is where the review screens already keep what
/// they hold in common ([ReviewFrame], [DecisionBar]); this is the same idea
/// one floor up.
///
/// Both say something the workspaces must not each answer for themselves. The
/// absence sentence belongs to the *server* — it knows which desk nobody holds
/// and which app the namespace does not run — and a screen composing its own
/// would be a second copy of a rule the session already carries. The governed
/// page belongs to the *customer*: a workspace that showed a verdict without
/// the page it was judged against would be asking to be trusted.
library;

import 'package:flutter/material.dart';

import 'governed_clause.dart';
import '../../models/session.dart';
import '../../theme/cumulus_theme.dart';
import '../../widgets/ui_kit.dart';

/// Why a workspace is not in this deployment, with a fallback sentence.
///
/// The server usually says it better than we can — it knows which flow package
/// is missing — but a deployment that answers `available: false` with no
/// reason must still leave a person with something true on the screen rather
/// than an empty paragraph.
String workspaceAbsenceReason(Session session, String slug) {
  final reason = session.workspaceReason(slug).trim();
  if (reason.isNotEmpty) return reason;
  return 'This deployment does not run the flow behind this workspace, so '
      'there is nothing here to read. Nothing is hidden from you — the rows '
      'do not exist.';
}

/// The governed page a workspace was judged against, at the revision it was
/// read — and, when the corpus holds no such page, the fact that a built-in
/// default is standing in.
///
/// Shared by all three plant workspaces because the claim is identical on each
/// of them: these clauses are not competing to have decided anything, they are
/// the page a supervisor argues with, and a screen that showed the verdict
/// without the page would be asking to be trusted.
class GovernedPageCard extends StatelessWidget {
  final List<GovernedClause> clauses;
  final String policyRef;
  final bool policyFound;
  final String subtitle;

  const GovernedPageCard({
    super.key,
    required this.clauses,
    required this.policyRef,
    required this.policyFound,
    required this.subtitle,
  });

  @override
  Widget build(BuildContext context) {
    return SectionCard(
      title: 'The governed page',
      subtitle: subtitle,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (!policyFound)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Text(
                'No such page is in the corpus, so a built-in default is '
                'standing in. What is below is this system\'s opinion, not '
                'your policy — write the page and this screen changes with '
                'it.',
                style: CumulusText.body.copyWith(color: CumulusColors.warn),
              ),
            ),
          if (clauses.isEmpty)
            Text('This page states no clauses.', style: CumulusText.meta)
          else
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final clause in clauses)
                  Tooltip(
                    message: clause.ref.isEmpty
                        ? 'no revision recorded'
                        : clause.ref,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 8,
                        vertical: 3,
                      ),
                      decoration: BoxDecoration(
                        color: CumulusColors.surface,
                        borderRadius: BorderRadius.circular(6),
                        border: Border.all(color: CumulusColors.border),
                      ),
                      child: Text(
                        '${clause.label}: ${clause.value}',
                        style: CumulusText.mono.copyWith(fontSize: 11),
                      ),
                    ),
                  ),
              ],
            ),
          if (policyRef.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('Read at $policyRef.', style: CumulusText.meta),
          ],
        ],
      ),
    );
  }
}
