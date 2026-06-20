import os
import unittest
from unittest.mock import MagicMock, patch
import pytest
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from crew_studio import auth
from crew_studio.auth import decode_and_verify_token, CurrentUser, MOCK_USER

# Generate RSA keypair for testing
private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048
)
public_key = private_key.public_key()

# Convert public key to PEM format
public_key_pem = public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo
)


class TestAuth(unittest.TestCase):
    def setUp(self):
        # Reset auth configurations to defaults before each test
        auth.AUTH_ENABLED = True
        auth.KEYCLOAK_ISSUER_URL = "http://mock-issuer/realms/opl-crew"
        auth.KEYCLOAK_JWKS_URL = "http://mock-issuer/realms/opl-crew/protocol/openid-connect/certs"
        
        # Mock the JWKS cache
        self.mock_jwks_cache = MagicMock()
        auth.jwks_cache = self.mock_jwks_cache

    def test_mock_user_fallback_when_disabled(self):
        auth.AUTH_ENABLED = False
        user = decode_and_verify_token("any-token-is-fine")
        self.assertEqual(user, MOCK_USER)
        self.assertTrue(user.is_admin)

    def test_valid_token_decode(self):
        # Mock public key retrieval
        self.mock_jwks_cache.get_public_key.return_value = public_key

        payload = {
            "sub": "user-456",
            "email": "test@example.com",
            "iss": "http://mock-issuer/realms/opl-crew",
            "azp": "opl-studio",
            "realm_access": {
                "roles": ["developer", "admin"]
            },
            "groups": ["/Platform Team", "/DevOps"]
        }

        # Encode token using private key and include kid in headers
        headers = {"kid": "key-id-123"}
        token = jwt.encode(payload, private_key, algorithm="RS256", headers=headers)

        # Decode & verify
        user = decode_and_verify_token(token)

        self.assertEqual(user.user_id, "user-456")
        self.assertEqual(user.email, "test@example.com")
        self.assertEqual(user.roles, ["developer", "admin"])
        self.assertEqual(user.teams, ["Platform Team", "DevOps"])
        self.assertTrue(user.is_admin)
        self.mock_jwks_cache.get_public_key.assert_called_with("key-id-123")

    def test_invalid_kid(self):
        # Mock public key to return None (meaning kid is not found in JWKS)
        self.mock_jwks_cache.get_public_key.return_value = None

        payload = {
            "sub": "user-456",
            "iss": "http://mock-issuer/realms/opl-crew"
        }
        token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "unknown-kid"})

        with self.assertRaises(jwt.exceptions.InvalidTokenError) as context:
            decode_and_verify_token(token)
        self.assertIn("Invalid public key ID", str(context.exception))

    def test_missing_kid(self):
        payload = {
            "sub": "user-456",
            "iss": "http://mock-issuer/realms/opl-crew"
        }
        token = jwt.encode(payload, private_key, algorithm="RS256")  # No headers/kid

        with self.assertRaises(jwt.exceptions.InvalidTokenError) as context:
            decode_and_verify_token(token)
        self.assertIn("Missing key ID", str(context.exception))

    def test_expired_token(self):
        self.mock_jwks_cache.get_public_key.return_value = public_key

        import time
        # Token expired 10 seconds ago
        payload = {
            "sub": "user-456",
            "iss": "http://mock-issuer/realms/opl-crew",
            "azp": "opl-studio",
            "exp": int(time.time()) - 10
        }
        token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "key-id-123"})

        with self.assertRaises(jwt.exceptions.ExpiredSignatureError):
            decode_and_verify_token(token)

    def test_mismatch_issuer(self):
        self.mock_jwks_cache.get_public_key.return_value = public_key

        payload = {
            "sub": "user-456",
            "iss": "http://different-issuer/realm",
            "azp": "opl-studio"
        }
        token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "key-id-123"})

        with self.assertRaises(jwt.exceptions.InvalidTokenError) as context:
            decode_and_verify_token(token)
        self.assertIn("Invalid issuer", str(context.exception))

    def test_invalid_authorized_party_and_audience(self):
        self.mock_jwks_cache.get_public_key.return_value = public_key

        payload = {
            "sub": "user-456",
            "iss": "http://mock-issuer/realms/opl-crew",
            "azp": "malicious-app",
            "aud": "unknown-audience"
        }
        token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "key-id-123"})

        with self.assertRaises(jwt.exceptions.InvalidTokenError) as context:
            decode_and_verify_token(token)
        self.assertIn("authorized party (azp) or audience not allowed", str(context.exception))
