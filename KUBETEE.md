# KubeTEE SGLang fork

Public fork of [sgl-project/sglang](https://github.com/sgl-project/sglang) used to publish a patched serving image:

`ghcr.io/kubetee-ai/sglang:v0.5.18-abort-disconnect` (`linux/amd64`)

## Branch `v0.5.18-abort-disconnect`

Based on tag **v0.5.18** (`71de97b264`). Carries [sgl-project/sglang#35936](https://github.com/sgl-project/sglang/pull/35936) **current head** only:

- On `generate_request` cleanup (`CancelledError` / `GeneratorExit` / any `BaseException`), dispatch `AbortReq` for each still-tracked rid **before** popping `rid_to_state`.
- Do **not** remove the `abort_request()` `rid not in rid_to_state` guard. Scheduler abort matching is `req.rid.startswith(...)`; a stale prefix such as `batch` would cancel live `batch_0`…`batch_10`.

This restores the v0.5.17 (#32588) disconnect abort that #34160 broke on the way to v0.5.18.

Not vendored: [#34894](https://github.com/sgl-project/sglang/pull/34894) (keep dispatched state + 2s delayed abort). Larger surface, still waits 2s, conflicts with later `main`.

## Image

Thin overlay — `FROM lmsysorg/sglang:v0.5.18@sha256:bde16a8447…` plus the patched `tokenizer_manager.py`. Workflow: `.github/workflows/kubetee-image.yml` (`workflow_dispatch`, `linux/amd64`).

Upstream GitHub Actions workflows are removed on this branch so a push does not start the official SGLang CI on the fork. Re-add them only if this branch is retired.

## Drop when upstream merges

When #35936 (or equivalent) lands in a release **after** v0.5.18, delete this overlay and pin `lmsysorg/sglang` again.
