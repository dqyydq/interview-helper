import pytest

from app.core.crypto import SecretCipher, SecretDecryptionError


def test_secret_cipher_round_trips_without_storing_plaintext() -> None:
    cipher = SecretCipher("a-local-secret-with-enough-entropy")

    encrypted = cipher.encrypt("sk-sensitive")
    encrypted_headers = cipher.encrypt_mapping({"x-tenant": "private-value"})

    assert "sk-sensitive" not in encrypted
    assert "private-value" not in str(encrypted_headers)
    assert cipher.decrypt(encrypted) == "sk-sensitive"
    assert cipher.decrypt_mapping(encrypted_headers) == {"x-tenant": "private-value"}


def test_secret_cipher_rejects_a_different_local_secret() -> None:
    encrypted = SecretCipher("first-local-encryption-secret").encrypt("secret")

    with pytest.raises(SecretDecryptionError):
        SecretCipher("second-local-encryption-secret").decrypt(encrypted)
