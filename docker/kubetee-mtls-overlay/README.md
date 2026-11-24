# KubeTEE SGLang transport-mTLS overlay (`kubetee-mtls-overlay`)

The reusable multi-base build: an SGLang base image (official release or upstream
nightly) + **only** the model-agnostic transport layer (Granian mTLS on `:8443`
+ health-only Uvicorn on `:8000`). Model-layer patches (DFlash penalties, MiMo
tool-loop) stay OUT of this layer — they are applied a la carte when a defect is
reproduced on the target base (user decision 2026-11-24).

## Multi-base: one Dockerfile, one transport patch per base

The transport patch is **not** shared across bases — upstream drift moves hunks
(a shared patch only reaches the other base via fuzz, which this build forbids:
the appliers are `git apply` / `patch -p1 -F 0`, zero fuzz). Each base has its
own cleanly-applicable patch + its own drift-tripwire table:

```
patches/
├── by-sha-297e54fd/           # official release v0.5.23 (SGLANG_SHA=297e54fd...)
│   ├── 0001-native-granian-mtls.patch
│   ├── preimage.sha           # sha256 of each touched file BEFORE the patch
│   └── postimage.sha          # sha256 of each touched file AFTER the patch
└── by-sha-3117f580/           # nightly-cu134-20261122-3117f58 (SGLANG_SHA=3117f580...)
    ├── 0001-native-granian-mtls.patch
    ├── preimage.sha
    └── postimage.sha
```

The Dockerfile selects the key from `--build-arg SGLANG_SHA` (the upstream source
commit the base image was built from): `by-sha-<first-8-of-SGLANG_SHA>`.
**A missing key fails the build** — no silent fallback to another base's patch.

### Supported bases (dispatch inputs for `build-sglang-overlay.yaml`)

| base | `base_image` | `source_sha` | patch key |
|---|---|---|---|
| official release | `lmsysorg/sglang:v0.5.23@sha256:f7876bef…` | `297e54fd105e974cdf401e5b4d20253a22c16f90` | `by-sha-297e54fd` |
| nightly (latest, 2026-11-22) | `lmsysorg/sglang:nightly-cu134-20261122-3117f58@sha256:ffb8652016b1b895cd2dee12f6bffcb7a5fa65af71378e1018c9f702faa05063` | `3117f5801752bfd8ea9aee8d3ba3dff8562378fd` | `by-sha-3117f580` |
| nightly-dev (same tree, 2026-11-22) | `lmsysorg/sglang:nightly-dev-cu13-20261122-e96e9675@sha256:2207e32ab09acb8ee120abd9a6ef0dba17c1570d19117f306b08b9e3faf42cc6` | `e96e96757018ce842d570781ec8312f8c470493b` | `by-sha-3117f580` (same trees — see note) |

`nightly-cu134` (the `-dev-` family `nightly-dev-cu13-*` builds at 00:50Z, the
`-cu134-` family at 10:50Z, both daily) both derive from the same upstream commit
for a given date when their SHAs match — for 2026-11-22 the three target files
are line-identical between `e96e96757018` and `3117f5801752`. The transport patch
+ tripwires key on the **source commit**, so `by-sha-3117f580` covers both tags
when their trees match; if you must pin the `-dev-` tag and its commit differs,
copy the dir to `by-sha-e96e9675/` and re-record the tripwires (the tables are
the authority — a wrong table fails the build, never ships).

### Adding a base (e.g. next nightly)

1. Resolve the base's upstream source commit (`gh api repos/sgl-project/sglang/commits/<sha>`),
   and fetch its three touched files (`arg_groups/fields/serving.py`,
   `arg_groups/serving_hook.py`, `entrypoints/http_server.py`) at that commit.
2. Test-apply the transport patch (`git apply --verbose`); if it applies clean,
   reuse the base's own postimages. If it drifts (usual for nightly), `git apply
   --reject`, re-author the rejected hunks against the new upstream code, and
   regenerate the patch from a real `git diff` (base → patched) — never hand-edit
   line numbers.
3. Record `patches/by-sha-<8>/preimage.sha` + `postimage.sha` (sha256 of each
   touched file before/after, paths as in-container `/sgl-workspace/sglang/...`).
4. Prove it: apply the generated patch to a **fresh** copy of the base tree and
   byte-compare against the reference (the CI runtime gate re-checks the
   postimages anyway).
5. Dispatch with the two args above; tag_prefix computes `<key>-<shortsha>-<date>`.

### The two tripwire layers (why both)

`git apply` alone would silently succeed against a drifted-but-similar base; the
**preimage** hashes make drift a hard build failure, and the **postimage** hashes
make a wrong-patch/fuzzy-apply a hard build failure. The CI build runs the real
TLS/transport test battery on top (`test_inbound_mtls.py` + the Granian/Uvicorn
transport boot) — those are the behavioral gates; the tripwires are the
byte-level provenance gates.

## Transport layer contents

See the Dockerfile header (a–d) and `nim/SGLANG-MTLS-DEPLOYMENT.md`. In short:
`serving.py` flags (`ssl_client_verify`, `health_only_port`, `enable_ssl_refresh`),
`serving_hook.handle_ssl_validation` + `self_check_client_cert`, the
`health_check.UvicornHealthServer` (health-only plaintext listener — never
serves inference), and `http_server` Granian mTLS wiring (`_inbound_cert_reqs` /
`_inbound_client_verify` derive mTLS from `ssl_ca_certs`, Path coercion for
Granian's `.resolve()`).

## Build mechanics (transplanted from the proven r4 build — do not "improve")

`set -eu` (NOT `set -euo pipefail` — base `/bin/sh` is `dash`), inline
`sha256sum -c <file>` tripwires (NOT heredocs mid-chain), `git` → `patch -p1 -F 0`
fallback, no `# syntax=` directive, `RUN python - <<'PY'` heredoc form. Each of
those was learned the hard way (runs 45340134044 / 45345770152). Local gates
before dispatch: `tests/validate_dockerfile_shell.py` (dash + heredoc-shape) and
`docker build --check` (the real buildkit parser).

## Provenance

- v0.5.23: `by-tag-v0.5.23` — postimages byte-match the proven r4 build's
  tripwire table where the preimage is shared (`serving.py` pre/post,
  `health_check.py` post).
- nightly: `by-sha-3117f580` — 3 `http_server.py` hunks reworked for upstream
  drift (`import traceback` inserted; `_run_granian_server` serve-wrap and
  `uvicorn_options` `ssl_cert_reqs` anchors moved). Replay-proven byte-exact onto
  the nightly; cross-base proof that the transport semantics are unchanged (the
  v0.5.23 postimages still byte-match after applying the same semantics).
