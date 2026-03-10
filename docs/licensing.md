# Licensing Model (Offline)

## Runtime enforcement

- Trial period: 7 days from first run.
- After trial expiry, app requires activation with a signed local license file.
- Validation is machine-bound using `machine_hash`.
- Signature verification uses Ed25519 public key from `LICENSE_PUBLIC_KEY_B64`.

## License file format

```json
{
  "payload": {
    "customer_id": "cust-001",
    "machine_hash": "hex-hash",
    "issued_at": "2026-03-10T00:00:00+00:00",
    "expires_at": "2026-09-10T00:00:00+00:00",
    "edition": "pro"
  },
  "signature": "base64-signature",
  "algo": "ed25519"
}
```

## Activation flow

1. Customer shares machine hash.
2. Vendor signs a license with `tools/license_issuer.py` (private key stays with vendor).
3. Customer activates via UI endpoint `/api/license/activate`.
4. App validates signature + machine binding + expiry at startup and trading requests.

## Security note

No desktop licensing is unbreakable. This model is standard for strong commercial deterrence:
- signed license documents
- machine binding
- tamper-evident local trial state
- binary distribution
