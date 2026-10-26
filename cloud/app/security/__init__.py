"""Security — authentication, RBAC, command signing."""

from app.security.auth import (
    create_access_token,
    decode_token,
    generate_api_key,
    get_connector_from_headers,
    get_current_user,
    hash_api_key,
    hash_password,
    require_roles,
    tenant_filter_org_id,
    verify_password,
)
from app.security.hmac_signing import build_signed_command, sign_command
from app.security.rbac import RBAC_MATRIX, can_access
from app.security.tenant import assert_org_access, get_tenant_owned

__all__ = [
    "RBAC_MATRIX",
    "assert_org_access",
    "build_signed_command",
    "can_access",
    "create_access_token",
    "decode_token",
    "generate_api_key",
    "get_connector_from_headers",
    "get_current_user",
    "get_tenant_owned",
    "hash_api_key",
    "hash_password",
    "require_roles",
    "sign_command",
    "tenant_filter_org_id",
    "verify_password",
]
