/// One governed value a workspace shows, named where a person can read it.
///
/// Mirrors `GovernedClause` in `server/app/ui_api.py`. Shared across the three
/// plant workspaces rather than restated in each of their `models.dart`,
/// because it is one shape on the wire and three copies of a parser is three
/// places for the same field name to be spelled differently.
///
/// It is deliberately *not* `accounts_payable/models.dart`'s [PolicyClause]:
/// that one carries `cites` and `deciding`, which are the posting rule's
/// attribution of a verdict to a clause. These clauses are not competing to
/// have decided anything — they are the page a supervisor argues with.
library;

class GovernedClause {
  final String id;
  final String label;
  final String value;

  /// The governed document, at the revision the screen read it.
  final String ref;

  const GovernedClause({
    required this.id,
    required this.label,
    required this.value,
    required this.ref,
  });

  factory GovernedClause.fromJson(Map<String, dynamic> json) => GovernedClause(
    id: json['id'] as String? ?? '',
    label: json['label'] as String? ?? '',
    value: json['value'] as String? ?? '',
    ref: json['ref'] as String? ?? '',
  );

  static List<GovernedClause> listFrom(dynamic raw) => [
    for (final row in (raw as List<dynamic>? ?? const []))
      GovernedClause.fromJson((row as Map).cast<String, dynamic>()),
  ];
}
