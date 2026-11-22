# syntax=docker/dockerfile:1.7
# K3.1 transport-only canary. Do NOT replace its parent with a newer nightly,
# pip-install sglang, or rebuild its CUDA/MoE extensions. They remain identical
# to the running guest by construction; the original feature verifier is a
# mandatory build gate.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SRC_SHA
ARG PATCH_SHA256

WORKDIR /sgl-workspace/sglang
COPY merged.patch /tmp/merged.patch
COPY changed_py.txt /tmp/changed_py.txt
COPY docker/k31-image /opt/k31-image

# Apply only the CI-expanded, allowlisted transport/test patch. The parent
# must hold its recorded deployed source exactly. Source provenance is the
# candidate commit encoded in this layer, not a mutable Git branch name.
RUN set -eux; \
    test "$(cat .k31-source-commit)" = "35708f76dfb720f831f7028ddb68250b6caa8f12"; \
    echo "${PATCH_SHA256} /tmp/merged.patch" | sha256sum -c -; \
    git apply --binary -p1 /tmp/merged.patch; \
    echo "${SRC_SHA}" > .k31-source-commit; \
    git config --global --add safe.directory /sgl-workspace/sglang; \
    python3 -c 'import sys; [compile(open(p, "rb").read(), p, "exec") for p in map(str.strip, open(sys.argv[1])) if p]' /tmp/changed_py.txt; \
    python3 /opt/k31-image/verify_image.py; \
    test -x /usr/bin/openssl; \
    python3 -c 'from importlib.metadata import version; print("granian=" + version("granian")); print("uvicorn=" + version("uvicorn"))'

# Real SGLang CLI + model stub, real Granian/Uvicorn. No GPU, HF token, KBS,
# Trustee, cluster or production certificate access. The test program imports
# only stdlib, h2, requests, and the classes sglang already bundles.
COPY scripts-transport-launcher.py /sgl-workspace/scripts/kimi_k31_granian_transport_test.py
COPY transport-test.py /tmp/transport-test.py
RUN set -eux; \
    mkdir -p /sgl-workspace/scripts; \
    touch /sgl-workspace/scripts/__init__.py; \
    python3 -m venv --system-site-packages /tmp/kimi-k31-canary-venv; \
    /tmp/kimi-k31-canary-venv/bin/pip install --no-cache-dir --require-hashes -r /opt/k31-image/canary-test-requirements.txt; \
    export SGLANG_TEST_CERTS="$(mktemp -d)"; \
    export PYTHONPATH=/sgl-workspace; \
    test -n "${SGLANG_TEST_CERTS}"; \
    /tmp/kimi-k31-canary-venv/bin/python /tmp/transport-test.py; \
    test ! -d "${SGLANG_TEST_CERTS}"; \
    unset SGLANG_TEST_CERTS; \
    rm -rf /tmp/kimi-k31-canary-venv /tmp/transport-test.py /sgl-workspace/scripts; \
    python3 /opt/k31-image/verify_image.py
