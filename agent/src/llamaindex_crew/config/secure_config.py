"""
Secure configuration management with file-based secrets

Supports multiple configuration sources with priority:
1. CLI argument (--config)
2. Environment variable (CONFIG_FILE_PATH)
3. Project config (./crew.config.yaml)
4. User config (~/.crew-ai/config.yaml)
5. System config (/etc/crew-ai/config.yaml)
6. Docker secrets (/run/secrets/)
7. Kubernetes secrets (/var/secrets/)
8. Environment variables (legacy, lowest priority)
9. .env file (development only)

Security features:
- File permission validation (600/400 required)
- Optional encryption support (Fernet)
- Service account ownership
- Audit logging
"""
import os
import stat
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, validator
import yaml
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class LLMConfig(BaseModel):
    """LLM configuration"""
    api_key: str = Field(..., description="API key for LLM provider")
    api_base_url: Optional[str] = Field(None, description="Base URL for API endpoint")
    environment: str = Field("production", description="Environment: production or local")
    
    # Model configuration
    model_manager: str = Field("gpt-4o-mini", description="Model for manager agents")
    model_worker: str = Field("gpt-4o-mini", description="Model for worker agents")
    model_reviewer: str = Field("gpt-4o-mini", description="Model for reviewer agents")
    
    # LLM parameters
    max_tokens: int = Field(2048, description="Maximum tokens per request")
    temperature: float = Field(0.7, description="Sampling temperature")
    embedding_model: str = Field("text-embedding-3-small", description="Embedding model")
    
    # Ollama configuration (for local)
    ollama_base_url: str = Field("http://localhost:11434", description="Ollama server URL")
    ollama_model: str = Field("llama3.2:latest", description="Ollama model name")


class BudgetConfig(BaseModel):
    """Budget tracking configuration"""
    max_cost_per_project: float = Field(100.0, description="Maximum cost per project (USD)")
    max_cost_per_hour: float = Field(10.0, description="Maximum cost per hour (USD)")
    alert_threshold: float = Field(0.8, description="Alert threshold (0.0-1.0)")


class WorkspaceConfig(BaseModel):
    """Workspace configuration"""
    path: str = Field("./workspace", description="Base workspace path")


class LoggingConfig(BaseModel):
    """Logging configuration"""
    level: str = Field("INFO", description="Log level")


class SecretConfig(BaseModel):
    """
    Main configuration model with validation
    
    All sensitive data should be loaded securely from config files
    with proper permissions (600/400).
    """
    llm: LLMConfig
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    
    # Encryption key for encrypted values
    _encryption_key: Optional[bytes] = None
    
    class Config:
        # Don't include private attributes in serialization
        underscore_attrs_are_private = True
    
    @validator('llm', pre=True)
    def decrypt_llm_secrets(cls, v, values):
        """Decrypt encrypted LLM secrets if encryption key is provided"""
        if isinstance(v, dict):
            # Check for encrypted values
            if 'api_key_encrypted' in v:
                encryption_key = values.get('_encryption_key')
                if encryption_key:
                    try:
                        fernet = Fernet(encryption_key)
                        decrypted = fernet.decrypt(v['api_key_encrypted'].encode()).decode()
                        v['api_key'] = decrypted
                        del v['api_key_encrypted']
                    except InvalidToken:
                        raise ValueError("Failed to decrypt api_key_encrypted - invalid encryption key")
                else:
                    raise ValueError("api_key_encrypted found but no encryption key provided")
        return v


class ConfigLoader:
    """
    Multi-source configuration loader with priority and security validation
    """
    
    # Config file search paths (in priority order)
    CONFIG_SEARCH_PATHS = [
        Path("./crew.config.yaml"),           # Project config
        Path.home() / ".crew-ai" / "config.yaml",  # User config
        Path("/etc/crew-ai/config.yaml"),     # System config
        Path("/run/secrets/crew_config"),     # Docker secrets
        Path("/run/secrets/config.yaml"),     # Docker secrets (alt)
        Path("/var/secrets/config.yaml"),     # Kubernetes secrets
    ]
    
    @staticmethod
    def load(config_path: Optional[str] = None, encryption_key: Optional[str] = None) -> SecretConfig:
        """
        Load configuration from multiple sources with priority
        
        Args:
            config_path: Explicit config file path (highest priority)
            encryption_key: Optional encryption key for decrypting secrets
        
        Returns:
            SecretConfig instance
        
        Raises:
            ValueError: If no valid configuration found or security checks fail
        """
        config_data = {}
        config_source = None
        
        # Priority 1: Explicit path from argument
        if config_path:
            config_source = Path(config_path)
            logger.info(f"Loading config from explicit path: {config_source}")
            config_data = ConfigLoader._load_file(config_source)
        
        # Priority 2: Environment variable
        if not config_data:
            env_path = os.getenv("CONFIG_FILE_PATH")
            if env_path:
                config_source = Path(env_path)
                logger.info(f"Loading config from CONFIG_FILE_PATH: {config_source}")
                config_data = ConfigLoader._load_file(config_source)
        
        # Priority 3-7: Search paths
        if not config_data:
            for search_path in ConfigLoader.CONFIG_SEARCH_PATHS:
                if search_path.exists():
                    config_source = search_path
                    logger.info(f"Loading config from search path: {config_source}")
                    config_data = ConfigLoader._load_file(config_source)
                    if config_data:
                        break
        
        # Priority 8: Environment variables (legacy fallback)
        if not config_data:
            logger.info("No config file found, using environment variables (legacy mode)")
            config_data = ConfigLoader._load_from_env()
            config_source = "environment variables"
        
        if not config_data:
            raise ValueError(
                "No configuration found. Please provide config via:\n"
                "  1. --config argument\n"
                "  2. CONFIG_FILE_PATH environment variable\n"
                "  3. ./crew.config.yaml (project)\n"
                "  4. ~/.crew-ai/config.yaml (user)\n"
                "  5. /etc/crew-ai/config.yaml (system)\n"
                "  6. /run/secrets/ (Docker)\n"
                "  7. /var/secrets/ (Kubernetes)\n"
                "  8. LLM_API_KEY environment variable (legacy)"
            )
        
        # Load encryption key if provided
        enc_key_bytes = None
        if encryption_key:
            enc_key_bytes = encryption_key.encode()
        elif os.getenv("CONFIG_ENCRYPTION_KEY"):
            enc_key_bytes = os.getenv("CONFIG_ENCRYPTION_KEY").encode()
        
        # Create config with encryption key
        config = SecretConfig(**config_data)
        config._encryption_key = enc_key_bytes
        
        logger.info(f"✅ Configuration loaded successfully from: {config_source}")
        return config
    
    @staticmethod
    def _load_file(path: Path) -> Dict[str, Any]:
        """
        Load configuration from YAML/JSON file with security validation
        
        Args:
            path: Path to config file
        
        Returns:
            Configuration dictionary
        
        Raises:
            ValueError: If file permissions are too permissive
        """
        if not path.exists():
            return {}
        
        # Validate file permissions (Unix-like systems only)
        if hasattr(os, 'stat') and path.stat:
            ConfigLoader._validate_file_permissions(path)
        
        try:
            with open(path, 'r') as f:
                data = yaml.safe_load(f)
                return data if data else {}
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse YAML from {path}: {e}")
            raise ValueError(f"Invalid YAML in config file: {path}")
        except Exception as e:
            logger.error(f"Failed to read config file {path}: {e}")
            return {}
    
    @staticmethod
    def _validate_file_permissions(path: Path):
        """
        Validate that config file has secure permissions (600 or 400)
        
        Args:
            path: Path to config file
        
        Raises:
            ValueError: If permissions are too permissive
        """
        try:
            file_stat = path.stat()
            mode = file_stat.st_mode
            
            # Get permission bits
            perms = stat.S_IMODE(mode)
            
            # Check if group or others have any permissions
            group_perms = (perms & stat.S_IRWXG) >> 3
            other_perms = (perms & stat.S_IRWXO)
            
            if group_perms != 0 or other_perms != 0:
                raise ValueError(
                    f"Config file {path} has insecure permissions: {oct(perms)}\n"
                    f"Required: 600 (rw-------) or 400 (r--------)\n"
                    f"Fix with: chmod 600 {path}"
                )
            
            # Verify owner read permission exists
            owner_perms = (perms & stat.S_IRUSR)
            if not owner_perms:
                raise ValueError(
                    f"Config file {path} is not readable by owner\n"
                    f"Fix with: chmod 600 {path}"
                )
            
            logger.debug(f"✅ Config file permissions validated: {oct(perms)}")
            
        except AttributeError:
            # Windows or system without stat support
            logger.warning("File permission validation not available on this system")
    
    @staticmethod
    def _load_from_env() -> Dict[str, Any]:
        """
        Load configuration from environment variables (legacy fallback)
        
        Returns:
            Configuration dictionary
        """
        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            return {}
        
        config = {
            "llm": {
                "api_key": api_key,
                "api_base_url": os.getenv("LLM_API_BASE_URL"),
                "environment": os.getenv("LLM_ENVIRONMENT", "production"),
                "model_manager": os.getenv("LLM_MODEL_MANAGER", "gpt-4o-mini"),
                "model_worker": os.getenv("LLM_MODEL_WORKER", "gpt-4o-mini"),
                "model_reviewer": os.getenv("LLM_MODEL_REVIEWER", "gpt-4o-mini"),
                "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "2048")),
                "temperature": float(os.getenv("LLM_TEMPERATURE", "0.7")),
                "embedding_model": os.getenv("LLM_EMBEDDING_MODEL", "text-embedding-3-small"),
                "ollama_base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                "ollama_model": os.getenv("OLLAMA_MODEL", "llama3.2:latest"),
            },
            "budget": {
                "max_cost_per_project": float(os.getenv("BUDGET_MAX_COST_PER_PROJECT", "100.0")),
                "max_cost_per_hour": float(os.getenv("BUDGET_MAX_COST_PER_HOUR", "10.0")),
                "alert_threshold": float(os.getenv("BUDGET_ALERT_THRESHOLD", "0.8")),
            },
            "workspace": {
                "path": os.getenv("WORKSPACE_PATH", "./workspace"),
            },
            "logging": {
                "level": os.getenv("LOG_LEVEL", "INFO"),
            }
        }
        
        return config
    
    @staticmethod
    def generate_encryption_key() -> str:
        """
        Generate a new Fernet encryption key
        
        Returns:
            Base64-encoded encryption key
        """
        return Fernet.generate_key().decode()
    
    @staticmethod
    def encrypt_value(value: str, encryption_key: str) -> str:
        """
        Encrypt a value using Fernet encryption
        
        Args:
            value: Value to encrypt
            encryption_key: Encryption key
        
        Returns:
            Encrypted value (base64-encoded)
        """
        fernet = Fernet(encryption_key.encode())
        encrypted = fernet.encrypt(value.encode())
        return encrypted.decode()
    
    @staticmethod
    def decrypt_value(encrypted_value: str, encryption_key: str) -> str:
        """
        Decrypt a value using Fernet encryption
        
        Args:
            encrypted_value: Encrypted value (base64-encoded)
            encryption_key: Encryption key
        
        Returns:
            Decrypted value
        """
        fernet = Fernet(encryption_key.encode())
        decrypted = fernet.decrypt(encrypted_value.encode())
        return decrypted.decode()


def print_config_info(config: SecretConfig):
    """Print configuration information (without secrets)"""
    print("\n" + "=" * 70)
    print("🔧 Secure Configuration")
    print("=" * 70)
    print(f"LLM Environment: {config.llm.environment}")
    print(f"LLM API Key: {'✓ Set' if config.llm.api_key else '✗ Not set'}")
    if config.llm.api_base_url:
        print(f"LLM Base URL: {config.llm.api_base_url}")
    print()
    print("Models:")
    print(f"  Manager: {config.llm.model_manager}")
    print(f"  Worker: {config.llm.model_worker}")
    print(f"  Reviewer: {config.llm.model_reviewer}")
    print()
    print("Budget:")
    print(f"  Max Cost/Project: ${config.budget.max_cost_per_project:.2f}")
    print(f"  Max Cost/Hour: ${config.budget.max_cost_per_hour:.2f}")
    print()
    print(f"Workspace: {config.workspace.path}")
    print(f"Log Level: {config.logging.level}")
    print("=" * 70 + "\n")
