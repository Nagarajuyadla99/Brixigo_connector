"""Device capability profiles — configuration, not protocol forks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class DeviceProfile:
    """
    Tunables for ZK-family pull devices.

    Protocol bytes stay identical; profiles only adjust timeouts and
    preferred record layouts for known product families.
    """

    code: str
    label: str
    manufacturers: tuple[str, ...] = ()
    model_keywords: tuple[str, ...] = ()
    connect_timeout: float = 12.0
    read_timeout: float = 30.0
    user_record_size: int = 72  # 72=zk8+/TFT, 28=older BW
    attendance_record_sizes: tuple[int, ...] = (40, 16, 8)
    prefer_tcp: bool = True
    supports_face_templates: bool = False
    max_retries: int = 3
    retry_backoff_seconds: float = 0.4
    notes: str = ""


# Generic default — works for eSSL K90 / K90 Pro and most ZK pull terminals.
DEFAULT_PROFILE = DeviceProfile(
    code="zk_pull_default",
    label="ZK / eSSL Pull Protocol (default)",
    manufacturers=("essl", "zkteco", "zk", "bioface"),
    model_keywords=(),
    user_record_size=72,
    notes="Official ZK TCP framing + CMD_AUTH; suitable for K90-class and peers.",
)

PROFILES: tuple[DeviceProfile, ...] = (
    DeviceProfile(
        code="essl_k90",
        label="eSSL K90 / K90 Pro",
        manufacturers=("essl", "bioface"),
        model_keywords=("k90", "k 90"),
        user_record_size=72,
        attendance_record_sizes=(40, 16),
        notes="Color TFT pull devices; Comm Key auth via CMD_AUTH.",
    ),
    DeviceProfile(
        code="zk_f18",
        label="ZKTeco F18",
        manufacturers=("zkteco", "zk"),
        model_keywords=("f18",),
        user_record_size=72,
    ),
    DeviceProfile(
        code="essl_mb20",
        label="eSSL / ZK MB20",
        manufacturers=("essl", "zkteco", "zk"),
        model_keywords=("mb20", "mb 20"),
        user_record_size=72,
    ),
    DeviceProfile(
        code="uface",
        label="UFace / Face terminals",
        manufacturers=("essl", "zkteco", "zk"),
        model_keywords=("uface", "face"),
        supports_face_templates=True,
        read_timeout=45.0,
    ),
    DeviceProfile(
        code="x990",
        label="X990 family",
        manufacturers=("essl", "zkteco", "zk"),
        model_keywords=("x990", "x 990"),
        user_record_size=72,
    ),
    DeviceProfile(
        code="zk_bw_legacy",
        label="Legacy B&W ZK terminals",
        manufacturers=("zkteco", "zk", "essl"),
        model_keywords=("bw",),
        user_record_size=28,
        attendance_record_sizes=(16, 8, 40),
    ),
    DEFAULT_PROFILE,
)


def resolve_profile(
    *,
    manufacturer: str = "",
    model: str = "",
    device_name: str = "",
    platform: str = "",
) -> DeviceProfile:
    text = " ".join(
        part.lower() for part in (manufacturer, model, device_name, platform) if part
    )
    mfr = (manufacturer or "").lower().strip()
    for profile in PROFILES:
        if profile.code == DEFAULT_PROFILE.code:
            continue
        mfr_ok = not profile.manufacturers or mfr in profile.manufacturers or not mfr
        if profile.manufacturers and mfr and mfr not in profile.manufacturers:
            # Still allow keyword match across brands (rebranded units)
            mfr_ok = True
        keyword_hit = any(k in text for k in profile.model_keywords) if profile.model_keywords else False
        if keyword_hit and mfr_ok:
            return profile
    return DEFAULT_PROFILE


def list_profiles() -> list[dict]:
    return [
        {
            "code": p.code,
            "label": p.label,
            "manufacturers": list(p.manufacturers),
            "model_keywords": list(p.model_keywords),
            "supports_face_templates": p.supports_face_templates,
            "notes": p.notes,
        }
        for p in PROFILES
    ]
