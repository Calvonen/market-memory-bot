# Expo OTA updates: when it's safe, and when it isn't

`deploy-seesam-hub.yml` never publishes an Expo OTA update automatically.
Every OTA publish is a manual `workflow_dispatch` run where a human picks
the `preview` or `production` channel. There is no automated check in the
workflow that decides whether a given commit is "OTA-safe" - that judgment
call is the responsibility of whoever triggers the run, using the rule
below.

The manual OTA run is a separate job (`publish-ota`) from the backend
deploy job (`deploy`): it never redeploys the backend and never touches
the seesam-hub git checkout. It always publishes whatever commit/ref the
workflow was dispatched from - never the backend host's currently
deployed commit - and refuses to run at all unless dispatched from
`feature/trading-system-foundation`.

It does, however, require that the exact commit being published has
already gone through a successful `deploy` run for that same commit. See
"Backend-deploy gate" below.

## Backend-deploy gate

`publish-ota` refuses to publish unless `github.sha` matches the SHA of
the last backend deploy that passed its health check. This is a plain
state file on the seesam-hub host, not something inferred from run
ordering or the workflow's `concurrency` group:

- `deploy` writes the deployed commit's SHA to
  `/home/marko/marketai-deploy-state/last-deployed-backend.sha` as its
  final step, only after the health check step has already succeeded (a
  failed step stops the job before this one runs).
- `publish-ota`'s first real step reads that file and compares it byte-for-byte
  to `github.sha`. Any mismatch - including the file not existing yet - fails
  the job immediately with a clear error, before checkout, before mobile
  validation, before `eas update` ever runs.

This means: push the commit, wait for `deploy` to go green, *then* run
`workflow_dispatch` for that exact commit. Dispatching for a commit that
hasn't deployed yet, or that's been superseded by a newer deploy, is
refused rather than silently publishing something unverified.

## The rule

| Change | OTA? |
| --- | --- |
| JS/TS source only (screens, components, hooks, styles, business logic) | Yes - manual EAS Update is fine |
| Anything that affects the compiled native binary (see list below) | No - do not use OTA |

An OTA update can only replace the JS bundle. It cannot change anything
that was baked into the native binary at build time. Publishing an OTA
containing a native-affecting change ships a bundle the installed binary
was never built to run, which can crash the app or silently misbehave.

### What counts as a native-affecting change

Anywhere under `mobile/`:

- `package.json`, `package-lock.json` (or `yarn.lock` / `pnpm-lock.yaml`) -
  any native module dependency add/remove/upgrade
- `app.json` / `app.config.js` / `app.config.ts` - app config, plugin
  config, permissions, bundle identifiers, etc.
- `eas.json` - build profiles, channels, environments
- `ios/`, `android/` - native project files
- `plugins/` - custom Expo config plugins
- any `*.podspec`
- **App icon, adaptive icon, and splash screen assets** - these are baked
  into the compiled binary at build time, not loaded from the JS bundle.
  In this project that specifically includes:
  - `mobile/assets/images/icon.png` (`expo.icon`)
  - `mobile/assets/expo.icon/**` (`expo.ios.icon`)
  - `mobile/assets/images/android-icon-foreground.png`,
    `android-icon-background.png`, `android-icon-monochrome.png`
    (`expo.android.adaptiveIcon`)
  - `mobile/assets/images/splash-icon.png` and the `expo-splash-screen`
    plugin config in `app.json` (background color, image width, etc.)

If a change touches any of the above, it is a native change: do not
publish it via OTA.

## Codex P1: bump the runtime version before any further OTA

After shipping a native change in a new build, that build **must** use a
different runtime version than the previous one before any further OTA
updates are published.

This project's `mobile/app.json` sets:

```json
"runtimeVersion": { "policy": "appVersion" }
```

With this policy, the runtime version is derived from `expo.version`.
Expo Updates only delivers an OTA update to a running binary whose runtime
version matches the update's runtime version - so as long as the version
is bumped correctly, Expo itself refuses to serve a mismatched update to
an old binary. That is the actual enforcement mechanism; the workflow does
not need to (and does not) duplicate it.

Concretely, for every native-affecting change:

1. Bump `expo.version` in `mobile/app.json` (e.g. `1.0.0` -> `1.1.0`) as
   part of the same change. This changes the runtime version for the next
   build.
2. Build: `eas build --profile <preview|production> --platform <ios|android|all>`
   from that commit.
3. Ship/install the new build on devices for that channel (internal
   distribution for `preview`, store submission for `production`).
4. Only after that build is installed, later commits can go out as manual
   OTA updates for that channel - as long as they stay JS/TS-only. Expo
   Updates will not deliver them to devices still running the old binary
   on the old runtime version, and will not deliver a stale runtime's
   update to the new binary either.

## Codex P2: icons and splash screens are native, not JS

App icon, adaptive icon, and splash screen assets are compiled into the
native binary at build time (icon resources, splash screen launch assets),
not bundled with the JS. Changing any of the files listed above - even
though they're "just images" - requires a new EAS build under a bumped
runtime version, exactly like a native dependency or `app.json` change.
Never ship an icon or splash update via OTA.

## No automated build

This repository does not run `eas build` automatically anywhere in CI.
Builds are always triggered manually by a human, from the commit they
intend to ship.
