# OTA native-compatibility baselines

`deploy-seesam-hub.yml` refuses to publish an Expo OTA update unless the
commit being deployed is "native-compatible" with the last EAS build that
was actually shipped to the target channel. This document explains how
that check works and how to update it after a new build.

## Why a baseline instead of "changed since last deploy"

An OTA update can only replace the JS bundle - it cannot change anything
that requires a new compiled binary (native dependencies, `app.json`,
`eas.json`, iOS/Android project files, config plugins, ...). The backend
host's previously *deployed* commit says nothing about what was last
*built* with EAS, so it must never be used as the compatibility reference.

Instead, each Expo channel (`preview`, `production`) has its own baseline
file recording the exact commit that channel's currently-installed native
binary was built from:

```
.github/ota-baselines/preview.sha
.github/ota-baselines/production.sha
```

Each file contains exactly one line: the full 40-character commit SHA of
the source the EAS build was produced from. Nothing else.

On every run, the workflow diffs the baseline commit against the commit
being deployed and looks for changes under `mobile/` that affect the
native build: `package.json`, `package-lock.json`, `yarn.lock`,
`pnpm-lock.yaml`, `app.json`, `app.config.{js,ts,json}`, `eas.json`,
anything under `ios/`, `android/`, or `plugins/`, and any `*.podspec`.

* No such changes since the baseline -> OTA is published.
* Any such change since the baseline -> OTA is skipped (push) or the job
  fails loudly (manual `workflow_dispatch`), and **stays blocked for every
  later commit** - including pure JS-only ones - because the baseline file
  does not move on its own. The block only clears once a human updates the
  baseline file to a newer, actually-built commit.

Preview and production are tracked independently: a build shipped to
preview does not unblock production, and vice versa.

## Fail-safe behavior

If the baseline file for a channel is missing, empty, not a valid 40-char
lowercase hex SHA, or its commit isn't reachable in the checkout's history,
the workflow treats that the same as "native change detected" and skips
OTA. It never guesses or falls back to some other commit as a stand-in
baseline.

## Updating the baseline after a new EAS build

1. Run the EAS build for the channel you're shipping, from the exact
   commit you intend to ship (e.g. `eas build --profile preview --platform
   all` from a clean checkout of that commit), and confirm it completes
   and is distributed/installed for that channel.
2. Note the commit SHA the build was produced from:
   ```
   git rev-parse HEAD
   ```
3. Write that SHA (and nothing else) into the channel's baseline file:
   ```
   git rev-parse HEAD > .github/ota-baselines/preview.sha      # or production.sha
   ```
4. Commit and push the updated baseline file, e.g.:
   ```
   git add .github/ota-baselines/preview.sha
   git commit -m "chore: update preview OTA baseline after EAS build"
   git push
   ```
5. Once that commit is on `feature/trading-system-foundation`, subsequent
   deploys are compared against the new baseline, and OTA publishing for
   that channel unblocks (as long as no further native-affecting files
   change after it).

Do this for `preview.sha` and `production.sha` separately - a build for one
channel never updates the other channel's baseline.
