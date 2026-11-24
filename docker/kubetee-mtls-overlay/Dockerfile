# syntax=docker/dockerfile:1.7
# =============================================================================
# KubeTEE SGLang TRANSPORT mTLS overlay on an OFFICIAL SGLang release
#
# Strategy (user decision 2026-11-24): build the official release base with ONLY
# the model-agnostic transport layer (Granian mTLS + health-only port), so one
# image serves many models on the newest stable sglang. Model-layer patches
# (DFlash penalties, MiMo tool-loop) are NOT baked in; each is applied a la carte
# as a single extra layer in docker/kubetee-mtls-overlay/model-patches/ only when
# the defect is reproduced on this base. See README.md.
#
# Base = official release (editable /sgl-workspace/sglang install), so the
# transport patch lands on the exact files the interpreter imports. The patch is
# proven fuzz-free: `git apply` on pristine v0.5.23 files reproduces the
# postimages BYTE-IDENTICALLY (sha tripwires below are the CI enforcement).
# =============================================================================
ARG BASE_IMAGE=lmsysorg/sglang:v0.5.23@sha256:f7876bef696ccf9cf75a2bf7ccb82970f5fac577f082fb8ca95c9a59d2160eb5
# Official release tag v0.5.23 -> tag object 7df5dd09bc2f0e7e1509a1f7c2a40dff00ad8a01
# (annotated tag), release commit 297e54fd105e974cdf401e5b4d20253a22c16f90.
ARG SGLANG_SHA=297e54fd105e974cdf401e5b4d20253a22c16f90
ARG KUBETEE_OVERLAY_TAG=v0.5.23-kubetee-mtls1
ARG SGLANG_SOURCE_URL=https://github.com/sgl-project/sglang

FROM ${BASE_IMAGE} AS base

ARG SGLANG_SHA
ARG KUBETEE_OVERLAY_TAG
ARG SGLANG_SOURCE_URL
ENV VIRTUAL_ENV=/opt/sglang
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PYTHONPATH="/sgl-workspace/sglang/python"
WORKDIR /sgl-workspace/sglang

USER root

# --- provenance: verify the editable tree is the release we patched -------------
RUN set -euo pipefail; \
    python3 -c "import sglang; print('sglang', getattr(sglang, '__version__', '(no __version__)'))"; \
    grep -q "0\.5\.23\|297e54fd" /sgl-workspace/sglang/python/sglang/version.py \
      || echo "WARN: version.py does not advertise 0.5.23"

# --- preimage tripwires + transport patch (fuzz-free, byte-exact) ----------------
# 0001: transport layer ONLY (serving.py flags, serving_hook validation +
#       self_check_client_cert, health_check.UvicornHealthServer, http_server
#       Granian mTLS + inbound verify derivation). Model patches stay out.
COPY overlay/docker/kubetee-mtls-overlay/patches/0001-native-granian-mtls.patch /tmp/kubetee-transport/
COPY overlay/docker/kubetee-mtls-overlay/checksums/ /tmp/kubetee-transport/checksums/
# NOTE: no shell heredocs in these RUN steps on purpose — a `<<EOF` body inside a
# \`-continued RUN breaks the Dockerfile parser (the body lines become "unknown
# instruction"). The checksum manifests live in checksums/*.sha instead.
RUN set -euo pipefail; \
    echo "0001 preimage: $(sha256sum /sgl-workspace/sglang/python/sglang/srt/arg_groups/fields/serving.py  | cut -d' ' -f1)"; \
    echo "0001 preimage: $(sha256sum /sgl-workspace/sglang/python/sglang/srt/arg_groups/serving_hook.py  | cut -d' ' -f1)"; \
    echo "0001 preimage: $(sha256sum /sgl-workspace/sglang/python/sglang/srt/entrypoints/http_server.py | cut -d' ' -f1)"; \
    sha256sum -c /tmp/kubetee-transport/checksums/preimage-v0.5.23.sha; \
    echo 'transport/ (mTLS+health-only) on official v0.5.23 (297e54fd)' \
      | tee /etc/kubetee-sglang-overlay-release; \
    git -C /sgl-workspace/sglang apply -v /tmp/kubetee-transport/0001-native-granian-mtls.patch; \
    echo "0001 postimage: $(sha256sum /sgl-workspace/sglang/python/sglang/srt/arg_groups/fields/serving.py  | cut -d' ' -f1)"; \
    echo "0001 postimage: $(sha256sum /sgl-workspace/sglang/python/sglang/srt/arg_groups/serving_hook.py  | cut -d' ' -f1)"; \
    echo "0001 postimage: $(sha256sum /sgl-workspace/sglang/python/sglang/srt/entrypoints/http_server.py | cut -d' ' -f1)"; \
    echo "0001 postimage: $(sha256sum /sgl-workspace/sglang/python/sglang/srt/entrypoints/health_check.py | cut -d' ' -f1)"; \
    sha256sum -c /tmp/kubetee-transport/checksums/postimage-transport0001.sha; \
    echo "TRANSPORT PATCH 0001 APPLIED CLEAN (byte-verified postimage)"

# --- build gate: transport tests must PASS --------------------------------------
# tests/ast_transport_check.py = stdlib-only AST check of the mTLS health-only +
# client-cert verification wiring (no heavy sglang imports; pytest not required in
# the image). The full pytest battery (incl. the Granian client/server round-trip)
# runs in dev via:  PYTHONPATH=python pytest docker/kubetee-mtls-overlay/tests/
COPY overlay/docker/kubetee-mtls-overlay/tests/ /tmp/kubetee-transport/tests/
RUN set -euo pipefail; \
    PYTHONDONTWRITEBYTECODE=1 /opt/sglang/bin/python3 /tmp/kubetee-transport/tests/ast_transport_check.py; \
    PYTHONDONTWRITEBYTECODE=1 /opt/sglang/bin/python3 -m py_compile \
      /sgl-workspace/sglang/python/sglang/srt/arg_groups/fields/serving.py \
      /sgl-workspace/sglang/python/sglang/srt/arg_groups/serving_hook.py \
      /sgl-workspace/sglang/python/sglang/srt/entrypoints/http_server.py \
      /sgl-workspace/sglang/python/sglang/srt/entrypoints/health_check.py; \
    echo "PY_COMPILE OK"

# --- live transport probe tooling (fixture cert-backed, post-deploy canary) -------
COPY overlay/scripts/kimi-k31-granian-transport-test.py /opt/kubetee/bin/kimi-k31-granian-transport-test.py

ENV SGLANG_OVERLAY_BRANCH="kubetee-overlay" \
    SGLANG_TRANSPORT_LAYER="granian-mtls+health-only" \
    SGLANG_BASE_RELEASE="v0.5.23" \
    SGLANG_BASE_COMMIT="297e54fd105e974cdf401e5b4d20253a22c16f90"
LABEL org.opencontainers.image.source="${SGLANG_SOURCE_URL}" \
      org.opencontainers.image.revision="${SGLANG_SHA}" \
      org.opencontainers.image.title="KubeTEE SGLang v0.5.23 transport mTLS overlay" \
      org.opencontainers.image.description="Official sglang v0.5.23 + transport/ (native Granian mTLS + health-only port). No model-layer patches." \
      ai.kubetee.overlay="${KUBETEE_OVERLAY_TAG}" \
      ai.kubetee.model-patches="none"
