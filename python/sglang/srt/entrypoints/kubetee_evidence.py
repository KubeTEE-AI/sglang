"""KubeTEE backend evidence relay: ``GET /kubetee/evidence`` (role-scoped).

Serves the guest's TDX evidence (quote + CC event log) to the measured
LiteLLM gateway over the strict-mTLS transport. This is the ONLY path by
which the guest's hardware registers (MRTD / RTMR0-3 / MRCONFIGID) leave the
TD: the measured agent policy denies ``ExecProcessRequest``, the in-guest
attestation-agent (``127.0.0.1:8006/aa/evidence``) is loopback-only, and the
host cannot read TD state. Relaying the AA evidence through this mTLS route
is what makes reference-measure capture and client verification possible.

Contract (kubetee-subnet/docs/CLIENT-FACING-ATTESTATION.md "Backend evidence
for nim-terminator"), two-layer freshness:

  Layer 1 (hardware epoch) -- one TD quote per epoch (TTL 300 s default).
  Its REPORTDATA is ``SHA-256(JCS(epoch_statement)) || 32 zero bytes`` (the
  same 64-byte convention as the gateway binding), over exactly::

      {"epoch_id":…, "issued_at":…, "valid_until":…,
       "keyset_digest":…, "purpose":"kubetee.attestation.v1"}

  (five strings; RFC 8785 JCS subset with string keys only — no numbers).
  ``keyset_digest`` commits the served keyset INCLUDING the per-boot
  ``tee-pubkey``, so the signing key is covered by the hardware quote.

  Layer 2 (caller freshness) -- Ed25519 signature by ``tee-pubkey`` over::

      SHA-256(quote_bytes) || SHA-256(nonce_16_bytes) || epoch_id_ascii ||
      counter_be64

  where ``counter`` is the monotonic per-epoch request counter served in the
  envelope. A cached quote is therefore non-replayable at a different caller
  or a different request.

``tee-pubkey`` is generated **in-guest at the first epoch mint** (Ed25519
seed in memory, or a 32-byte seed file via ``KUBETEE_EVIDENCE_TEE_KEY``).
Being named in the epoch statement, it is bound into the hardware
report_data — an honest per-boot key, unlike the KBS-released TLS key.

Envelope (the exact shape the gateway's ``_normalize_backend_evidence``
reads and relays verbatim):

    {spec_version, tee, tee_type, role, subject, runtime_class, pod,
     epoch_id, issued_at, valid_until, report_data, report_data_statement,
     keyset, bindings, counter, quote, cc_eventlog,
     evidence: {quote, quote_report_data, event_log},
     tee_pubkey, signature}

Notes:
  * ``role`` is the MEASURED InitData role (``image-gen``); ``subject`` is
    the contract role (``nim-terminator``). They name different things and
    both are served. The route refuses any other ``?role=``.
  * The TDX quote certificate chain is not carried (the AA evidence carries
    none); quote verification uses DCAP collateral (PCCS/qgsd) as in
    ``gm-miner/attestd``.
  * The shared-cache fetch (gateway relay path) sends NO nonce and receives
    NO signature: a quote is sealed at mint and serves every caller in the
    epoch. ``signature`` is populated only for ``?nonce=<64-hex>`` requests.

Crypto is self-contained (stdlib only) by image contract: the canary image
keeps its parent bit-for-bit identical except this source patch, so no new
serving-path dependency (e.g. ``cryptography``) may be introduced. Ed25519
is the deterministic RFC 8032 construction (pinned by known-answer vectors
in test/registered/unit/entrypoints/test_kubetee_evidence.py); the X.509
walk extracts exactly the DER SubjectPublicKeyInfo + notAfter from the
serving leaf. Deterministic signing means both signing paths (this one and
any future library-based one) produce identical bytes.

Fail-closed: AA failure or missing key material returns 503 — never a
fabricated or partially-bound evidence envelope.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import time
from typing import Any, Final

PURPOSE: Final = "kubetee.attestation.v1"
SPEC_VERSION: Final = "kubetee.attestation.v1"
TEE: Final = "tdx"
TEE_TYPE: Final = "tdx"
DEFAULT_ROLE: Final = "image-gen"
SUBJECT: Final = "nim-terminator"
RUNTIME_CLASS: Final = "kata-qemu-nvidia-gpu-tdx-runtime-rs"

_AA_BASE: Final = os.environ.get("KUBETEE_AA_BASE", "http://127.0.0.1:8006")
_EPOCH_TTL_S: Final = float(os.environ.get("KUBETEE_EVIDENCE_EPOCH_TTL_S", "300"))
_ROLE: Final = os.environ.get("KUBETEE_EVIDENCE_ROLE", DEFAULT_ROLE)

_NONCE_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_PEM_CERT_BEGIN: Final = "-----BEGIN CERTIFICATE-----"
_PEM_CERT_END: Final = b"-----END CERTIFICATE-----"

# fastapi/aiohttp exist in the serving image but not in pure-logic contexts
# (the conformance test). Import them lazily so the crypto core above is
# importable with the stdlib alone.
try:  # pragma: no cover - exercised in the serving image
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse

    router: Any = APIRouter()
except ImportError:  # pragma: no cover - pure-logic test contexts
    router = None


# ---------------------------------------------------------------- JCS (subset)


def _jcs_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def jcs(obj: "str | dict[str, Any] | tuple[Any, ...]") -> str:
    """RFC 8785 canonical JSON for the attestation input class.

    Identical semantics to the gateway binding (kubetee_binding.jcs):
    objects with keys sorted by UTF-16 code units (NOT code-point order — an
    astral-plane key sorts by its surrogate pair, see the KATs), arrays,
    well-formed strings; numbers/booleans/null are rejected — the statements
    hashed into report_data never contain them.
    """
    if isinstance(obj, str):
        return _jcs_string(obj)
    if isinstance(obj, dict):
        items: Final = sorted(obj.items(), key=lambda kv: kv[0].encode("utf-16-be"))
        return "{" + ",".join(_jcs_string(k) + ":" + jcs(v) for k, v in items) + "}"
    if isinstance(obj, tuple):
        return "[" + ",".join(jcs(v) for v in obj) + "]"
    raise TypeError(
        f"JCS: unsupported type {type(obj).__name__}; objects, arrays and "
        "strings only"
    )


def report_data(statement: str) -> bytes:
    """SHA-256 of the exact statement bytes, zero-padded to 64 bytes.

    Verifiers hash the SERVED ``report_data_statement`` bytes the same way;
    never re-canonicalize.
    """
    return hashlib.sha256(statement.encode("utf-8")).digest() + b"\x00" * 32


# ------------------------------------------------------ X.509 walk (stdlib)


def _pem_cert_der(cert_pem: bytes) -> bytes:
    """DER bytes of the FIRST PEM certificate block (the serving leaf)."""
    text = cert_pem.decode("ascii")
    begin = text.index(_PEM_CERT_BEGIN)
    end = text.index("-----END CERTIFICATE-----", begin)
    b64 = text[begin + len(_PEM_CERT_BEGIN):end]
    return base64.b64decode("".join(b64.split()))


def _der_read_tlv(der: bytes, offset: int) -> tuple[int, int, int, int]:
    """(tag, tlv_start, content_start, content_end) of the TLV at ``offset``."""
    tag = der[offset]
    length = der[offset + 1]
    start = offset + 2
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(der[start:start + n], "big")
        start += n
    return tag, offset, start, start + length


def _der_children(der: bytes, start: int, end: int):
    """Yield (tag, tlv_start, content_start, content_end) for each child TLV."""
    offset = start
    while offset < end:
        tag, tlv_start, cstart, cend = _der_read_tlv(der, offset)
        yield tag, tlv_start, cstart, cend
        offset = cend


def cert_spki_and_not_after(cert_pem: bytes) -> tuple[bytes, str]:
    """(DER SubjectPublicKeyInfo TLV, notAfter RFC3339) of the leaf.

    Minimal DER walk of Certificate -> tbsCertificate -> [version] serial
    signature issuer validity subject subjectPublicKeyInfo. The SPKI is
    hashed exactly as the verifier does: SHA-256 over the complete DER TLV,
    matching ``openssl pkey -pubin -outform DER`` on the same certificate.
    """
    der = _pem_cert_der(cert_pem)
    # Certificate ::= SEQUENCE { tbsCertificate, signatureAlgorithm, signatureValue }
    _, _, cert_body_start, _ = _der_read_tlv(der, 0)
    # tbsCertificate is the FIRST child of the Certificate sequence body.
    _, _, tbs_start, tbs_end = next(
        _der_children(der, cert_body_start, len(der))
    )
    fields = list(_der_children(der, tbs_start, tbs_end))
    # [0] EXPLICIT version is context-tagged (0xA0); skip it when present.
    if fields and fields[0][0] == 0xA0:
        fields = fields[1:]
    # serial(2) signature(2) issuer(0x30) validity(0x30) subject(0x30) spki(0x30)
    _, _, validity_start, validity_end = fields[3]
    _, spki_start, _, spki_end = fields[5]

    times = list(_der_children(der, validity_start, validity_end))
    _, _, not_after_start, not_after_end = times[1]
    not_after_raw = der[not_after_start:not_after_end].decode("ascii")
    # UTCTime (YYMMDDHHMMSSZ) or GeneralizedTime (YYYYMMDDHHMMSSZ).
    fmt = "%Y%m%d%H%M%SZ" if len(not_after_raw) == 15 else "%y%m%d%H%M%SZ"
    not_after = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.strptime(not_after_raw, fmt))

    return der[spki_start:spki_end], not_after


# ----------------------------------------------------- Ed25519 (RFC 8032)


_P: Final = 2**255 - 19
_L: Final = 2**252 + 27742317777372353535851937790883648493
_D: Final = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1: Final = pow(2, (_P - 1) // 4, _P)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _SQRT_M1) % _P
    if x % 2 != 0:
        x = _P - x
    return x


_BY: Final = 4 * pow(5, _P - 2, _P)
_BX: Final = _xrecover(_BY)
_B: Final = (_BX, _BY, 1, (_BX * _BY) % _P)  # extended homogeneous coords
_NEUTRAL: Final = (0, 1, 1, 0)


def _ed_add(p: tuple[int, int, int, int], q: tuple[int, int, int, int]):
    """Extended-coordinate point addition (add-2008-hwcd-3)."""
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = (y1 - x1) * (y2 - x2) % _P
    b = (y1 + x1) * (y2 + x2) % _P
    c = 2 * t1 * t2 * _D % _P
    d = 2 * z1 * z2 % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _ed_scalarmult(point, scalar: int):
    result = _NEUTRAL
    while scalar > 0:
        if scalar & 1:
            result = _ed_add(result, point)
        point = _ed_add(point, point)
        scalar >>= 1
    return result


def _ed_compress(point) -> bytes:
    x, y, z, _ = point
    zinv = pow(z, _P - 2, _P)
    x = x * zinv % _P
    y = y * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _ed_decompress(data: bytes):
    if len(data) != 32:
        raise ValueError("point must be 32 bytes")
    y_int = int.from_bytes(data, "little")
    sign = y_int >> 255
    y = y_int & ((1 << 255) - 1)
    if y >= _P:
        raise ValueError("point y out of range")
    x = _xrecover(y)
    if x & 1 != sign:
        x = _P - x
    return (x, y, 1, x * y % _P)


def _ed_expand(seed: bytes) -> tuple[int, bytes]:
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must be 32 bytes")
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def ed25519_public_key(seed: bytes) -> bytes:
    """RFC 8032 public key (32 bytes) from a 32-byte seed."""
    a, _ = _ed_expand(seed)
    return _ed_compress(_ed_scalarmult(_B, a))


def ed25519_sign(seed: bytes, message: bytes) -> bytes:
    """Deterministic RFC 8032 Ed25519 signature (64 bytes)."""
    a, prefix = _ed_expand(seed)
    big_a = _ed_compress(_ed_scalarmult(_B, a))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    big_r = _ed_compress(_ed_scalarmult(_B, r))
    k = int.from_bytes(hashlib.sha512(big_r + big_a + message).digest(), "little") % _L
    s = (r + k * a) % _L
    return big_r + s.to_bytes(32, "little")


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """RFC 8032 Ed25519 verification (used by the conformance test)."""
    if len(signature) != 64 or len(public_key) != 32:
        return False
    try:
        big_a = _ed_decompress(public_key)
        big_r = _ed_decompress(signature[:32])
    except ValueError:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    k = int.from_bytes(
        hashlib.sha512(signature[:32] + public_key + message).digest(), "little"
    ) % _L
    left = _ed_scalarmult(_B, s)
    right = _ed_add(big_r, _ed_scalarmult(big_a, k))
    return _ed_compress(left) == _ed_compress(right)


# ------------------------------------------------------------- tee-pubkey


class _TeeKeypair:
    """Per-boot in-guest Ed25519 evidence-signing key (the ``tee-pubkey``).

    Generated in the guest at first use (os.urandom) or loaded from a 32-byte
    seed file (or 64-hex text) at ``KUBETEE_EVIDENCE_TEE_KEY``. Its public
    half rides in the served keyset's ``signing_keys`` and is thereby
    committed by ``keyset_digest`` into the hardware REPORTDATA.
    """

    def __init__(self, seed: bytes | None = None) -> None:
        if seed is not None and len(seed) != 32:
            raise ValueError("tee-pubkey seed must be 32 bytes")
        self._seed: Final = seed if seed is not None else os.urandom(32)
        raw_pub = ed25519_public_key(self._seed)
        self.x: Final = base64.urlsafe_b64encode(raw_pub).decode("ascii").rstrip("=")
        self.spki_sha256: Final = hashlib.sha256(_spki_tlv(raw_pub)).hexdigest()

    def jwk(self) -> dict[str, str]:
        return {"name": "tee-pubkey", "kty": "OKP", "crv": "Ed25519",
                "alg": "EdDSA", "use": "sig", "x": self.x}

    def sign(self, message: bytes) -> bytes:
        return ed25519_sign(self._seed, message)


def _spki_tlv(raw_pub: bytes) -> bytes:
    """DER SubjectPublicKeyInfo TLV for a raw Ed25519 public key.

    SPKI = SEQUENCE { AlgorithmIdentifier { id-Ed25519 }, BIT STRING pubkey };
    id-Ed25519 = 1.3.101.112 = OID 06 03 2B 65 70.
    """
    alg = bytes.fromhex("300306032b6570")
    bitstring = b"\x03" + bytes([len(raw_pub) + 1]) + b"\x00" + raw_pub
    content = alg + bitstring
    return b"\x30" + bytes([len(content)]) + content


def _load_seed() -> bytes | None:
    path = os.environ.get("KUBETEE_EVIDENCE_TEE_KEY", "")
    if not path:
        return None
    blob = open(path, "rb").read().strip()
    if re.fullmatch(rb"[0-9a-fA-F]{64}", blob):
        return bytes.fromhex(blob.decode("ascii"))
    if len(blob) == 32:
        return blob
    raise ValueError("KUBETEE_EVIDENCE_TEE_KEY must be 32 raw bytes or 64 hex")


_keypair: _TeeKeypair | None = None
_keypair_lock: Final = asyncio.Lock()


async def _tee_keypair() -> _TeeKeypair:
    global _keypair
    if _keypair is None:
        async with _keypair_lock:
            if _keypair is None:
                _keypair = _TeeKeypair(_load_seed())
    return _keypair


# ------------------------------------------------------------- keyset


def _tls_keyset(domain: str, cert_pem: bytes, keypair: _TeeKeypair) -> dict[str, Any]:
    """Served keyset: TLS leaf identity + the committed ``tee-pubkey``.

    Same shape as the gateway's kubetee_binding.keyset_from_cert_pem (whose
    ``spki_sha256`` is SHA-256 of the DER SubjectPublicKeyInfo), with
    ``signing_keys`` carrying the per-boot tee-pubkey (empty on the gateway).
    Tuples serialize as JSON arrays and are JCS-stable.
    """
    spki_tlv, not_after = cert_spki_and_not_after(cert_pem)
    return {
        "subject": f"{SPEC_VERSION}/{domain}",
        "not_after": not_after,
        "tls_public_keys": (
            {"domain": domain, "spki_sha256": hashlib.sha256(spki_tlv).hexdigest()},
        ),
        "signing_keys": (keypair.jwk(),),
        "e2ee_keys": (),
    }


def _serving_cert() -> bytes | None:
    try:
        from sglang.srt.runtime_context import get_serving

        path = getattr(get_serving(), "ssl_certfile", None)
        return open(path, "rb").read() if path else None
    except Exception:
        return None


# ------------------------------------------------------------- epoch mint


async def _aa_fetch(runtime_data_b64url: str) -> dict[str, Any]:
    """GET the in-guest attestation-agent evidence (loopback-only).

    Returns {"quote": <b64 str>, "cc_eventlog": <b64 str|None>} — the CoCo
    AA TdxEvidence envelope (content-type octet-stream, body JSON).
    """
    import aiohttp

    url = (
        f"{_AA_BASE}/aa/evidence?runtime_data={runtime_data_b64url}&encoding=base64"
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"aa/evidence HTTP {resp.status}")
            return json.loads(await resp.read())


class _Epoch:
    __slots__ = ("epoch_id", "issued_at", "valid_until", "valid_until_epoch",
                 "statement", "keyset_digest_value", "keyset", "report_data",
                 "quote", "cc_eventlog", "counter")

    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


_epoch: _Epoch | None = None
_mint_lock: Final = asyncio.Lock()
_epoch_seq: int = 0


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


async def _mint_epoch() -> _Epoch:
    """Mint one hardware quote and seal an epoch statement in its REPORTDATA.

    Single-flight: concurrent requests share one mint. ``epoch_id`` is unique
    per mint (``<unix_ns>-<seq>``) so no statement is ever reused across
    mints. The statement is JCS-serialized (keys sorted by UTF-16 units).
    """
    global _epoch, _epoch_seq
    async with _mint_lock:
        now = time.time()
        if _epoch is not None and now < _epoch.valid_until_epoch:
            return _epoch

        keypair = await _tee_keypair()
        cert_pem = _serving_cert()
        if not cert_pem:
            raise RuntimeError("serving certificate unavailable for the keyset")
        domain = os.environ.get("KUBETEE_EVIDENCE_DOMAIN", "sglang-backend")
        keyset = _tls_keyset(domain, cert_pem, keypair)
        digest = "sha256:" + hashlib.sha256(jcs(keyset).encode("utf-8")).hexdigest()

        _epoch_seq += 1
        epoch_id = f"{int(now * 1e9)}-{_epoch_seq}"
        issued_at = _iso(now)
        valid_until = _iso(now + _EPOCH_TTL_S)
        statement = jcs({
            "epoch_id": epoch_id,
            "issued_at": issued_at,
            "valid_until": valid_until,
            "keyset_digest": digest,
            "purpose": PURPOSE,
        })
        rd = report_data(statement)
        rd_b64url = base64.urlsafe_b64encode(rd).decode("ascii").rstrip("=")
        evidence = await _aa_fetch(rd_b64url)
        quote = evidence.get("quote")
        if not isinstance(quote, str) or not quote:
            raise RuntimeError("aa/evidence returned no quote")

        _epoch = _Epoch(
            epoch_id=epoch_id,
            issued_at=issued_at,
            valid_until=valid_until,
            valid_until_epoch=now + _EPOCH_TTL_S,
            statement=statement,
            keyset_digest_value=digest,
            keyset=keyset,
            report_data=rd,
            quote=quote,
            cc_eventlog=evidence.get("cc_eventlog"),
            counter=0,
        )
        return _epoch


# ------------------------------------------------------------- signature


def signature_input(quote_b64: str, nonce_hex: str, epoch_id: str,
                    counter: int) -> bytes:
    """Normative Layer-2 signature input (see module docstring).

    SHA-256(quote_bytes) || SHA-256(nonce_16_bytes) || epoch_id || counter(8B).
    Byte-identical to the client checklist in CLIENT-FACING-ATTESTATION.md.
    """
    nonce = (nonce_hex or "").strip().lower()
    if not _NONCE_RE.match(nonce):
        raise ValueError("nonce must be exactly 64 hex characters")
    quote_bytes = base64.b64decode(quote_b64 + "=" * (-len(quote_b64) % 4))
    return (
        hashlib.sha256(quote_bytes).digest()
        + hashlib.sha256(bytes.fromhex(nonce)).digest()
        + epoch_id.encode("ascii")
        + int(counter).to_bytes(8, "big")
    )


# ------------------------------------------------------------- envelope


async def evidence_envelope(nonce: str | None = None) -> dict[str, Any]:
    """The self-describing evidence envelope (contract shape)."""
    ep = await _mint_epoch()
    keypair = await _tee_keypair()

    counter = 0
    signature = ""
    if nonce is not None:
        ep.counter += 1
        counter = ep.counter
        sig_input = signature_input(ep.quote, nonce, ep.epoch_id, counter)
        signature = base64.b64encode(keypair.sign(sig_input)).decode("ascii")

    rd_b64 = base64.b64encode(ep.report_data).decode("ascii")
    return {
        "spec_version": SPEC_VERSION,
        "tee": TEE,
        "tee_type": TEE_TYPE,
        "role": _ROLE,
        "subject": SUBJECT,
        "runtime_class": RUNTIME_CLASS,
        "pod": os.environ.get("HOSTNAME", ""),
        "epoch_id": ep.epoch_id,
        "issued_at": ep.issued_at,
        "valid_until": ep.valid_until,
        "report_data": rd_b64,
        "report_data_statement": ep.statement,
        "keyset": ep.keyset,
        "bindings": {
            "version": SPEC_VERSION,
            "report_data_statement": ep.statement,
            "keyset_digest": ep.keyset_digest_value,
        },
        "counter": counter,
        "quote": ep.quote,
        "cc_eventlog": ep.cc_eventlog,
        "evidence": {
            "quote": ep.quote,
            "quote_report_data": rd_b64,
            "event_log": ep.cc_eventlog,
        },
        "tee_pubkey": keypair.jwk(),
        "signature": signature,
    }


if router is not None:

    # Two mount paths for one handler. The gateway builds its fetch URL as
    # ``api_base.rstrip('/') + '/kubetee/evidence'`` where ``api_base`` already
    # ends in ``/v1``, so it requests ``/v1/kubetee/evidence``. A direct
    # operator/client fetch (and the documented client guide) uses the root
    # ``/kubetee/evidence``. Serving both lets the relay and the capture work
    # with zero gateway changes. Role-scoped either way (404 on unknown role).
    @router.get("/kubetee/evidence")
    @router.get("/v1/kubetee/evidence")
    async def get_evidence(role: str | None = None, nonce: str | None = None) -> Any:
        """Relay the guest's TDX evidence over the strict-mTLS listener.

        ``role`` scopes the route (the gateway fetches with
        ``?role=nim-terminator``); an unknown role is refused — role-scoped,
        not an open relay. With ``nonce`` (64 hex) the response also carries
        the Layer-2 ``signature``.
        """
        if role is not None and role != SUBJECT:
            return JSONResponse({"error": "unknown role"}, status_code=404)
        if nonce is not None:
            nonce = nonce.strip().lower()
            if not _NONCE_RE.match(nonce):
                return JSONResponse(
                    {"error": "nonce must be exactly 64 hex characters"},
                    status_code=400,
                )
        try:
            return await evidence_envelope(nonce)
        except Exception as e:  # fail closed — never fabricate evidence
            return JSONResponse(
                {"error": f"evidence unavailable: {e}"}, status_code=503
            )
