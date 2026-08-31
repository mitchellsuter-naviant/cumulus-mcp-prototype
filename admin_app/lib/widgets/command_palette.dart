import 'dart:async';

import 'package:flutter/material.dart';

import '../models/asset.dart';
import '../models/identity.dart';
import '../models/session.dart';
import '../models/team.dart';
import '../services/api_client.dart';
import '../theme/cumulus_theme.dart';
import '../theme/domain_tones.dart';
import 'answer_view.dart';
import 'ui_kit.dart';

/// The command palette: search, navigation, and question answering in one
/// overlay owned by the shell.
///
/// The console previously routed the top-bar query into whichever page happened
/// to be mounted, which meant a search typed while an asset was open ran, stored
/// its results, and rendered nothing — the editor was drawn over them. Results
/// are page state no longer. They live here, above every page, so searching
/// from anywhere behaves the same way and never disturbs what you were doing.
///
/// Four lanes, in descending order of how fast they can answer:
///
///   1. **In this document** — client-side, zero latency, only when an asset is
///      open. ⌘K doubles as in-document find.
///   2. **Jump to** — fuzzy match over an already-fetched catalog of assets,
///      teams and identities. Navigation should never wait on a round trip.
///   3. **Find** — the server's hybrid BM25 + vector search, debounced.
///   4. **Ask** — the answering endpoint. Deliberately explicit: a planner runs
///      several model turns, so it must never fire on a keystroke.

// ---- Intents ----------------------------------------------------------------

/// What the palette asks the application to do. The palette knows how to find
/// things; it does not know how this console navigates.
sealed class PaletteIntent {
  const PaletteIntent();
}

class OpenAssetIntent extends PaletteIntent {
  final String assetId;
  const OpenAssetIntent(this.assetId);
}

class OpenTeamIntent extends PaletteIntent {
  final String slug;
  const OpenTeamIntent(this.slug);
}

class OpenIdentityIntent extends PaletteIntent {
  final String uid;
  const OpenIdentityIntent(this.uid);
}

class CreateAssetIntent extends PaletteIntent {
  const CreateAssetIntent();
}

class OpenSectionIntent extends PaletteIntent {
  final int index;
  const OpenSectionIntent(this.index);
}

// ---- The shell's navigation vocabulary --------------------------------------
//
// One definition, read by both the rail and the palette. It lived in two
// places and drifted: three of the palette's copies still pointed where those
// sections used to be, so "Knowledge layer" opened the identity workbench and
// "Flows" opened System state. It sits here rather than in `home_shell.dart`
// because `OpenSectionIntent` above is the type that carries an index, and the
// shell already imports this file — the other direction would be a cycle.

const int homeIndex = 0;
const int calendarIndex = 1;
const int tasksIndex = 2;
const int expenseWorkspaceIndex = 3;
const int payablesWorkspaceIndex = 4;
const int identityWorkspaceIndex = 5;
const int systemStateIndex = 6;
const int knowledgeIndex = 7;
const int flowsIndex = 8;
const int identitiesIndex = 9;
const int employeeHubIndex = 10;

/// The queues the two business workspaces belong to. Named because the rail,
/// the palette and the shell's unmounting rule all have to agree, and three
/// copies of a string is how they stop agreeing.
const String expenseQueue = 'expense-review';
const String payablesQueue = 'ap-clerk';

/// Whether this session reaches a workspace whose work sits in one queue.
///
/// The rule the identity workbench already draws, applied to the two
/// workspaces that had none. A Workspace is a Seeing Space — you watch a
/// workstream without being assigned anything in it — but *whose* workstream
/// is still a question, and both of these carry more than their rows: the AP
/// one opens the vendor master, every open purchase order, the posting policy
/// and a control that runs the flow. An own-scoped persona bound to neither
/// desk was being offered all of it.
///
/// Reading past your own work is the other way in, so Naviant Support and the
/// customer's admin keep both. A null session means the answer is still in
/// flight, and offering a stop that turns out not to be yours is the better of
/// the two errors — the route behind it refuses in a sentence.
bool ownsWorkspace(Session? session, String queue) =>
    session == null || session.scope != 'own' || session.queues.contains(queue);

/// Escape hatch to the full-page result list when the dropdown's ten aren't
/// enough.
class ShowAllResultsIntent extends PaletteIntent {
  final String query;
  final SearchFilters filters;
  const ShowAllResultsIntent(this.query, this.filters);
}

// ---- Document scope ---------------------------------------------------------

/// The document currently open in the editor, published to the shell.
///
/// Held as callbacks rather than as a snapshot because the content changes
/// under us while someone types — an in-document find has to search what is on
/// screen now, not what was loaded from the server.
class DocumentScope {
  final String assetId;
  final String title;

  /// The editor's live content.
  final String Function() readContent;

  /// Select a range in the editor and scroll it into view.
  final void Function(int start, int end) reveal;

  /// Whether leaving would discard work. Consulted before navigating away.
  final bool Function() isDirty;

  const DocumentScope({
    required this.assetId,
    required this.title,
    required this.readContent,
    required this.reveal,
    required this.isDirty,
  });
}

// ---- Query parsing ----------------------------------------------------------

const _filterAliases = <String, String>{
  'owner': 'owner',
  'team': 'owner',
  'authority': 'authority',
  'level': 'authority',
  'status': 'status',
  'tag': 'tag',
  'path': 'path',
  'in': 'path',
};

const _authorityValues = ['policy', 'standard', 'guideline', 'how-to', 'note'];
const _statusValues = ['draft', 'published', 'deprecated'];

/// Splits `owner:hr expense approval` into its filters and its free text.
///
/// Filters are a separate plane from the query: the server applies them before
/// ranking, so `owner:hr` narrows what is scored rather than what survives.
/// Unknown keys are left in the query text — `http://x` should not silently
/// become a filter named `http`.
({String query, SearchFilters filters}) parseSearchInput(String raw) {
  final words = raw.split(RegExp(r'\s+')).where((w) => w.isNotEmpty);
  final rest = <String>[];
  String? owner, authority, status, tag, path;

  for (final word in words) {
    final colon = word.indexOf(':');
    if (colon <= 0 || colon == word.length - 1) {
      rest.add(word);
      continue;
    }
    final key = _filterAliases[word.substring(0, colon).toLowerCase()];
    final value = word.substring(colon + 1);
    switch (key) {
      case 'owner':
        owner = value.toLowerCase();
      case 'authority':
        // A misspelled level would otherwise filter everything away and read
        // as "no results" rather than as a typo.
        if (_authorityValues.contains(value.toLowerCase())) {
          authority = value.toLowerCase();
        } else {
          rest.add(word);
        }
      case 'status':
        if (_statusValues.contains(value.toLowerCase())) {
          status = value.toLowerCase();
        } else {
          rest.add(word);
        }
      case 'tag':
        tag = value;
      case 'path':
        path = value;
      default:
        rest.add(word);
    }
  }

  return (
    query: rest.join(' '),
    filters: SearchFilters(
      owner: owner,
      authorityLevel: authority,
      status: status,
      tag: tag,
      pathPrefix: path,
    ),
  );
}

/// Openers that mean someone wants an answer, not a document.
const _questionOpeners = <String>{
  'who', 'what', 'which', 'where', 'when', 'why', 'how', 'does', 'do', 'did',
  'is', 'are', 'can', 'should', 'tell', 'list', 'show', 'explain', 'summarize',
  'summarise', 'count', 'am', 'has', 'have', 'will', 'would',
};

/// Whether to put the Ask lane above the results rather than below them.
///
/// Only reorders the palette; the ask itself is always explicit. Guessing wrong
/// costs a glance, never a model call.
bool looksLikeQuestion(String query) {
  final trimmed = query.trim();
  if (trimmed.endsWith('?')) return true;
  final words = trimmed.split(RegExp(r'\s+'));
  // Two words is a lookup ("leave policy"); a sentence is a question.
  if (words.length < 3) return false;
  return _questionOpeners.contains(words.first.toLowerCase());
}

/// Ranks a *title* against typed text. Null means no match.
///
/// Three tiers: a prefix beats a word-boundary substring beats scattered
/// letters. The subsequence tier is deliberately fenced in — it only applies
/// to short, human-readable labels, needs at least three characters, and the
/// matched letters must land close together. Unfenced, it matches almost
/// anything: "vpn" is a subsequence of "Expense Appro*v*al *P*olicy ... fina*n*ce"
/// and of half the corpus besides.
int? fuzzyScore(String haystack, String needle) {
  if (needle.isEmpty) return 0;
  final h = haystack.toLowerCase();
  final n = needle.toLowerCase();

  final index = h.indexOf(n);
  if (index == 0) return 1000 - h.length;
  if (index > 0) {
    // A match at a word boundary is a near-prefix; mid-word is weaker.
    final boundary = !RegExp(r'[a-z0-9]').hasMatch(h[index - 1]);
    return (boundary ? 800 : 650) - index - h.length ~/ 4;
  }

  // Two scattered letters are a coincidence, not an abbreviation.
  if (n.length < 3) return null;

  var cursor = 0;
  var gaps = 0;
  var previous = -1;
  var first = -1;
  for (var i = 0; i < n.length; i++) {
    final found = h.indexOf(n[i], cursor);
    if (found < 0) return null;
    if (first < 0) first = found;
    if (previous >= 0) gaps += found - previous - 1;
    previous = found;
    cursor = found + 1;
  }
  // "eap" for "Expense Approval Policy" is an abbreviation; the same three
  // letters spread over sixty characters is noise.
  if (previous - first + 1 > n.length * 4) return null;
  return 400 - gaps - h.length ~/ 4;
}

/// Ranks a bag of keywords — ids, slugs, paths, tags, emails — against typed
/// text. Substring only, never subsequence.
///
/// This is the fix for the palette returning half the console for "vpn". The
/// keyword blob is a concatenation of unrelated identifiers, so "are these
/// letters findable in order somewhere across it" is always true for a short
/// query and means nothing. "Does some token actually contain this" is the
/// only question worth asking of a bag of tokens.
int? keywordScore(String keywords, String needle) {
  final k = _normalizeKeywords(keywords);
  final n = _normalizeKeywords(needle);
  if (n.isEmpty) return null;

  // A needle with punctuation in it — a team slug (`finance-ops`), an email,
  // a path fragment (`engineering/notes`) — normalizes to several parts, and
  // those parts have to appear *contiguously*. Requiring adjacency is what
  // separates this from the subsequence rule that matched everything: the
  // letters must sit together in the order typed, not merely exist.
  if (n.contains(' ')) {
    final at = k.indexOf(n);
    if (at < 0) return null;
    final onBoundary = at == 0 || k[at - 1] == ' ';
    return (onBoundary ? 760 : 620) - at ~/ 4;
  }

  int? best;
  for (final token in k.split(' ')) {
    if (token.isEmpty) continue;
    final at = token.indexOf(n);
    if (at < 0) continue;
    final score = token == n
        ? 900
        : (at == 0 ? 780 - token.length : 620 - at - token.length ~/ 2);
    if (best == null || score > best) best = score;
  }
  return best;
}

/// Whether a hit belongs under the "weaker matches" divider.
///
/// The judgement itself is the server's (see `search.mark_weak_hits`): it has
/// the whole ranking, and having the rule in two languages meant the console
/// and the usage counters could disagree about what "surfaced" means. This is
/// the one place the client consults it, kept as a function so the intent
/// reads the same as it did when the arithmetic lived here.
bool isWeakSemanticHit(SearchHit hit) => hit.weak;

final _nonAlphanumeric = RegExp(r'[^a-z0-9]+');

/// Collapses punctuation to single spaces so both sides of a comparison are
/// tokenized the same way. Without this, splitting only the keywords means a
/// hyphenated slug can never match the tokens it was split into.
String _normalizeKeywords(String text) =>
    text.toLowerCase().replaceAll(_nonAlphanumeric, ' ').trim();

// ---- Catalog ----------------------------------------------------------------

/// One navigable thing in the console.
class CatalogEntry {
  final String label;
  final String detail;

  /// Extra text matched against but not displayed — ids, slugs, paths.
  final String keywords;
  final IconData icon;
  final CumulusTone tone;
  final PaletteIntent intent;
  final String? badge;
  final CumulusTone badgeTone;

  const CatalogEntry({
    required this.label,
    required this.detail,
    required this.keywords,
    required this.icon,
    required this.intent,
    this.tone = CumulusTone.neutral,
    this.badge,
    this.badgeTone = CumulusTone.neutral,
  });
}

/// Everything the palette can jump to, fetched once and reused.
///
/// Loaded on first open rather than at startup: a console session that never
/// opens the palette should not pay for it. Each source fails independently —
/// a plain user is not allowed to list identities, and that must cost them the
/// identities lane, not the whole palette.
class CommandCatalog extends ChangeNotifier {
  final ApiClient _api;

  CommandCatalog(this._api);

  List<AssetSummary> _assets = const [];
  List<Team> _teams = const [];
  List<Identity> _identities = const [];
  bool _loaded = false;
  bool _loading = false;

  bool get isLoaded => _loaded;
  List<Team> get teams => _teams;

  /// The acting session, so the palette offers what the rail offers.
  ///
  /// Set by the shell whenever the session changes. Null before the first load
  /// — and null means offer everything, on the same reasoning the rail uses:
  /// hiding a stop from somebody who turns out to be entitled to it is the
  /// worse of the two errors while the answer is still in flight.
  Session? _session;

  set session(Session? value) {
    if (value == _session) return;
    _session = value;
    notifyListeners();
  }

  /// Every destination the palette can jump to, with the predicate that
  /// decides whether it exists for this reader.
  ///
  /// The section indices are the shell's own constants. They are imported
  /// rather than written out because they were written out once and drifted:
  /// three of them still pointed where those sections used to live, so
  /// "Knowledge layer" opened the identity workbench and "Flows" opened
  /// System state.
  ///
  /// The predicates are the rail's, for the reason the rail has them: a
  /// governance section an own-scoped persona cannot open renders as a blank
  /// screen, which is worse than not being offered at all — the shell
  /// unmounts what the rail drops, so the palette was navigating to a
  /// deliberately empty widget and calling it a destination.
  static final List<(CatalogEntry, bool Function(Session?))> _navigation = [
    (
      const CatalogEntry(
        label: 'New asset',
        detail: 'author a document in the knowledge layer',
        keywords: 'create add new document write',
        icon: Icons.add_circle_outline,
        tone: CumulusTone.accent,
        intent: CreateAssetIntent(),
      ),
      _governs,
    ),
    (
      const CatalogEntry(
        label: 'Home',
        detail: 'your day, and where accounts payable stands',
        keywords: 'home today schedule day start dashboard',
        icon: Icons.home_outlined,
        intent: OpenSectionIntent(homeIndex),
      ),
      _always,
    ),
    (
      const CatalogEntry(
        label: 'Calendar',
        detail: 'your week, mirrored from the calendar provider',
        keywords: 'calendar week schedule meetings agenda free busy',
        icon: Icons.calendar_today_outlined,
        intent: OpenSectionIntent(calendarIndex),
      ),
      _always,
    ),
    (
      const CatalogEntry(
        label: 'My Tasks',
        detail: 'the queue — judgments waiting on a person',
        keywords: 'tasks queue inbox decisions approve review hitl',
        icon: Icons.inbox_outlined,
        intent: OpenSectionIntent(tasksIndex),
      ),
      _always,
    ),
    (
      const CatalogEntry(
        label: 'System state',
        detail: 'the whole deployment, on one screen',
        keywords: 'system state dashboard governance health map feeds desks',
        icon: Icons.monitor_heart_outlined,
        intent: OpenSectionIntent(systemStateIndex),
      ),
      _governs,
    ),
    (
      const CatalogEntry(
        label: 'Knowledge layer',
        detail: 'the governed corpus',
        keywords: 'assets documents corpus taxonomy ontology sources',
        icon: Icons.hub_outlined,
        intent: OpenSectionIntent(knowledgeIndex),
      ),
      _governs,
    ),
    (
      const CatalogEntry(
        label: 'Flows',
        detail: 'authored logic — definitions, runs, cost and audit',
        keywords:
            'flows workflows dataflows agents pipelines runs cost roi testing',
        icon: Icons.account_tree_outlined,
        intent: OpenSectionIntent(flowsIndex),
      ),
      _always,
    ),
    (
      const CatalogEntry(
        label: 'Identities, access & keys',
        detail: 'people, agents, teams and API keys',
        keywords: 'users agents teams api keys access',
        icon: Icons.groups_outlined,
        intent: OpenSectionIntent(identitiesIndex),
      ),
      _governs,
    ),
    // The workspaces, each gated on the desk whose work it is — the same
    // predicate the rail's dropdown draws its children with.
    (
      const CatalogEntry(
        label: 'Expense Reimbursement',
        detail: 'workspace — requests, decisions, spend',
        keywords: 'workspace expenses reimbursement receipts claims spend',
        icon: Icons.folder_outlined,
        intent: OpenSectionIntent(expenseWorkspaceIndex),
      ),
      _ownsExpenses,
    ),
    (
      const CatalogEntry(
        label: 'Accounts Payable',
        detail: 'workspace — invoices, matching, posting',
        keywords: 'workspace ap accounts payable invoices vendors posting',
        icon: Icons.folder_outlined,
        intent: OpenSectionIntent(payablesWorkspaceIndex),
      ),
      _ownsPayables,
    ),
    (
      const CatalogEntry(
        label: 'Counterparty identity',
        detail: 'workspace — proposed merges, evidence, decisions',
        keywords: 'identity merge steward counterparty vendor company duplicate',
        icon: Icons.compare_arrows,
        intent: OpenSectionIntent(identityWorkspaceIndex),
      ),
      _curatesIdentity,
    ),
  ];

  static bool _always(Session? s) => true;
  static bool _governs(Session? s) => s == null || s.scope != 'own';
  static bool _curatesIdentity(Session? s) => s == null || s.mayOpenIdentity;
  static bool _ownsExpenses(Session? s) => ownsWorkspace(s, expenseQueue);
  static bool _ownsPayables(Session? s) => ownsWorkspace(s, payablesQueue);

  /// The destinations this session may actually open.
  List<CatalogEntry> get navigationEntries => [
    for (final (entry, allowed) in _navigation)
      if (allowed(_session)) entry,
  ];

  Future<void> ensureLoaded() async {
    if (_loaded || _loading) return;
    _loading = true;
    // Three independent requests; one failing must not void the others.
    final results = await Future.wait([
      _api.listAssets().then<Object?>((v) => v).catchError((_) => null),
      _api.listTeams().then<Object?>((v) => v).catchError((_) => null),
      _api.listIdentities().then<Object?>((v) => v).catchError((_) => null),
    ]);
    _assets = (results[0] as List<AssetSummary>?) ?? const [];
    _teams = (results[1] as List<Team>?) ?? const [];
    _identities = (results[2] as List<Identity>?) ?? const [];
    _loaded = true;
    _loading = false;
    notifyListeners();
  }

  /// Re-fetch after a write, so a document authored a moment ago is jumpable.
  Future<void> refresh() async {
    _loaded = false;
    return ensureLoaded();
  }

  List<CatalogEntry> get entries => [
    ...navigationEntries,
    for (final a in _assets)
      CatalogEntry(
        label: a.title,
        detail: a.path,
        keywords: '${a.id} ${a.path} ${a.tags.join(" ")} ${a.owner}',
        icon: Icons.description_outlined,
        tone: toneForAuthority(a.authorityLevel),
        badge: a.status == 'published' ? null : a.status,
        badgeTone: toneForStatus(a.status),
        intent: OpenAssetIntent(a.id),
      ),
    for (final t in _teams)
      CatalogEntry(
        label: t.name,
        detail: 'team · ${t.slug}',
        keywords: '${t.slug} team ${t.description}',
        icon: Icons.groups_2_outlined,
        tone: CumulusTone.teal,
        intent: OpenTeamIntent(t.slug),
      ),
    for (final i in _identities)
      CatalogEntry(
        label: i.label,
        detail: '${labelForKind(i.kind)} · ${i.role}',
        keywords: '${i.uid} ${i.email} ${i.kind} ${i.role}',
        icon: Icons.badge_outlined,
        tone: toneForKind(i.kind),
        intent: OpenIdentityIntent(i.uid),
      ),
  ];

  /// Weakest match worth showing. A lane that lists everything is a lane
  /// nobody reads, and "no matches" is a useful answer.
  static const _minScore = 350;

  /// Best matches for typed text, best first.
  List<CatalogEntry> match(String query, {int limit = 6}) {
    final needle = query.trim();
    if (needle.isEmpty) return const [];
    final scored = <(int, CatalogEntry)>[];
    for (final entry in entries) {
      final direct = fuzzyScore(entry.label, needle);
      // Keyword hits are real but weaker than a title match, so they rank
      // below one rather than competing with it.
      final viaKeywords = keywordScore(entry.keywords, needle);
      final candidates = [
        if (direct != null) direct,
        if (viaKeywords != null) viaKeywords - 200,
      ];
      if (candidates.isEmpty) continue;
      final best = candidates.reduce((a, b) => a > b ? a : b);
      if (best < _minScore) continue;
      scored.add((best, entry));
    }
    scored.sort((a, b) => b.$1.compareTo(a.$1));
    return [for (final s in scored.take(limit)) s.$2];
  }
}

// ---- In-document matches ----------------------------------------------------

class DocumentMatch {
  final int start;
  final int end;
  final String excerpt;
  final int line;

  const DocumentMatch({
    required this.start,
    required this.end,
    required this.excerpt,
    required this.line,
  });
}

/// Plain case-insensitive occurrences of [needle] in [content].
///
/// Not fuzzy on purpose. In-document find is a mechanical operation; a fuzzy
/// match here would highlight text that does not contain what you typed.
List<DocumentMatch> findInDocument(
  String content,
  String needle, {
  int limit = 8,
}) {
  if (needle.trim().length < 2) return const [];
  final haystack = content.toLowerCase();
  final target = needle.toLowerCase();
  final matches = <DocumentMatch>[];
  var from = 0;
  while (matches.length < limit) {
    final at = haystack.indexOf(target, from);
    if (at < 0) break;
    final start = (at - 40).clamp(0, content.length);
    final end = (at + target.length + 60).clamp(0, content.length);
    final excerpt = content
        .substring(start, end)
        .replaceAll('\n', ' ')
        .trim();
    matches.add(
      DocumentMatch(
        start: at,
        end: at + target.length,
        excerpt: '${start > 0 ? "…" : ""}$excerpt${end < content.length ? "…" : ""}',
        line: '\n'.allMatches(content.substring(0, at)).length + 1,
      ),
    );
    from = at + target.length;
  }
  return matches;
}

// ---- Rows -------------------------------------------------------------------

class PaletteRow {
  final IconData icon;
  final String title;
  final String subtitle;
  final CumulusTone tone;
  final String? badge;
  final CumulusTone badgeTone;
  final VoidCallback activate;

  const PaletteRow({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.activate,
    this.tone = CumulusTone.neutral,
    this.badge,
    this.badgeTone = CumulusTone.neutral,
  });
}

class PaletteSection {
  final String title;
  final String? note;
  final List<PaletteRow> rows;

  const PaletteSection({required this.title, required this.rows, this.note});
}

// ---- Controller -------------------------------------------------------------

/// Owns the palette's query, its four lanes, and the keyboard selection.
///
/// A controller rather than widget state because three widgets need it at once:
/// the top-bar text field intercepts the arrow keys, the shell decides whether
/// to draw the panel, and the panel renders the rows. All three must agree on
/// which row is selected.
class CommandPaletteController extends ChangeNotifier {
  final ApiClient api;
  final CommandCatalog catalog;
  final void Function(PaletteIntent intent) onIntent;

  final TextEditingController input = TextEditingController();
  final FocusNode focusNode = FocusNode(debugLabel: 'command-search');

  CommandPaletteController({
    required this.api,
    required this.catalog,
    required this.onIntent,
  }) {
    input.addListener(_onInputChanged);
    catalog.addListener(notifyListeners);
  }

  /// Long enough that typing a word does not fire three searches, short enough
  /// that the results feel attached to the keystroke.
  static const _debounceDelay = Duration(milliseconds: 180);

  bool _open = false;
  String _query = '';
  SearchFilters _filters = const SearchFilters();
  List<SearchHit> _hits = const [];
  bool _searching = false;
  String? _searchError;

  AskAnswer? _answer;
  String? _answeredQuestion;
  bool _asking = false;
  String? _askError;

  int _selected = 0;
  DocumentScope? _scope;
  Timer? _debounce;
  bool _disposed = false;

  /// Discards responses from superseded queries. Without it a slow request for
  /// "exp" can land after a fast one for "expense" and overwrite it.
  int _generation = 0;

  bool get isOpen => _open;
  String get query => _query;
  SearchFilters get filters => _filters;
  List<SearchHit> get hits => _hits;
  bool get searching => _searching;
  String? get searchError => _searchError;
  AskAnswer? get answer => _answer;
  String? get answeredQuestion => _answeredQuestion;
  bool get asking => _asking;
  String? get askError => _askError;
  int get selected => _selected;
  DocumentScope? get scope => _scope;

  /// The editor publishes the open document here; null once it closes.
  ///
  /// The notification is deferred to after the current frame. The editor
  /// publishes from `initState` and `dispose`, both of which run inside the
  /// parent's build and teardown — notifying synchronously would mark the
  /// shell dirty while it is already building and throw.
  set scope(DocumentScope? value) {
    final changed = _scope?.assetId != value?.assetId;
    _scope = value;
    if (!changed) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_disposed) notifyListeners();
    });
  }

  @override
  void dispose() {
    _disposed = true;
    _debounce?.cancel();
    input.removeListener(_onInputChanged);
    catalog.removeListener(notifyListeners);
    input.dispose();
    focusNode.dispose();
    super.dispose();
  }

  // -- Opening and closing ------------------------------------------------

  void open({bool selectAll = true}) {
    _open = true;
    catalog.ensureLoaded();
    focusNode.requestFocus();
    if (selectAll && input.text.isNotEmpty) {
      input.selection = TextSelection(
        baseOffset: 0,
        extentOffset: input.text.length,
      );
    }
    notifyListeners();
  }

  /// Closes the panel but keeps the text, so reopening resumes where you were.
  void close() {
    if (!_open) return;
    _open = false;
    _selected = 0;
    focusNode.unfocus();
    notifyListeners();
  }

  void clear() {
    input.clear();
    _answer = null;
    _answeredQuestion = null;
    _askError = null;
    notifyListeners();
  }

  // -- Query --------------------------------------------------------------

  void _onInputChanged() {
    final parsed = parseSearchInput(input.text);
    _query = parsed.query;
    _filters = parsed.filters;
    _selected = 0;
    // An answer belongs to the question that produced it. Once the text moves
    // on, leaving it on screen would attach it to a question nobody asked.
    if (_answeredQuestion != null && _answeredQuestion != input.text.trim()) {
      _answer = null;
      _askError = null;
      _answeredQuestion = null;
    }
    _scheduleSearch();
    notifyListeners();
  }

  void _scheduleSearch() {
    _debounce?.cancel();
    final hasQuery = _query.trim().length >= 2;
    if (!hasQuery) {
      _generation++;
      _hits = const [];
      _searching = false;
      _searchError = null;
      return;
    }
    _searching = true;
    _debounce = Timer(_debounceDelay, _runSearch);
  }

  Future<void> _runSearch() async {
    final generation = ++_generation;
    final query = _query;
    final filters = _filters;
    try {
      final hits = await api.search(query, limit: 8, filters: filters);
      if (generation != _generation) return;
      _hits = hits;
      _searchError = null;
    } catch (e) {
      if (generation != _generation) return;
      _hits = const [];
      _searchError = '$e';
    } finally {
      if (generation == _generation) {
        _searching = false;
        notifyListeners();
      }
    }
  }

  // -- Asking -------------------------------------------------------------

  Future<void> runAsk() async {
    final question = input.text.trim();
    if (question.isEmpty || _asking) return;
    _asking = true;
    _answer = null;
    _askError = null;
    _answeredQuestion = question;
    notifyListeners();
    try {
      final answer = await api.ask(question, scopeAssetId: _scope?.assetId);
      // The question may have moved on during a multi-second planner run.
      if (_answeredQuestion != question) return;
      _answer = answer;
    } catch (e) {
      if (_answeredQuestion != question) return;
      _askError = '$e';
    } finally {
      if (_answeredQuestion == question) {
        _asking = false;
        notifyListeners();
      }
    }
  }

  // -- Rows ---------------------------------------------------------------

  /// The flattened, ordered rows. Keyboard selection indexes into this, so it
  /// has to be the single source both the panel and the key handler read.
  List<PaletteSection> buildSections() {
    final text = input.text.trim();
    if (text.isEmpty) return _restingSections();

    final sections = <PaletteSection>[];
    final askSection = _askSection(text);
    final questionFirst = looksLikeQuestion(text);

    if (questionFirst) sections.add(askSection);

    final documentMatches = _documentSection();
    if (documentMatches != null) sections.add(documentMatches);

    final jump = catalog.match(text);
    if (jump.isNotEmpty) {
      sections.add(
        PaletteSection(
          title: 'Jump to',
          rows: [for (final entry in jump) _rowForEntry(entry)],
        ),
      );
    }

    sections.addAll(_findSections());
    if (!questionFirst) sections.add(askSection);
    return sections;
  }

  List<PaletteSection> _restingSections() {
    // With no query the palette is a launcher, not a search. Showing the whole
    // corpus here would bury the three things anyone actually wants.
    return [
      PaletteSection(
        title: 'Actions',
        rows: [
          for (final entry in catalog.navigationEntries)
            _rowForEntry(entry),
        ],
        note: 'type to search · owner: authority: status: tag: path: to filter',
      ),
    ];
  }

  PaletteSection? _documentSection() {
    final scope = _scope;
    if (scope == null) return null;
    final needle = _query.trim();
    final matches = findInDocument(scope.readContent(), needle);
    if (matches.isEmpty) return null;
    return PaletteSection(
      title: 'In this document',
      note: scope.title,
      rows: [
        for (final match in matches)
          PaletteRow(
            icon: Icons.subject,
            title: 'Line ${match.line}',
            subtitle: match.excerpt,
            tone: CumulusTone.accent,
            activate: () {
              close();
              scope.reveal(match.start, match.end);
            },
          ),
      ],
    );
  }



  PaletteRow _hitRow(SearchHit hit) => PaletteRow(
    icon: Icons.description_outlined,
    title: hit.asset.title,
    subtitle: hit.snippet,
    tone: toneForAuthority(hit.asset.authorityLevel),
    // Only the vector index found this, so no word you typed appears in the
    // document. Saying so is the difference between a result that looks wrong
    // and one that explains itself.
    badge: hit.isSemanticOnly
        ? 'semantic'
        : (hit.asset.status == 'published' ? null : hit.asset.status),
    badgeTone: hit.isSemanticOnly
        ? CumulusTone.violet
        : toneForStatus(hit.asset.status),
    activate: () => _dispatch(OpenAssetIntent(hit.asset.id)),
  );

  List<PaletteSection> _findSections() {
    final strong = [
      for (final h in _hits)
        if (!isWeakSemanticHit(h)) h,
    ];
    final weakCount = _hits.length - strong.length;

    // Weak hits are counted here but never listed. A dense retriever scores
    // every document, so the weak bucket is reliably "the rest of the corpus"
    // — a section that always contains everything else carries no information
    // and reads as broken. The count is honest about what was held back, and
    // the full screen is where there is room to actually show it.
    final overflow = weakCount > 0 || _hits.length >= 8;
    return [
      PaletteSection(
        title: 'Find in the knowledge layer',
        rows: [
          for (final hit in strong) _hitRow(hit),
          if (overflow)
            PaletteRow(
              icon: Icons.manage_search,
              title: 'See all results',
              subtitle: weakCount == 0
                  ? 'facets, sorting, and retrieval detail'
                  : '$weakCount weaker ${weakCount == 1 ? "match" : "matches"} '
                        'held back · facets, sorting, retrieval detail',
              activate: () => _dispatch(ShowAllResultsIntent(_query, _filters)),
            ),
        ],
      ),
    ];
  }

  PaletteSection _askSection(String text) {
    return PaletteSection(
      title: 'Ask',
      rows: [
        PaletteRow(
          icon: Icons.auto_awesome_outlined,
          title: 'Ask Cumulus: "$text"',
          subtitle: _scope == null
              ? 'answers from documents and from corpus metadata, with citations'
              : 'answers with ${_scope!.title} in context',
          tone: CumulusTone.accent,
          activate: runAsk,
        ),
      ],
    );
  }

  PaletteRow _rowForEntry(CatalogEntry entry) => PaletteRow(
    icon: entry.icon,
    title: entry.label,
    subtitle: entry.detail,
    tone: entry.tone,
    badge: entry.badge,
    badgeTone: entry.badgeTone,
    activate: () => _dispatch(entry.intent),
  );

  void _dispatch(PaletteIntent intent) {
    close();
    onIntent(intent);
  }

  /// Follow a citation in an answer back to its document.
  void openCitation(String assetId) => _dispatch(OpenAssetIntent(assetId));

  /// Hand the current query and filters to the full search screen.
  ///
  /// Bound to shift+enter as well as the footer row, so leaving the dropdown
  /// for the full view never requires arrowing to the bottom of the list.
  void showAllResults() {
    if (input.text.trim().isEmpty) return;
    _dispatch(ShowAllResultsIntent(_query, _filters));
  }

  // -- Keyboard -----------------------------------------------------------

  List<PaletteRow> get _flatRows => [
    for (final section in buildSections()) ...section.rows,
  ];

  void moveSelection(int delta) {
    final rows = _flatRows;
    if (rows.isEmpty) return;
    // Wraps, because a palette with five rows should not need five presses to
    // get from the bottom back to the top.
    _selected = (_selected + delta) % rows.length;
    if (_selected < 0) _selected += rows.length;
    notifyListeners();
  }

  void activateSelection() {
    final rows = _flatRows;
    if (rows.isEmpty) return;
    rows[_selected.clamp(0, rows.length - 1)].activate();
  }

  /// The selection clamped to the rows that currently exist. Results arriving
  /// can shorten the list under a selection made against the previous one.
  int selectedIndexIn(int rowCount) =>
      rowCount == 0 ? 0 : _selected.clamp(0, rowCount - 1);

  /// Pointer hover drives the same selection as the arrow keys, so the mouse
  /// and the keyboard can never disagree about what Enter would activate.
  void _hoverTo(int index) {
    if (_selected == index) return;
    _selected = index;
    notifyListeners();
  }
}

// ---- The panel --------------------------------------------------------------

/// The dropdown itself. Rendered by the shell directly beneath the search bar,
/// over the page rather than inside it.
class CommandPalettePanel extends StatelessWidget {
  final CommandPaletteController palette;

  const CommandPalettePanel({super.key, required this.palette});

  @override
  Widget build(BuildContext context) {
    final sections = palette.buildSections();
    final rowCount = sections.fold<int>(0, (n, s) => n + s.rows.length);
    final selected = palette.selectedIndexIn(rowCount);

    return Container(
      constraints: const BoxConstraints(maxHeight: 540),
      decoration: BoxDecoration(
        color: CumulusColors.surface,
        borderRadius: BorderRadius.circular(CumulusRadius.card),
        border: Border.all(color: CumulusColors.borderStrong),
        boxShadow: CumulusShadows.floating,
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (!palette.filters.isEmpty) _FilterBar(controller: palette),
          Flexible(
            child: SingleChildScrollView(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                mainAxisSize: MainAxisSize.min,
                children: [
                  _AnswerBlock(controller: palette),
                  ..._sectionWidgets(sections, selected),
                ],
              ),
            ),
          ),
          _Footer(controller: palette),
        ],
      ),
    );
  }

  List<Widget> _sectionWidgets(List<PaletteSection> sections, int selected) {
    final widgets = <Widget>[];
    var index = 0;
    for (final section in sections) {
      widgets.add(_SectionHeader(section: section, controller: palette));
      if (section.rows.isEmpty) {
        widgets.add(_EmptyLane(controller: palette));
        continue;
      }
      for (final row in section.rows) {
        // Copied per iteration: Dart closures capture the loop variable by
        // reference, so every row would otherwise hover-select the last index.
        final rowIndex = index;
        widgets.add(
          _Row(
            row: row,
            selected: rowIndex == selected,
            onHover: () => palette._hoverTo(rowIndex),
          ),
        );
        index++;
      }
    }
    return widgets;
  }
}

class _SectionHeader extends StatelessWidget {
  final PaletteSection section;
  final CommandPaletteController controller;

  const _SectionHeader({required this.section, required this.controller});

  @override
  Widget build(BuildContext context) {
    // Only the Find lane can be mid-flight; the others resolve synchronously.
    final busy = section.title.startsWith('Find') && controller.searching;
    return Padding(
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 6),
      child: Row(
        children: [
          Text(section.title.toUpperCase(), style: CumulusText.statLabel),
          if (busy) ...[
            const SizedBox(width: 8),
            const SizedBox(
              width: 10,
              height: 10,
              child: CircularProgressIndicator(strokeWidth: 1.6),
            ),
          ],
          const Spacer(),
          if (section.note != null)
            Flexible(
              child: Text(
                section.note!,
                style: CumulusText.meta,
                overflow: TextOverflow.ellipsis,
                textAlign: TextAlign.right,
              ),
            ),
        ],
      ),
    );
  }
}

/// What the Find lane says when it has nothing — the one place the console can
/// notice that the corpus has a gap.
class _EmptyLane extends StatelessWidget {
  final CommandPaletteController controller;

  const _EmptyLane({required this.controller});

  @override
  Widget build(BuildContext context) {
    final String message;
    if (controller.searchError != null) {
      message = controller.searchError!;
    } else if (controller.searching) {
      message = 'Searching…';
    } else if (controller.query.trim().length < 2) {
      message = 'Keep typing to search the corpus.';
    } else {
      message =
          'Nothing in the governed corpus matches. Ask instead, or author the '
          'document that should have answered it.';
    }
    return Padding(
      padding: const EdgeInsets.fromLTRB(18, 2, 18, 10),
      child: Text(message, style: CumulusText.meta),
    );
  }
}

class _Row extends StatelessWidget {
  final PaletteRow row;
  final bool selected;
  final VoidCallback onHover;

  const _Row({
    required this.row,
    required this.selected,
    required this.onHover,
  });

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      // Hover moves the keyboard selection too, so the pointer and the arrow
      // keys can never disagree about which row Enter would activate.
      onEnter: (_) => onHover(),
      child: GestureDetector(
        onTap: row.activate,
        behavior: HitTestBehavior.opaque,
        child: Container(
          color: selected ? CumulusColors.accentSoft : Colors.transparent,
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 9),
          child: Row(
            children: [
              Container(
                width: 26,
                height: 26,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: selected
                      ? CumulusColors.surface
                      : CumulusColors.surfaceMuted,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Icon(
                  row.icon,
                  size: 15,
                  color: selected
                      ? CumulusColors.accentInk
                      : CumulusColors.inkSecondary,
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      row.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: CumulusText.rowTitle,
                    ),
                    if (row.subtitle.isNotEmpty) ...[
                      const SizedBox(height: 2),
                      Text(
                        row.subtitle,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: CumulusText.meta,
                      ),
                    ],
                  ],
                ),
              ),
              if (row.badge != null) ...[
                const SizedBox(width: 10),
                StatusBadge(row.badge!, tone: row.badgeTone, dense: true),
              ],
              if (selected) ...[
                const SizedBox(width: 10),
                const Text('↵', style: CumulusText.meta),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// The parsed filter tokens, shown so it is obvious the query was narrowed.
class _FilterBar extends StatelessWidget {
  final CommandPaletteController controller;

  const _FilterBar({required this.controller});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
      decoration: const BoxDecoration(
        color: CumulusColors.surfaceMuted,
        border: Border(bottom: BorderSide(color: CumulusColors.border)),
      ),
      child: Wrap(
        spacing: 6,
        runSpacing: 6,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          const Text('FILTERED', style: CumulusText.statLabel),
          const SizedBox(width: 2),
          for (final (label, value) in controller.filters.chips)
            MetaPill('$label: $value', tone: CumulusTone.accent),
        ],
      ),
    );
  }
}

/// The answer, once one has been asked for.
///
/// The body is [AnswerPanel], shared with the full search page so the same
/// answer never formats two ways.
class _AnswerBlock extends StatelessWidget {
  final CommandPaletteController controller;

  const _AnswerBlock({required this.controller});

  @override
  Widget build(BuildContext context) {
    if (controller.asking) {
      return const Padding(
        padding: EdgeInsets.fromLTRB(18, 16, 18, 10),
        child: Row(
          children: [
            SizedBox(
              width: 13,
              height: 13,
              child: CircularProgressIndicator(strokeWidth: 1.8),
            ),
            SizedBox(width: 10),
            Text(
              'Reading the corpus…',
              style: TextStyle(fontSize: 12.5, color: CumulusColors.inkSecondary),
            ),
          ],
        ),
      );
    }
    if (controller.askError != null) {
      return Padding(
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 4),
        child: ErrorNotice(
          message: controller.askError!,
          onRetry: controller.runAsk,
        ),
      );
    }
    final answer = controller.answer;
    if (answer == null) return const SizedBox.shrink();

    return Padding(
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 6),
      child: AnswerPanel(
        answer: answer,
        onOpenAsset: controller.openCitation,
        dense: true,
      ),
    );
  }
}

class _Footer extends StatelessWidget {
  final CommandPaletteController controller;

  const _Footer({required this.controller});

  @override
  Widget build(BuildContext context) {
    final scope = controller.scope;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 9),
      decoration: const BoxDecoration(
        color: CumulusColors.surfaceMuted,
        border: Border(top: BorderSide(color: CumulusColors.border)),
      ),
      child: Row(
        children: [
          const Text(
            '↑↓ navigate   ↵ open   ⇧↵ all results   ⌘↵ ask   esc close',
            style: CumulusText.meta,
          ),
          const Spacer(),
          if (scope != null)
            Flexible(
              child: Text(
                'scoped to ${scope.title}',
                style: CumulusText.meta,
                overflow: TextOverflow.ellipsis,
              ),
            ),
        ],
      ),
    );
  }
}
