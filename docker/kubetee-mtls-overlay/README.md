# KubeTEE SGLang transport mTLS overlay — official-release based

Reusable SGLang image = **official release + the KubeTEE mTLS transport layer
only**, serving many models. Model-layer patches are applied a la carte, one
layer at a time, only when the defect reproduces (see `model-patches/`).

## Branch strategy (user decision 2026-11-24)

| Branch | Base | Contents | Use |
|---|---|---|---|
| `kubetee-overlay` (this) | official release tag (currently `v0.5.23`) | **transport/ only** — Granian mTLS + health-only `:8000` | reusable multi-model base |
| `kubetee/mimo-penfix-r4-mtls-h2` | nightly `nightly-dev-cu13-20261105-8df0cd51` | transport + 5 model patches | current MiMo-V2.6-pro RL production (Dallas/H200) |

To move to the next official release: re-point `kubetee-overlay` at the new tag,
re-fuzz the single transport patch (see below), update the sha tripwires, and
build. The model patches are unchanged — each re-applies on the new base.

## The transport layer (what "mTLS" means here)

From `nim/SGLANG-MTLS-DEPLOYMENT.md` — the east-west mTLS boundary:

- **Inbound mTLS** on the Granian HTTP/2 listener (`:8443`): `ssl_client_verify`
  requires a client cert signed by `--ssl-ca-certs`. Granian's `SSLVerifyAdaptor`
  derives the client's verify depth from the CA file (real CA chain depth), not
  the stale `kimi_granian_mtls.py` `SslVerify::Optional(1)` depth-1 hardcode.
- **`self_check_client_cert()`** — warmup self-probes present the serving cert as
  a client identity over loopback.
- **Health-only port** (`:8000`) — `UvicornHealthServer` answers **only**
  `GET /health` + `GET /health_generate` (200); every other path 404s. For
  Kubelet probes inside the mTLS guest. Never serves inference/admin/proxy.
- `ssl_cert_reqs=_inbound_cert_reqs(...)` (uvicorn paths) / `ssl_verify=
  _inbound_client_verify(...)` (Granian path) derive `CERT_REQUIRED` whenever
  `--ssl-ca-certs` is set, else `CERT_NONE`.

Ported onto **v0.5.23** (official release commit `297e54fd`) as a single
fuzz-free patch `patches/0001-native-granian-mtls.patch`, touching only:
`arg_groups/fields/serving.py` (+3 flags), `arg_groups/serving_hook.py`
(validation + `self_check_client_cert`), `entrypoints/health_check.py` (new),
`entrypoints/http_server.py` (Granian mTLS + verify derivation).

**Proof of determinism:** `git apply` of `0001` on pristine v0.5.23 files
reproduces the patched files **byte-identically** (no fuzz). The Dockerfile's
pre/post `shasum -a 256 -c` tripwires fail the build on any drift.

## Build (GitHub Actions only — no cluster-node image builds)

```bash
# via the reusable workflow (variant=mtls), or manually:
docker buildx build --provenance=false \
  -f docker/kubetee-mtls-overlay/Dockerfile \
  -t ghcr.io/kubetee-ai/sglang:v0.5.23-kubetee-mtls1 .
```

Base pin: `lmsysorg/sglang:v0.5.23@sha256:f7876bef696ccf9cf75a2bf7ccb82970f5fa...`
(linux/amd64 manifest). New immutable tag + digest per build.

## Post-deploy canary

Run `scripts/kimi-k31-granian-transport-test.py` (in-image at
`/opt/kubetee/bin/`) against the live pod — it drives the Granite HTTP/2 mTLS
round trip and the health-only surface with the fixture cert/key/CA. Do not use
it in production with the fixture certs.
