import 'package:cumulus_admin/models/session.dart';
import 'package:cumulus_admin/services/api_client.dart';
import 'package:cumulus_admin/widgets/command_palette.dart';
import 'package:cumulus_admin/widgets/cumulus_shell.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// The top bar's identity cluster. The persona switcher used to be a control
/// of its own beside the avatar; it now flies out of the account menu, and
/// these pin the three things that move with it: the roster is reachable but
/// not in the way, it opens to the side rather than over the account's own
/// actions, and an impersonation still says whose session you are in without
/// opening anything.
void main() {
  testWidgets('the roster flies out beside the account menu, not into it', (
    tester,
  ) async {
    final switched = <String>[];
    await _pumpShell(
      tester,
      session: _session(),
      onSwitchPersona: switched.add,
    );

    // Nothing in the bar names a persona until the menu is opened.
    expect(find.text('Kim Nowak'), findsNothing);

    await tester.tap(find.byTooltip('mitchell@naviant.com'));
    await tester.pumpAndSettle();

    // The account menu names who you are acting as and offers the roster
    // without unrolling it over the account's own actions.
    expect(find.text('Switch persona'), findsOneWidget);
    expect(find.text('2 personas on the roster'), findsOneWidget);
    expect(find.text('Employee Hub'), findsOneWidget);
    expect(find.text('Sign out'), findsOneWidget);
    expect(find.text('CUSTOMER USERS'), findsNothing);
    expect(find.text('Kim Nowak'), findsNothing);

    await tester.tap(find.text('Switch persona'));
    await tester.pumpAndSettle();

    // Beside the menu that opened it, on the only side with room: the account
    // button is the last thing on the bar.
    expect(find.text('CUSTOMER USERS'), findsOneWidget);
    expect(find.text('Kim Nowak'), findsOneWidget);
    expect(find.text('Sign out'), findsOneWidget);
    expect(
      tester.getRect(find.text('Kim Nowak')).right,
      lessThan(tester.getRect(find.text('Sign out')).left),
    );

    await tester.tap(find.text('Kim Nowak'));
    await tester.pumpAndSettle();

    expect(switched, ['kim']);
    // Choosing a persona closes the whole tree, not just the flyout.
    expect(find.text('Sign out'), findsNothing);
  });

  testWidgets('an impersonation names both people with the menu shut', (
    tester,
  ) async {
    await _pumpShell(
      tester,
      session: _session(personaId: 'kim', impersonatedBy: 'uid-mitchell'),
    );

    expect(find.text('MITCHELL SUTER · ACTING AS'), findsOneWidget);
    expect(find.text('Kim Nowak'), findsOneWidget);
  });

  testWidgets(
    'a persona who cannot switch gets an account menu, not a roster',
    (tester) async {
      await _pumpShell(
        tester,
        session: _session(personaId: 'kim', maySwitchPersona: false),
        onSwitchPersona: null,
      );

      await tester.tap(find.byTooltip('mitchell@naviant.com'));
      await tester.pumpAndSettle();

      expect(find.text('Switch persona'), findsNothing);
      expect(find.text('Sign out'), findsOneWidget);
    },
  );
}

Future<void> _pumpShell(
  WidgetTester tester, {
  required Session session,
  ValueChanged<String>? onSwitchPersona,
}) async {
  final api = ApiClient();
  addTearDown(api.close);
  final palette = CommandPaletteController(
    api: api,
    catalog: CommandCatalog(api),
    onIntent: (_) {},
  );
  addTearDown(palette.dispose);

  tester.view.physicalSize = const Size(1400, 900);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(
    MaterialApp(
      home: CumulusShell(
        destinations: const [
          RailDestination(icon: Icons.home_outlined, label: 'Home', index: 0),
        ],
        selectedIndex: 0,
        onDestinationSelected: (_) {},
        palette: palette,
        accountLabel: 'mitchell@naviant.com',
        session: session,
        onSwitchPersona: onSwitchPersona,
        child: const SizedBox.shrink(),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

Session _session({
  String personaId = 'mitchell',
  String? impersonatedBy,
  bool maySwitchPersona = true,
}) {
  final me = _persona(
    id: 'mitchell',
    uid: 'uid-mitchell',
    label: 'Naviant Support',
    person: 'Mitchell Suter',
    title: 'Platform engineer',
  );
  final kim = _persona(
    id: 'kim',
    uid: 'uid-kim',
    label: 'AP Clerk',
    person: 'Kim Nowak',
    title: 'Accounts payable',
  );
  final acting = personaId == 'kim' ? kim : me;
  return Session(
    uid: acting.uid,
    email: acting.email,
    personaId: acting.id,
    label: acting.label,
    person: acting.person,
    title: acting.title,
    authority: 'user',
    scope: 'platform',
    queues: const [],
    inWorkforce: true,
    impersonatedBy: impersonatedBy,
    maySwitchPersona: maySwitchPersona,
    mayOpenIdentity: true,
    workspaces: const [],
    groups: [
      PersonaGroup(label: 'Naviant', personas: [me]),
      PersonaGroup(label: 'Customer users', personas: [kim]),
    ],
  );
}

PersonaOption _persona({
  required String id,
  required String uid,
  required String label,
  required String person,
  required String title,
}) => PersonaOption(
  id: id,
  uid: uid,
  label: label,
  title: title,
  person: person,
  email: '$id@naviant.com',
  authority: 'user',
  scope: 'own',
  queues: const [],
  blurb: 'A test subject.',
  inWorkforce: true,
);
