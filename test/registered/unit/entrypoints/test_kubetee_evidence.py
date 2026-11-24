# SPDX-License-Identifier: Apache-2.0
"""Conformance test for the KubeTEE backend evidence relay (pure logic).

Covers the normative cross-language constructions a client or the gateway
verifies byte-for-byte: JCS (RFC 8785 subset), the epoch statement hashed
into the quote's REPORTDATA, the Layer-2 signature input, RFC 8032 Ed25519,
and the X.509 walk (cross-checked against the `openssl` CLI, the only crypto
tool guaranteed in the canary image). No model, GPU, network or sglang import
chain — the module is loaded by file path so torch is never pulled in.
Run: python3 test_kubetee_evidence.py  (exit 0 == conformant).
"""

import asyncio
import base64
import hashlib
import importlib.util
import subprocess
from pathlib import Path

_MODULE_PATH = (
    Path(__file__).resolve().parents[4]
    / "python/sglang/srt/entrypoints/kubetee_evidence.py"
)
_spec = importlib.util.spec_from_file_location("kubetee_evidence", _MODULE_PATH)
ke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ke)


def openssl(*args):
    return subprocess.run(("openssl", *map(str, args)), check=True,
                          capture_output=True).stdout


def jcs_kats():
    assert ke.jcs({"a": "b"}) == '{"a":"b"}'
    assert ke.jcs(()) == "[]"
    assert ke.jcs(("c", "b", "a")) == '["c","b","a"]'
    assert ke.jcs({"b": "2", "a": "1"}) == '{"a":"1","b":"2"}'
    assert ke.jcs({"a": "x\ny"}) == '{"a":"x\\ny"}'
    # Keys sort by UTF-16 code UNITS, not code points: the astral-plane key
    # U+1D400 encodes as the surrogate pair D835 DC00 and therefore sorts
    # AFTER U+2028 (20 28). Code-point order would sort it before — pin the
    # normative JCS behavior (RFC 8785 section 3.2.3).
    assert ke.jcs({"\u2028": "a", "\U0001D400": "b"}) == (
        '{"\u2028":"a","\U0001D400":"b"}'
    )
    # ASCII keys sort before U+2029 (00 61 < 20 29).
    assert ke.jcs({"a": "\u2028", "\u2029": "\U0001D400"}) == (
        '{"a":"\u2028","\u2029":"\U0001D400"}'
    )
    for bad in (5, 5.5, True, None):
        try:
            ke.jcs(bad)
        except TypeError:
            pass
        else:
            raise AssertionError(f"JCS accepted a non-string scalar: {bad!r}")
    print("JCS: RFC 8785 subset conformant")


def statement_and_report_data_kats():
    # Known-answer vectors (JCS key order: epoch_id, issued_at, keyset_digest,
    # purpose, valid_until) — hash the SERVED statement bytes, never a
    # re-canonicalization.
    keyset_digest = "sha256:" + "ab" * 32
    statement = ke.jcs({
        "epoch_id": "1700000000000000000-1",
        "issued_at": "2026-11-24T12:00:00Z",
        "valid_until": "2026-11-24T12:05:00Z",
        "keyset_digest": keyset_digest,
        "purpose": ke.PURPOSE,
    })
    assert statement == (
        '{"epoch_id":"1700000000000000000-1",'
        '"issued_at":"2026-11-24T12:00:00Z",'
        f'"keyset_digest":"{keyset_digest}",'
        f'"purpose":"{ke.PURPOSE}",'
        '"valid_until":"2026-11-24T12:05:00Z"}'
    )
    rd = ke.report_data(statement)
    assert rd.hex() == (
        "7e779a9b03bee463a2ef333f4387d8bf364470b5b8964101f4e77da107a88e0d"
        + "00" * 32
    )
    assert len(rd) == 64 and rd[32:] == b"\x00" * 32
    print("epoch statement + REPORTDATA: known-answer vectors pinned")


def signature_input_kats():
    nonce = "ab" * 32
    epoch_id = "1700000000000000000-1"
    sig_in = ke.signature_input(base64.b64encode(b"hello-quote").decode(),
                                nonce, epoch_id, 7)
    assert sig_in.hex() == "ea00fd958854a303e798402c0c005cf8b136fc17dfab261f6142d02e71adc6229a2db2e23f1504cd056606553ac049c5e718e8f9ce9233876df1a7a1821af885313730303030303030303030303030303030302d310000000000000007"
    assert len(sig_in) == 93
    try:
        ke.signature_input("x", "zz", "e", 0)
    except ValueError:
        pass
    else:
        raise AssertionError("malformed nonce accepted")
    print("Layer-2 signature input: known-answer vector pinned")


def ed25519_kats():
    # RFC 8032 section 7.1 test vectors (empty, "abc", 2x, ... 1024x).
    vectors = [
        ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
         "", "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
         "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
        ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
         "72", "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
         "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ]
    for seed_hex, msg_hex, pub_hex, sig_hex in vectors:
        seed, msg = bytes.fromhex(seed_hex), bytes.fromhex(msg_hex)
        assert ke.ed25519_public_key(seed) == bytes.fromhex(pub_hex), "public key"
        sig = ke.ed25519_sign(seed, msg)
        assert sig == bytes.fromhex(sig_hex), "signature"
        assert ke.ed25519_verify(bytes.fromhex(pub_hex), msg, sig), "verify"
        assert not ke.ed25519_verify(bytes.fromhex(pub_hex), b"x", sig), "reject"
    print("Ed25519: RFC 8032 test vectors pinned (sign + verify)")


def x509_walk_vs_openssl():
    tmp = Path(subprocess.run(["mktemp", "-d"], check=True,
                              capture_output=True).stdout.decode().strip())
    try:
        for keytype, keyfile, pubfile in (
            ("rsa", "rsa.key", "rsa.pub"),          # the real serving leaf type
            ("ed25519", "ed.key", "ed.pub"),        # short-form SPKI length
        ):
            openssl("req", "-x509", "-newkey", keytype, "-keyout", tmp / keyfile,
                    "-out", tmp / "cert.pem", "-days", "2", "-nodes",
                    "-subj", "/CN=sglang-backend")
            cert_pem = (tmp / "cert.pem").read_bytes()

            spki_tlv, not_after = ke.cert_spki_and_not_after(cert_pem)

            # Ground truth: OpenSSL's own DER SubjectPublicKeyInfo + enddate.
            pub_pem = openssl("x509", "-in", tmp / "cert.pem", "-pubkey", "-noout")
            pub_path = _write(tmp, pubfile, pub_pem)
            ref_spki = openssl("pkey", "-pubin", "-outform", "DER", "-in", pub_path)
            assert spki_tlv == ref_spki, f"{keytype}: SPKI DER mismatch"
            enddate = openssl("x509", "-in", tmp / "cert.pem", "-enddate",
                              "-noout").decode().strip().split("=", 1)[1]
            ref_iso = _openssl_date_to_iso(enddate)
            assert not_after == ref_iso, f"{keytype}: notAfter {not_after} != {ref_iso}"
        print("X.509 walk: SPKI + notAfter match openssl (RSA + Ed25519 leaves)")
    finally:
        subprocess.run(["rm", "-rf", str(tmp)], check=False)


def _write(directory, name, blob):
    path = directory / name
    path.write_bytes(blob)
    return path


def _openssl_date_to_iso(enddate):
    # "Nov 24 12:00:00 2026 GMT" -> RFC3339.
    import time as _t
    return _t.strftime("%Y-%m-%dT%H:%M:%SZ",
                       _t.strptime(enddate, "%b %d %H:%M:%S %Y %Z"))


def envelope_contract():
    seed = bytes.fromhex("11" * 32)
    tmp = Path(subprocess.run(["mktemp", "-d"], check=True,
                              capture_output=True).stdout.decode().strip())
    try:
        openssl("req", "-x509", "-newkey", "rsa", "-keyout", tmp / "k",
                "-out", tmp / "c", "-days", "2", "-nodes",
                "-subj", "/CN=sglang-backend")
        cert_pem = (tmp / "c").read_bytes()
    finally:
        subprocess.run(["rm", "-rf", str(tmp)], check=False)

    async def fake_aa(runtime_data_b64url):
        return {"quote": base64.b64encode(b"hello-quote").decode(),
                "cc_eventlog": "AAAA"}

    ke._aa_fetch = fake_aa
    ke._serving_cert = lambda: cert_pem
    keypair = ke._TeeKeypair(seed)
    ke._keypair = keypair

    env = asyncio.run(ke.evidence_envelope())
    assert env["role"] == ke.DEFAULT_ROLE and env["subject"] == ke.SUBJECT
    assert env["tee"] == "tdx" and env["signature"] == ""  # nonce-less = relay
    assert env["bindings"]["report_data_statement"] == env["report_data_statement"]
    # The statement's keyset_digest commits the served keyset (tee-pubkey).
    assert env["bindings"]["keyset_digest"] == "sha256:" + hashlib.sha256(
        ke.jcs(env["keyset"]).encode()).hexdigest()
    assert any(k.get("name") == "tee-pubkey"
               for k in env["keyset"]["signing_keys"])
    # report_data == SHA-256(statement) || 32 zeros (verifier contract).
    rd = base64.b64decode(env["report_data"])
    assert rd == ke.report_data(env["report_data_statement"])

    # Signed request: signature verifies under the served tee-pubkey and
    # covers quote || nonce || epoch_id || counter.
    nonce = "ab" * 32
    signed = asyncio.run(ke.evidence_envelope(nonce))
    assert signed["counter"] == 1
    pub = ke.ed25519_public_key(seed)
    sig_in = ke.signature_input(signed["quote"], nonce,
                                signed["epoch_id"], signed["counter"])
    assert ke.ed25519_verify(pub, sig_in, base64.b64decode(signed["signature"]))
    signed2 = asyncio.run(ke.evidence_envelope(nonce))
    assert signed2["counter"] == 2, "counter must be monotonic"
    print("envelope contract: fields, keyset commitment, signature, counter OK")


def evidence_route_paths():
    # The handler is registered under BOTH the root path and the /v1 alias
    # because the gateway builds `api_base.rstrip('/') + '/kubetee/evidence'`
    # where api_base ends in `/v1`. Both must decorate get_evidence or the
    # relay 404s while a direct capture still works (a silent split-brain).
    import ast
    tree = ast.parse(Path(_MODULE_PATH).read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "get_evidence":
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "get" and dec.args
                        and isinstance(dec.args[0], ast.Constant)):
                    found.add(dec.args[0].value)
    assert "/kubetee/evidence" in found, f"root path missing: {found}"
    assert "/v1/kubetee/evidence" in found, f"/v1 alias missing: {found}"
    print(f"evidence route paths: {sorted(found)} OK")


def main():
    jcs_kats()
    statement_and_report_data_kats()
    signature_input_kats()
    ed25519_kats()
    x509_walk_vs_openssl()
    envelope_contract()
    evidence_route_paths()
    print("KubeTEE evidence relay: all conformance checks passed")


if __name__ == "__main__":
    main()
