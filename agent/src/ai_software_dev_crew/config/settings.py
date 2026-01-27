import os
from pathlib import Path
from typing import Optional
from pydantic import Field, BaseModel

class Settings(BaseModel):
    """Centralized application settings"""
    
    # Project Settings
    PROJECT_ID: str = Field(default="default-project", description="ID of the current project")
    WORKSPACE_PATH: Path = Field(default=Path("./workspace"), description="Path to the workspace directory")
    
    # Logging
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")
    
    # Execution
    MAX_RETRY_ATTEMPTS: int = Field(default=3, description="Maximum retry attempts for crew execution")
    ENABLE_GIT: bool = Field(default=True, description="Enable Git operations")
    
    # LLM Configuration (loaded from env)
    OPENAI_API_KEY: Optional[str] = Field(default=None, description="OpenAI API Key")
    OPENAI_MODEL_NAME: str = Field(default="gpt-4", description="OpenAI Model Name")
    
    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'

    def __init__(self, **data):
        super().__init__(**data)
        # Ensure workspace path is a Path object and exists
        if isinstance(self.WORKSPACE_PATH, str):
            self.WORKSPACE_PATH = Path(self.WORKSPACE_PATH)
        self.WORKSPACE_PATH.mkdir(parents=True, exist_ok=True)

# Global settings instance
settings = Settings(
    PROJECT_ID=os.getenv("PROJECT_ID", "default-project"),
    WORKSPACE_PATH=Path(os.getenv("WORKSPACE_PATH", "./workspace")),
    LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
    MAX_RETRY_ATTEMPTS=int(os.getenv("MAX_RETRY_ATTEMPTS", "3")),
    ENABLE_GIT=os.getenv("ENABLE_GIT", "true").lower() in ("true", "1", "yes")
)
