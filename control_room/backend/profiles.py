from __future__ import annotations

import yaml
from pydantic import BaseModel, Field

from .settings import Settings


class ProfileEntry(BaseModel):
    key: str
    label: str
    args: list[str] = Field(default_factory=list)


class ProfileGroup(BaseModel):
    entries: dict[str, ProfileEntry] = Field(default_factory=dict)


class ProfilesConfig(BaseModel):
    version: int = 1
    asr: dict[str, ProfileEntry] = Field(default_factory=dict)
    lexicon: dict[str, ProfileEntry] = Field(default_factory=dict)
    rag: dict[str, ProfileEntry] = Field(default_factory=dict)


def _default_profiles() -> ProfilesConfig:
    return ProfilesConfig(
        asr={
            "default": ProfileEntry(key="default", label="ASR défaut", args=[]),
            "talkshow": ProfileEntry(
                key="talkshow", label="ASR Talkshow", args=["--profile", "talkshow"]
            ),
        },
        lexicon={
            "default": ProfileEntry(key="default", label="Lexicon standard", args=[]),
        },
        rag={
            "default": ProfileEntry(key="default", label="RAG défaut", args=[]),
            "nas": ProfileEntry(
                key="nas", label="RAG NAS", args=["--version-tag", "nas_v1"]
            ),
        },
    )


def load_profiles(settings: Settings) -> ProfilesConfig:
    path = settings.profiles_path
    if not path.exists():
        return _default_profiles()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return _default_profiles()
    if not isinstance(data, dict):
        return _default_profiles()
    version = int(data.get("version", 1))

    def _parse(group: str) -> dict[str, ProfileEntry]:
        raw = data.get(group, {})
        entries: dict[str, ProfileEntry] = {}
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, dict):
                    label = value.get("label") or key
                    args = value.get("args") or []
                    if isinstance(args, list):
                        entries[key] = ProfileEntry(
                            key=key,
                            label=str(label),
                            args=[str(part) for part in args],
                        )
        return entries

    config = ProfilesConfig(
        version=version,
        asr=_parse("asr"),
        lexicon=_parse("lexicon"),
        rag=_parse("rag"),
    )
    if not config.asr:
        config.asr = _default_profiles().asr
    if not config.lexicon:
        config.lexicon = _default_profiles().lexicon
    if not config.rag:
        config.rag = _default_profiles().rag
    return config
