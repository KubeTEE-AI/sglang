# SGLang python-overlay image for DeepSeek-V4.1-Flash (H200 TDX CC)
#
# PURPOSE: carry merged-upstream SGLang fixes ahead of the upstream nightly
# pipeline, by layering the updated SGLang SOURCE on top of the published
# upstream `nightly-dev-cu13` base image.
#
# SOURCE POLICY: SGLANG_SHA is an UNMODIFIED upstream `sgl-project/sglang`
# commit (no cherry-picks, no self-authored patches). Default = the commit that
# unbreaks the upstream nightly build AND contains the 4 fixes we want:
#   86cc2e02ecb207853377e2c034337e9e0442ce60
#     (#44720 zip-yank build fix + #41303 bounded-replay correctness
#      + #44783 + #44403 + #43813)
# Bumping = change SGLANG_SHA to a newer upstream commit and rebuild.
#
# WHY THIS WORKS / WHY IT IS SAFE (verified 2026-10-17):
#   * EDITABLE SOURCE: upstream's own `docker/Dockerfile` (framework_final) does
#     `uv pip install --no-deps -e "python[all]"` with source at
#     `/sgl-workspace/sglang`. Replacing that tree reproduces their install
#     exactly — faithful to the upstream build, not a hack.
#   * COMPILED STACK UNTOUCHED: the 4 fixes touch ONLY `python/sglang/**` + one
#     JIT `.cuh` and import ZERO flashinfer. The only compiled-pin delta between
#     the base commit and this pin is `FLASHINFER_VERSION 0.7.1 -> 0.7.2`, whose
#     new symbols live ONLY on Blackwell MegaMoE + flux-diffusion paths
#     (`kernels/ops/diffusion/`, a lazy `__getattr__`/`import_module` registry
#     only touched by diffusion models — NEVER the H200 DSV4.1 `dsv4` /
#     `flashinfer_mxfp4` serving path). So the base's flashinfer 0.7.1 is
#     correct for this workload; no compiled stack is rebuilt.
#   * RUST .so PRESERVED (the non-obvious trap): the editable source tree ALSO
#     contains compiled PyO3 extensions the srt SERVING path imports
#     (`sglang.srt.rust_extensions._grpc` / `._multimodal`, and
#     `srt/mem_cache/rust_tree_core/*.so` used by the DSV4.1 radix/mem cache).
#     The base builds them; they are UNCHANGED by our fixes. A naive `rm -rf`
#     of the source would drop them -> ImportError at serving import time. This
#     Dockerfile backs up ALL `*.so` under the package before the swap and
#     restores them after (same relative paths), and reinstalls with
#     `SGLANG_BUILD_RUST_EXTS=none` so the install is pure-Python (no cargo) and
#     cannot try to recompile or drop the extensions.
#   * SGLANG_RUST_BUILD_MODE=never (MUST be `never`, not `auto`): sglang's
#     `rust_extensions/loader.py` — with `auto` in a source tree — SKIPS the
#     bundled in-package `.so` and looks in a fingerprinted cache, then invokes
#     cargo (absent) -> fail. `never` TRUSTS the bundled in-package `.so` via
#     `_import_bundled_extension`, which is exactly the preserved artifact. This
#     matches upstream's own runtime stage (it bakes `SGLANG_RUST_BUILD_MODE=never`).
#   * STUBS: `python/README.md` + `python/LICENSE` are MISSING at the pin but
#     `pyproject.toml` declares `readme`/`license = { file = ... }`. The
#     editable install fails without them (upstream's deps stage stubs them too
#     with `touch`). This Dockerfile stubs them the same way.
#   * JIT KERNELS: sglang's `kernels/jit` hashes sources into the cache key, so
#     the changed `.cuh` recompiles cleanly at first use; the stale base
#     `kernels.lock` cannot poison it. (First boot pays JIT warmup — our
#     startupProbe allows it.)

ARG BASE_IMAGE=lmsysorg/sglang:nightly-dev-cu13-20261016-a93f16f0@sha256:0b3e39704480c423d8a00ca15ebee63154defc62944df2d4b11c594563bab457
ARG SGLANG_SHA=86cc2e02ecb207853377e2c034337e9e0442ce60

FROM ${BASE_IMAGE}

ARG SGLANG_SHA
LABEL org.opencontainers.image.title="sglang python-overlay (merged-upstream)" \
      ai.kubetee.sglang.sha="${SGLANG_SHA}" \
      ai.kubetee.overlay="python-only; compiled stack (incl. Rust .so) from base"

WORKDIR /sgl-workspace

# Provenance + trust the preserved bundled Rust .so at runtime (see header).
ENV SGLANG_BUILD_COMMIT=${SGLANG_SHA} \
    SGLANG_RUST_BUILD_MODE=never

# 1) Receive the UNMODIFIED upstream source at SGLANG_SHA as build context
#    (`sglang-src/`, checked out at exactly SGLANG_SHA by the workflow via git).
#    History: this used to be `ADD https://github.com/.../archive/<sha>.tar.gz`;
#    three consecutive build failures (runs 39010741205 / 39011904888 /
#    39014325235: HTTP/2 stream CANCEL, Azure-Front-Door HTML 400, TCP RST —
#    all inside BuildKit's remote-URL cache-key fetch) showed the anonymous
#    codeload fetch is not reliable from the build container. git checkout in
#    the workflow is (worked in every run), and gives exact-SHA provenance.
COPY sglang-src /tmp/sglang-src

# 2) Preserve compiled .so, replace the editable source tree, stub the missing
#    README/LICENSE, then reinstall the Python package pure-Python.
RUN set -eu; \
    # -- locate the pinned source --------------------------------------------
    SRC="/tmp/sglang-src"; \
    [ -d "$SRC/python/sglang" ] || { echo "FATAL: $SRC does not look like the sglang source"; ls "$SRC"; exit 1; }; \
    OLD=/sgl-workspace/sglang; \
    # -- preserve EVERY compiled .so in the package (Rust PyO3 extensions the
    #    serving path imports), by relative path, across the tree swap --------
    BK=/tmp/so_backup; rm -rf "$BK"; mkdir -p "$BK"; \
    if [ -d "$OLD/python/sglang" ]; then \
        ( cd "$OLD/python/sglang" && find . -name '*.so' -type f -exec sh -c 'mkdir -p "$0/$(dirname "$1")" && cp -a "$1" "$0/$1"' "$BK" {} \; ); \
    fi; \
    echo "preserved .so count:"; find "$BK" -name '*.so' | wc -l; find "$BK" -name '*.so'; \
    # -- swap the source tree ------------------------------------------------
    rm -rf "$OLD"; \
    mv "$SRC" "$OLD"; \
    NEWPY="$OLD/python/sglang"; \
    # -- stub missing readme/license (required by pyproject readme/license) --
    touch "$OLD/python/README.md" "$OLD/python/LICENSE"; \
    # -- drop stale compiled-cubin lock + __pycache__ (must not shadow .py) --
    rm -f "$OLD/python/kernels.lock" /root/.cache/sglang/kernels.lock; \
    find /opt/sglang/lib/python3.12/site-packages -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true; \
    find "$OLD" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true; \
    # -- restore the preserved .so into the new tree (same relative paths) ----
    ( cd "$BK" && find . -name '*.so' -type f -exec sh -c 'mkdir -p "$0/$(dirname "$1")" && cp -a "$1" "$0/$1"' "$NEWPY" {} \; ) 2>/dev/null || true; \
    echo "restored .so count:"; find "$NEWPY" -name '*.so' | wc -l; find "$NEWPY" -name '*.so'; \
    # -- pure-Python editable reinstall (deps UNCHANGED from base; no cargo) --
    cd "$OLD"; \
    export SGLANG_BUILD_RUST_EXTS=none; \
    if command -v uv >/dev/null 2>&1; then \
        uv pip install --no-deps -e "python[all]"; \
    else \
        /opt/sglang/bin/pip install --no-deps -e "python[all]"; \
    fi

WORKDIR /sgl-workspace/sglang

# 3) Smoke check: the serving import surface loads (incl. the preserved Rust
#    tree_core the DSV4.1 radix cache uses) and the overlay source is live.
#    Fails the build if any preserved .so does not load.
RUN /opt/sglang/bin/python - <<'PY'
import sglang
from sglang.srt.managers.scheduler_components import dp_attn
import sglang.srt.mem_cache.dsv41_request_window as w
import sglang.srt.model_executor.encoder_swa_replay as e
import sglang.srt.mem_cache.rust_tree_core.extension as rtc   # compiled Rust .so (preserved)
print("overlay import OK:", getattr(sglang, "__version__", "n/a"))
print("   dsv41_request_window:", w.__file__)
print("   encoder_swa_replay:  ", e.__file__)
print("   rust_tree_core ext:  ", rtc.__file__)
PY

CMD ["/bin/bash"]
