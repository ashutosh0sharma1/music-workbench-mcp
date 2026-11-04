# Contributing to Music Workbench MCP

Start with a small change that somebody can reproduce from a clean checkout. This is an experimental public source release; the README and VALIDATION.md describe the supported scope.

## Find the code

`workbench_server.py` coordinates saved-project copies, rendering and checkpoints; `flp_bytes.py` handles byte-sensitive records; `gemini_server.py` bounds cloud listening.

## Useful contributions

- Add a synthetic FLP fixture that reproduces an unsupported event without bundling a commercial project.
- Make installer recovery explicit after an interrupted download while preserving an existing runtime.
- Document a new FL/plugin compatibility result with version, a synthetic project and observed readback.

## Verify a change

Install the README dependencies, then run the relevant checks:

```sh
runtime\.venv\Scripts\python.exe -m unittest test_gemini_server -v
```

Run only checks appropriate to your change. Do not turn a unit test into a live provider call, model download, deployment or mutation of a real account. Keep optional integration acceptance explicit and report skipped prerequisites.

## Submit a pull request

Explain the observed problem, the resulting behavior, and the evidence used to verify it. Include a small synthetic reproduction for a bug. Keep unrelated formatting out of the change. Preserve upstream copyright/license notices and identify any new dependency and its license. Contributions are made under the applicable repository/source-file license.

Never include credentials, signed URLs, personal recordings, private transcripts, customer data, production exports or unlicensed media. Security reports belong in GitHub's private vulnerability reporting channel when available; do not post secrets in a public issue.
