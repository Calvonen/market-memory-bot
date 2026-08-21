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

## Concurrency is isolated per job

`deploy` and `publish-ota` each have their own job-level `concurrency`
group - there is no shared/top-level group for the workflow:

- `deploy` uses the single fixed group `marketai-backend-deploy`, so
  backend deploys still serialize among themselves.
- `publish-ota` uses `marketai-ota-<channel>` (i.e. `marketai-ota-preview`
  or `marketai-ota-production`, depending on the channel picked at
  dispatch time), so two OTA runs for the same channel serialize, but a
  `preview` run and a `production` run never block each other.

Because these groups never overlap, a manual OTA dispatch can never
queue behind, cancel, or replace a pending/running backend deploy, and a
push-triggered deploy can never do the same to a pending/running OTA
run. `cancel-in-progress: false` on both, so even within a group runs
queue rather than cancelling each other.

## Backend-deploy gate

`publish-ota` refuses to publish unless `github.sha` matches the SHA of
the last backend deploy that passed its health check. This is a plain
state file on the seesam-hub host, not something inferred from run
ordering or either job's concurrency group:

- `deploy`'s locked step (see "Filesystem lock" below) writes the
  deployed commit's SHA to
  `/home/marko/marketai-deploy-state/last-deployed-backend.sha` only
  after the fast-forward, service restart and health check have all
  already succeeded.
- `publish-ota` checks this twice: an early, unlocked check right after
  the branch restriction (fails fast, before spending time on checkout
  and mobile validation), and an authoritative, locked re-check
  immediately before `eas update` runs. Either one failing - including
  the file not existing yet - refuses to publish with a clear error.

This means: push the commit, wait for `deploy` to go green, *then* run
`workflow_dispatch` for that exact commit. Dispatching for a commit that
hasn't deployed yet, or that's been superseded by a newer deploy, is
refused rather than silently publishing something unverified.

## Filesystem lock closes the approve/publish race

The early check above is a convenience, not a guarantee: `github.sha`
could match the deployed SHA when that check runs and no longer match by
the time `eas update` actually executes a few seconds/minutes later, if a
new `deploy` run lands on the backend in between. GitHub's concurrency
groups don't prevent this either - they only stop `deploy` and
`publish-ota` runs from racing each other at the *scheduling* level, they
say nothing about interleaving within the time between one job's steps.

To close that gap, both jobs take the same exclusive `flock` on
`/tmp/marketai-deploy-ota.lock` (host-local, both jobs run on the same
seesam-hub self-hosted runner) around their respective critical
sections:

- `deploy`'s "Deploy backend to seesam-hub (locked)" step holds the lock
  for the entire fast-forward -> dependency install -> service restart ->
  health check -> state-file write sequence.
- `publish-ota`'s "Verify deployed SHA and publish OTA update (locked)"
  step acquires the *same* lock, re-reads the state file, re-compares it
  to `github.sha`, and only then runs `eas update` - all still holding the
  lock. It releases only once `eas update` has finished (success or
  failure).

Because both critical sections hold the same lock, they can never
interleave: `deploy` cannot advance the state file while `publish-ota` is
between its locked re-check and the actual `eas update` call, and
`publish-ota` cannot publish based on a SHA that `deploy` is only
partway through changing.

Both `flock` calls use `-w 300` (a 5-minute wait), so a stuck lock holder
fails the *other* job with a clear error after 5 minutes instead of
hanging it forever.

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
