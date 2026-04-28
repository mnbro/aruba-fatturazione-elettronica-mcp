from aruba_fatturazione_elettronica_mcp.redaction import redact


def test_redacts_tokens_passwords_base64_and_pii() -> None:
    payload = {
        "password": "secret",
        "access_token": "token",
        "vatcodeSender": "IT12345678901",
        "data_base64": "A" * 200,
    }
    redacted = redact(payload)
    assert redacted["password"] == "<redacted>"
    assert redacted["access_token"] == "<redacted>"
    assert redacted["vatcodeSender"]["redacted"] is True
    assert redacted["data_base64"]["kind"] == "sensitive_payload"
