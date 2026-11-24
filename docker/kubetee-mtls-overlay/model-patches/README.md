# Model-layer patches (applied a la carte, NOT in the base image)

The `kubetee-mtls-overlay` image carries **only** the transport layer. Each patch
below is a **single self-contained layer** applied on top of that base — via an
extra build stage (`COPY` + `RUN git -C /sgl-workspace/sglang apply`) or, for an
urgent hotfix, by patching the editable tree in a pod's initContainer. **Only
apply a patch when the defect is reproduced on the clean v0.5.23 base**, per the
2026-11-24 decision.

These are the **upstream-AUTHORED but UNMERGED** PR patches carried on
`kubetee/mimo-penfix-r4-mtls-h2` (copied verbatim). They are authored by upstream
contributors, not by KubeTEE.

| Patch | Fix | Upstream PR | Repro gate (apply ONLY if this reproduces on v0.5.23) |
|---|---|---|---|
| `0001-dflash-verify-stale-grammar-mask.patch` | DFlash speculative-verify stale grammar mask | [#43518](https://github.com/sgl-project/sglang/pull/43518) (open) | spec-decode grammar-mask accept/reject divergence |
| `0002-dflash-penalizer-cumulate.patch` | DFlash penalties don't cumulate across spec tokens | [#41738](https://github.com/sgl-project/sglang/pull/41738) (open) | repeated-token penalty ignored during spec decode |
| `0003-verify-penalty-reconcile.patch` | reconcile patch making 0001+0002 coexist on this base | (KubeTEE glue) | only together with 0001+0002 |
| `0004-mimo-stream-drain-and-flush.patch` | MiMo tool-loop: flush trailing prose + drain multi-call chunks | [#43518](https://github.com/sgl-project/sglang/pull/43518)-class / MiMo detector | MiMo tool-loop: trailing prose after a tool call dropped; only first tool call per chunk emitted |

**NOT ported here yet** (nothing reproduced on v0.5.23 so far): the MiMo
arg-coercion fixes (#43046 `"null"`→None / bool, #46566) and the MiMo arg-group
drift (#41738/#43207). The live MiMo "50 empty tool calls" spin is still under
diagnosis (agent-side vs `serving_chat.py` tool-call parsing) and has **no**
patch here — do not add one until the repro is pinned.

## How to apply one

Each patch is a standard `git apply` diff against the **v0.5.23 + transport**
tree (the same layout the base image patches). To add, e.g., the MiMo
stream-drain patch to a test image:

```dockerfile
ARG BASE_IMAGE=ghcr.io/kubetee-ai/sglang:v0.5.23-kubetee-mtls1@sha256:<digest>
FROM ${BASE_IMAGE} AS model
COPY overlay/docker/kubetee-mtls-overlay/model-patches/0004-mimo-stream-drain-and-flush.patch /tmp/kubetee-model/
RUN git -C /sgl-workspace/sglang apply -v /tmp/kubetee-model/0004-mimo-stream-drain-and-flush.patch \
 && echo "model patch 0004 applied"
```

Add the patch's own pre/post sha tripwires (copy the pattern from the base
`Dockerfile`) and its unit test to the build gate when the patch is authored.

## On merge day

When an upstream PR merges and lands in a release/nightly: delete its patch from
this directory, bump `kubetee-overlay` to the base containing the merge, rebuild.
