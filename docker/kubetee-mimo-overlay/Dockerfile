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
#   0004  MIMO TOOL-LOOP FIX - KubeTEE-authored (policy exception, see below).
#         `--tool-call-parser mimo` swallowed output three ways, each of which
#         makes an agent re-issue the same call forever:
#           a) `finish()` was never overridden, so every stream that ended after
#              a tool call DISCARDED all text buffered behind it. The client saw
#              "call, no answer" and asked again.
#           b) `parse_streaming_increment` returned after `calls[0]` and parked
#              the rest of the buffer. With DFlash multi-token chunks, a chunk
#              carrying two complete calls delivered only the first; the second
#              waited for a chunk that never came.
#           c) `detect_and_parse` returned only the text BEFORE the first call,
#              dropping prose between/after calls (non-streaming path).
#         Upstream: sgl-project/sglang#46566 (items 3+4), #41909, #50615.
#         Also fixes a pre-existing bug: the FIRST tool call was passed through
#         as plain text whenever `bot_token` did not appear in the first chunk.
#         Gate: tests/test_mimo_stream_end_flush.py (7 CPU tests; 6 of them FAIL
#         against the stock parser - that is the regression proof).
#   0005  NATIVE Granian mTLS + HEALTH-ONLY PORT - ports the KubeTEE east-west
#         mTLS transport boundary (nim/SGLANG-MTLS-DEPLOYMENT.md, validated live
#         on kimi-k3-1-pro39-sglang-b300-cc-0) and REPLACES the HAProxy mTLS
#         sidecar entirely. Four pieces:
#           a) `serving_hook.self_check_client_cert()` - the engine's own startup
#              self-probes (the /model_info wait, the warmup POST, disagg warmup,
#              /freeze_gc) present the serving pair as a client identity. Without
#              it the probe dies at the TLS handshake and the engine SIGQUITs
#              "Initialization failed. warmup error" AFTER the weights load.
#              Fails CLOSED if --ssl-client-verify lacks the cert/key pair.
#           b) `handle_ssl_validation()` transport constraints -
#              --ssl-client-verify requires --enable-http2 + cert/key/CA;
#              --health-only-port requires mTLS + one tokenizer worker and must
#              differ from --port.
#           c) `health_check.UvicornHealthServer` - a restricted plaintext
#              listener serving ONLY GET /health and /health_generate (all other
#              paths/methods 404) for kubelet probes in a mTLS guest. Not a mount
#              of the FastAPI app - it never serves inference, admin or proxy
#              routes. (CoCo `ExecProcessRequest` stays denied; no exec probes.)
#           d) `http_server` - Granian terminates strict mTLS on :8443 with
#              `ssl_client_verify`, and `ssl_cert`/`ssl_key`/`ssl_ca` are coerced
#              to `Path` (Granian calls `.resolve()` on non-None TLS paths - the
#              CLI supplies `str`, so plain strings crash). The inbound Uvicorn
#              path derives its verify_mode from --ssl-ca-certs via
#              `_inbound_cert_reqs()`: with a CA bundle the listener DEMANDS a
#              client certificate. Without this half the Uvicorn path loaded the
#              CA but left verify_mode CERT_NONE, so a client presenting NO
#              certificate was accepted (TLS, but not mutual TLS and no
#              authentication). Without a CA bundle behaviour is unchanged
#              (plain TLS, CERT_NONE).
#         Cert + TLS state now live in the SGLang process - inside the attested
#         evidence surface. Gates: tests/test_inbound_mtls.py (uvicorn behaviour,
#         fails on stock) + the transport battery in
#         scripts/kimi-k31-granian-transport-test.py (SSE_H2, INFERENCE_MTLS,
#         NO_INFERENCE_PLAINTEXT, HEALTH_ONLY_PATHS, MISSING_CLIENT_CERT_REJECTED,
#         UNTRUSTED_CA_REJECTED, CLEAN_SHUTDOWN).
#
# SOURCE POLICY CARVE-OUT (2026-10-17): `docker/kubetee-overlay/Dockerfile`'s
# "UNMODIFIED upstream commit — no self-authored patches" rule applies to the
# dsv41 variant. This variant exists specifically to carry upstream-AUTHORED
# patches from OPEN PRs — we author nothing here, we ship their diffs. 0003 is
# the minimal reconcile required to make 0001+0002 coexist on this base (it is
# glue, not a new fix). On merge day: delete the merged patches, bump to the
# first nightly containing the merges, rebuild.
#
# 0004 POLICY EXCEPTION (2026-11-24, explicit user approval): 0004 is OUR code,
# not an upstream diff. It is authorized because #46566 was closed by the
# inactivity bot (2026-11-23) rather than fixed, #41909/#50615 are still open
# with no merged change, and the defect silently breaks every agent session on
# this model. Same merge-day rule: when upstream lands an equivalent fix, delete
# 0004, bump to that nightly, and re-run the 7-test gate against it.
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
      ai.kubetee.overlay="mimo: #43518 stale grammar mask + #41738 penalizer cumulate + reconcile + 0004 tool-loop streaming drain/flush; base source byte-stable otherwise"

WORKDIR /sgl-workspace

# Provenance + trust the base's bundled Rust .so at runtime (see header).
ENV SGLANG_BUILD_COMMIT=${SGLANG_SHA} \
    SGLANG_RUST_BUILD_MODE=never

# 1) Receive the patch set from the build context (the workflow's `overlay/`).
COPY overlay/docker/kubetee-mimo-overlay/patches /tmp/patches
COPY overlay/docker/kubetee-mimo-overlay/tests /tmp/mimo-tests
COPY overlay/scripts/kimi-k31-granian-transport-test.py /tmp/mimo-tests/kimi-k31-granian-transport-test.py

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
    MD=$ROOT/python/sglang/srt/function_call/mimo_detector.py; \
    HS=$ROOT/python/sglang/srt/entrypoints/http_server.py; \
    SH=$ROOT/python/sglang/srt/arg_groups/serving_hook.py; \
    SV=$ROOT/python/sglang/srt/arg_groups/fields/serving.py; \
    HC=$ROOT/python/sglang/srt/entrypoints/health_check.py; \
    mkdir -p /tmp/patches "$ROOT/test/registered/unit/spec"; \
    ls -1 /tmp/patches; \
    for p in 0001 0002 0003 0004 0005; do \
      f=$(ls /tmp/patches/${p}-*.patch); \
      [ -f "$f" ] || { echo "FATAL: ${p} patch missing"; exit 1; }; \
    done; \
    echo "--- preimage drift tripwire (base @ ${SGLANG_SHA})"; \
    echo "cfd01653a34b269e95fe6e2c75fac9a5ff60acaba270250f39cd911707bfdad2  $DF" | sha256sum -c -; \
    echo "395ddca12d82243c827720ca549d7b6db14b7e1f98e55330770582087e859684  $MD" | sha256sum -c -; \
    echo "5586b34fe77a30949e74cce41a77dfe64513936d38f2d6fe5df6747183636fc4  $HS" | sha256sum -c -; \
    echo "b973bbd083503e5e7ec67462ae6b4f9cda7cc146bc074fc694864f582e2038d6  $SH" | sha256sum -c -; \
    echo "d076f80d479b362062f43f897e017bfd69f0341bd0268018e3eae581dcc473a8  $SV" | sha256sum -c -; \
    echo "--- applying patch set 0001 -> 0002 -> 0003 -> 0004 -> 0005"; \
    for p in 0001 0002 0003 0004 0005; do \
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
    echo "928dea26b5570d8aeceffae84599ec35ab66272d97263dd85ab187f4e791b8d9  $MD" | sha256sum -c -; \
    echo "0801b3c206422bb83e83420bebfe261c625111cd5aa39d32f0ab7d38c9f976ce  $HS" | sha256sum -c -; \
    echo "3bb6dcd28243dcae4c20f6c59e671459e3c393a0d532dacc0bd7a051d96ab138  $SH" | sha256sum -c -; \
    echo "df87b8ee3d6707b2596a95150bc5245023248fa5e4e679ae89c724836de145bd  $SV" | sha256sum -c -; \
    echo "c11df3a8d8f02445a735905bfb3b2f19b722c901b0e816c5f83addc7ee2a028b  $HC" | sha256sum -c -; \
    echo "--- smoke check: the two upstream unit tests against the patched tree"; \
    cd "$ROOT/test/registered/unit/spec"; \
    /opt/sglang/bin/python -m unittest test_dflash_verify_grammar_mask test_dflash_penalizer_cumulate -v

# 2b) 0004 (MiMo streaming drain + finish() flush) has no upstream test to run,
#     so its own CPU regression suite is the gate. It drives the REAL detector
#     with a stubbed entrypoints/environ import surface (the base image has
#     torch, so the real modules work too — the stub only keeps this step fast).
#     These 7 tests fail 6/7 on the stock parser; that is the proof they test
#     something (see the patch header).
RUN set -eu; \
    ROOT=/sgl-workspace/sglang; \
    cd "$ROOT"; \
    /opt/sglang/bin/python -m unittest discover \
      -s /tmp/mimo-tests -p 'test_mimo_stream_end_flush.py' -v

# 2c) 0005 (inbound mTLS) — a REAL TLS listener test, not a mock: it stands up
#     uvicorn exactly as SGLang configures it and asserts that a client with NO
#     certificate is refused when --ssl-ca-certs is set. On stock SGLang that
#     client connects (verify_mode stays CERT_NONE) — an auth bypass for any
#     deployment relying on client certs, so this gate is mandatory.
RUN set -eu; \
    cd /sgl-workspace/sglang; \
    /opt/sglang/bin/python -m pytest /tmp/mimo-tests/test_inbound_mtls.py -v

# 2d) 0005 transport battery — boots the REAL engine transport (Granian HTTP/2
#     mTLS on :8443 + the health-only Uvicorn on :8000) with a stubbed engine and
#     runs the 7 checks of the deploy guide: HTTP/2 SSE over mTLS, inference over
#     mTLS, plaintext inference rejected, health-only route gating, missing
#     client cert rejected at handshake, untrusted-CA client rejected, clean
#     shutdown. This is the strongest gate — it exercises self_check_client_cert,
#     the Path coercion, ssl_client_verify wiring and UvicornHealthServer
#     startup together.
RUN set -eu; \
    export SGLANG_TRANSPORT_LAUNCHER=/tmp/mimo-tests/kimi-k31-granian-transport-test.py; \
    export SGLANG_TEST_CERTS=/tmp/mtls-transport-certs; \
    rm -rf "$SGLANG_TEST_CERTS"; \
    cd /sgl-workspace/sglang; \
    /opt/sglang/bin/pip install -q h2 requests 2>/dev/null || \
      /opt/sglang/bin/python -m pip install -q h2 requests; \
    /opt/sglang/bin/python /tmp/mimo-tests/test_granian_mtls_health_only.py

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
from sglang.srt.function_call.base_format_detector import BaseFormatDetector
print("penfix1 overlay import OK:", getattr(sglang, "__version__", "n/a"))
print("   dflash_utils:        ", d.__file__)
print("   ScheduleBatch:       ", ScheduleBatch.__module__)
print("   orchestrator:        ", BatchedPenalizerOrchestrator.__module__)
print("   rust_tree_core ext:  ", rtc.__file__)
# Guardrail: the multi-token cumulate API (#41738) must exist after the patch set.
assert hasattr(ScheduleBatch, "cumulate_penalty_output_tokens_since_last"), "41738 cursor missing"
assert hasattr(BatchedPenalizerOrchestrator, "cumulate_output_tokens_multi"), "41738 orchestrator API missing"
print("   41738 API present: cumulate_penalty_output_tokens_since_last + cumulate_output_tokens_multi")

# Guardrail: 0004 must be the only finish() among the MiMo detector's ancestry,
# and the drain loop must actually be present.
from sglang.srt.function_call.mimo_detector import MiMoDetector
assert "finish" in MiMoDetector.__dict__, "0004 missing: MiMoDetector.finish not overridden"
assert MiMoDetector.finish is not BaseFormatDetector.finish, "0004 missing: finish() is the base no-op"
print("   0004 present: MiMoDetector.finish overridden")
PY

CMD ["/bin/bash"]
