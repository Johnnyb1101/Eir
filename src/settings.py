from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    provider: Literal["anthropic", "ollama"]
    model: str = Field(min_length=1)

    @field_validator("model")
    @classmethod
    def nonblank_model(cls, value):
        if not value.strip():
            raise ValueError("Model name must not be blank")
        return value


class Settings(ModelSettings):
    visual: ModelSettings | None = None


def read_config(path):
    with path.open(encoding="utf-8") as stream:
        try:
            data = yaml.safe_load(stream)
        except yaml.YAMLError as error:
            raise ValueError(f"Invalid YAML in {path.name}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a configuration mapping")
    return data


def load_config(base_path=None):
    base_path = (Path(base_path) if base_path is not None
                 else Path(__file__).resolve().parent.parent / "config.yaml")
    shared = Settings.model_validate(read_config(base_path)).model_dump()
    local_path = base_path.with_name("config.local.yaml")
    if local_path.exists():
        local = read_config(local_path)
        if ("provider" in local and local["provider"] != shared["provider"]
                and "model" not in local):
            raise ValueError("Changing provider in config.local.yaml also requires a model")
        # Replace visual settings as a unit to avoid mixing provider/model pairs.
        shared.update(local)
    return Settings.model_validate(shared)
