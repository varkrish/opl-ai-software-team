import os
import time
import logging
from dataclasses import dataclass
from typing import List, Optional
import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
import httpx
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

logger = logging.getLogger("crew_studio.auth")

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() == "true"
KEYCLOAK_JWKS_URL = os.getenv("KEYCLOAK_JWKS_URL")
KEYCLOAK_ISSUER_URL = os.getenv("KEYCLOAK_ISSUER_URL")

@dataclass
class CurrentUser:
    user_id: str
    email: str
    roles: List[str]
    teams: List[str]
    is_admin: bool

# Mock user for local development when AUTH_ENABLED is False
MOCK_USER = CurrentUser(
    user_id="mock-user-123",
    email="alice@company.com",
    roles=["developer", "admin"],
    teams=["Platform Team", "Migration Team"],
    is_admin=True
)

security = HTTPBearer(auto_error=False)

class JWKSCache:
    def __init__(self, jwks_url: str):
        self.jwks_url = jwks_url
        self.keys = {}
        self.last_fetched = 0
        self.ttl = 300  # 5 minutes

    def get_public_key(self, kid: str):
        now = time.time()
        if not self.keys or kid not in self.keys or (now - self.last_fetched) > self.ttl:
            self.fetch_keys()
        return self.keys.get(kid)

    def fetch_keys(self):
        try:
            logger.info(f"Fetching JWKS from {self.jwks_url}")
            response = httpx.get(self.jwks_url, timeout=10.0)
            response.raise_for_status()
            jwks_data = response.json()
            
            jwk_set = jwt.PyJWKSet.from_dict(jwks_data)
            new_keys = {}
            for jwk in jwk_set.keys:
                if jwk.key_id:
                    new_keys[jwk.key_id] = jwk.key
            self.keys = new_keys
            self.last_fetched = time.time()
            logger.info(f"Successfully cached {len(self.keys)} JWK keys")
        except Exception as e:
            logger.error(f"Failed to fetch JWKS: {e}")

jwks_cache = JWKSCache(KEYCLOAK_JWKS_URL) if KEYCLOAK_JWKS_URL else None

def decode_and_verify_token(token: str) -> CurrentUser:
    """Decode and verify JWT token. Raises InvalidTokenError/ExpiredSignatureError on failure."""
    if not AUTH_ENABLED:
        return MOCK_USER

    # 1. Unverified decode to extract kid (key ID)
    headers = jwt.get_unverified_header(token)
    kid = headers.get("kid")
    if not kid:
        raise InvalidTokenError("Missing key ID (kid) in token header")

    # 2. Get the public key
    if not jwks_cache:
        raise InvalidTokenError("Keycloak JWKS URL is not configured")
    
    public_key = jwks_cache.get_public_key(kid)
    if not public_key:
        raise InvalidTokenError("Invalid public key ID")

    # 3. Decode & validate JWT
    options = {
        "verify_aud": False,  # Checked azp/audience manually to handle multi-scope clients
        "verify_iss": True,
    }
    
    payload = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        issuer=KEYCLOAK_ISSUER_URL,
        options=options
    )

    # 4. Manual azp / audience validation
    azp = payload.get("azp")
    if azp not in ["opl-studio", "connector-service"]:
        aud = payload.get("aud")
        aud_list = [aud] if isinstance(aud, str) else (aud or [])
        if not any(a in ["opl-studio", "connector-service"] for a in aud_list):
            raise InvalidTokenError("Token authorized party (azp) or audience not allowed")

    # 5. Extract fields
    user_id = payload.get("sub")
    email = payload.get("email") or payload.get("preferred_username") or f"service-account-{azp}@example.com"
    
    realm_access = payload.get("realm_access", {})
    roles = realm_access.get("roles", [])
    is_admin = "admin" in roles
    
    raw_teams = payload.get("groups", [])
    teams = [team.lstrip("/") for team in raw_teams if team]

    return CurrentUser(
        user_id=user_id,
        email=email,
        roles=roles,
        teams=teams,
        is_admin=is_admin
    )

async def get_current_user(request: Request) -> CurrentUser:
    """FastAPI dependency to retrieve the authenticated user from request state (populated by middleware)."""
    if not AUTH_ENABLED:
        return MOCK_USER
    
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication credentials not found or invalid")
    return user
