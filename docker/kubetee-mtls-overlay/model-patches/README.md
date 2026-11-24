# Model-layer patches (applied a la carte, NOT in the base image)

The `kubetee-mtls-overlay` image carries **only** the transport layer. Each patch
below is a **single self-contained layer** applied on top of that base — via an
extra build stage (`--build-arg MODEL_PATCHES="0002"` + a `COPY`/`RUN git apply`
layer) or, for an urgent hotfix, by patching the editable tree in a pod's
initContainer. **Only apply a patch when the defect is reproduced on the clean
v0.5.23 base**, per the 2026-11-24 decision.

| Patch | Defect it fixes | Files | Repro gate (apply ONLY if this reproduces) | Upstream status |
|---|---|---|---|---|
| `0001-dflash-verify-grammar-mask` | DFlash speculative-verify grammar mask | `srt/speculative/` | A spec-decode accept/reject divergence with grammar masks on | — |
| `0002-dflash-penalizer-cumulate` | DFlash penalties don't cumulate across spec tokens | `srt/sampling/penalizers/` | Repeated-token penalty ignored during spec decode | — |
| `0003-mimo-stream-parse` | MiMo-V2.6 `mimo-v2.6-pro` tool-loop prose flush + multi-call drain | `srt/function_call/mimo_detector.py` | Tool-loop: trailing prose after a tool call is dropped; only the first tool call in a chunk is emitted | tool-parser rewrite (PRs #41649/#42583 closed; #41909/#43518 open) |
| `0004-mimo-parser` | MiMo tool-call arg coercion (`"null"`→None, bool) | `srt/function_call/` | A tool arg's literal `"null"` string or `"true"`/`"false"` is coerced wrongly | #43046 (open), #46566 (open) |
| `0005-mimo-args` | MiMo arg-group drift | `srt/arg_groups/` | — | #41738/#43207 (open) |
| `0006-mimo-b105` | MiMo B105 quirk | `srt/function_call/` | — | — |

## How to apply one

Every patch is a standard `git apply` diff against the **v0.5.23 + transport**
tree (the same layout the base image patches). To add, e.g., the MiMo parser
patch to a test image:

```dockerfile
ARG BASE_IMAGE=ghcr.io/kubetee-ai/sglang:v0.5.23-kubetee-mtls1@sha256:<digest>
FROM ${BASE_IMAGE} AS model
COPY model-patches/0004-mimo-parser.patch /tmp/kubetee-model/
RUN git -C /sgl-workspace/sglang apply -v /tmp/kubetee-model/0004-mimo-parser.patch \
 && echo "model patch 0004 applied"
ENV SGLANG_MODEL_PATCHES="0004-mimo-parser"
```

Add the patch's own pre/post sha tripwires (copy the pattern from the base
`Dockerfile`) and its unit test to the build gate when the patch is authored.

## Current status (2026-11-24)

These five/six model patches exist on `kubetee/mimo-penfix-r4-mtls-h2`
(`docker/kubetee-mimo-overlay/patches/`) but have **not** been ported here yet,
because none is a proven repro on v0.5.23 yet. Port one here the moment its
repro fires. The MiMo live-loop (the `50 empty tool calls` spin) is still under
diagnosis (agent-side vs `serving_chat.py` tool-call parsing) and has **no**
patch here yet — do not add one until the repro is pinned.
