import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken


class SecretDecryptionError(ValueError):
    pass


class SecretCipher:
    def __init__(self, secret: str) -> None:
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise SecretDecryptionError("无法解密本地密钥，请检查加密配置") from exc

    def encrypt_mapping(self, value: dict[str, str]) -> dict[str, str]:
        if not value:
            return {}
        return {"ciphertext": self.encrypt(json.dumps(value, ensure_ascii=False))}

    def decrypt_mapping(self, value: dict) -> dict[str, str]:
        ciphertext = value.get("ciphertext") if value else None
        if not ciphertext:
            return {}
        decoded = json.loads(self.decrypt(str(ciphertext)))
        if not isinstance(decoded, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in decoded.items()
        ):
            raise SecretDecryptionError("加密请求头格式无效")
        return decoded
