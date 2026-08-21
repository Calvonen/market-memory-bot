# OTA native-compatibility baselines

Per-channel files recording the commit each channel's last EAS build was
produced from. Used by `.github/workflows/deploy-seesam-hub.yml` to decide
whether it's safe to publish an OTA update.

* `preview.sha` - last commit built and shipped to the `preview` channel.
* `production.sha` - last commit built and shipped to the `production` channel.

Each file holds a single full 40-character commit SHA and nothing else.
Neither file exists yet, so OTA publishing for both channels is blocked
(fail-safe) until someone records a baseline after the first EAS build.

See `docs/ota-native-baseline.md` for the full explanation and the exact
steps to create or update these files after a build.
