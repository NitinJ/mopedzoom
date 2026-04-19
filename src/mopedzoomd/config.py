# src/mopedzoomd/config.py
from __future__ import annotations
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field
import yaml

class RepoConfig(BaseModel):
    path: str
    default_branch: str = "main"
    aliases: list[str] = Field(default_factory=list)
    pr_reviewers: list[str] = Field(default_factory=list)

class ChannelConfig(BaseModel):
    bot_token: str
    chat_id: int
    mode: Literal["auto", "topics", "header"] = "auto"

class AgentsConfig(BaseModel):
    allow: list[str] = Field(default_factory=lambda: ["*"])
    deny: list[str] = Field(default_factory=list)

class PermissionsConfig(BaseModel):
    default_mode: Literal["bypass", "ask", "allowlist"] = "bypass"
    allowlist: list[str] = Field(default_factory=list)

class DashboardConfig(BaseModel):
    enabled: bool = True
    port: int = 9876

class MetricsConfig(BaseModel):
    enabled: bool = False
    port: int = 9877

class DeliverablesConfig(BaseModel):
    research_repo: str | None = None
    research_path: str = "docs/research/"
    pr_body_template: str | None = None

class LimitsConfig(BaseModel):
    max_concurrent_tasks: int = 4
    default_stage_timeout_s: int = 1800
    grace_period_days: int = 7

class Config(BaseModel):
    channel: ChannelConfig
    repos: dict[str, RepoConfig] = Field(default_factory=dict)
    default_repo: str | None = None
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    deliverables: DeliverablesConfig = Field(default_factory=DeliverablesConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)

def load_config(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return Config.model_validate(data)

def save_config(cfg: Config, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))
