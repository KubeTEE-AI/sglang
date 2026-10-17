# SGLang mimo-overlay image: base image source + UNMERGED upstream patches.
#
# PURPOSE: carry an upstream-authored but UNMERGED fix (PR #43518 — the MiMo
# DFlash/DSpark stale-grammar-mask verify loop, issue #43514) onto a
# digest-pinned `nightly-dev-cu13` base WITHOUT disturbing anything else in
# the base image. Target: XiaomiMiMo/MiMo-V2.6-Pro-RL on B200 CC
# (nim/mimo-v2-6-pro-rl-sglang-b200-cc.yaml).
#
# SOURCE POLICY CARVE-OUT (2026-10-17): `docker/kubetee-overlay/Dockerfile`'s
# "UNMODIFIED upstream commit — no self-authored patches" rule applies to the
# dsv41 variant. This variant exists specifically to carry upstream-AUTHORED
# patches from OPEN PRs (#43518 by Hahaa909) — we author nothing here, we
# ship their diff verbatim. On merge day: delete the patch, bump to the first
# nightly containing the merge (or rebuild variant=dsv41), retire this file.
#
# WHY PATCH-IN-PLACE (unlike kubetee-overlay's tree swap): that variant layers
# a newer FULL source tree because its fixes are MERGED upstream at a newer
# SHA. #43518 is not merged — no tree contains it. Patching the base's own
# installed tree keeps every other byte of the base stable, matching the plan
# recorded in the MiMo manifest: "FROM the pinned nightly + COPY the patched
# module over the installed path" (applied in place instead of COPY so the
# upstream unit test can prove the fix at build time).
#
# SAFETY (verified 2026-10-17 against base nightly-dev-cu13-20261016-a93f16f0,
# build commit ed8c3139):
#   * PATCH DRIFT TRIPWIRES: the patch preimage is asserted against a sha256
#     of the base's file BEFORE patching and the postimage AFTER. `git apply`
#     alone would silently succeed against a drifted-but-similar base; the
#     hashes make drift a hard build failure. (dflash_utils.py is byte-equal
#     at ed8c3139 and a93f16f0 — sha256 5f8f2669… — so the target is
#     unambiguous across those two build commits.)
#   * BUILD-TIME TEST: the exact unit test shipped by PR #43518
#     (test/registered/unit/spec/test_dflash_verify_grammar_mask.py) is RUN as
#     the smoke check against the patched installed tree — CPU-only, ~5s,
#     5 test methods covering stale-mask clearing, per-position verify masks,
#     and penalty/logit-bias survival. A patch that does not fix the bug, or a
#     base whose API moved, fails the build instead of shipping.
#   * COMPILED STACK UNTOUCHED: one pure-Python file changes. No wheels, no
#     JIT, no flashinfer, no Rust .so involvement. The base's bundled Rust
#     .so handling is inherited as-is (SGLANG_RUST_BUILD_MODE=never — see the
#     kubetee-overlay header for why `never` and not `auto`).
#
# PATCH SET: docker/kubetee-mimo-overlay/patches/*.patch, applied in lexical
# order with the kubetee-mimo patch path layout (paths inside the patches are
# relative to /sgl-workspace/sglang).

ARG BASE_IMAGE=lmsysorg/sglang:nightly-dev-cu13-20261016-a93f16f0@sha256:0b3e39704480c423d8a00ca15ebee63154defc62944df2d4b11c594563bab457
ARG SGLANG_SHA=ed8c3139f1f643544b68102cf8c44504e5e55e3d

FROM ${BASE_IMAGE}

ARG SGLANG_SHA
LABEL org.opencontainers.image.title="sglang mimo-overlay (base source + unmerged patches)" \
      ai.kubetee.sglang.sha="${SGLANG_SHA}" \
      ai.kubetee.overlay="mimo: #43518 dflash verify stale grammar mask; base source byte-stable otherwise"

WORKDIR /sgl-workspace

# Provenance + trust the base's bundled Rust .so at runtime (see header).
ENV SGLANG_BUILD_COMMIT=${SGLANG_SHA} \
    SGLANG_RUST_BUILD_MODE=never

# 1) Receive the patch set from the build context (the workflow's `overlay/`).
COPY overlay/docker/kubetee-mimo-overlay/patches /tmp/patches

# 2) Drift-tripwire the preimage, apply the patches in place (the base installs
#    `python[all]` editable at /sgl-workspace/sglang, so patching that tree IS
#    the install — no reinstall needed), tripwire the postimage, then run the
#    PR's own unit test as the smoke check. No `git`/`patch` trust: whichever
#    applier runs, the postimage hash is the authority.
RUN set -eu; \
    PKG=/sgl-workspace/sglang/python/sglang/srt/speculative/dflash_utils.py; \
    PATCH=/tmp/patches/0001-dflash-verify-stale-grammar-mask.patch; \
    [ -f "$PKG" ]   || { echo "FATAL: $PKG missing in base image"; exit 1; }; \
    [ -f "$PATCH" ] || { echo "FATAL: patch missing from build context"; ls -la /tmp/patches; exit 1; }; \
    echo "--- preimage drift tripwire (dflash_utils.py @ ed8c3139)"; \
    echo "5f8f266964e4dd15b426d197e944c13d7a75c4d1749187a9f09506bc8cfb61a6  $PKG" | sha256sum -c -; \
    echo "--- applying patch set"; \
    mkdir -p /sgl-workspace/sglang/test/registered/unit/spec; \
    if command -v git >/dev/null 2>&1; then \
        ( cd /sgl-workspace/sglang && git apply --verbose "$PATCH" ); \
    else \
        ( cd /sgl-workspace/sglang && patch -p1 -F 0 < "$PATCH" ); \
    fi; \
    echo "--- postimage drift tripwire (dflash_utils.py + #43518)"; \
    echo "81b58d09fb65c51d3bd3b73bc17757b8851269ff529f8a4cc2bf8b0818f437e4  $PKG" | sha256sum -c -; \
    echo "--- smoke check: PR #43518's own unit test against the patched tree"; \
    cd /sgl-workspace/sglang/test/registered/unit/spec; \
    /opt/sglang/bin/python -m unittest test_dflash_verify_grammar_mask -v

# 3) The full serving import surface still loads (paranoia — the patch is one
#    pure-Python file, but this costs seconds and fails the build on surprises).
RUN /opt/sglang/bin/python - <<'PY'
import sglang
import sglang.srt.speculative.dflash_utils as d
from sglang.srt.managers.scheduler_components import dp_attn
import sglang.srt.mem_cache.dsv41_request_window as w
import sglang.srt.model_executor.encoder_swa_replay as e
import sglang.srt.mem_cache.rust_tree_core.extension as rtc   # compiled Rust .so (base)
from sglang.srt.speculative.dflash_utils import apply_dflash_verify_logits_adjustments
print("mimo overlay import OK:", getattr(sglang, "__version__", "n/a"))
print("   dflash_utils:        ", d.__file__)
print("   dsv41_request_window:", w.__file__)
print("   encoder_swa_replay:  ", e.__file__)
print("   rust_tree_core ext:  ", rtc.__file__)
PY

CMD ["/bin/bash"]
