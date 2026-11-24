# KubeTEE SGLang TRANSPORT mTLS overlay on an OFFICIAL SGLang release ("mtls1").
#
# PURPOSE: the reusable multi-model base — the official release source + ONLY the
# model-agnostic transport layer (Granian mTLS + health-only port). Model-layer
# patches (DFlash penalties, MiMo tool-loop) are NOT baked in; each is applied a
# la carte as a single extra layer under docker/kubetee-mtls-overlay/model-patches/
# only when its defect is reproduced on this base. Strategy + rationale:
# docker/kubetee-mtls-overlay/README.md (user decision 2026-11-24).
#
# BUILD MECHANICS ARE TRANSPLANTED FROM docker/kubetee-mimo-overlay/Dockerfile
# (the r4 penfix1 build, which builds green in this base image class). Do NOT
# "improve" them: `set -eu` (NOT `set -euo pipefail` — the base /bin/sh is dash,
# which rejects `-o pipefail`), inline `echo "hash  $VAR" | sha256sum -c -`
# tripwires (NOT `<<EOF` heredocs mid-chain — the parser ends the instruction),
# `command -v git` fallback to `patch -p1 -F 0`, no `# syntax=` directive. Each of
# those was learned the hard way (runs 45340134044 / 45345770152).
#
# TRANSPORT LAYER (what "mTLS" means here — from nim/SGLANG-MTLS-DEPLOYMENT.md,
# the east-west mTLS boundary, validated live on kimi-k3-1-pro39-sglang-b300-cc-0):
#   a) `serving_hook.self_check_client_cert()` — the engine's own startup
#      self-probes (model_info wait, warmup POST, disagg warmup, /freeze_gc)
#      present the serving pair as a client identity. Without it the probe dies
#      at the TLS handshake and the engine SIGQUITs "Initialization failed.
#      warmup error" AFTER the weights load. Fails CLOSED without cert/key pair.
#   b) `handle_ssl_validation()` transport constraints — --ssl-client-verify
#      requires --enable-http2 + cert/key/CA; --health-only-port requires mTLS +
#      one tokenizer worker and must differ from --port.
#   c) `health_check.UvicornHealthServer` — restricted plaintext listener serving
#      ONLY GET /health and /health_generate (everything else 404) for kubelet
#      probes in a mTLS guest. Not a mount of the FastAPI app — never serves
#      inference, admin or proxy routes. (CoCo ExecProcessRequest stays denied.)
#   d) `http_server` — Granian terminates strict mTLS on :8443 with
#      `ssl_client_verify`, with `ssl_cert`/`ssl_key`/`ssl_ca` coerced to `Path`
#      (Granian calls `.resolve()` on non-None TLS paths; the CLI supplies `str`,
#      so plain strings crash). The inbound Uvicorn path derives verify_mode from
#      --ssl-ca-certs via `_inbound_cert_reqs()` — with a CA bundle the listener
#      DEMANDS a client certificate (without this half it loaded the CA but left
#      verify_mode CERT_NONE: TLS but not mutual, no authentication).
#      Sidenote from the r4 header worth keeping: this base declares
#      `ssl_verify=False,  # MTls is not supported` in `_run_granian_server`'s
#      signature — the transport layer is what makes it supported.
#
# BASE IMAGE: official lmsysorg/sglang:v0.5.23 (release commit 297e54fd; the
# annotated tag v0.5.23 points at 7df5dd09). The base installs sglang EDITABLE at
# /sgl-workspace/sglang (upstream Dockerfile: `uv pip install --no-deps -e
# "python[...]"`), so patching that tree IS the install — no reinstall needed.
#
# SAFETY (same two layers as r4):
#   * DRIFT-TRIPWIRES: preimage sha256 of each touched source file is asserted
#     BEFORE the patch and the postimages AFTER. `git apply` alone would silently
#     succeed against a drifted-but-similar base; the hashes make drift a hard
#     build failure. The postimage hashes are cross-validated against the r4
#     build's own proven tripwire table (identical where the preimage is shared).
#   * BUILD-TIME GATES: the transport test battery runs against the patched tree —
#     a real TLS listener test (a client with NO certificate must be refused when
#     --ssl-ca-certs is set; on stock SGLang that client connects), the real
#     Granian/Uvicorn transport boot, and a runtime import + behaviour check.
#     Pure-Python only. No wheels, no JIT, no flashinfer, no Rust .so involvement.
#
# COMPILED STACK UNTOUCHED; inherits the base's SGLANG_RUST_BUILD_MODE=never.

# MULTI-BASE (one Dockerfile, key-selected transport patch — see README):
#   * v0.5.23  BASE_IMAGE=lmsysorg/sglang:v0.5.23@sha256:f7876bef...  SGLANG_SHA=297e54fd...
#   * nightly  BASE_IMAGE=lmsysorg/sglang:nightly-cu134-20261122-3117f58@sha256:ffb86520...  SGLANG_SHA=3117f580...
#              (also covers nightly-dev-cu13-20261122-e96e9675@sha256:2207e32a... — same
#               sglang tree at e96e96757018; the transport patch + tripwires key on the
#               SOURCE COMMIT, so both tags are one base. Pass SGLANG_SHA=e96e96757018ce84...
#               and add patches/by-sha-e96e9675/ = a copy of by-sha-3117f580/ if you need
#               the -dev- tag; identical trees, identical hashes.)
#   * a NEW base = a patches/by-sha-<8>/ dir (patch + preimage.sha + postimage.sha)
#     + these two args. Fail-closed: an unknown SGLANG_SHA aborts the build.
ARG BASE_IMAGE=lmsysorg/sglang:nightly-cu134-20261122-3117f58@sha256:ffb8652016b1b895cd2dee12f6bffcb7a5fa65af71378e1018c9f702faa05063
ARG SGLANG_SHA=3117f5801752bfd8ea9aee8d3ba3dff8562378fd

FROM ${BASE_IMAGE}

ARG SGLANG_SHA
LABEL org.opencontainers.image.title="sglang transport-mTLS overlay (sglang base + transport layer)" \
      org.opencontainers.image.revision="${SGLANG_SHA}" \
      ai.kubetee.overlay="mtls: native Granian mTLS + health-only :8000 on official v0.5.23; NO model-layer patches" \
      ai.kubetee.model-patches="none"

WORKDIR /sgl-workspace

ENV SGLANG_BUILD_COMMIT=${SGLANG_SHA} \
    SGLANG_RUST_BUILD_MODE=never

# 1) Receive the transport layer from the build context (the workflow's `overlay/`).
COPY overlay/docker/kubetee-mtls-overlay/patches /tmp/patches
COPY overlay/docker/kubetee-mtls-overlay/tests /tmp/mtls-tests
COPY overlay/scripts/kimi-k31-granian-transport-test.py /tmp/mtls-tests/kimi-k31-granian-transport-test.py

# 2) Multi-base support: the transport patch is ONE PER BASE (cleanly applicable
#    with zero fuzz — upstream drift moves hunks; a single shared patch would only
#    reach the other base via fuzz, which this build forbids). Each base's patch +
#    pre/postimage drift-tripwire tables live in patches/<key>/. The key is
#    selected from --build-arg SGLANG_SHA (= the upstream source commit the base
#    image was built from). To add a base: drop patches/by-tag-<tag>/ (or
#    by-sha-<8>), the patch + its two .sha tables (see README "adding a base").
#    A missing key FAILS CLOSED — no silent fallback to another base's patch.
#    No `git`/`patch` trust: whichever applier runs (zero fuzz either way), the
#    postimage hashes are the authority.
RUN set -eu; \
    ROOT=/sgl-workspace/sglang; \
    SV=$ROOT/python/sglang/srt/arg_groups/fields/serving.py; \
    SH=$ROOT/python/sglang/srt/arg_groups/serving_hook.py; \
    HS=$ROOT/python/sglang/srt/entrypoints/http_server.py; \
    HC=$ROOT/python/sglang/srt/entrypoints/health_check.py; \
    SHORT=$(printf '%.8s' "$SGLANG_SHA"); \
    KEY="/tmp/patches/by-sha-$SHORT"; \
    [ -d "$KEY" ] || { echo "FATAL: no transport patch for this base (SGLANG_SHA=${SGLANG_SHA}); add patches/by-sha-$SHORT/ — fail closed"; exit 1; }; \
    f=$(ls "$KEY"/0001-*.patch); \
    [ -f "$f" ] || { echo "FATAL: 0001 transport patch missing in $KEY"; exit 1; }; \
    echo "--- preimage drift tripwire (${KEY##*/} @ ${SGLANG_SHA})"; \
    sha256sum -c "$KEY/preimage.sha"; \
    echo "transport/ (mTLS + health-only) on ${KEY##*/} (${SGLANG_SHA})" > /etc/kubetee-sglang-overlay-release; \
    echo "--- applying transport patch: $f"; \
    if command -v git >/dev/null 2>&1; then \
      ( cd "$ROOT" && git apply --verbose "$f" ); \
    else \
      ( cd "$ROOT" && patch -p1 -F 0 < "$f" ); \
    fi; \
    echo "--- postimage drift tripwire (transport 0001)"; \
    sha256sum -c "$KEY/postimage.sha"; \
    echo "--- smoke check: the patched files still compile"; \
    /opt/sglang/bin/python -m py_compile "$SV" "$SH" "$HS" "$HC"

# 2b) Inbound mTLS gate — a REAL TLS listener test, not a mock: it stands up
#     uvicorn exactly as SGLang configures it and asserts that a client with NO
#     certificate is refused when --ssl-ca-certs is set. On stock SGLang that
#     client connects (verify_mode stays CERT_NONE) — an auth bypass for any
#     deployment relying on client certs, so this gate is mandatory.
RUN set -eu; \
    cd /sgl-workspace/sglang; \
    /opt/sglang/bin/python -m pytest /tmp/mtls-tests/test_inbound_mtls.py -v

# 2c) Transport battery — boots the REAL engine transport (Granian HTTP/2 mTLS on
#     :8443 + the health-only Uvicorn on :8000) with a stubbed engine and runs the
#     deploy-guide checks: HTTP/2 SSE over mTLS, inference over mTLS, plaintext
#     inference rejected, health-only route gating, missing client cert rejected
#     at handshake, untrusted-CA client rejected, clean shutdown. Exercises
#     self_check_client_cert, the Path coercion, ssl_client_verify wiring and
#     UvicornHealthServer startup together.
RUN set -eu; \
    export SGLANG_TRANSPORT_LAUNCHER=/tmp/mtls-tests/kimi-k31-granian-transport-test.py; \
    export SGLANG_TEST_CERTS=/tmp/mtls-transport-certs; \
    rm -rf "$SGLANG_TEST_CERTS"; \
    cd /sgl-workspace/sglang; \
    /opt/sglang/bin/pip install -q h2 requests 2>/dev/null || \
      /opt/sglang/bin/python -m pip install -q h2 requests; \
    /opt/sglang/bin/python /tmp/mtls-tests/test_granian_mtls_health_only.py

# 3) Runtime import + behaviour check (paranoia that pays: the transport layer
#    must not merely compile but load and DERIVE its verify mode correctly).
RUN /opt/sglang/bin/python - <<'PY'
import ssl
from types import SimpleNamespace

import sglang
from sglang.srt.arg_groups.fields.serving import Serving
from sglang.srt.arg_groups.serving_hook import handle_ssl_validation, self_check_client_cert
from sglang.srt.entrypoints.health_check import UvicornHealthServer
from sglang.srt.entrypoints.http_server import (
    _inbound_cert_reqs,
    _inbound_client_verify,
)

print("transport overlay import OK:", getattr(sglang, "__version__", "n/a"))
print("   UvicornHealthServer:     ", UvicornHealthServer.__module__)

# Serving flags must exist on the struct.
fields = set(getattr(Serving, "__struct_fields__", ())) or {
    n for n in dir(Serving) if not n.startswith("_")
}
for flag in ("ssl_client_verify", "health_only_port", "enable_ssl_refresh"):
    assert flag in fields, f"serving flag missing: {flag}"
print("   serving flags present: ssl_client_verify + health_only_port + enable_ssl_refresh")

# Behaviour, not just presence: with a CA bundle the listener DEMANDS a client
# cert; without one, behaviour is unchanged (plain TLS, CERT_NONE).
assert _inbound_cert_reqs(SimpleNamespace(ssl_ca_certs="/etc/ssl/certs/ca-certificates.crt")) == ssl.CERT_REQUIRED
assert _inbound_cert_reqs(SimpleNamespace(ssl_ca_certs=None)) == ssl.CERT_NONE
# _inbound_client_verify derives mTLS from the CA BUNDLE (not a separate flag):
# bool(ssl_ca_certs). This is the mirror of _inbound_cert_reqs above.
assert _inbound_client_verify(SimpleNamespace(ssl_ca_certs="/etc/ssl/certs/ca-certificates.crt")) is True
assert _inbound_client_verify(SimpleNamespace(ssl_ca_certs=None)) is False
print("   inbound verify derivation OK: CA bundle -> CERT_REQUIRED + client-verify True, no CA -> CERT_NONE + False")

# self_check_client_cert must exist and be the real function (the engine's own
# startup self-probes depend on it or the engine SIGQUITs after weight load).
assert callable(self_check_client_cert) and callable(handle_ssl_validation)
print("   self_check_client_cert + handle_ssl_validation callable")
PY

# 4) The post-deploy canary tooling, available in-image.
COPY overlay/scripts/kimi-k31-granian-transport-test.py /opt/kubetee/bin/kimi-k31-granian-transport-test.py

ENV SGLANG_OVERLAY_BRANCH="kubetee-overlay" \
    SGLANG_TRANSPORT_LAYER="granian-mtls+health-only"
