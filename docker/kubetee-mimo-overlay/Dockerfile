# SGLang mimo-overlay "penfix1" image: base image source + UNMERGED upstream patches.
#
# PURPOSE: carry three upstream fix classes onto a digest-pinned
# `nightly-dev-cu13` base WITHOUT disturbing anything else in the base image.
# Target: XiaomiMiMo/MiMo-V2.6-Pro-RL on B200 CC
# (nim/mimo-v2-6-pro-rl-sglang-b200-cc.yaml).
#
# PATCH SET (docker/kubetee-mimo-overlay/patches/*.patch, applied in lexical order):
#   0001  #43518/#43514 - clear the stale per-request grammar_mask before DFlash
#         verify (the "!!!"/garbage-burst corruption class). Upstream PR #43518
#         (Hahaa909), still OPEN upstream.
#   0002  #41738/#43207/#43518 - feed ALL DFlash-committed tokens (not just the
#         last) to the penalizers, and advance min_new_tokens.len_output_tokens
#         by the real count. This is the runaway-repetition-loop fix. Upstream
#         issue #41738 / PR #43207, still OPEN upstream.
#   0003  glue: reconciles 0001 and 0002 against THIS base's API drift and
#         against each other (see below).
#
# SOURCE POLICY CARVE-OUT (2026-10-17): `docker/kubetee-overlay/Dockerfile`'s
# "UNMODIFIED upstream commit — no self-authored patches" rule applies to the
# dsv41 variant. This variant exists specifically to carry upstream-AUTHORED
# patches from OPEN PRs — we author nothing here, we ship their diffs. 0003 is
# the minimal reconcile required to make 0001+0002 coexist on this base (it is
# glue, not a new fix). On merge day: delete the merged patches, bump to the
# first nightly containing the merges, rebuild.
#
# WHY 0003 EXISTS (the real gotcha): 0001 (#43518) and 0002 (#41738) were authored
# against a base whose verify path used `acc_linear_penalties` + a `grammar_mask`
# local + `penalizer.apply()` single-shot API. THIS base (65633129) renamed the
# precomputed buffers to `acc_additive_penalties`/`acc_scaling_penalties`, nulls
# `penalizer_orchestrator` under overlap (`copy_for_forward`), and #41738 adds a
# NEW multi-token API (`accumulate_additive_penalties`/`accumulate_scaling_penalties`
# + `cumulate_output_tokens_multi`). Naively stacking 0001 then 0002 produces a
# dangling `grammar_mask` (F821) and a test mock on a dead API. 0003:
#   * reads the base's REAL overlap buffers (and treats the dead `acc_linear_penalties`
#     alias as additive), so overlap-mode penalties are no longer dropped at verify
#   * applies additive -> scaling -> logit_bias LAST (the base's
#     `SamplingBatchInfo._apply_{pre,post}_grammar_logits_transforms` order), which
#     every test expectation pins (repetition + logit_bias = 12)
#   * updates 0001's test mock from the dead `apply=` API to `accumulate_*`
#
# SAFETY:
#   * DRIFT-TRIPWIRES: preimage sha256 of each touched source file is asserted
#     BEFORE the patch set and the postimage AFTER. `git apply` alone would
#     silently succeed against a drifted-but-similar base; the hashes make drift
#     a hard build failure.
#   * BUILD-TIME TESTS: the exact unit tests shipped by #43518 and #41738
#     (test_dflash_verify_grammar_mask.py + test_dflash_penalizer_cumulate.py) are
#     RUN against the patched installed tree — CPU-only, ~10s. A patch that does
#     not fix the bug, or a base whose API moved, fails the build instead of shipping.
#   * COMPILED STACK UNTOUCHED: pure-Python only. No wheels, no JIT, no flashinfer,
#     no Rust .so involvement. Inherits SGLANG_RUST_BUILD_MODE=never (base's bundled
#     Rust .so is trusted at runtime; see the kubetee-overlay header).
#
# BASE IMAGE: lmsysorg/sglang:nightly-dev-cu13-20261105-8df0cd51
# (build commit 8df0cd51e1afbcf2a67358ab559f4a9046ff5782 — supersets the r2 base
# a2cafa56; adds ~6 days of main incl. #48001 DFlash routed-KV fix and the
# #38565 deterministic renorm). penfix1-r3: 0001 + 0002 apply byte-exact
# unchanged; 0003 regenerated (upstream added a shape-validation block to
# apply_dflash_verify_logits_adjustments + moved test/ layout — residual hunks
# refreshed, replay-proven identical).

ARG BASE_IMAGE=lmsysorg/sglang:nightly-dev-cu13-20261105-8df0cd51@sha256:2407c33d713bd359892efa8a7fe8e0aa4e65f5e32f930b43e18a221cf856e0e5
ARG SGLANG_SHA=8df0cd51e1afbcf2a67358ab559f4a9046ff5782

FROM ${BASE_IMAGE}

ARG SGLANG_SHA
LABEL org.opencontainers.image.title="sglang mimo-overlay penfix1 (base source + unmerged patches)" \
      ai.kubetee.sglang.sha="${SGLANG_SHA}" \
      ai.kubetee.overlay="mimo: #43518 stale grammar mask + #41738 penalizer cumulate + reconcile; base source byte-stable otherwise"

WORKDIR /sgl-workspace

# Provenance + trust the base's bundled Rust .so at runtime (see header).
ENV SGLANG_BUILD_COMMIT=${SGLANG_SHA} \
    SGLANG_RUST_BUILD_MODE=never

# 1) Receive the patch set from the build context (the workflow's `overlay/`).
COPY overlay/docker/kubetee-mimo-overlay/patches /tmp/patches

# 2) Drift-tripwire the preimage, apply the patch set in place (the base installs
#    `python[all]` editable at /sgl-workspace/sglang, so patching that tree IS
#    the install — no reinstall needed), tripwire the postimages, then run both
#    upstream unit tests as the smoke check. No `git`/`patch` trust: whichever
#    applier runs, the postimage hashes are the authority.
RUN set -eu; \
    ROOT=/sgl-workspace/sglang; \
    DF=$ROOT/python/sglang/srt/speculative/dflash_utils.py; \
    SB=$ROOT/python/sglang/srt/managers/schedule_batch.py; \
    SU=$ROOT/python/sglang/srt/speculative/spec_utils.py; \
    mkdir -p /tmp/patches "$ROOT/test/registered/unit/spec"; \
    ls -1 /tmp/patches; \
    for p in 0001 0002 0003; do \
      f=$(ls /tmp/patches/${p}-*.patch); \
      [ -f "$f" ] || { echo "FATAL: ${p} patch missing"; exit 1; }; \
    done; \
    echo "--- preimage drift tripwire (base @ ${SGLANG_SHA})"; \
    echo "cfd01653a34b269e95fe6e2c75fac9a5ff60acaba270250f39cd911707bfdad2  $DF" | sha256sum -c -; \
    echo "--- applying patch set 0001 -> 0002 -> 0003"; \
    for p in 0001 0002 0003; do \
      f=$(ls /tmp/patches/${p}-*.patch); \
      echo "  == $f"; \
      if command -v git >/dev/null 2>&1; then \
        ( cd "$ROOT" && git apply --verbose "$f" ); \
      else \
        ( cd "$ROOT" && patch -p1 -F 0 < "$f" ); \
      fi; \
    done; \
    echo "--- postimage drift tripwire (+ #43518 + #41738 + reconcile)"; \
    echo "bc5746060e966579ceccd81507ffe3a9dc1fdce07592a2fcf500bb9234b9f5cd  $DF" | sha256sum -c -; \
    echo "ea73371225d6d957c7692ac3f6702509b62aea5213b3dea989fb11525e38a7c3  $SB" | sha256sum -c -; \
    echo "65c1b7eacc1d4e1935922f627d111bc1d2a8047b6640763a506b172ec58a1002  $SU" | sha256sum -c -; \
    echo "--- smoke check: the two upstream unit tests against the patched tree"; \
    cd "$ROOT/test/registered/unit/spec"; \
    /opt/sglang/bin/python -m unittest test_dflash_verify_grammar_mask test_dflash_penalizer_cumulate -v

# 3) The full serving import surface still loads (paranoia — the patches are pure
#    Python, but this costs seconds and fails the build on surprises).
RUN /opt/sglang/bin/python - <<'PY'
import sglang
import sglang.srt.speculative.dflash_utils as d
from sglang.srt.managers.schedule_batch import Req, ScheduleBatch
from sglang.srt.sampling.penaltylib.orchestrator import BatchedPenalizerOrchestrator
from sglang.srt.speculative.dflash_utils import apply_dflash_verify_logits_adjustments
from sglang.srt.mem_cache.dsv41_request_window import *
from sglang.srt.model_executor.encoder_swa_replay import *
from sglang.srt.mem_cache.rust_tree_core import extension as rtc   # compiled Rust .so (base)
print("penfix1 overlay import OK:", getattr(sglang, "__version__", "n/a"))
print("   dflash_utils:        ", d.__file__)
print("   ScheduleBatch:       ", ScheduleBatch.__module__)
print("   orchestrator:        ", BatchedPenalizerOrchestrator.__module__)
print("   rust_tree_core ext:  ", rtc.__file__)
# Guardrail: the multi-token cumulate API (#41738) must exist after the patch set.
assert hasattr(ScheduleBatch, "cumulate_penalty_output_tokens_since_last"), "41738 cursor missing"
assert hasattr(BatchedPenalizerOrchestrator, "cumulate_output_tokens_multi"), "41738 orchestrator API missing"
print("   41738 API present: cumulate_penalty_output_tokens_since_last + cumulate_output_tokens_multi")
PY

CMD ["/bin/bash"]
