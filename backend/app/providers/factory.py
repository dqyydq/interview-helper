from app.core.config import settings
from app.core.crypto import SecretCipher
from app.db.models.common import ProviderType
from app.db.models.model_connection import ModelConnection
from app.providers.anthropic_compatible import AnthropicCompatibleProvider
from app.providers.base import ChatProvider, ProviderError
from app.providers.openai_compatible import OpenAICompatibleProvider


def build_provider(connection: ModelConnection) -> ChatProvider:
    cipher = SecretCipher(settings.encryption_secret)
    if not connection.encrypted_api_key:
        raise ProviderError(
            code="provider_key_missing",
            message="模型连接尚未配置 API Key",
        )
    api_key = cipher.decrypt(connection.encrypted_api_key)
    headers = cipher.decrypt_mapping(connection.extra_headers_encrypted)
    common = {
        "base_url": connection.base_url,
        "api_key": api_key,
        "model": connection.model_name,
        "extra_headers": headers,
    }
    if connection.provider_type == ProviderType.OPENAI_COMPATIBLE:
        return OpenAICompatibleProvider(**common)
    if connection.provider_type == ProviderType.ANTHROPIC_COMPATIBLE:
        return AnthropicCompatibleProvider(**common)
    raise ProviderError(code="provider_type_unsupported", message="不支持该模型协议")
