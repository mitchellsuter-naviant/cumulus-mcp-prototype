# cumulus_admin

Flutter **web** admin UI for the Cumulus knowledge layer: browse the taxonomy,
search, create/edit/delete assets, and manage platform users and their roles.

See the [root README](../README.md) for the full picture; this file covers only
what is specific to the Flutter app.

## Run

```bash
flutter pub get
flutter run -d chrome --dart-define=API_BASE_URL=http://localhost:8080 --dart-define=DEV_AUTH=true
```

`DEV_AUTH=true` skips the Firebase sign-in gate (the committed
`firebase_options.dart` is real, so without it the app demands a Firebase
login even against a local server running `AUTH_BACKEND=none`). Local use
only — never set it on a hosted build.

Inside the dev container there is no display — use
`-d web-server --web-port 3000 --web-hostname 0.0.0.0` and your host browser.

## Build-time configuration

| `--dart-define` | Default | Purpose |
| --- | --- | --- |
| `API_BASE_URL` | `http://localhost:8080` | Base URL of the FastAPI server |
| `API_KEY` | empty | `X-API-Key` machine credential — **local use only** |
| `DEV_AUTH` | `false` | Skip the Firebase gate for local dev — **local use only** |

> **Do not set `API_KEY` for a hosted build.** A dart-define is compiled into the
> JavaScript bundle and is readable by anyone who loads the page, and the key
> carries owner privileges. Hosted builds authenticate with Firebase ID tokens.

## Auth modes

`main()` calls `Firebase.initializeApp()`. If
[lib/firebase_options.dart](lib/firebase_options.dart) still holds the
`REPLACE_ME` placeholders, that call throws, the app catches it, and
`AuthService.devMode` is set — no login screen, and every request goes out
unauthenticated. Pair this with a server running `AUTH_BACKEND=none`, which
treats every caller as an owner. "Sign out" is a no-op in dev mode.

Against a server with `AUTH_BACKEND=firebase`, **all** requests need a
credential — reads included — so the app attaches the Firebase ID token to every
call, not just writes.

For real auth, run `flutterfire configure --platforms=web` to regenerate
`firebase_options.dart`, and set `AUTH_BACKEND=firebase` on the server. Roles
(`owner > admin > user`) come from the `role` custom claim on the Firebase user.

## Layout

| Path | Contents |
| --- | --- |
| [lib/services/api_client.dart](lib/services/api_client.dart) | HTTP wrapper over the REST API |
| [lib/services/auth_service.dart](lib/services/auth_service.dart) | FirebaseAuth wrapper + dev-mode fallback |
| [lib/models/](lib/models/) | `Asset`, `AssetSummary`, `SearchHit`, `PlatformUser` |
| [lib/screens/](lib/screens/) | Login, asset list, asset editor, user management |

## Behaviour notes

- Every request carries a 20s timeout; past that the UI surfaces
  `ApiException(408, …)` instead of spinning indefinitely.
- Screens dispose their controllers and close their `ApiClient` (and its
  connection pool) in `dispose()`.

## Checks

```bash
flutter analyze
```

There are no widget tests yet.
