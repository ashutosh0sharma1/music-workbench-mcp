# Validation

Release preparation: 2026-11-04. These are results from the clean source export on Windows; the workflow badge reports subsequent fresh Linux CI results.

## Verified

3 Gemini safety tests and 4 offline FL project/checkpoint tests passed. The pinned file backend installer fetched and hash-verified 95 upstream files.

## Limits

CI runs the Gemini safety subset. Offline workbench tests used the installed patched upstream Python environment and a newly generated eight-second tone. Full Cargo/runtime reconstruction and live FL/plugin control were not rerun for this export.

Provider credentials were excluded from test environments. Test success does not establish production readiness or compatibility with every machine. Reproduce the documented checks after installing dependencies; report failures with synthetic fixtures and environment versions.
