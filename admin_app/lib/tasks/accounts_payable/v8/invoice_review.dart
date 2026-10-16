/// The invoice as a clerk should meet it — accounts-payable v8.
///
/// v6 asks a person to read a form of thirty inputs and find the wrong number
/// in it. That is the machine's work handed back: the run already knows which
/// values it was unsure of and which ones fail their own arithmetic, and it
/// knew before it raised the task. v8's flow says so — a confidence per field,
/// a deterministic arithmetic check, and then a second, cheap model call that
/// locates **only** the fields in doubt and stores their boxes. This screen is
/// the other half of that: **a clerk is asked about three fields, not thirty.**
///
/// So the shape of the screen is the shape of the claim. The document is on
/// one side with boxes on exactly the disputed values. Beside it, the fields
/// that need a person come first — each with the check's own sentence, its
/// confidence, and an input — and everything the run was sure about is folded
/// away underneath. Selecting a field lights its box; selecting a box focuses
/// its field.
///
/// Three states, and none of them is the same as the others:
///
/// - **Fields in doubt.** The normal case, and what the layout is for.
/// - **No field in doubt.** A clean invoice that still routed to review, which
///   is what the governed posting policy at $0 makes the *common* case. This
///   has to read as a fast approve and say why it is here — an empty form
///   would tell a clerk to go looking for a problem that does not exist.
/// - **No verdicts at all.** A task raised by a run that never computed them.
///   Not "nothing was wrong" — *nobody looked* — so the screen falls back to
///   v6's behaviour and shows the whole extraction open.
///
/// The correction travels twice, deliberately: as the patch every version's
/// applier already reads ([invoicePatch]), and as `fields`, keyed by the
/// canonical fact path the verdict arrived under, which is what makes the
/// correction set a clean raw→corrected pair rather than a diff somebody has
/// to reconstruct. The screen never mints a path of its own — see [_payload].
///
/// **v9 folds a second question into the same page.** When the resolver could
/// not settle which vendor issued the invoice, the scored candidates ride the
/// proposal ([TaskProposal.vendor]) instead of raising a gate of their own, and
/// a compact picker appears above the fields. Two gates over one document is
/// two interruptions for one reading of it: the person checking the numbers can
/// see the name printed on the page while they do it. A task with no vendor
/// block — every v8 task, and every v9 task whose resolver settled it — renders
/// exactly as it did before.
library;

import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../../../models/data_layer.dart';
import '../../../models/task.dart';
import '../../../services/api_client.dart';
import '../../../theme/cumulus_theme.dart';
import '../../../widgets/document_drawer.dart';
import '../../../widgets/ui_kit.dart';
import '../../shared/review_frame.dart';
import '../invoice_drafts.dart';
import '../models.dart';

// ---------------------------------------------------------------------------
// Paths, and what they point at
// ---------------------------------------------------------------------------

/// Which editable thing a canonical fact path names.
///
/// A verdict arrives keyed by a fact path — `invoice.total_usd`,
/// `line_items[2].amount_usd` — and the screen has to put an input under it.
/// Resolving is deliberately tolerant about the surface of the path and strict
/// about the meaning: the `invoice.` prefix is optional because the region
/// vocabulary carries it and the contract's examples do not, and the index is
/// **zero-based** because that is what both agree on (`line_items[2]` is
/// labelled "Line 3").
///
/// A path this cannot place is not an error and must never be dropped: the
/// field still gets a focused input of its own, keyed by the path, and the
/// correction still travels. Losing a flagged field because the screen did not
/// recognise its name is the one failure mode worse than an ugly label.
class InvoiceFieldRef {
  final String path;

  /// Zero-based index into the extraction's lines; null for a header field.
  final int? lineIndex;

  /// The normalised field name — `invoice_number`, `total_usd`, `quantity`
  /// and the rest of [_leaf]'s vocabulary — or empty for a doubt about a line
  /// as a whole, which is what an arithmetic contradiction actually is.
  final String leaf;

  const InvoiceFieldRef({
    required this.path,
    this.lineIndex,
    required this.leaf,
  });
}

final _linePath = RegExp(r'^(?:line_items|lines)\[(\d+)\]\.?(.*)$');

/// The editable thing [path] names, or null when nothing on this screen is it.
InvoiceFieldRef? resolveInvoicePath(String path) {
  var rest = path.trim();
  if (rest.startsWith('invoice.')) rest = rest.substring('invoice.'.length);
  final line = _linePath.firstMatch(rest);
  if (line != null) {
    final index = int.tryParse(line.group(1)!);
    if (index == null) return null;
    return InvoiceFieldRef(
      path: path,
      lineIndex: index,
      leaf: _leaf(line.group(2)!),
    );
  }
  final leaf = _leaf(rest);
  if (leaf.isEmpty || leaf == 'quantity' || leaf == 'unit_price_usd' ||
      leaf == 'amount_usd') {
    // A line leaf with no line is not a header field, it is a malformed path.
    return null;
  }
  return InvoiceFieldRef(path: path, leaf: leaf);
}

String _leaf(String raw) => switch (raw) {
  'invoice_number' || 'number' || 'invoice_no' => 'invoice_number',
  'po_number' || 'po' || 'purchase_order' => 'po_number',
  'quantity' || 'qty' => 'quantity',
  'unit_price_usd' || 'unit_price' || 'price' => 'unit_price_usd',
  'amount_usd' || 'amount' => 'amount_usd',
  'total_usd' || 'total' => 'total_usd',
  'subtotal_usd' || 'subtotal' => 'subtotal_usd',
  'freight_usd' || 'freight' => 'freight_usd',
  'tax_usd' || 'tax' => 'tax_usd',
  'invoice_date' || 'date' => 'invoice_date',
  'due_date' => 'due_date',
  'currency' => 'currency',
  _ => '',
};

/// The header fields the applier takes by name that the folded form has no
/// input for — a total, a tax line, the dates.
///
/// They are here because `check-arithmetic` implicates exactly these:
/// "subtotal + freight + tax ≈ total" and "dates sane" are two of its four
/// rules, so a run raising a verdict on `invoice.total_usd` is the expected
/// traffic, not an edge case. Each gets a control of its own, seeded from what
/// the run read, and travels in the patch under its own key as well as under
/// its path — so a corrected total lands whether the flow applies the patch or
/// reads the path-keyed corrections.
///
/// `subtotal_usd` and `freight_usd` are deliberately **not** in the patch map:
/// `_apply_invoice_patch` derives both from the lines after applying, so
/// sending them would be a value the applier throws away. They are still
/// editable and still reported under their path, which is the channel that
/// can actually carry them.
const _patchNumbers = {'total_usd', 'tax_usd'};
const _patchStrings = {'invoice_date', 'due_date', 'currency'};

/// A verdict, resolved against this invoice's drafts.
///
/// [line] and [own] are the two ways a field becomes editable and they are
/// exclusive. A verdict that lands on something the folded form already has an
/// input for — a line, the invoice number, the PO — is edited *through that
/// input's own controller*, so the lead panel and the folded form are two
/// views of one draft rather than two drafts of one fact. Everything else — a
/// total, a date, a path nothing here recognises — gets [own].
class _Field {
  final FieldVerdict verdict;
  final InvoiceFieldRef? ref;
  final InvoiceLineDraft? line;
  final TextEditingController? own;

  const _Field({required this.verdict, this.ref, this.line, this.own});

  DocumentRegion? get region => verdict.region;

  /// The key the box on the page is highlighted by. The region's own field
  /// rather than the verdict's path: the contract says they are the same
  /// vocabulary, and if a run ever disagrees with itself the two-way selection
  /// should still work rather than silently stop.
  String? get boxKey => verdict.region?.field;
}

// ---------------------------------------------------------------------------
// The screen the registry opens
// ---------------------------------------------------------------------------

/// Fetches the invoice and its artifact, then hands both to the panel.
///
/// Two fetches and two paints on purpose: the extraction is the decidable part
/// and the PDF is the slowest thing on the screen, so the fields appear as
/// soon as they can and the page fills in beside them.
class InvoiceReview extends StatefulWidget {
  final ApiClient api;
  final QueueTask task;

  const InvoiceReview({super.key, required this.api, required this.task});

  @override
  State<InvoiceReview> createState() => _InvoiceReviewState();
}

class _InvoiceReviewState extends State<InvoiceReview> {
  PayableInvoice? _invoice;
  Future<(StoredDocument, Uint8List?)>? _artifact;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final invoice = await widget.api.getPayable(widget.task.caseRef);
      if (!mounted) return;
      setState(() {
        _invoice = invoice;
        _artifact = invoice.documentId.isEmpty
            ? null
            : _fetchArtifact(widget.api, invoice.documentId);
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final invoice = _invoice;
    if (_error != null) {
      return _frame(ErrorNotice(message: _error!, onRetry: _load));
    }
    if (invoice == null) return _frame(const CumulusLoading());
    return InvoiceReviewPanel(
      task: widget.task,
      invoice: invoice,
      verdicts: widget.task.proposal?.fields,
      vendor: widget.task.proposal?.vendor,
      artifact: _artifact,
      onDecide: (outcome) => Navigator.of(context).pop(outcome),
    );
  }

  /// The chrome around a state that has no decision in it yet. The panel draws
  /// its own frame, because the footer's controls depend on its drafts.
  Widget _frame(Widget child) => ReviewFrame(
    eyebrow: 'Invoice review · ${widget.task.queue}',
    title: widget.task.title,
    subtitle: _subtitle,
    footer: const SizedBox.shrink(),
    child: child,
  );
}

const _subtitle =
    'The document as it was filed, with a box on each field the run put in '
    'doubt. Correct what was read wrong and the checks run again over the '
    'corrected values; approving accepts them as they stand.';

/// The invoice and its artifact, as one settled future — the same pairing the
/// document drawer loads, and for the same reason: the metadata is worth
/// showing even when the bytes will not come.
Future<(StoredDocument, Uint8List?)> _fetchArtifact(
  ApiClient api,
  String documentId,
) async {
  final document = await api.getDocument(documentId);
  if (!document.hasBytes) return (document, null);
  try {
    return (document, await api.documentContent(documentId));
  } on ApiException {
    return (document, null);
  }
}

// ---------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------

/// The screen proper, over data somebody else fetched.
///
/// Public and data-in so it can be pumped with a fixture: what it says when a
/// verdict has no box, when no field is in doubt, and when the run raised no
/// verdicts at all are three claims about the system, and those are worth
/// holding still.
class InvoiceReviewPanel extends StatefulWidget {
  final QueueTask task;
  final PayableInvoice invoice;

  /// Null when the run never computed verdicts — see [TaskProposal.fields].
  final List<FieldVerdict>? verdicts;

  /// The entity question, when the resolver could not settle it. Null is the
  /// normal case: the resolver named a vendor, or the version that raised this
  /// task never asked. Both mean there is nothing to pick, and the screen shows
  /// no picker rather than an empty one.
  final VendorQuestion? vendor;

  /// Null when the invoice names no artifact at all.
  final Future<(StoredDocument, Uint8List?)>? artifact;

  final ValueChanged<ReviewOutcome> onDecide;

  const InvoiceReviewPanel({
    super.key,
    required this.task,
    required this.invoice,
    required this.verdicts,
    required this.artifact,
    required this.onDecide,
    this.vendor,
  });

  @override
  State<InvoiceReviewPanel> createState() => _InvoiceReviewPanelState();
}

/// Below this the two panes stack. The dialog opens at 1080, so side by side
/// is the normal case and stacking is what a narrow window gets — with the
/// work above the page, because the fields are what the screen is for.
const double _twoPaneWidth = 880;

class _InvoiceReviewPanelState extends State<InvoiceReviewPanel> {
  final _note = TextEditingController();
  final _invoiceNumber = TextEditingController();
  final _poNumber = TextEditingController();
  final List<InvoiceLineDraft> _lines = [];
  final List<_Field> _fields = [];

  /// The fields to lead with, worst first, and otherwise in the order the run
  /// reported them — a run's own ordering is the document's ordering, and
  /// re-sorting two equally urgent fields would send a clerk up and down the
  /// page for nothing. Settled once with the drafts rather than recomputed per
  /// build: the order is a property of the task, and an order that could shift
  /// under a rebuild would move a card out from under a pointer.
  final List<_Field> _disputed = [];

  /// The page being looked at, one-based.
  int _page = 1;

  /// The field path of the card the clerk has selected, and the region field
  /// of the box it lights. Two keys rather than one because a verdict with no
  /// region is still selectable, and a box is still hoverable on a page whose
  /// card is scrolled out of sight.
  String? _focus;
  String? _active;

  bool _showRest = false;
  bool _showMatch = false;

  /// The reviewer's answer to the vendor question: a candidate's id, the empty
  /// string for "none of these", and null while it is unanswered.
  ///
  /// Three states, because "not yet" and "not any of them" are different
  /// answers and they produce different writes. Nothing is pre-selected: the
  /// run's whole reason for asking is that it could not choose, and a screen
  /// that pre-ticked the top candidate would collect a signature on a choice
  /// nobody made.
  String? _vendorChoice;

  @override
  void initState() {
    super.initState();
    _build();
  }

  @override
  void didUpdateWidget(InvoiceReviewPanel old) {
    super.didUpdateWidget(old);
    if (old.invoice.id != widget.invoice.id) {
      _disposeDrafts();
      _build();
    }
  }

  @override
  void dispose() {
    _disposeDrafts();
    _note.dispose();
    _invoiceNumber.dispose();
    _poNumber.dispose();
    super.dispose();
  }

  void _disposeDrafts() {
    for (final line in _lines) {
      line.dispose();
    }
    for (final field in _fields) {
      field.own?.dispose();
    }
  }

  /// The drafts, the resolved verdicts, and where the screen opens.
  void _build() {
    final invoice = widget.invoice;
    _invoiceNumber.text = invoice.invoiceNumber;
    _poNumber.text = invoice.poNumber;
    _lines
      ..clear()
      ..addAll(invoiceLineDrafts(invoice));
    _fields
      ..clear()
      ..addAll(_resolve(widget.verdicts ?? const []));
    _disputed
      ..clear()
      ..addAll(_worstFirst());

    // A run that reported nothing is a run nobody can trust to have looked, so
    // the whole extraction is open rather than folded behind a chevron.
    _showRest = widget.verdicts == null;
    _showMatch = !_matchClean;

    // The screen opens on the first thing that needs a person, and on the page
    // that thing is printed on.
    final first = _disputed.isEmpty ? null : _disputed.first;
    _focus = first?.verdict.path;
    _active = first?.boxKey;
    final page = first?.region?.page ?? 1;
    _page = page < 1 ? 1 : page;
  }

  List<_Field> _resolve(List<FieldVerdict> verdicts) {
    final resolved = <_Field>[];
    for (final verdict in verdicts) {
      final ref = resolveInvoicePath(verdict.path);
      final index = ref?.lineIndex;
      final line = index != null && index >= 0 && index < _lines.length
          ? _lines[index]
          : null;
      final shared = line != null ||
          ref?.leaf == 'invoice_number' ||
          ref?.leaf == 'po_number';
      resolved.add(_Field(
        verdict: verdict,
        ref: ref,
        line: line,
        // A field the folded form already has an input for is edited *through
        // that input's controller*, so one fact has one value on this screen
        // however many places it appears. Everything else gets its own — a
        // header field the form never had, or a path nothing here recognises.
        // Both are seeded from what the run read, so opening the screen is not
        // an edit.
        own: shared
            ? null
            : TextEditingController(
                text: _headerText(widget.invoice, ref?.leaf ?? '') ??
                    verdict.value,
              ),
      ));
    }
    return resolved;
  }

  // -------------------------------------------------------------------------
  // What the panel knows about itself
  // -------------------------------------------------------------------------

  /// The flagged fields in the order the clerk should meet them. Sorted by the
  /// index they arrived at as the tie-break, because `List.sort` is not stable
  /// and two equally urgent fields swapping places between builds is exactly
  /// the kind of nothing that costs somebody a click.
  List<_Field> _worstFirst() {
    final flagged = [
      for (final field in _fields)
        if (field.verdict.disputed) field,
    ];
    final indexed = [
      for (var i = 0; i < flagged.length; i++) (i, flagged[i]),
    ]..sort((a, b) {
      final bySeverity = _severity(a.$2.verdict.status)
          .compareTo(_severity(b.$2.verdict.status));
      return bySeverity != 0 ? bySeverity : a.$1.compareTo(b.$1);
    });
    return [for (final entry in indexed) entry.$2];
  }

  int get _settled => _fields.length - _disputed.length;

  bool get _matchClean {
    final verdict = widget.invoice.matchVerdict;
    return verdict.isEmpty ||
        verdict == 'matched' ||
        verdict == 'allowed-no-po';
  }

  /// Line indices and header leaves already asked about above, so the folded
  /// form shows them without a second input on the same controller.
  Set<int> get _liftedLines => {
    for (final field in _disputed)
      if (field.line != null) field.line!.ordinal - 1,
  };

  Set<String> get _liftedHeaders => {
    for (final field in _disputed)
      if (field.line == null && field.ref != null) field.ref!.leaf,
  };

  /// What the run read for a header field this screen holds a control for, in
  /// the invoice's own rendering — the baseline both the dirty test and the
  /// correction compare against. Null when [leaf] is not one of them.
  static String? _headerText(PayableInvoice invoice, String leaf) =>
      switch (leaf) {
        'total_usd' => invoice.totalUsd.toStringAsFixed(2),
        'subtotal_usd' => invoice.subtotalUsd.toStringAsFixed(2),
        'freight_usd' => invoice.freightUsd.toStringAsFixed(2),
        'tax_usd' => invoice.taxUsd.toStringAsFixed(2),
        'invoice_date' => invoice.invoiceDate,
        'due_date' => invoice.dueDate,
        'currency' => invoice.currency,
        _ => null,
      };

  /// The value a field with its own control started at.
  String _baseline(_Field field) =>
      _headerText(widget.invoice, field.ref?.leaf ?? '') ?? field.verdict.value;

  /// The vendor block's own answer, when it has one to send.
  ///
  /// `vendor_id` only, and only when the reviewer named a candidate — which is
  /// the whole contract. "None of these" travels as the *absence* of the key on
  /// a `corrected` verdict, which is what the applier reads as "mint the
  /// printed name as a new vendor". That asymmetry is why picking none makes
  /// the screen dirty: on an approval an absent id means "take the top
  /// candidate", so offering Approve there would post against a company the
  /// reviewer had just declined.
  Map<String, dynamic> get _vendorAnswer {
    final choice = _vendorChoice;
    if (widget.vendor == null || choice == null || choice.isEmpty) {
      return const {};
    }
    return {'vendor_id': choice};
  }

  bool get _dirty {
    if (_vendorChoice == '') return true;
    if (invoiceEdited(
      invoice: widget.invoice,
      invoiceNumber: _invoiceNumber.text,
      poNumber: _poNumber.text,
      lines: _lines,
    )) {
      return true;
    }
    for (final field in _fields) {
      final own = field.own;
      if (own != null && own.text.trim() != _baseline(field).trim()) {
        return true;
      }
    }
    return false;
  }

  /// The corrected value for one verdict's path, or null when this field did
  /// not change — or when the screen cannot honestly say that *this path* is
  /// what changed.
  ///
  /// The second case is a line whose leaf the resolver did not recognise: the
  /// clerk's edit to that line still travels in the patch, and inventing a
  /// value for a path whose meaning is unknown would put a wrong pair into the
  /// correction set, which is the one artifact here that is supposed to be
  /// clean enough to tune on.
  String? _correction(_Field field) {
    final own = field.own;
    if (own != null) {
      final value = own.text.trim();
      return value == _baseline(field).trim() ? null : value;
    }
    final invoice = widget.invoice;
    final line = field.line;
    if (line == null) {
      switch (field.ref?.leaf) {
        case 'invoice_number':
          final value = _invoiceNumber.text.trim();
          return value == invoice.invoiceNumber ? null : value;
        case 'po_number':
          final value = _poNumber.text.trim();
          return value == invoice.poNumber ? null : value;
      }
      return null;
    }
    final read = invoice.lines[line.ordinal - 1];
    switch (field.ref?.leaf) {
      case 'quantity':
        return (line.quantityValue - read.quantity).abs() > 0.0001
            ? numberText(line.quantityValue)
            : null;
      case 'unit_price_usd':
        return (line.unitPriceValue - read.unitPriceUsd).abs() > 0.004
            ? line.unitPriceValue.toStringAsFixed(2)
            : null;
      // The amount is not typed, it is `qty × price` — so this reports a
      // change whenever the two numbers no longer multiply to what the
      // extractor printed. That includes the case where the clerk touched
      // nothing on this line and the arithmetic was already wrong, and it
      // should: applying is what makes the recomputed amount the stored one,
      // so 1,844.00 → 1,944.00 is the pair the run is about to write.
      case 'amount_usd':
        return (line.amount - read.amountUsd).abs() > 0.004
            ? line.amount.toStringAsFixed(2)
            : null;
    }
    return null;
  }

  /// The patch every version's applier reads, plus the path-keyed corrections.
  ///
  /// `fields` carries only paths the *run itself* named, and only those whose
  /// value actually moved. Two rules, both about the correction set: a key the
  /// screen invented could not be matched against the raw extraction, and a
  /// pair whose two halves are equal is not a correction.
  Map<String, dynamic> _payload() {
    final patch = invoicePatch(
      invoiceNumber: _invoiceNumber.text,
      poNumber: _poNumber.text,
      lines: _lines,
    );
    final corrections = <String, String>{};
    for (final field in _fields) {
      final value = _correction(field);
      if (value == null) continue;
      corrections[field.verdict.path] = value;
      // A header field the applier takes by name goes into the patch too, so
      // a corrected total lands whether the flow applies the patch or reads
      // the path-keyed corrections. An unparseable number is left out rather
      // than sent as zero — the path-keyed value still carries what was typed.
      final leaf = field.ref?.leaf ?? '';
      if (_patchNumbers.contains(leaf)) {
        final number = double.tryParse(value.replaceAll(',', ''));
        if (number != null) patch[leaf] = number;
      } else if (_patchStrings.contains(leaf)) {
        patch[leaf] = value;
      }
    }
    if (corrections.isNotEmpty) patch['fields'] = corrections;
    patch.addAll(_vendorAnswer);
    return patch;
  }

  void _decide(String verdict) => widget.onDecide(ReviewOutcome(
    verdict: verdict,
    note: _note.text.trim(),
    payload: switch (verdict) {
      'corrected' => _payload(),
      // The vendor answer is not a corrected field — it is the answer to a
      // question the run asked — and the applier reads it on an approval too,
      // which is what lets "it is Acme Industrial" and "post this" be one
      // press instead of a correction round.
      'approved' => _vendorAnswer,
      // A rejection denies the invoice. Naming a vendor on the way out would
      // assert an identity for a document nobody is paying.
      _ => const {},
    },
  ));

  /// One gesture, both selections: the card and its box on the page.
  void _select(_Field field) => setState(() {
    if (_focus == field.verdict.path) {
      _focus = null;
      _active = null;
      return;
    }
    _focus = field.verdict.path;
    _active = field.boxKey;
    final page = field.region?.page ?? _page;
    _page = page < 1 ? 1 : page;
  });

  /// The page asking for a field. Null — the pointer leaving a box — is
  /// ignored on purpose: a clerk who moves off a box to type into its input
  /// has not stopped working on that field.
  void _selectBox(String? boxField) {
    if (boxField == null) return;
    setState(() {
      _active = boxField;
      for (final field in _disputed) {
        if (field.boxKey == boxField) {
          _focus = field.verdict.path;
          return;
        }
      }
    });
  }

  // -------------------------------------------------------------------------
  // Build
  // -------------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return ReviewFrame(
      eyebrow: 'Invoice review · ${widget.task.queue}',
      title: widget.task.title,
      subtitle: _subtitle,
      footer: DecisionBar(
        note: _note,
        dirty: _dirty,
        correctLabel: 'Apply and re-check',
        onApprove: () => _decide('approved'),
        onReject: () => _decide('rejected'),
        onCorrect: () => _decide('corrected'),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          WhyElevated(
            reasons: [
              if (widget.invoice.verdictReason.isNotEmpty)
                widget.invoice.verdictReason,
              for (final finding in widget.invoice.anomalyFindings)
                '${finding.kind}: ${finding.detail}',
            ],
          ),
          _identity(),
          const SizedBox(height: 20),
          LayoutBuilder(
            builder: (context, constraints) {
              if (constraints.maxWidth < _twoPaneWidth) {
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    ..._workPane(),
                    const SizedBox(height: 24),
                    ..._pagePane(),
                  ],
                );
              }
              // The question first, the evidence beside it. A document viewer
              // with a form bolted to its right is the layout of a tool for
              // reading invoices; this is a tool for answering three
              // questions about one, and the page is what you check them
              // against.
              return Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: _workPane(),
                    ),
                  ),
                  const SizedBox(width: 26),
                  SizedBox(
                    width: 340,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: _pagePane(),
                    ),
                  ),
                ],
              );
            },
          ),
        ],
      ),
    );
  }

  Widget _identity() => Wrap(
    spacing: 10,
    runSpacing: 10,
    children: [
      MetaPill(
        '${widget.invoice.vendorName} · ${widget.invoice.vendorResolution}',
        tone: CumulusTone.teal,
      ),
      if (widget.invoice.matchVerdict.isNotEmpty)
        MetaPill(
          'match: ${widget.invoice.matchVerdict}',
          tone: _matchClean ? CumulusTone.success : CumulusTone.warn,
        ),
      MetaPill(widget.invoice.policyRef),
      // Whose invoice this is, when the Task says. Under v9 the review is
      // routed back to the person who filed it, so this is usually the reader
      // — and the case where it is not is the one worth seeing.
      if (widget.task.subjectEmail.isNotEmpty)
        MetaPill('about ${widget.task.subjectEmail}'),
    ],
  );

  // -------------------------------------------------------------------------
  // The page pane
  // -------------------------------------------------------------------------

  List<Widget> _pagePane() {
    final artifact = widget.artifact;
    if (artifact == null) {
      return [
        FieldLabel('The invoice', hint: 'no artifact'),
        _muted(
          'This invoice names no document. Nothing was filed for the run to '
          'read, so there is no page to draw and no box to draw on it — the '
          'values beside are the whole of what is known.',
        ),
      ];
    }
    return [
      FutureBuilder<(StoredDocument, Uint8List?)>(
        future: artifact,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                FieldLabel('The invoice', hint: 'the artifact did not load'),
                _muted(
                  'The extraction is still decidable: what the run read is '
                  'beside, and correcting it does not need the page. '
                  '${snapshot.error}',
                ),
              ],
            );
          }
          if (!snapshot.hasData) {
            return const Padding(
              padding: EdgeInsets.symmetric(vertical: 40),
              child: Center(child: CircularProgressIndicator()),
            );
          }
          final (document, bytes) = snapshot.data!;
          final pages = document.pages < 1 ? 1 : document.pages;
          final page = _page.clamp(1, pages);
          final boxes = regionsOnPage(_boxes, page);
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              FieldLabel(
                'The invoice',
                hint: document.filename.isEmpty
                    ? document.id
                    : document.filename,
              ),
              if (pages > 1) ...[
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: [
                    for (var n = 1; n <= pages; n++)
                      LensChip(
                        label: 'PAGE $n',
                        tone: CumulusTone.accent,
                        selected: n == page,
                        onTap: () => setState(() => _page = n),
                      ),
                  ],
                ),
                const SizedBox(height: 12),
              ],
              DocumentPageView(
                document: document,
                bytes: bytes,
                page: page,
                regions: boxes,
                active: _active,
                onActive: _selectBox,
                maxWidth: 340,
              ),
              const SizedBox(height: 10),
              Text(_boxNote(boxes.length), style: CumulusText.meta),
            ],
          );
        },
      ),
    ];
  }

  /// Only the disputed fields are boxed. The document carries the extractor's
  /// own regions too and this deliberately does not draw them: thirty boxes on
  /// a page point at nothing, and the whole point of the locate step running
  /// on three fields is that three boxes mean something.
  List<DocumentRegion> get _boxes => [
    for (final field in _disputed)
      if (field.region != null) field.region!,
  ];

  String _boxNote(int here) {
    if (widget.verdicts == null) {
      return 'This run raised no field verdicts, so nothing is boxed. The page '
          'is the artifact it read.';
    }
    if (_disputed.isEmpty) {
      return 'Nothing is boxed: no field was in doubt, so no field was located '
          '— the second model call is skipped entirely on a clean invoice.';
    }
    final boxed = _boxes.length;
    if (boxed == 0) {
      return 'None of the ${_disputed.length} fields in doubt came back with a '
          'box. They are still editable beside; a missing box is the locator '
          'saying it could not find the value, not the value being fine.';
    }
    return '$here of $boxed box(es) on this page. A box is where the run '
        'looked, not the answer — the value beside it is what you are '
        'correcting.';
  }

  // -------------------------------------------------------------------------
  // The work pane
  // -------------------------------------------------------------------------

  List<Widget> _workPane() {
    final match = _matchSection();
    return [
      _lede(),
      ..._vendorSection(),
      if (_disputed.isNotEmpty) ...[
        const SizedBox(height: 14),
        for (var i = 0; i < _disputed.length; i++) ...[
          if (i > 0) const SizedBox(height: 10),
          _VerdictCard(
            field: _disputed[i],
            selected: _focus == _disputed[i].verdict.path,
            autofocus: i == 0,
            invoiceNumber: _invoiceNumber,
            poNumber: _poNumber,
            onSelect: () => _select(_disputed[i]),
            onChanged: () => setState(() {}),
          ),
        ],
      ],
      const SizedBox(height: 22),
      // A run that never got as far as a match has nothing to fold away, and
      // an empty chevron would say it did.
      if (match.isNotEmpty) ...[...match, const SizedBox(height: 18)],
      ..._restSection(),
    ];
  }

  /// The entity question, folded in — nothing at all when the resolver settled
  /// it, which is the normal case.
  ///
  /// Compact on purpose: it is one question with a shortlist, sitting above
  /// three questions with inputs, and it should not look like the point of the
  /// page. It is above them rather than below because the flow itself puts the
  /// unsettled vendor first among its reasons, and because posting against the
  /// wrong company is the one mistake here that no later correction recovers.
  List<Widget> _vendorSection() {
    final vendor = widget.vendor;
    if (vendor == null) return const [];
    final printed = vendor.printed.isEmpty ? 'no clear party' : vendor.printed;
    return [
      const SizedBox(height: 14),
      Container(
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
        decoration: BoxDecoration(
          color: CumulusColors.surface,
          borderRadius: BorderRadius.circular(CumulusRadius.control),
          border: Border.all(color: CumulusColors.borderStrong),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            FieldLabel('Which vendor issued this', hint: 'printed: “$printed”'),
            Text(
              vendor.candidates.isEmpty
                  ? 'Nothing in the master came close to that name, so the only '
                      'honest answer is that this is a company the graph does '
                      'not have yet. Nothing has been asserted either way.'
                  : 'The resolver matched that name to more than one entry in '
                      'the master and none of them well enough to assert on its '
                      'own. Nothing has been asserted yet — naming one records '
                      'the printed name as an alias of that company, sourced '
                      'to you.',
              style: CumulusText.body,
            ),
            const SizedBox(height: 10),
            for (final candidate in vendor.candidates)
              _VendorChoice(
                label: candidate.name.isEmpty ? candidate.id : candidate.name,
                meta: '${(candidate.score * 100).round()}% · ${candidate.id}',
                selected: _vendorChoice == candidate.id,
                onTap: () => setState(() => _vendorChoice = candidate.id),
              ),
            _VendorChoice(
              label: 'None of these — “$printed” is a new vendor',
              meta: 'onboards it, and the checks run again',
              selected: _vendorChoice == '',
              onTap: () => setState(() => _vendorChoice = ''),
            ),
            // The trap this closes: the applier reads an *absent* id on an
            // approval as "take the top candidate", so a clerk who answers
            // nothing and presses Approve has picked one without meaning to.
            // Said out loud rather than defended by disabling the button —
            // accepting the resolver's best guess is a legitimate thing to
            // mean, and it should be legible before it happens.
            if (_vendorChoice == null && vendor.candidates.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(
                'Approving without naming one accepts the top candidate: '
                '${vendor.candidates.first.name.isEmpty
                    ? vendor.candidates.first.id
                    : vendor.candidates.first.name}',
                style: CumulusText.meta,
              ),
            ],
          ],
        ),
      ),
    ];
  }

  /// What the screen says it is asking, before it asks it.
  Widget _lede() {
    if (widget.verdicts == null) {
      return _Lede(
        tone: CumulusTone.neutral,
        title: 'This run reported no field verdicts',
        body: 'The version that raised this task did not say which fields it '
            'was unsure of, so the screen cannot narrow the question — that '
            'is different from every field being fine. The whole extraction '
            'is below, open, and correcting any of it re-runs the checks.',
      );
    }
    if (_disputed.isEmpty) {
      // The vendor is a question about the invoice even when every number on
      // it read cleanly, so "nothing needed a person" would be false and
      // "approving is the whole job" would be worse than false.
      if (widget.vendor != null) {
        return const _Lede(
          tone: CumulusTone.warn,
          title: 'No field is in doubt — the vendor is',
          body: 'Every value the run read came back confident and the '
              'arithmetic checks agree, so the reading is not the question. '
              'Which company issued this is: the name printed on the page did '
              'not resolve to one entry in the master, and nothing has been '
              'asserted about it. Answer that above and the invoice is done.',
        );
      }
      return _Lede(
        tone: CumulusTone.success,
        title: 'Nothing needed a person',
        body: _matchClean
            ? 'Every field the run read came back confident and the arithmetic '
                'checks agree, so no field was raised for review. This is in '
                'front of you because the governed posting policy sends every '
                'invoice to a clerk — not because anything is in doubt. '
                'Approving is the whole job.'
            : 'Every field the run read came back confident and the arithmetic '
                'checks agree — the reading is not the problem. The three-way '
                'match is: it came back “${widget.invoice.matchVerdict}”, and '
                'the pairings are below.',
      );
    }
    final settled = _settled;
    return _Lede(
      tone: CumulusTone.warn,
      title: _disputed.length == 1
          ? 'One field needs you'
          : '${_disputed.length} fields need you',
      body: 'These are the values the run could not settle on its own, in the '
          'check\'s own words, boxed on the page beside. '
          '${settled > 0 ? "The other $settled field(s) it read came back "
              "clean and are folded below." : "Everything else it read is "
              "folded below."}'
          '${widget.vendor == null ? "" : " The vendor is unsettled too, and "
              "the candidates are above."}',
    );
  }

  List<Widget> _matchSection() {
    final invoice = widget.invoice;
    if (invoice.matchPairings.isEmpty && invoice.anomalyBasis.isEmpty) {
      return const [];
    }
    return [
      _Fold(
        label: 'Three-way match',
        caption: invoice.matchSummary.isNotEmpty
            ? invoice.matchSummary
            : '${invoice.matchPairings.length} pairing(s)',
        open: _showMatch,
        onToggle: () => setState(() => _showMatch = !_showMatch),
        children: [
          for (final pairing in invoice.matchPairings)
            Padding(
              padding: const EdgeInsets.only(bottom: 5),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 30,
                    child: Text('${pairing.invoiceOrdinal}.',
                        style: CumulusText.meta),
                  ),
                  SizedBox(
                    width: 140,
                    child: StatusBadge(
                      pairing.verdict,
                      tone: pairing.clean
                          ? CumulusTone.success
                          : CumulusTone.warn,
                      dense: true,
                    ),
                  ),
                  Expanded(child: Text(pairing.reason, style: CumulusText.body)),
                ],
              ),
            ),
          if (invoice.anomalyBasis.isNotEmpty) ...[
            const SizedBox(height: 12),
            FieldLabel('Anomaly basis', hint: invoice.anomalyBasis),
          ],
        ],
      ),
    ];
  }

  /// Everything the run was sure about. Folded, because a clerk who wants to
  /// check it should be able to and a clerk who does not should never have to
  /// scroll past it to reach the decision.
  List<Widget> _restSection() {
    final invoice = widget.invoice;
    final total = _lines.fold<double>(0, (sum, line) => sum + line.amount);
    final lifted = _liftedLines;
    final liftedHeaders = _liftedHeaders;
    return [
      _Fold(
        label: 'The rest of the extraction',
        caption: widget.verdicts == null
            ? 'the whole reading — nothing was singled out'
            : '${_lines.length} line(s), none of them flagged',
        open: _showRest,
        onToggle: () => setState(() => _showRest = !_showRest),
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              SizedBox(
                width: 220,
                child: liftedHeaders.contains('invoice_number')
                    ? _asked('Invoice number', _invoiceNumber.text)
                    : TextField(
                        controller: _invoiceNumber,
                        onChanged: (_) => setState(() {}),
                        decoration: const InputDecoration(
                          labelText: 'Invoice number',
                          isDense: true,
                        ),
                      ),
              ),
              const SizedBox(width: 12),
              SizedBox(
                width: 200,
                child: liftedHeaders.contains('po_number')
                    ? _asked('PO number', _poNumber.text)
                    : TextField(
                        controller: _poNumber,
                        onChanged: (_) => setState(() {}),
                        decoration: const InputDecoration(
                          labelText: 'PO number',
                          hintText: 'blank if the invoice carries none',
                          isDense: true,
                        ),
                      ),
              ),
            ],
          ),
          const SizedBox(height: 18),
          const FieldLabel(
            'Lines',
            hint: 'quantity × unit price; the amount is recomputed so the '
                'arithmetic cannot drift',
          ),
          for (final line in _lines)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: lifted.contains(line.ordinal - 1)
                  ? _askedLine(line)
                  : _editableLine(line),
            ),
          const SizedBox(height: 6),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              Text('Lines total', style: CumulusText.statLabel),
              const SizedBox(width: 14),
              Text('\$${total.toStringAsFixed(2)}',
                  style: CumulusText.statValue),
              const SizedBox(width: 20),
              Text(
                'invoice reads \$${invoice.totalUsd.toStringAsFixed(2)}',
                style: CumulusText.meta,
              ),
            ],
          ),
        ],
      ),
    ];
  }

  Widget _editableLine(InvoiceLineDraft line) => Row(
    crossAxisAlignment: CrossAxisAlignment.end,
    children: [
      SizedBox(
        width: 30,
        child: Text('${line.ordinal}.', style: CumulusText.meta),
      ),
      Expanded(child: Text(line.description, style: CumulusText.body)),
      if (line.kind != 'goods') ...[
        StatusBadge(line.kind, dense: true),
        const SizedBox(width: 8),
      ],
      _LineFields(line: line, onChanged: () => setState(() {})),
    ],
  );

  /// A line already asked about above. Shown, never a second input: one draft,
  /// one editable place, so a clerk cannot type two answers to one question.
  Widget _askedLine(InvoiceLineDraft line) => Row(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      SizedBox(
        width: 30,
        child: Text('${line.ordinal}.', style: CumulusText.meta),
      ),
      Expanded(child: Text(line.description, style: CumulusText.body)),
      const SizedBox(width: 8),
      StatusBadge('asked above', tone: CumulusTone.warn, dense: true),
      const SizedBox(width: 12),
      SizedBox(
        width: 92,
        child: Text(
          '\$${line.amount.toStringAsFixed(2)}',
          textAlign: TextAlign.right,
          style: CumulusText.body.copyWith(fontWeight: FontWeight.w600),
        ),
      ),
    ],
  );

  Widget _asked(String label, String value) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(label.toUpperCase(), style: CumulusText.statLabel),
      const SizedBox(height: 4),
      Row(
        children: [
          Flexible(
            child: Text(value.isEmpty ? '—' : value, style: CumulusText.mono),
          ),
          const SizedBox(width: 8),
          const StatusBadge('asked above',
              tone: CumulusTone.warn, dense: true),
        ],
      ),
    ],
  );

  Widget _muted(String message) => Container(
    width: double.infinity,
    padding: const EdgeInsets.all(14),
    decoration: BoxDecoration(
      color: CumulusColors.surfaceMuted,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
    ),
    child: Text(message, style: CumulusText.body),
  );
}

int _severity(String status) => switch (status) {
  'missing' => 0,
  'inconsistent' => 1,
  'low_confidence' => 2,
  _ => 3,
};

(String, CumulusTone) _statusPill(String status) => switch (status) {
  'missing' => ('not found', CumulusTone.danger),
  'inconsistent' => ('does not add up', CumulusTone.danger),
  'low_confidence' => ('unsure', CumulusTone.warn),
  'ok' => ('clean', CumulusTone.success),
  '' => ('status not stated', CumulusTone.neutral),
  _ => (status, CumulusTone.neutral),
};

// ---------------------------------------------------------------------------
// The pieces
// ---------------------------------------------------------------------------

/// What the screen is asking, in one sentence, before it asks it.
class _Lede extends StatelessWidget {
  final CumulusTone tone;
  final String title;
  final String body;

  const _Lede({required this.tone, required this.title, required this.body});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(16, 13, 16, 14),
      decoration: BoxDecoration(
        color: tone.background,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: CumulusText.rowTitle.copyWith(color: tone.foreground),
          ),
          const SizedBox(height: 6),
          Text(body, style: CumulusText.body),
        ],
      ),
    );
  }
}

/// One answer to the vendor question: a name, how well it scored, and whether
/// it is the one picked.
///
/// A row rather than a `DropdownButton` because the scores are the reason a
/// person is being asked at all — 0.88 against 0.86 is a different call from
/// 0.88 against 0.31 — and a dropdown shows one of them at a time.
class _VendorChoice extends StatelessWidget {
  final String label;
  final String meta;
  final bool selected;
  final VoidCallback onTap;

  const _VendorChoice({
    required this.label,
    required this.meta,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Material(
      color: selected ? CumulusColors.accentSoft : Colors.transparent,
      borderRadius: BorderRadius.circular(6),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(6),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 7),
          child: Row(
            children: [
              Icon(
                selected
                    ? Icons.radio_button_checked
                    : Icons.radio_button_unchecked,
                size: 15,
                color: selected
                    ? CumulusColors.accent
                    : CumulusColors.inkMuted,
              ),
              const SizedBox(width: 9),
              Expanded(child: Text(label, style: CumulusText.body)),
              const SizedBox(width: 10),
              Text(meta, style: CumulusText.meta),
            ],
          ),
        ),
      ),
    );
  }
}

/// One field in doubt: what was read, why it is in doubt, and the input.
///
/// A card rather than a row in a table, because the check's sentence is the
/// most useful thing on the screen and a table has nowhere to put it. It is
/// shown verbatim — the run wrote it knowing what it looked at, and a screen
/// paraphrasing "line 3: 4 × $486.00 is $1,944.00, not $1,844.00" into
/// "amount mismatch" has thrown away the answer.
class _VerdictCard extends StatelessWidget {
  final _Field field;
  final bool selected;
  final bool autofocus;
  final TextEditingController invoiceNumber;
  final TextEditingController poNumber;
  final VoidCallback onSelect;
  final VoidCallback onChanged;

  const _VerdictCard({
    required this.field,
    required this.selected,
    required this.autofocus,
    required this.invoiceNumber,
    required this.poNumber,
    required this.onSelect,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    final verdict = field.verdict;
    final (label, tone) = _statusPill(verdict.status);
    return Material(
      color: selected ? CumulusColors.accentSoft : CumulusColors.surface,
      borderRadius: BorderRadius.circular(CumulusRadius.control),
      child: InkWell(
        onTap: onSelect,
        borderRadius: BorderRadius.circular(CumulusRadius.control),
        child: Container(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(CumulusRadius.control),
            border: Border.all(
              color: selected
                  ? CumulusColors.accent
                  : CumulusColors.borderStrong,
              width: selected ? 1.6 : 1,
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(
                      verdict.displayLabel,
                      style: CumulusText.rowTitle,
                    ),
                  ),
                  const SizedBox(width: 10),
                  StatusBadge(label, tone: tone, dense: true),
                ],
              ),
              const SizedBox(height: 6),
              Text(verdict.reason, style: CumulusText.body),
              const SizedBox(height: 10),
              _input(),
              const SizedBox(height: 8),
              Wrap(
                spacing: 12,
                runSpacing: 4,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Text('read “${verdict.value.isEmpty ? "nothing" :
                      verdict.value}”', style: CumulusText.meta),
                  Text(
                    verdict.confidence <= 0
                        ? 'no confidence reported'
                        : '${(verdict.confidence * 100).round()}% confident',
                    style: CumulusText.meta,
                  ),
                  Text(verdict.path, style: CumulusText.mono),
                  Text(
                    field.region == null
                        ? 'no box on the page'
                        : 'boxed on page ${field.region!.page < 1 ? 1 :
                            field.region!.page}',
                    style: CumulusText.meta.copyWith(
                      color: field.region == null
                          ? CumulusColors.inkMuted
                          : CumulusColors.accentInk,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// The control, chosen by what the path resolved to.
  ///
  /// A doubt anywhere on a line opens the *whole* line, not just the value the
  /// path names. That is the honest ask: `check-arithmetic` reports that
  /// quantity × unit price does not equal the amount, and which of the three
  /// was misread is exactly the thing it cannot know.
  Widget _input() {
    final line = field.line;
    if (line != null) {
      return Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: Text(
              '${line.sku.isEmpty ? "line ${line.ordinal}" : line.sku} · '
              '${line.description}',
              style: CumulusText.meta,
            ),
          ),
          _LineFields(
            line: line,
            autofocus: autofocus,
            onChanged: onChanged,
            onTap: onSelect,
          ),
        ],
      );
    }
    final controller = switch (field.ref?.leaf) {
      'invoice_number' => invoiceNumber,
      'po_number' => poNumber,
      _ => field.own!,
    };
    return TextField(
      controller: controller,
      autofocus: autofocus,
      onChanged: (_) => onChanged(),
      onTap: onSelect,
      // Not the field's name a second time — the card's own title said that a
      // line above. What the label is for is saying what typing here means.
      decoration: const InputDecoration(
        labelText: 'As it should read',
        isDense: true,
      ),
    );
  }
}

/// Quantity, unit price, and the amount the two of them make.
///
/// One widget for both places a line is editable — inside a verdict card and
/// inside the folded extraction — because they are the same three controls
/// over the same draft, and two copies of them is how the lead panel and the
/// context quietly come to offer different arithmetic.
class _LineFields extends StatelessWidget {
  final InvoiceLineDraft line;
  final bool autofocus;
  final VoidCallback onChanged;
  final VoidCallback? onTap;

  const _LineFields({
    required this.line,
    required this.onChanged,
    this.autofocus = false,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        SizedBox(
          width: 84,
          child: TextField(
            controller: line.quantity,
            autofocus: autofocus,
            onChanged: (_) => onChanged(),
            onTap: onTap,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: 'Qty', isDense: true),
          ),
        ),
        const SizedBox(width: 10),
        SizedBox(
          width: 108,
          child: TextField(
            controller: line.unitPrice,
            onChanged: (_) => onChanged(),
            onTap: onTap,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(
              labelText: 'Unit price',
              prefixText: '\$',
              isDense: true,
            ),
          ),
        ),
        const SizedBox(width: 12),
        SizedBox(
          width: 92,
          child: Text(
            '\$${line.amount.toStringAsFixed(2)}',
            textAlign: TextAlign.right,
            style: CumulusText.body.copyWith(fontWeight: FontWeight.w600),
          ),
        ),
      ],
    );
  }
}

/// A disclosure, in the console's own idiom: a chevron, a small-caps label,
/// and a caption that says what is behind it while it is shut.
class _Fold extends StatelessWidget {
  final String label;
  final String caption;
  final bool open;
  final VoidCallback onToggle;
  final List<Widget> children;

  const _Fold({
    required this.label,
    required this.caption,
    required this.open,
    required this.onToggle,
    required this.children,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          onTap: onToggle,
          borderRadius: BorderRadius.circular(6),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 3, horizontal: 2),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  open ? Icons.expand_less : Icons.expand_more,
                  size: 15,
                  color: CumulusColors.inkMuted,
                ),
                const SizedBox(width: 4),
                Text(label.toUpperCase(), style: CumulusText.statLabel),
                const SizedBox(width: 8),
                Flexible(
                  child: Text(
                    caption,
                    style: CumulusText.statCaption,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ),
        ),
        if (open) ...[
          const SizedBox(height: 10),
          ...children,
        ],
      ],
    );
  }
}
