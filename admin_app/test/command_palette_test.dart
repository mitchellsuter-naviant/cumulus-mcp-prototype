import 'package:cumulus_admin/models/asset.dart';
import 'package:cumulus_admin/models/session.dart';
import 'package:cumulus_admin/services/api_client.dart';
import 'package:cumulus_admin/widgets/command_palette.dart';
import 'package:flutter_test/flutter_test.dart';

/// The palette's pure logic: what a typed string means, how candidates rank,
/// and how in-document find behaves. These run without a server and without a
/// widget tree, which is most of why they are worth having.
void main() {
  group('parseSearchInput', () {
    test('splits filter tokens from the free text', () {
      final result = parseSearchInput('owner:hr expense approval');
      expect(result.query, 'expense approval');
      expect(result.filters.owner, 'hr');
    });

    test('accepts the aliases people actually type', () {
      expect(parseSearchInput('team:finance-ops').filters.owner, 'finance-ops');
      expect(parseSearchInput('level:policy').filters.authorityLevel, 'policy');
      expect(parseSearchInput('in:people/how-to').filters.pathPrefix,
          'people/how-to');
    });

    test('leaves unknown keys in the query rather than inventing a filter', () {
      // A URL must not silently become a filter named "https".
      final result = parseSearchInput('https://example.com/policy');
      expect(result.query, 'https://example.com/policy');
      expect(result.filters.isEmpty, isTrue);
    });

    test('keeps an invalid enum value as text', () {
      // Accepting it would filter everything away, which reads as "no results"
      // rather than as the typo it is.
      final result = parseSearchInput('authority:polcy leave');
      expect(result.filters.authorityLevel, isNull);
      expect(result.query, contains('authority:polcy'));
    });

    test('ignores a bare colon and a trailing colon', () {
      expect(parseSearchInput(':x').filters.isEmpty, isTrue);
      expect(parseSearchInput('owner:').filters.owner, isNull);
      expect(parseSearchInput('owner:').query, 'owner:');
    });

    test('lowercases slugs and enums but preserves tag and path casing', () {
      final result = parseSearchInput('owner:HR tag:Network path:People/Docs');
      expect(result.filters.owner, 'hr');
      expect(result.filters.tag, 'Network');
      expect(result.filters.pathPrefix, 'People/Docs');
    });

    test('combines several filters with the remaining text', () {
      final result =
          parseSearchInput('status:draft owner:hr onboarding checklist');
      expect(result.filters.status, 'draft');
      expect(result.filters.owner, 'hr');
      expect(result.query, 'onboarding checklist');
      expect(result.filters.chips.length, 2);
    });
  });

  group('looksLikeQuestion', () {
    test('recognises questions', () {
      expect(looksLikeQuestion('which assets are managed by the HR team'),
          isTrue);
      expect(looksLikeQuestion('expense policy?'), isTrue);
      expect(looksLikeQuestion('tell me who owns the VPN note'), isTrue);
    });

    test('treats short lookups as searches', () {
      // Ordering only. Guessing wrong costs a glance, never a model call.
      expect(looksLikeQuestion('leave policy'), isFalse);
      expect(looksLikeQuestion('vpn'), isFalse);
      expect(looksLikeQuestion('onboarding'), isFalse);
    });
  });

  group('fuzzyScore', () {
    test('ranks prefix above word-boundary above mid-word', () {
      final prefix = fuzzyScore('Expense Approval Policy', 'expense')!;
      final boundary = fuzzyScore('Expense Approval Policy', 'approval')!;
      final midWord = fuzzyScore('Expense Approval Policy', 'pproval')!;
      expect(prefix, greaterThan(boundary));
      expect(boundary, greaterThan(midWord));
    });

    test('falls back to a subsequence, and ranks it lowest', () {
      final subsequence = fuzzyScore('Expense Approval Policy', 'eap')!;
      final substring = fuzzyScore('Expense Approval Policy', 'polic')!;
      expect(subsequence, lessThan(substring));
    });

    test('returns null when a letter is missing', () {
      expect(fuzzyScore('Expense Approval Policy', 'zzz'), isNull);
    });

    test('prefers the shorter of two equally-prefixed candidates', () {
      expect(
        fuzzyScore('VPN', 'vpn')!,
        greaterThan(fuzzyScore('VPN Setup Notes For Engineers', 'vpn')!),
      );
    });

    test('is case insensitive', () {
      expect(fuzzyScore('Onboarding', 'ONBOARD'), isNotNull);
    });
  });

  group('findInDocument', () {
    const content = 'Line one mentions VPN.\nLine two also mentions vpn here.';

    test('finds every occurrence regardless of case', () {
      final matches = findInDocument(content, 'vpn');
      expect(matches.length, 2);
      expect(matches.first.line, 1);
      expect(matches.last.line, 2);
    });

    test('reports offsets that select the match itself', () {
      final match = findInDocument(content, 'VPN').first;
      expect(content.substring(match.start, match.end).toLowerCase(), 'vpn');
    });

    test('refuses a needle too short to be meaningful', () {
      expect(findInDocument(content, 'v'), isEmpty);
      expect(findInDocument(content, ' '), isEmpty);
    });

    test('does not match fuzzily', () {
      // In-document find is mechanical; highlighting text that does not
      // contain what you typed would be wrong, not helpful.
      expect(findInDocument(content, 'vpnn'), isEmpty);
    });

    test('honours the limit and cannot loop on overlapping matches', () {
      final matches = findInDocument('aaaaaaaaaa', 'aa', limit: 3);
      expect(matches.length, 3);
      expect(matches[1].start, greaterThan(matches[0].start));
    });
  });


  // Built from the exact catalog that produced a bad dropdown: typing "vpn"
  // returned two people, an expense policy, a travel guideline and an incident
  // standard alongside the one document actually about VPN.
  group('catalog matching against the real corpus', () {
    const keywords = {
      'VPN Setup Notes':
          'vpn-setup-note engineering/notes/vpn-setup vpn network setup platform-eng',
      'Expense Approval Policy':
          'expense-approval-policy finance/policies/expense-approval expenses approvals finance finance-ops',
      'Incident Response Standard':
          'incident-response-standard engineering/standards/incident-response incidents oncall sev engineering platform-eng',
      'Corporate Travel Booking Guideline':
          'travel-booking-guideline finance/guidelines/travel-booking travel booking expenses finance-ops',
      'Jake Rouse': 'PUcvTYgg3GbSbVv85d7nzO1PA1L2 jrouse@naviant.com person admin',
      'masuter@naviant.com':
          '3QRUh960iqQeNinEmV4CUOJsf013 masuter@naviant.com person owner',
    };

    int? score(String label, String query) {
      final direct = fuzzyScore(label, query);
      final viaKeywords = keywordScore(keywords[label]!, query);
      final candidates = [
        if (direct != null) direct,
        if (viaKeywords != null) viaKeywords - 200,
      ];
      if (candidates.isEmpty) return null;
      final best = candidates.reduce((a, b) => a > b ? a : b);
      return best < 350 ? null : best;
    }

    test('"vpn" matches only the VPN document', () {
      expect(score('VPN Setup Notes', 'vpn'), isNotNull);
      for (final label in keywords.keys.where((k) => k != 'VPN Setup Notes')) {
        expect(score(label, 'vpn'), isNull, reason: '$label should not match "vpn"');
      }
    });

    test('the old subsequence rule is what matched all of them', () {
      // Each of these is a genuine subsequence of its keyword blob, which is
      // exactly why scanning the blob that way was wrong.
      for (final label in [
        'Expense Approval Policy',
        'Incident Response Standard',
        'Jake Rouse',
        'masuter@naviant.com',
      ]) {
        expect(_isSubsequence(keywords[label]!, 'vpn'), isTrue);
        expect(keywordScore(keywords[label]!, 'vpn'), isNull);
      }
    });

    test('keywords still match on a real token', () {
      expect(score('Expense Approval Policy', 'expenses'), isNotNull);
      expect(score('Corporate Travel Booking Guideline', 'finance-ops'), isNotNull);
      expect(score('Jake Rouse', 'jrouse@naviant.com'), isNotNull);
      expect(score('VPN Setup Notes', 'network'), isNotNull);
    });

    test('an id or path fragment still finds its document', () {
      expect(score('Incident Response Standard', 'incident'), isNotNull);
      expect(score('Corporate Travel Booking Guideline', 'travel-booking'), isNotNull);
    });
  });

  group('fuzzyScore subsequence fencing', () {
    test('rejects letters scattered too far apart', () {
      // o(0) ... e(13) ... s(18): a real subsequence, spread over nineteen
      // characters. That is a coincidence, not an abbreviation.
      expect(
        fuzzyScore('Onboarding Checklist For New Engineering Hires', 'oes'),
        isNull,
      );
    });

    test('still accepts a genuine abbreviation', () {
      expect(fuzzyScore('Expense Approval Policy', 'eap'), isNotNull);
    });

    test('refuses to subsequence-match fewer than three characters', () {
      // "vp" is a subsequence of a great many titles and means nothing.
      expect(fuzzyScore('Corporate Travel Booking Guideline', 'vp'), isNull);
      // A real substring of that length is still fine.
      expect(fuzzyScore('VPN Setup Notes', 'vp'), isNotNull);
    });
  });

  // The arithmetic now lives on the server (see app/search.py, with the
  // measured distributions that calibrated it, and its tests). What is left
  // here is the contract the client depends on: it reads the server's verdict
  // and never second-guesses it.
  group('weak-hit divider', () {
    SearchHit hit({required bool weak, String retrieval = 'semantic'}) =>
        SearchHit(
          asset: AssetSummary(
            id: 'a', path: 'p/a', title: 'A', contentType: 'text/markdown',
            authorityLevel: 'note', scope: 'org', owner: '', tags: const [],
            status: 'published', updatedAt: DateTime(2026),
          ),
          score: 0, snippet: '', retrieval: retrieval, weak: weak,
        );

    test('defers to the server verdict', () {
      expect(isWeakSemanticHit(hit(weak: true)), isTrue);
      expect(isWeakSemanticHit(hit(weak: false)), isFalse);
    });

    test('a hit missing the field is treated as strong', () {
      // An older server that does not send `weak` must not cause every result
      // to vanish behind a divider.
      final decoded = SearchHit.fromJson({
        'asset': {
          'id': 'a', 'path': 'p/a', 'title': 'A',
          'content_type': 'text/markdown', 'authority_level': 'note',
          'scope': 'org', 'owner': '', 'tags': <String>[],
          'status': 'published', 'updated_at': '2026-01-01T00:00:00Z',
        },
        'score': 1.0,
        'snippet': '',
        'retrieval': 'semantic',
      });
      expect(decoded.weak, isFalse);
      expect(isWeakSemanticHit(decoded), isFalse);
    });
  });

  // The palette navigates in the shell's vocabulary. Two things have to hold
  // of that vocabulary and neither did: the indices have to name the sections
  // they claim to, and the destinations have to be the ones this reader can
  // actually open.
  group('navigation entries', () {
    CommandCatalog catalogFor(Session? session) =>
        CommandCatalog(ApiClient())..session = session;

    Map<String, int> sectionsOf(CommandCatalog catalog) => {
      for (final entry in catalog.navigationEntries)
        if (entry.intent case OpenSectionIntent(:final index))
          entry.label: index,
    };

    test('every section entry points at the section it names', () {
      // The copy that lived here drifted after a rail reorder: "Knowledge
      // layer" opened the identity workbench and "Flows" opened System state,
      // because three indices were written out by hand and never revisited.
      expect(sectionsOf(catalogFor(null)), {
        'Home': homeIndex,
        'Calendar': calendarIndex,
        'My Tasks': tasksIndex,
        'System state': systemStateIndex,
        'Knowledge layer': knowledgeIndex,
        'Flows': flowsIndex,
        'Identities, access & keys': identitiesIndex,
        'Desks': queuesIndex,
        'Expense Reimbursement': expenseWorkspaceIndex,
        'Accounts Payable': payablesWorkspaceIndex,
        'Counterparty identity': identityWorkspaceIndex,
      });
    });

    test('an own-scoped persona is not offered what the rail drops', () {
      // Kim: own scope, no queues, no identity desk. The shell unmounts every
      // one of these for her, so offering them meant navigating to a
      // deliberately blank widget and calling it a destination.
      final kim = catalogFor(_session(scope: 'own', queues: const []));
      final labels = kim.navigationEntries.map((e) => e.label).toSet();

      expect(labels, {'Home', 'Calendar', 'My Tasks', 'Flows'});
      expect(labels, isNot(contains('System state')));
      expect(labels, isNot(contains('Knowledge layer')));
      expect(labels, isNot(contains('New asset')));
      expect(labels, isNot(contains('Accounts Payable')));
    });

    test('a queue binding is what opens a workspace, not a rung', () {
      // Connie and Diane sit on the same rung of the write ladder and own
      // different desks, which is the whole reason a queue binding exists.
      final connie =
          catalogFor(_session(scope: 'own', queues: const ['ap-clerk']));
      final diane =
          catalogFor(_session(scope: 'own', queues: const ['expense-review']));

      final hers = connie.navigationEntries.map((e) => e.label);
      final his = diane.navigationEntries.map((e) => e.label);
      expect(hers, contains('Accounts Payable'));
      expect(hers, isNot(contains('Expense Reimbursement')));
      expect(his, contains('Expense Reimbursement'));
      expect(his, isNot(contains('Accounts Payable')));
    });

    test('reading past your own work opens everything', () {
      // Naviant Support owns no queue at all and still reads the deployment:
      // scope and bindings are orthogonal axes, and a workspace is a Seeing
      // Space before it is a desk.
      final support =
          catalogFor(_session(scope: 'platform', queues: const []));
      final labels = support.navigationEntries.map((e) => e.label);
      expect(labels, contains('Accounts Payable'));
      expect(labels, contains('Expense Reimbursement'));
      expect(labels, contains('System state'));
    });
  });
}

Session _session({required String scope, required List<String> queues}) =>
    Session(
      uid: 'u',
      email: 'u@naviant.com',
      personaId: 'p',
      label: 'P',
      person: 'P',
      title: '',
      authority: 'user',
      scope: scope,
      queues: queues,
      inWorkforce: true,
      impersonatedBy: null,
      maySwitchPersona: false,
      // The workbench's own rule, answered by the server. Off unless a
      // steward desk is held; `platform` reach turns it on there.
      mayOpenIdentity: scope != 'own',
      groups: const [],
    );

/// Mirrors the old, unfenced subsequence rule so a test can assert it was the
/// culprit rather than just asserting the new behaviour.
bool _isSubsequence(String haystack, String needle) {
  var cursor = 0;
  for (final ch in needle.toLowerCase().split('')) {
    final at = haystack.toLowerCase().indexOf(ch, cursor);
    if (at < 0) return false;
    cursor = at + 1;
  }
  return true;
}
