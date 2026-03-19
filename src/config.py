import yaml
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    gemini_api_key: str = Field(..., env="GEMINI_API_KEY")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def load_config(path: str = "config.yaml") -> dict:
    with open(Path(path), "r") as f:
        return yaml.safe_load(f)
