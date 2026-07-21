"""시나리오 가격 산정의 보수성 파라미터 + 로딩.

설계 근거: docs/03-scenario.md §4.2, §4.3

이 모듈의 역할:
- `ScenarioPricingConfig` — 가격 산식(`pricing.py`)이 historical·peer 결합을
  어떻게 보수적으로 하는지 결정하는 파라미터 9개 (4 그룹). 모든
  `ExpectedReturn` 산출에 사용된 config 를 함께 저장 → 사후 sensitivity·회귀.
- `load_pricing_config` — 기본값 < Lambda 환경변수 < Step Functions 입력 JSON
  순으로 병합 (docs §4.3 변경 채널).

`pricing.py`(#3)·`schemas.py`(#2 ExpectedReturn 필드)가 이 모델을 import 하므로
구현 순서상 leaf — 가장 먼저 (docs §11 v0.9 의존성 교정, ExpectedReturn→config).

기본값은 *보수적 셋팅* — CHARTER §6 할루시네이션 리스크 보수.
순수 데이터/설정 — 네트워크/S3/LLM 호출 없음.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "ScenarioPricingConfig",
    "load_pricing_config",
    "alternative_configs",
]


class ScenarioPricingConfig(BaseModel):
    """가격 산식의 보수성 파라미터 (9 필드 / 4 그룹).

    docs §4.2. 기본값은 보수적 — bull 은 작은 상승, bear 는 작은 하락,
    base 는 현재가 이하로 cap.
    """

    # 1. Bull/Bear 가격 산정의 historical vs peer 결합 방식 (pricing.combine 모드)
    #    bear 의 'conservative' 는 *작은 하락* (= max) 을 의미 — is_bear 분기 (§4.1)
    bull_aggressiveness: Literal["conservative", "balanced", "aggressive"] = "conservative"
    bear_conservatism: Literal["conservative", "balanced", "aggressive"] = "conservative"

    # 2. Peer P/E percentile 폭 (순서: bear ≤ base ≤ bull — validator 강제)
    peer_pe_bull_percentile: float = Field(default=75.0, ge=50.0, le=95.0)
    peer_pe_base_percentile: float = Field(default=50.0, ge=40.0, le=60.0)
    peer_pe_bear_percentile: float = Field(default=25.0, ge=5.0, le=50.0)

    # 3. Base price cap (현재가 대비 fair value 상한)
    #    0.0: base ≤ 현재가 (보수) / None: cap 없음
    #    bound -0.5~1.0 — 무경계 latent bug 방지 (§4.2 v0.6)
    base_price_cap_pct: float | None = Field(default=0.0, ge=-0.5, le=1.0)

    # 3b. Bear price cap (현재가 대비 bear 시나리오 가격 상한 — §4.2 v0.16)
    #    딥밸류 종목에서 peer 함의 적정가 ≫ 현재가일 때 bear conservative=max 가
    #    bear 가격을 현재가 위로 올려 bear > bull 역전을 만드는 것 차단
    #    (07-14/07-20 운영에서 7/20 종목 재현 — retro §0.5). 0.0: bear ≤ 현재가.
    #    기본 None: 기존 동작 유지 — sensitivity 대안 "bear_capped" 로 A/B 관찰
    #    후 §12.3 에서 primary 승격 결정.
    bear_price_cap_pct: float | None = Field(default=None, ge=-0.5, le=1.0)

    # 4. LLM 확률 가중치 자체 보정 (마지막 가드 — bull/bear 대칭)
    #    None: LLM 출력 그대로 / 활성 시: 잉여를 나머지 원래 비율로 비례 분배 (§4.1)
    #    calibration 측정 전 *임시 가드* — M3 말 회고 시 재평가 (§12.3)
    bull_probability_cap: float | None = Field(default=None, ge=0.0, le=1.0)
    bear_probability_cap: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_percentile_order(self) -> Self:
        """config 레벨 가격 역전 사전 차단 (docs §4.2 v0.6).

        Field bound 가 겹쳐 (bear le=50, base ge=40) 순서 역전이 가능 →
        bear peer target > base peer target 같은 §4.1 가격 순서 위반의 근본 원인.
        """
        if not (
            self.peer_pe_bear_percentile
            <= self.peer_pe_base_percentile
            <= self.peer_pe_bull_percentile
        ):
            raise ValueError(
                f"percentile 순서 위반: bear={self.peer_pe_bear_percentile}, "
                f"base={self.peer_pe_base_percentile}, "
                f"bull={self.peer_pe_bull_percentile} — bear ≤ base ≤ bull 필요"
            )
        return self


# ---------- 로딩 (docs §4.3 변경 채널) ----------


def _parse_opt_float(raw: str) -> float | None:
    """환경변수 문자열 → float | None. 'none'/'null'/'' 는 None."""
    if raw.strip().lower() in {"none", "null", ""}:
        return None
    return float(raw)


# 환경변수 이름 → (필드, 파서). Literal 필드는 문자열 그대로 (Pydantic 이 검증).
_ENV_FIELDS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "SCENARIO_BULL_AGGRESSIVENESS": ("bull_aggressiveness", str),
    "SCENARIO_BEAR_CONSERVATISM": ("bear_conservatism", str),
    "SCENARIO_PEER_PE_BULL_PERCENTILE": ("peer_pe_bull_percentile", float),
    "SCENARIO_PEER_PE_BASE_PERCENTILE": ("peer_pe_base_percentile", float),
    "SCENARIO_PEER_PE_BEAR_PERCENTILE": ("peer_pe_bear_percentile", float),
    "SCENARIO_BASE_PRICE_CAP_PCT": ("base_price_cap_pct", _parse_opt_float),
    "SCENARIO_BEAR_PRICE_CAP_PCT": ("bear_price_cap_pct", _parse_opt_float),
    "SCENARIO_BULL_PROBABILITY_CAP": ("bull_probability_cap", _parse_opt_float),
    "SCENARIO_BEAR_PROBABILITY_CAP": ("bear_probability_cap", _parse_opt_float),
}


def load_pricing_config(
    env: Mapping[str, str] | None = None,
    override: Mapping[str, Any] | None = None,
) -> ScenarioPricingConfig:
    """기본값 < 환경변수 < 입력 JSON override 순으로 병합 (docs §4.3).

    - env: Lambda 환경변수 (`SCENARIO_*`). None 이면 `os.environ`.
      운영 중 즉시 override (예: 위기 시 보수화).
    - override: Step Functions 입력 JSON 의 `pricing_config_override` — 가장 우선
      (dry-run / 백테스트 일회성 변경).

    누락 키는 모델 기본값. 최종 검증(percentile 순서·bound)은
    `ScenarioPricingConfig.model_validate` 가 수행.
    """
    source = os.environ if env is None else env
    data: dict[str, Any] = {}
    for env_key, (field, parse) in _ENV_FIELDS.items():
        if env_key in source:
            data[field] = parse(source[env_key])
    if override:
        data.update(override)  # 입력 JSON 이 환경변수보다 우선
    return ScenarioPricingConfig.model_validate(data)


# ---------- Sensitivity 대안 config (docs §4.4, #12) ----------


def _variant(base: ScenarioPricingConfig, **changes: Any) -> ScenarioPricingConfig:
    """base 에 일부 필드만 바꾼 새 config (validator 재실행)."""
    return ScenarioPricingConfig.model_validate({**base.model_dump(), **changes})


def alternative_configs(
    base: ScenarioPricingConfig,
) -> dict[str, ScenarioPricingConfig]:
    """기본 config 대비 sensitivity 비교용 대안들 (docs §4.4, #12).

    같은 ScenarioOpinion 에 적용해 ExpectedReturnsBundle.alternatives 생성 —
    추가 LLM 비용 0. M3 운영 음수 skew(base_cap=0.0 부작용) 의 config 민감도를
    A/B 누적하기 위함. 4단계 설계 시 어느 config 가 적절한지 데이터로 판단.
    """
    return {
        # 결합을 산술평균으로 (min/max 대신) — bull 상승·bear 하락 모두 중도
        "balanced": _variant(
            base, bull_aggressiveness="balanced", bear_conservatism="balanced"
        ),
        # base fair value 를 현재가 +10% 까지 허용 (음수 skew 주원인 완화)
        "base_cap_10": _variant(base, base_price_cap_pct=0.10),
        # 상방 적극 + base cap 완화 (상단 비교군)
        "aggressive": _variant(
            base, bull_aggressiveness="aggressive", base_price_cap_pct=0.10
        ),
        # bear ≤ 현재가 강제 — bear>bull 가격 역전(딥밸류 peer 적정가 ≫ 현재가
        # + conservative=max 상호작용) 차단 후보. A/B 관찰 후 §12.3 에서
        # primary 승격 판단 (retro §0.5 07-20).
        "bear_capped": _variant(base, bear_price_cap_pct=0.0),
    }
