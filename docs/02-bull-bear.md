# 02. Bull/Bear 에이전트 설계

> **단계**: 2단계 — Bull/Bear 리서치 (LLM 핵심 사용 지점)
> **상위 문서**: [`CHARTER.md`](../CHARTER.md), [`CLAUDE.md`](../CLAUDE.md)
> **선행 문서**: [`docs/01-screening.md`](01-screening.md) — 본 단계 입력원, M1에서 운영 중
> **버전**: v0.7 (2026-04-30)
> **상태**: **M2 마일스톤 §9 #1~#9 모두 완료**. 다음 월요일 06:00 ET (CHARTER §2.4) 부터 EventBridge 자동 트리거로 운영 진입. 운영 모니터링 정책은 §11.
>
> **v0.7 변경 (M2 종료 + 운영 모니터링 정책)**:
> ① §9 #9 ✅ *완전* 완료 표기 — EventBridge 정기 트리거 연동 사용자 작업으로 완료, 다음 월요일부터 자동 운영,
> ② §11 신설 — 운영 모니터링 정책 (비용 / 실행 성공률 / turnover / 응답 품질 회귀 / retry·fallback 빈도) 임계값 표 박제,
> ③ 부록 A 디렉토리 구조 트리에 M2 *완전* 완료 + M3 진입 준비 표기,
> ④ docs 본문은 더 이상 "대기" 항목 없음 — M2 회고 (CHARTER §7) 이후 갱신은 v1.0 으로.
>
> **v0.6 변경 (2026-04-30 응답 품질 인간 검토 — 5 sub-sector sample)**:
> ① §10 "응답 품질 인간 검토" ✅ 추가 — 운영 ScreeningResult 5종목 (IVZ/NTRS/SPG/NEE/MU)
> bull/bear pair 검토. 5 hard rule 모두 통과, 자동 추천 어휘 grep 40건 0히트,
> ② sector 보강 효과 *최종 검증* — 6 sub-sector (Diversified Bank/Custody Bank/
> Investment Mgmt/REIT/Utility/Memory Semi) 모두에서 LLM 이 sector-specific 회계·
> 비즈니스 특성을 정확히 활용 (예: REIT 의 FFO 부재 자각, Utility 의 capex 타이밍
> 인지, Memory 의 cyclical commodity 분석). 추가 sector 별 프롬프트 분기 *불필요*
> 결론을 운영 데이터로 강력 재검증,
> ③ 운영 응답 품질이 골든 케이스와 일관됨 확인 — MU/NEE 의 cache miss (새 호출)
> 응답이 골든 4종목과 동등 품질, Sonnet primary 1차 성공의 운영 안정성 입증,
> ④ 결측 데이터 (EPS n/a) 일관 처리 — 호출 스킵 정책 불필요 (§10 미해결 항목 결론).
>
> **v0.5 변경 (2026-04-30 #8 ASL 확장 + 첫 20종목 dry-run 검증)**:
> ① §9 #8 ✅ Step Functions ASL 확장 — `BullBearMap` (Map MaxConcurrency=1 + Parallel Bull/Bear) + 종목별 Catch 격리 (`RecordItemFailure` Pass state),
> ② §9 #9 *부분* ✅ 20종목 첫 dry-run 통과 — 40 invoke, cache hit 35/40 (87.5%), 사다리 retry 0회, Haiku 폴백 0회, **총 비용 $0.083** (캐시 누적 효과로 추정 $0.5~0.7 의 1/7),
> ③ §10 "Anthropic rate limit 처방" ✅ 추가 — 1차 dry-run 에서 5종목이 사다리 3회 실패 (Sonnet primary/retry 모두 429 + Haiku schema 위반) → MaxConcurrency 5→1 결정 (8,000 tok/min 한도 산정 공식 §5.2.2 참조), 2차 실행 0건,
> ④ §10 "Haiku 폴백 schema 보강" ✅ 추가 — bull/bear system prompt 에 strict output schema 섹션(특히 `key_risks_to_thesis` plain string list, summary 200자 명시) 추가,
> ⑤ §5.2.2 추가 — Anthropic rate limit 산정 공식 (MaxConcurrency × 2 stance × max_tokens ≤ org tier 한도),
> ⑥ 부록 A 디렉토리 구조에 ASL `BullBearMap` 완료 표기.
>
> **v0.4 변경 (2026-04-30 #7 완료 + 운영 invoke 검증)**:
> ① §9 #7 ✅ 완료 — `lambda_core.py` (캐시 hit/miss 분기) + `agent_bullbear_{bull,bear}/handler.py`
> thin wrapper + FMP 분기 statement fetcher 추가 (`fetch_income/cashflow_quarterly_with_cache`),
> ② §10 "결정성 정책 운영 검증" ✅ 추가 — APA invoke 1차 (cache=miss, $0.039) →
> 2차 재호출 (cache=hit, $0, attempts=0) 으로 운영 레벨 100% 결정성 확인,
> ③ 부록 A 디렉토리 구조 — Lambda 두 개 + lambda_core 완료 표기,
> ④ deploy 인프라 (`scripts/deploy_lambda.sh`, `.github/workflows/deploy-lambdas.yml`)
> 가 `agents/` 패키징 + path 트리거 인식하도록 갱신 (부록 A 디렉토리 구조 참조).
>
> **v0.3 변경 (2026-04-30 골든 케이스 1차 실행 결과 반영)**:
> ① §5.2 비용 추정에 골든 실측 행 추가 (호출당 평균 $0.018, 추정과 정합),
> ② §9 #6 골든 케이스 ✅ 완료 표시 + 첫 실행 결과 ($0.17, 9 attempts, 1회 retry),
> ③ §10 "sector-specific 팩터 보강 효과 측정" ✅ JPM 골든으로 검증 완료 — 추가
> sector별 프롬프트 분기 *불필요*, ④ 운영 메모 추가: max_tokens 1024 → 2048
> 상향 (1차 실행 AAPL_bear 잘림 사례), ⑤ 부록 A 디렉토리 구조에 anthropic_adapter,
> 골든 스크립트, 스냅샷 디렉토리 반영.
>
> **v0.2 변경**: 1단계 스크리닝이 M1에서 운영 단계 진입(2026-04-25 첫 실행 — 483→20)함에 따라
> ① `StockContext`를 평탄화 구조로 확정, ② `ScreenedStock`이 사전 조립한 `peer_context`를
> 그대로 활용하도록 수정, ③ 밸류 출처를 `key-metrics-ttm` 단일 엔드포인트로 통일,
> ④ 기존 `screening_workflow.asl.json`을 Map state로 확장하는 방향으로 변경,
> ⑤ §10에 sector-specific 팩터 보강 효과·turnover 영향 평가 항목 추가.

---

## 0. TL;DR

스크리닝 통과 종목(상위 15~20개, [`ScreeningResult.selected`](../src/screening/schemas.py))에 대해 **Bull 에이전트**와 **Bear 에이전트**를 각각 1회 호출해 매수·매도 근거를 독립적으로 생성한다. 두 출력은 다음 단계(시나리오 모델링)에 입력으로 전달된다.

- 모델: **Sonnet 4.6** (Haiku 4.5 폴백)
- 호출량: 종목 15~20개 × 2 에이전트 × 주 1회 = **주 30~40회 / 월 ~120~160회**
- 예상 비용: **월 ~$5 미만** (peer_context 사전 조립으로 토큰 절감 — §5 참조. CHARTER §3.3 월 $200 예산 내, v2 Debate 실험 여력 큼)
- 출력: Pydantic 검증 JSON. 채택/기각 결정은 룰 기반 다음 단계가 수행 — 본 단계는 **근거 생성만**.

---

## 1. 목적과 범위

### 1.1 무엇을 하는가
1. **입력**: 스크리닝 통과 종목 1개 + 해당 종목의 펀더멘털·가격 컨텍스트
2. **처리**: Bull/Bear 두 관점의 독립 LLM 호출
3. **출력**: 각 관점에서의 핵심 논거 3~5개와 신뢰도, 출처 근거(reference)

### 1.2 비범위 (이 단계가 하지 않는 것)
- ❌ 매수/매도 결정 (룰 기반 5단계 리밸런서가 결정)
- ❌ 가격 타깃 산출 (3단계 시나리오 모델링)
- ❌ 포지션 사이징 (4단계 최적화)
- ❌ 외부 뉴스 실시간 검색 (MVP는 FMP에 들어있는 정형 데이터만)
- ❌ 멀티턴 자기검증/디베이트 (v2 고도화 — CHARTER §3.3의 "병렬 Debate 패턴"은 별도 실험 트랙)

### 1.3 CHARTER 정합성 체크
| 원칙 | 본 설계 적용 |
|---|---|
| 1순위 LLM 학습 | 단일 호출 패턴(Orchestrator)을 먼저 안정화 → v2에서 Debate/Self-Verify와 비교 |
| 2순위 산출물 | 모든 호출 입출력을 S3에 원본 보존 (사후 재현) |
| 비용 상한 월 $200 | 본 단계 예산 $25~$50 |
| LLM은 근거만, 매매는 룰 | Bull/Bear는 평문 + 구조화 논거만 반환, 점수는 룰 기반 후속 단계가 산출 |

---

## 2. 입출력 스키마

### 2.1 입력 (`StockContext`)

**평탄화 구조** — `ScreenedStock`을 그대로 임베드하지 않고, Bull/Bear가 보는 필드만 1-depth로 명시한다. 결정 근거: 스크리닝-Bull/Bear 결합도 ↓, 입력 토큰 효율 ↑, "LLM이 보는 데이터의 정확한 형태"를 한 타입에 박제 → 프롬프트 회귀 테스트 단순.

```python
class PriceSummary(BaseModel):
    return_1y: float | None         # 직전 252영업일 수익률
    return_6m: float | None
    pct_from_52w_high: float | None # (close - 52w_high) / 52w_high
    pct_from_52w_low: float | None
    beta_1y: float | None           # SPY 대비 1Y 회귀 베타

class FundamentalsTimeseries(BaseModel):
    quarters: list[QuarterlyFigures]   # 직전 4분기 (매출·EPS·FCF)
    revenue_cagr_5y: float | None
    eps_cagr_5y: float | None
    fcf_cagr_5y: float | None

class StockContext(BaseModel):
    # ── Identity (from ScreenedStock) ──
    symbol: str
    company_name: str | None
    sector: str | None
    sub_sector: str | None
    as_of_date: date

    # ── Screening signals (from ScreenedStock — LLM에 노출) ──
    composite_score: float          # rank 1이 최상위, 다른 종목과의 상대값
    momentum_z: float | None        # 같은 sub_sector 내 z-score (없으면 sector 폴백)
    value_z: float | None
    pe_ttm: float | None            # FMP key-metrics-ttm 의 1/earningsYieldTTM
    ev_ebitda: float | None         # evToEBITDATTM
    fcf_yield: float | None         # freeCashFlowYieldTTM (음수도 보존)

    # ── Peer context (from ScreenedStock — 사전 조립) ──
    peer_context: list[PeerComparable]   # sub_sector 우선 → sector 폴백, 최대 5

    # ── Time-series context (Bull/Bear context_builder가 캐시에서 조립) ──
    price_summary: PriceSummary
    fundamentals: FundamentalsTimeseries

    # ── Lineage (LLM 프롬프트로는 안 감, audit/재현용) ──
    run_id: str                     # ScreeningResult.run_id 그대로
    screening_s3_key: str           # screening/dt={...}/result.json 포인터
    data_quality_flags: list[str]   # ScreenedStock에서 그대로 (프롬프트엔 미포함)
```

#### 2.1.1 출처 매핑

| 영역 | 출처 | 조립 책임 | 비고 |
|---|---|---|---|
| Identity, screening signals, peer_context, lineage | [`ScreenedStock`](../src/screening/schemas.py) | 스크리닝 (M1 운영 중) | 매퍼 `screened_to_context()`가 1:1 평탄화 |
| `price_summary` | OHLCV 캐시 (`s3://{bucket}/ohlcv/ticker={SYM}/data.parquet`) | Bull/Bear `context_builder` | 본 단계에서 조립 |
| `fundamentals` | FMP `income-statement`/`cash-flow-statement` (분기) — 캐싱 계층 경유 | Bull/Bear `context_builder` | TTM 멀티플은 이미 `pe_ttm` 등으로 채워짐 → 분기 시계열만 추가 호출 |

**원칙**:
- FMP 직접 호출 금지 — [`common/fmp_client.py`](../src/common/fmp_client.py) 캐싱 계층 경유 (CLAUDE.md)
- `pe_ttm`/`ev_ebitda`/`fcf_yield`는 **재호출 금지** — `ScreenedStock.factors`에 이미 동일 시점 값 존재 (스크리닝과 동일 캐시 시점 보장 = `run_id` 일관성)
- 컨텍스트 토큰을 6K 이하로 묶는다 (Sonnet 입력 단가 절감). 표 데이터는 Markdown table로 직렬화

#### 2.1.2 LLM 프롬프트 노출 vs 미노출

평탄화 구조의 핵심은 **"LLM이 보는 것"과 "감사·재현용"의 분리**:

- **프롬프트로 들어감**: identity, screening signals, peer_context, price_summary, fundamentals
- **프롬프트로 안 감**: `run_id`, `screening_s3_key`, `data_quality_flags`
  - 이유: 이들은 LLM 추론에 가치 없고 토큰만 소모. `data_quality_flags`는 결측 정보를 LLM에 전달하면 "데이터 부족"을 핑계로 한 회피적 답변을 유도할 위험 → 시스템 프롬프트의 "데이터에 없는 추정 금지" 원칙과 충돌. 단, S3에 저장되는 `StockContext` JSON에는 보존되어 사후 분석에 활용.
- 직렬화 컨벤션: `context_builder`의 `to_prompt_markdown()` 메서드가 노출 필드만 화이트리스트로 골라 Markdown 변환. `model_dump()`는 전체 보존(S3 저장용).

### 2.2 출력 (`BullBearOpinion`)

Bull/Bear 공통 스키마. `stance` 필드로 구분.

```python
class Argument(BaseModel):
    claim: str            # 한 문장 핵심 주장
    evidence: str         # StockContext의 어느 수치/사실에서 도출됐는지
    confidence: Literal["low", "medium", "high"]

class BullBearOpinion(BaseModel):
    symbol: str
    stance: Literal["bull", "bear"]
    as_of_date: date
    summary: str                    # 200자 이내 요약
    arguments: list[Argument]       # 3~5개
    key_risks_to_thesis: list[str]  # 자기 입장에 대한 반증 시나리오 1~3개
    model: str                      # 호출 모델 ID
    input_tokens: int
    output_tokens: int
    cost_usd: float
```

**핵심 결정**: Bull도 자기 입장에 대한 반증(`key_risks_to_thesis`)을 강제 생성한다. 이는 단일 호출에서 단순 합리화 패턴을 줄이기 위한 프롬프트 장치 (Self-Critique 변형, v2 Debate의 다리 역할).

### 2.3 검증
- `arguments` 길이 3~5개 강제 (Pydantic `Field(min_length=3, max_length=5)`)
- JSON mode + Pydantic 파싱 실패 시 **1회만** 재시도 (재시도도 Sonnet, 두 번째 실패는 Haiku로 폴백)
- 폴백 호출도 동일 스키마 검증, 두 번째 실패 시 해당 종목은 시나리오 단계에서 제외하고 운영 로그에 기록

---

## 3. 프롬프트 설계

### 3.1 파일 배치 (CLAUDE.md 규칙)

```
src/agents/
├── bull_bear/
│   ├── __init__.py
│   ├── agent.py              # 호출 진입점 (Pydantic 검증·로깅·재시도)
│   ├── context_builder.py    # StockContext 조립
│   └── schemas.py            # 위 2.1, 2.2 모델
└── prompts/
    ├── bull_system.md        # 역할/제약
    ├── bear_system.md        # 역할/제약
    └── bullbear_user.md      # StockContext 렌더링 템플릿 (Bull/Bear 공용)
```

### 3.2 시스템 프롬프트 핵심 (Bull 예시 발췌)

```
당신은 한 종목에 대한 매수 측 리서치 애널리스트다.

[규칙]
- 주어진 데이터에서 도출 가능한 주장만 한다. 데이터에 없는 사실을 추정하지 않는다.
- 각 주장(claim)은 evidence 필드에서 입력 데이터의 구체 수치를 인용해야 한다.
- 자신의 입장에 대한 반증 시나리오를 1~3개 반드시 제시한다 (key_risks_to_thesis).
- 추천(Buy/Hold/Sell), 가격 타깃, 포지션 비중을 제안하지 않는다.
- 출력은 정의된 JSON 스키마만 반환한다.
```

Bear는 동일 구조에 입장만 반대.

**의도**:
- "데이터 없는 추정 금지" → 할루시네이션 억제 (CHARTER §6 리스크 1)
- "추천/타깃 금지" → 매매 결정 룰 기반 유지 (CHARTER §6 리스크 2)
- 반증 강제 → 단일 호출 단점(편향) 일부 보완

**스크리닝 시그널 명시 노출** (v0.2 추가):
- 사용자 프롬프트에 `composite_score` / `momentum_z` / `value_z`를 **명시**하되, "이 종목은 (예: 모멘텀 z=+1.8, 밸류 z=-0.4) 점수로 통과됨" 한 줄로 컨텍스트만 제공
- 의도: LLM이 "왜 이 종목이 통과됐는지"를 파악해 *반대 측면*도 같이 보게 함 (예: 모멘텀 강한 종목의 밸류 부담을 Bear가 짚도록)
- 단, 시스템 프롬프트에 "screening score는 통과 사유일 뿐 매수/매도 근거가 아니다 — 자체 데이터로 새로 추론할 것" 명시 → score 자체가 논거가 되지 않도록

### 3.3 사용자 프롬프트
`StockContext`를 Markdown 표 형태로 직렬화 + 마지막에 "이 데이터를 근거로 {bull|bear} 관점의 의견을 작성하라" 한 줄.

---

## 4. 오케스트레이션

### 4.1 호출 패턴 — 기존 워크플로우 확장

M1에서 [`infra/step_functions/screening_workflow.asl.json`](../infra/step_functions/screening_workflow.asl.json)이 이미 운영 중(EventBridge Mon 06:00 ET → `RunScreening` 단일 state). 본 단계는 **새 워크플로우를 만들지 않고** 기존 ASL에 Map state를 이어 붙인다 (해당 파일 Comment에도 "M2 에 Bull/Bear Map state 추가 예정" 명시).

```
EventBridge (Mon 06:00 ET)
        │
        ▼
[Step Functions — screening_workflow.asl.json 확장]
  ├─ RunScreening                     ← M1 운영 중
  │     │ ScreeningResult.selected[15~20]
  │     ▼
  ├─ BullBearMap (Map state, MaxConcurrency=5)   ← M2 추가
  │     for each ScreenedStock:
  │       ├─ BuildContext             (Lambda or inline Pass+Choice)
  │       ├─ Parallel:
  │       │   ├─ BullAgent  (Lambda)
  │       │   └─ BearAgent  (Lambda)
  │       └─ S3 write: agents/bullbear/dt={...}/symbol={SYM}/{bull|bear}.json
  │
  └─ (다음: 시나리오 → 최적화 → 리밸런서 — M3 이후)
```

- **Bull/Bear는 서로 독립** → 동일 종목 내에서도 Parallel state로 동시 호출
- **종목 간**도 Map state로 병렬 (`MaxConcurrency=5`로 Anthropic rate limit 보호)
- 한 종목의 한쪽이 실패해도 다른 종목/스탠스에 영향 없음 (`Catch` per-state)
- 1주 1회 배치이므로 Lambda 동시성 부담 없음

### 4.2 구성 요소 매핑 (CHARTER §3.4 기존 자산 재사용)
- **EventBridge**: 기존 트리거 그대로 — 신규 스케줄 없음
- **Step Functions**: `screening_workflow.asl.json`에 `BullBearMap` state 추가
- **IaC 방식**: Plain ASL JSON + [`scripts/deploy_step_functions.sh`](../scripts/deploy_step_functions.sh) + GitHub Actions 자동 배포 (CLAUDE.md 기술 스택). SAM/CDK 재검토는 워크플로우 복잡도 증가 시점(M3 이후)으로 이연
- **Lambda**: Bull/Bear는 별도 Lambda 함수 2개로 분리 (모델·프롬프트 격리, 비용 추적 단순)
  - `agent_bullbear_bull` / `agent_bullbear_bear`
  - `context_builder`는 별도 Lambda 또는 각 에이전트 Lambda 내부 모듈 — M2 초반 구현 시 결정
- **S3 레이아웃**:
  - `s3://{bucket}/agents/bullbear/dt={yyyy-mm-dd}/symbol={SYM}/stance={bull|bear}.json` — 출력
  - `s3://{bucket}/agents/bullbear/dt={yyyy-mm-dd}/symbol={SYM}/context.json` — 입력 `StockContext` 원본 (재현용)
- **Athena**: S3 출력에 외부 테이블 연결 → 의사결정 로그 사후 분석 (CHARTER 2순위)

---

## 5. 모델 선택과 비용

### 5.1 모델 정책 (CLAUDE.md 준수)
- **기본**: `claude-sonnet-4-6`
- **폴백**: `claude-haiku-4-5-20251001` (1차 검증 실패 시에만)
- **금지**: Opus 무근거 사용

### 5.2 토큰·비용 추정 (단일 호출)

`peer_context`가 스크리닝에서 사전 조립되어 오므로 입력 토큰이 v0.1 추정보다 작다.

| 항목 | 추정 (설계 시) | 실측 (2026-04-30 골든 9회) |
|---|---|---|
| 시스템 프롬프트 | ~600 tok | (input 합계에 포함) |
| StockContext (평탄화, peer_context 5개 포함) | ~2,500 tok | (input 합계에 포함) |
| 사용자 프롬프트 지시문 | ~150 tok | (input 합계에 포함) |
| 입력 합계 | ~3,250 | **평균 ~1,560** (1,497~1,599) |
| 출력 (JSON: arguments 3~5개 + risks) | ~600 | **평균 ~900** (713~1,024) |

실측이 추정과 양방향으로 다른데 비용은 거의 일치 — 입력이 추정보다 작아서 ($/1M 단가가 출력의 1/5) 입력·출력 차이가 상쇄.

Sonnet 4.6 가격 (2026-04 기준): 입력 $3/1M, 출력 $15/1M
- 호출당 비용 (실측 평균): 1,560 × $3/1M + 900 × $15/1M ≈ **$0.018**
- 골든 9 attempts 합계: $0.166 (호출당 평균 $0.0184, 추정과 정합)
- **단일 호출 $1 상한 (CLAUDE.md)** 대비 50배 이상 여유

#### 5.2.1 max_tokens 상한 (운영 메모 — 2026-04-30 갱신)

`AgentConfig.max_tokens` 기본값을 **1024 → 2048** 로 상향. 2026-04-30 골든 케이스 1차 실행에서 AAPL_bear 가 `output_tokens=1024` 정확히 hit 하며 응답이 잘림 → JSON 파싱 실패 → primary retry 로 복구되었으나 사다리 비용 발생. 출력 평균 ~900, 최대 1024 (잘림) 라 1024 는 too tight. 비용은 *실제* 사용량만 청구되므로 상한 확대로 인한 비용 증가 없음.

**검증 (2026-04-30 2차 실행)**: 동일 fixture 재호출 → AAPL_bear 가 `output_tokens=1025` 로 자연 종료 (LLM 자체 stop, 잘림 없음). 1024 hit 이 우연이 아니라 정확히 한계 잘림이었다는 결정적 증거 — 2048 상한에서 1025 까지만 사용. 8회 모두 1차 성공 (retry 0회), 총 비용 $0.166 → **$0.144 (~13% 절감)**. 평균 토큰: 입력 1,551 / 출력 894 / 호출당 $0.0181 — 본 표 추정과 정합.

#### 5.2.2 Anthropic rate limit 산정 공식 (운영 메모 — 2026-04-30 갱신)

Anthropic 은 호출 시점에 `max_tokens` 값을 *예약* 해서 분당 한도(output tokens/min) 에 가산. 동시 호출 burst 시 일순간에 한도 초과 → 429.

**공식**: `Step Functions Map MaxConcurrency × Bull/Bear Parallel(2) × max_tokens ≤ 조직 분당 한도`

본 단계 운영 (Anthropic Tier 1 가정, 8,000 output tokens/min):
- `MaxConcurrency=1` × 2 × 2,048 = **4,096** ≤ 8,000 ✓ (현재 정책)
- `MaxConcurrency=2` × 2 × 2,048 = 8,192 ≥ 8,000 ✗ (worst case 살짝 초과)
- `MaxConcurrency=3` × 2 × 1,024 = 6,144 ≤ 8,000 ✓ — 단 1,024 는 docs §5.2.1 잘림 위험

**한도 상향 시 점진적 증가** (Anthropic Console 신청 시): 한도 N tok/min → `MaxConcurrency = floor(N / (2 × max_tokens))`. 예: 16,000 → 3, 24,000 → 5.

처리 시간 영향 (`MaxConcurrency=1`): 20종목 직렬 × 한 종목당 ~5초 (Bull/Bear Parallel 이라 max latency) = **~100~150초**. 주 1회 배치라 견딤. 한도 상향 후 동시성 ↑ 시 처리 시간 ↓.

**검증 (2026-04-30 1차 vs 2차 dry-run)**:
- 1차 (MaxConcurrency=5): 5종목 (VLO/NEE/DAL/...)이 Sonnet primary+retry 모두 429 → Haiku 폴백 → schema 위반 → BullBearAgentError → Step Functions Map 전체 중단
- 2차 (MaxConcurrency=1 + Haiku schema 보강): 20종목 모두 1차 성공, 사다리 retry 0회, 한 종목 실패도 ASL Catch 가 격리할 수 있는 안전망 보유

### 5.3 월 비용 추정

| 시나리오 | 종목 | 주간 호출 | 월 호출 | 월 비용 |
|---|---|---|---|---|
| MVP 기본 (15종목) | 15 | 30 | 120 | ~$2.3 |
| 상한 (20종목, 첫 운영 실측) | 20 | 40 | 160 | ~$3.0 |
| 재시도 +20% 가정 | 20 | 48 | 192 | ~$3.6 |

**여유분이 큰 이유**:
1. `peer_context` 사전 조립으로 같은 시점에 같은 데이터 다시 수집·직렬화하지 않음
2. 2단계 자체보다 3단계 시나리오 모델링 비용이 더 무거울 것으로 예상 — CHARTER §3.3의 "월 $30~$80" 추정의 대부분은 3단계 몫

본 단계는 **월 $5 미만**으로 운영 가능 → v2에서 Debate 패턴(에이전트 수 5~10배) 실험 여력 확보.

### 5.4 비용 가드레일
- 단일 호출이 입력 6K tok 초과 시 **컨텍스트 빌더 단계에서 컷** (예외 발생, 호출 안 함)
- 월 누적 비용이 $50 초과 시 CloudWatch 알람 → 수동 검토

---

## 6. 로깅과 관측

CLAUDE.md "모든 LLM 호출은 다음을 로깅" 규칙 준수.

### 6.1 호출 로그 (CloudWatch + S3)
호출당 1행:
```json
{
  "timestamp": "2026-05-04T10:00:00Z",
  "model": "claude-sonnet-4-6",
  "purpose": "bullbear.bull",
  "symbol": "AAPL",
  "input_tokens": 4123,
  "output_tokens": 587,
  "cost_usd": 0.0211,
  "latency_ms": 4210,
  "retry_count": 0,
  "schema_valid": true
}
```

### 6.2 결과 보존
- 입력 컨텍스트 원본도 S3에 저장 (`agents/bullbear/dt=.../symbol=.../input.json`)
- 출력 JSON 별도 저장
- → **사후 재현 가능성** (CHARTER §4.1 실전 전환 기준 항목)

### 6.3 주간 리포트
월요일 실행 종료 후 자동 생성:
- 총 호출 수, 비용, 폴백/실패 건수
- Bull/Bear 의견의 `confidence` 분포
- 종목별 Bull-Bear 주장 수 비대칭 (양쪽 다 빈약하면 데이터 부족 시그널)

---

## 7. 실패·예외 처리

| 케이스 | 처리 |
|---|---|
| Anthropic API 5xx/타임아웃 | 지수 백오프 2회 (1s, 4s) |
| Pydantic 검증 실패 | Sonnet 1회 재시도 → 실패 시 Haiku로 1회 → 그래도 실패 시 해당 (symbol, stance) 누락 처리 |
| Rate limit | Step Functions Map의 MaxConcurrency로 사전 방어 + 429 시 재시도 큐 |
| 컨텍스트 6K tok 초과 | 호출 안 함, 운영 로그에 기록, 다음 단계는 해당 종목 스킵 |
| 한 종목의 Bull만 성공 / Bear 실패 | 시나리오 단계에 한쪽만 전달 (시나리오 단계에서 비대칭 처리) |
| 월 비용 알람 발동 | 수동 개입까지 다음 주간 실행 일시 중단 (Lambda env flag) |

---

## 8. 테스트 전략 (CLAUDE.md 준수)

### 8.1 단위 테스트
- `context_builder`: FMP 캐시 응답 픽스처 → `StockContext` 조립 (순수 함수, LLM 호출 X)
- `schemas`: Pydantic 검증 케이스 (인자 개수, 필드 누락, JSON 파싱 실패)
- 비용 계산 함수: 토큰 → 달러 환산

### 8.2 LLM 호출 모킹
- Anthropic SDK 클라이언트를 주입식으로 받는 구조 (`Protocol`)
- 테스트에서는 `FakeAnthropicClient`가 사전 정의된 JSON 반환
- 정상/검증실패/5xx 시나리오 모두 픽스처화

### 8.3 골든 케이스 테스트 (수동)
- 잘 알려진 종목 3개(예: AAPL, XOM, NVDA)로 실제 Sonnet 호출 → 출력 스냅샷을 인간이 검토 → `tests/golden/bullbear/{symbol}.json`로 보존
- CI에서는 실행하지 않음 (`pytest -m golden`로 분리)

### 8.4 통합 테스트
- `moto`로 S3 모킹, 가짜 LLM 클라이언트 주입, Step Functions 로컬 실행
- 한 사이클(스크리닝 → Bull/Bear → S3 저장)이 깨지지 않는지

---

## 9. 구현 순서 (M2 마일스톤)

> M1 종료 (스크리닝 운영 진입) 다음 단계. 각 단계는 별도 커밋. LLM 호출 추가 커밋은 메시지에 비용 추정 명시 (CLAUDE.md 커밋 컨벤션).

1. **schemas.py** — `StockContext`(평탄), `PriceSummary`, `FundamentalsTimeseries`, `QuarterlyFigures`, `Argument`, `BullBearOpinion` Pydantic 모델 + 단위 테스트
2. **screened_to_context 매퍼** — `ScreenedStock` → `StockContext` 평탄화 함수 (1:1 필드 매핑, 순수 함수). 단위 테스트로 매핑 누락 가드 (Pydantic 필드 변경이 silent 불일치를 만들지 않도록)
3. **context_builder.py** — `screened_to_context` + OHLCV/분기 펀더멘털 캐시 조립. `to_prompt_markdown()` 화이트리스트 직렬화. LLM 호출 없음 → `FakeFMPClient`로 테스트
4. **프롬프트 파일 3종** — `bull_system.md`, `bear_system.md`, `bullbear_user.md` (composite_score/momentum_z/value_z 명시 포함)
5. **agent.py 골격** — Anthropic SDK 호출, JSON mode, Pydantic 검증, 재시도/폴백, 로깅 (`FakeAnthropicClient`로 단위 테스트)
6. **golden 케이스 4개** ✅ **완료 (2026-04-30)** — AAPL/XOM/NVDA + JPM (금융 sector 추가) 실제 호출, 비용 $0.166 (9 attempts, 1회 retry — `output_tokens=1024` 잘림 → max_tokens 1024→2048 갱신 §5.2.1). false-positive 정정 후 회귀 가드 48 검증 통과. 결과: [`tests/golden/bullbear/`](../tests/golden/bullbear/), 실행 스크립트 [`scripts/run_bullbear_golden.py`](../scripts/run_bullbear_golden.py). 인간 검토 결과는 §10 sector-specific 항목 참조
7. **Lambda 핸들러 + S3 캐싱** ✅ **완료 (2026-04-30)** — 분할 진행: (#7-A) FMP 분기 statement 메서드 + cache-aside (`fetch_income/cashflow_quarterly_with_cache`), (#7-B) [`agents/bull_bear/lambda_core.py`](../src/agents/bull_bear/lambda_core.py) (입력 파싱 → OHLCV/statements 로드 → `build_context` → `context_input_hash` → 캐시 hit/miss 분기 → S3 저장) + thin wrapper [`agent_bullbear_bull/handler.py`](../src/lambdas/agent_bullbear_bull/handler.py), [`agent_bullbear_bear/handler.py`](../src/lambdas/agent_bullbear_bear/handler.py), (#7-C) 12개 단위 테스트 (cache miss/hit/stale, env/event 검증, wrapper 라우팅). 운영 invoke 검증: APA bull+bear 1차 cache=miss $0.039 → 2차 cache=hit $0 (§10 결정성 정책 운영 검증 항목 참조). 배포 인프라 갱신: `agents/` 패키징 + GitHub Actions path 트리거.
8. **Step Functions ASL 확장** ✅ **완료 (2026-04-30)** — 기존 [`screening_workflow.asl.json`](../infra/step_functions/screening_workflow.asl.json) 에 `BullBearMap` 추가 (`MaxConcurrency: 1` — §5.2.2 rate limit 산정 공식 근거), `ItemSelector` 로 ScreenedStock + lineage 4필드 매핑, `ItemProcessor` 안에 `BullBearParallel` (Bull/Bear 동시 호출). 한 종목 실패 격리를 위해 `BullBearParallel.Catch[States.ALL]` → `RecordItemFailure` Pass state 추가. `deploy_step_functions.sh` 가 placeholder 3개(`<<RUN_SCREENING_LAMBDA>>`, `<<BULL_LAMBDA>>`, `<<BEAR_LAMBDA>>`) 치환. 1차 dry-run 후 rate limit + Haiku schema 위반 발견 → 2차에서 정정 검증 (#9 참조).
9. **20종목 주간 배치 첫 실행 + EventBridge 자동 트리거** ✅ **완전 완료 (2026-04-30)**. M2 종료.
   - **첫 dry-run** (2026-04-30): 40 invoke (Bull+Bear × 20), cache hit 35 (87.5%) / cache miss 5 (VLO/DAL/NEE), 사다리 retry 0회, Haiku 폴백 0회, 총 비용 $0.083, 처리 시간 ~100초, Catch 격리 발동 0건
   - **응답 품질 인간 검토** (§10 [x] 항목): 5 sub-sector sample (IVZ/NTRS/SPG/NEE/MU) 통과, sector 보강 효과 6 sub-sector 최종 검증
   - **EventBridge 정기 트리거 연동**: Step Functions state machine `portfolio-mvp-screening` 에 사용자 작업으로 매주 월 06:00 ET cron 연결됨 — **다음 월요일부터 자동 운영**
   - 흥미 신호: NEE 가 bull miss / bear hit 비대칭 — 캐시 키가 stance 별 분리된 덕분에 한쪽만 재호출 (의도대로)
   - 운영 진입 후 모니터링은 **§11 운영 모니터링 정책** 참조

---

## 10. 미해결 / 다음 결정 필요

- [ ] FMP statements 캐시 TTL이 현재 90일 — Bull/Bear `fundamentals` 시계열은 분기 발표 직후 신선도가 중요. **이벤트 기반 캐시 무효화** 도입 여부 (분기 발표 시즌 첫 주만 강제 갱신)
- [ ] `key_risks_to_thesis`의 출력이 실제로 단일 호출 편향 보완 효과가 있는지 — M2 골든 케이스 3건으로 1차 평가, 첫 주간 실행으로 2차 평가
- [ ] v2 Debate 패턴 실험 시점 — 본 단계 단일 호출 패턴이 4주 안정 운영 후 도입 (M3 후반 후보)
- [ ] 의견 출력의 한국어 vs 영어 — 일관성 차원에서 영어가 무난하지만 산출물(블로그) 관점에서 한국어 이점 존재 → 골든 케이스 3건에서 양쪽 비교 후 결정
- [x] ~~**Sector-specific 팩터 보강 효과 측정**~~ ✅ **2026-04-30 골든 케이스 (JPM) 검증 완료**. ([`docs/01-screening.md` §10](01-screening.md#10-미해결--다음-결정-필요)에서 본 단계로 위임된 항목 — 금융 sector EV/EBITDA·FCF Yield 의 구조적 왜곡). 결과: JPM 골든 입력에 `EV/EBITDA=32.00` (피어 28~35의 구조적 왜곡), `FCF Yield=-20.00%` (모든 피어 음수) 명시했음에도 **Bull/Bear 모두 두 멀티플을 evidence 에 0회 인용**. P/E (13.0x vs 피어 10.0~14.5x), 분기별 매출/EPS 추세, 5Y CAGR (revenue +6%, EPS +10%) 로만 reasoning. 시스템 프롬프트의 `Sector context: standard multiples ... structurally distorted` 섹션이 효과적으로 작동. **결론**: 추가 sector 별 프롬프트 분기 또는 sector 가중치 조정 *불필요*. 단일 시스템 프롬프트로 충분. 골든 스냅샷 [`tests/golden/bullbear/JPM_{bull,bear}.json`](../tests/golden/bullbear/) 참조.
- [ ] **종목 풀 turnover의 호출량 영향**: 01-screening §10 "선정 종목 안정성" 평가가 4주 운영 후 진행됨. turnover 높으면 Bull/Bear는 매주 신규 종목에 대해 1회차 의견을 내야 함 → 캐시 적중률 0% 가정. turnover 측정 결과에 따라 (a) 의견 캐싱(2주 TTL) 도입 또는 (b) 스크리닝에 hysteresis 도입 중 선택. 본 단계는 turnover 데이터를 받아 결정만 반영
- [ ] `data_quality_flags`를 프롬프트에 미노출하기로 결정(§2.1.2)했으나, 결측이 너무 많은 종목은 *호출 자체를 스킵*해야 할 수도 있음 — 첫 주간 실행에서 결측 분포 본 후 임계값 결정
- [ ] **DeepEval judge 의 cross-vendor 다양성** (§11.5 baseline 의 self-preference bias 검증) — 현재 평가 setup 은 Bull/Bear 본 호출과 judge 가 모두 Sonnet 4.6 (동일 family). Zheng et al. *"Judging LLM-as-a-Judge"* (NeurIPS 2023) 등 후속 연구에서 동일 family judge 가 자기 출력을 체계적으로 후하게 평가하는 **self-preference bias** 가 반복 확인됨. baseline (§11.5) 의 0.7~1.0 점수 분포에 이 bias 가 얼마나 반영됐는지 미측정.
  - **검증 시점**: M3 진입 후 운영 데이터 4주 누적 시점 1회 cross-validation. GPT-4o (또는 Gemini) judge 로 동일 골든 8건 × 3 criteria 재평가 → §11.5 baseline 대비 점수 변동 분포 측정. 비용 ~$0.4 (일회성).
  - **판단 기준**:
    - ±0.05 이내 → bias 미미, Sonnet judge 단독 유지 (Pattern A 유지)
    - ±0.15 이상 → criteria 문구 또는 시스템 프롬프트가 vendor 의존적이라는 신호 → 양쪽 judge 병행 검토 (Pattern B: 변경 시점 이중 검증) 또는 앙상블 (Pattern C: 매 평가 다중 judge)
    - 특정 criterion 만 크게 벌어지면 → 해당 criteria 문구 재검토 (vendor 의존성 신호)
  - **운영 비용**:
    - Pattern A (분기 1회 cross-check): +~$1.5/년
    - Pattern B (변경 시점 이중): 평소 비용 동일, 변경 시 +100%
    - Pattern C (매회 앙상블 3 judge): +200% (Sonnet 단독 대비)
  - **추가 복잡도**: API 키 1→2개 (CHARTER §2.2 비밀 관리 표면 ↑), 청구·rate limit 별도. DeepEval 은 OpenAI native 지원이라 코드 변경 작음 (~50 LOC, 한나절).
  - **Bedrock 추론 전환과의 긴장**: Bedrock 은 GPT/Gemini 미호스팅. 추론을 Bedrock 으로 옮기면서 cross-vendor judge 도 도입하면 **"추론은 Bedrock, judge 는 OpenAI/Google 직접 호출"** 의 혼합 구조 — vendor 단순화 의도와 충돌. M3 에서 두 결정 통합 검토 필요.
  - **현 단계 권장**: 본 단계에서는 결정 보류 (PoC 효과 vs 운영 비용 trade-off 가 4주 운영 데이터 없이는 평가 불가). M3 entry 와 동기.
- [x] ~~**max_tokens 1024 의 적절성**~~ ✅ **2026-04-30 골든 1차 실행 후 2048 로 상향**. AAPL_bear 가 정확히 1024 hit 하며 잘림 → §5.2.1 참조. 출력 평균 ~900, 최대 1024 (잘림) → 1024 too tight. 비용 영향 없음 (실제 사용량 청구).
- [x] ~~**추천 어휘 자동 가드 정규식**~~ ✅ **2026-04-30 정밀화 완료**. 1차 실행에서 NVDA_bull 의 `key_risks_to_thesis` "NVDA's ability to **sell** advanced AI chips" 가 false-positive (LLM 추천이 아니라 회사의 비즈니스 동사). `\b(buy|sell|hold)\b` 단독 매치를 제거하고 추천 *컨텍스트* 명시 표현(target price, outperform, recommend buy, rate as sell, rating: hold 등) 만 매치하도록 좁힘. 골든 회귀 가드는 [`tests/test_bullbear_golden.py`](../src/tests/test_bullbear_golden.py) `RECOMMENDATION_WORDS`.
- [x] ~~**Anthropic rate limit 처방**~~ ✅ **2026-04-30 1차 dry-run 결과 후 적용**. 1차 (MaxConcurrency=5) 에서 VLO/NEE/DAL 등 5종목이 Sonnet primary+retry 모두 429 (`This request would exceed your organization's rate limit of 8,000 output tokens per minute`) → Haiku 폴백 → schema 위반 → `BullBearAgentError` → Map 전체 중단. **원인**: Anthropic 이 호출 시점에 `max_tokens=2048` 을 *예약* 해 한도 산정. 동시 = MaxConcurrency × 2 stance × 2048 = 5×2×2048 = **20,480 tokens 예약** > 8,000. **처방**: ASL `MaxConcurrency: 5 → 1` (§5.2.2 산정 공식). **검증 (2차 dry-run)**: 20종목 모두 1차 성공, retry 0회. 한도 상향 시 점진적 증가 가능.
- [x] ~~**Haiku 폴백 schema 보강**~~ ✅ **2026-04-30 system prompt 강화 완료**. 1차 dry-run 에서 Haiku 4.5 가 폴백 호출됐을 때 응답 형식 위반: (a) `key_risks_to_thesis` 가 `list[dict]` (예: `{"risk": "...", "likelihood": "medium"}`) 로 반환 — schema 는 `list[str]`, (b) `summary` 200자 초과. **처방**: [`bull_system.md`](../src/agents/prompts/bull_system.md) / [`bear_system.md`](../src/agents/prompts/bear_system.md) 에 "Output schema (strict)" 섹션 추가 — 명시적 JSON 예시 + 4개 critical 룰 (특히 plain string list, 200자 한도). 2차 dry-run 에서는 Haiku 폴백이 아예 호출 안 됨 (rate limit 처방 효과). 실제 검증은 다음 Haiku 호출 시 (드물 것).
- [x] ~~**응답 품질 인간 검토 (5 sub-sector sample)**~~ ✅ **2026-04-30 완료 — sector 보강 효과 운영 *재검증***. 첫 dry-run 의 selected 20종목 중 sector 다양성 위주로 5종목 (IVZ/NTRS/SPG/NEE/MU) bull/bear pair 검토 (총 10 의견).

  **자동 가드** (test_bullbear_golden 의 RECOMMENDATION_WORDS 정규식): 40 운영 의견 전수 0히트. schema 검증 100% 통과 (Pydantic 호출 측에서 이미 강제됐지만 사후 가드 OK).

  **인간 검토 5 hard rule 결과** (시스템 프롬프트 기준):
  | # | Rule | 5종목 통과율 |
  |---|---|---|
  | 1 | Evidence-bound (입력 수치 인용) | 10/10 |
  | 2 | No recommendations | 10/10 (자동 가드와 일치) |
  | 3 | Self-critique (자기 입장 반증, specific) | 10/10 |
  | 4 | Screening signals 메타 점수 재사용 안 함 | 10/10 |
  | 5 | Sector 적합성 | 10/10 (아래 sub-sector 분석 참조) |

  **Sector 보강 효과 — 6 sub-sector 최종 검증** (JPM 골든 + 5 운영):

  | Sub-sector | LLM 의 sector context 인식 | 검증 sample |
  |---|---|---|
  | Diversified Bank | EV/EBITDA·FCF Yield 구조적 왜곡 회피 | JPM (골든) |
  | Custody Bank | Banks 와 동일 회피 패턴 (commercial bank consistency) | NTRS (운영) |
  | Investment Mgmt | 자산운용은 멀티플 정상 → 정상 활용 | IVZ (운영) |
  | REIT | EV/EBITDA·P/E 활용 + **FFO 부재를 risk 로 자각** | SPG (운영) |
  | Utility | capex 타이밍·seasonal FCF 음수 가능성 인지 | NEE (운영) |
  | Memory Semi | 사이클 변동성 + HBM structural vs cyclical 구분 | MU (운영) |

  → **결론**: 6 sub-sector 모두에서 LLM 이 sector-specific 회계·비즈니스 특성을 *과도한 일반화 없이* 정확히 활용. 시스템 프롬프트의 단일 `Sector context` 섹션이 도메인 지식과 결합해 충분. *추가 sector 별 프롬프트 분기 또는 schema 분리 불필요* — 본 항목 [§10 sector-specific] 결론이 *극도로 robust* 하게 재검증됨.

  **인상적 케이스**:
  - **SPG (REIT)**: Bear 의 risks 에 "P/E discount may reflect REIT accounting conventions (depreciation, FFO vs. GAAP EPS) rather than genuine fundamental weakness" 명시 — 입력에 없는 industry-standard 지표 (FFO) 를 *없다고 인지* 하고 self-critique 으로 노출. prompt 가 강제 안 한, LLM 도메인 지식 + self-critique 정책의 결합 효과
  - **NEE (Utility)**: Bear risks 에 "utilities often have negative FCF in winter/spring due to capex timing and working capital" 명시 — Q1 -$580M FCF 가 sector seasonal 이라는 가능성을 *bear thesis 무너뜨리는 risk* 로 정직 노출
  - **MU (Memory)**: $9.30B → $23.86B 매출 *3배* 성장에도 Bear 가 "$23.86B 한 분기 ≈ 이전 두 분기 합" 으로 피크 base 함정을 정확히 지적. Self-critique 으로 "HBM 이 structural step-change 인지 cyclical peak 인지 불확실" 명시 → 자기 입장도 자각
  - **IVZ (Investment Mgmt)**: 같은 데이터로 Bull (4Q 가속 + FCF 5Y CAGR 8x revenue) vs Bear (5Y revenue CAGR 1.21% + Q1 FCF 64% 급락) — *시간 frame 차이로 정반대 결론*

  **결측 데이터 일관 처리** (§10 별도 항목 참조): IVZ/NTRS/NEE 모두 EPS n/a. 5종목 모두 결측을 *추정 안 하고* evidence 또는 risk 로 명시적 노출 — *호출 자체 스킵* 정책은 *불필요* 결론.

  **운영 응답 품질이 골든 케이스와 일관**: MU/NEE 는 cache miss (새 호출), Bull/Bear 둘 다 골든 4종목 (AAPL/XOM/NVDA/JPM) 과 동등 품질. Sonnet primary 1차 성공의 운영 안정성 입증.

  **결론**: EventBridge 정기 트리거 활성화 (§9 #9 마무리) 준비 완료.

- [x] ~~**결정성 정책 운영 검증**~~ ✅ **2026-04-30 APA 운영 invoke 로 확인**. 사용자 우려였던 "동일 질의 동일 답변" 의 **운영 레벨 보장** 검증. 운영 ScreeningResult `selected[0]` (APA, Energy/Oil & Gas E&P) 페이로드로 두 람다(`agent_bullbear_{bull,bear}`) 각 2회 invoke:

  | 회차 | bull `cache` | bull `cost_usd` | bear `cache` | bear `cost_usd` | bull/bear `input_hash` |
  |---|---|---|---|---|---|
  | 1차 (cold) | miss | 0.020877 | miss | 0.017721 | `46ae5ef8…` (동일) |
  | 2차 (재호출) | **hit** | **0** | **hit** | **0** | `46ae5ef8…` (동일) |

  검증 결과: (a) `context_input_hash` 가 stance 무관하게 결정적 (bull/bear 동일 hash), (b) 캐시 키가 stance 별로 분리되어 충돌 없음, (c) 동일 input_hash 재호출 시 LLM 호출 0회 / `attempts=0` / `cost_usd=0` / 저장본 그대로 반환 → **운영 레벨 100% 결정성**. 의도치 않은 재호출 (Lambda retry, Step Functions 재실행, 디버깅 invoke) 에서 비용 폭주 0 보장.

---

## 11. 운영 모니터링 정책

M2 마일스톤 종료 후 자동 운영 진입 (다음 월요일 06:00 ET) — 본 절은 운영 진입 후 모니터링·임계값을 박제한다. CHARTER §6 (리스크 & 완화) / §7 (체크포인트) / §4.1 (실전 전환 기준) 의 본 단계 구체화.

### 11.1 모니터링 항목 표

| 항목 | 측정 방법 | 임계값·조치 | 출처 |
|---|---|---|---|
| **월 LLM 비용** | AWS Cost Explorer (모델별 분리) + Anthropic Console usage | **$200 hard cap** — 초과 시 Lambda 자동 중단 (CHARTER §6 명시 정책). 사전 알람: $50/$100/$150/$180 단계 (§11.3 권장 후속) | CHARTER §2.2 |
| **주간 실행 성공률** | Step Functions execution history (콘솔 또는 `aws stepfunctions list-executions`) | **3개월 연속 ≥90%** = 실전 전환 자격. 미달 시 페이퍼 유지 | CHARTER §4.1 |
| **종목 turnover** | 매주 `s3://{bucket}/agents/bullbear/dt=*/` 의 selected 비교 (이전 주 대비 added/removed) | **4주 누적 후 평가** — turnover 너무 높으면 (a) 의견 캐싱 2주 TTL 또는 (b) 스크리닝 hysteresis 도입 | docs §10, 01-screening.md §10 |
| **응답 품질 회귀** | **DeepEval G-Eval 자동 평가** (`scripts/run_bullbear_deepeval.py`, judge = Sonnet 4.6, 기본 3 criteria — §11.5) + 분기별 인간 sector sample 검토 (1~2 종목 신규/변경 sub_sector 우선) | criterion fail (= §11.5 baseline threshold 미만) → judge reasoning 검토 후 system prompt 강화. 추천 어휘 / schema 위반은 기존 정규식 가드·Pydantic 이 결정적으로 차단 | docs §10 [x] 응답 품질 항목, **§11.5** |
| **사다리 retry / fallback 빈도** | CloudWatch Logs Insights 쿼리: `fields @timestamp, stage, error \| filter stage in ['primary_retry', 'fallback']` | **5% 이상이면** rate limit 한도 상향 신청 또는 `max_tokens` 조정 (§5.2.2 산정 공식) | docs §5.2.2 |
| **결정성 정책** | 같은 dt 내 재실행 시 cache hit 비율 | 동일 input_hash 재호출 시 100% cache hit 유지 (LLM 호출 0회) | docs §10 [x] 결정성 항목 |
| **CHARTER §6 리스크 — 할루시네이션** | 매매 결정이 LLM 출력에 *직접* 의존하는지 점검 | 매매는 룰 기반, LLM 은 근거 생성만 — 본 단계 출력은 시나리오 모델링(M3)/리밸런서(M4) 의 *입력 컨텍스트* 로만 사용. 위반 발견 시 다운스트림 단계 재설계 | CHARTER §6 |

### 11.2 회고 시점

| 시점 | 이벤트 | 본 단계 입력 |
|---|---|---|
| **매주 월 ~07:30 ET** | Step Functions execution 종료 직후 결과 검토 | 비용·실행 성공·실패 종목 |
| **4주 누적** | turnover 평가 및 hysteresis 의사결정 | docs §10 turnover 항목 |
| **CHARTER M2 말** (M2 → M3 전환) | 페이퍼 트레이딩 안정성 + 비용 + tracking error 종합 | CHARTER §7 체크포인트 |
| **CHARTER M3 말** | 실전 전환 vs 페이퍼 유지 결정 | CHARTER §4.1 4개 기준 |

### 11.3 권장 후속 (선택, 우선순위 순)

운영 안정성 향상을 위해 도입 가치 있는 항목들. 미도입 상태에서도 운영은 가능하지만 *이상 감지가 늦음*:

1. **CloudWatch 비용 알람** — SNS topic 으로 $50/$100/$150/$180 단계별. CHARTER §6 hard cap 이 *수동 대응* 기준이라 자동 알람으로 사전 인지 필요
2. **주간 실행 후 자동 리포트** — Step Functions 종료 시 SNS 발송. 처리 종목·cache 분포·실패 종목·총 비용·신규 vs 이전 주 selected (turnover 신호)
3. **Athena 외부 테이블** — `s3://{bucket}/agents/bullbear/dt=*/symbol=*/stance=*.json` 에 외부 테이블 연결. SQL 로 시계열 분석 가능 (예: 종목별 confidence 추이, sector 별 평균 cost_usd)
4. **운영 검증용 골든 디렉토리 외부화** — [`tests/test_bullbear_golden.py`](../src/tests/test_bullbear_golden.py) 의 `GOLDEN_DIR` 을 환경변수로 받도록 수정. 매주 운영 결과를 그대로 골든 회귀 가드로 자동 검증 가능

### 11.4 운영 진입 시 보유한 안전망 (M2 종료 시점 정리)

| 안전망 | 구체 메커니즘 | 발동 조건 |
|---|---|---|
| 사다리 retry/fallback | Sonnet primary → Sonnet retry → Haiku fallback (3회) | JSON 파싱·schema 검증·네트워크 실패 |
| ASL Catch 격리 | `BullBearParallel.Catch[States.ALL]` → `RecordItemFailure` Pass | 한 종목 사다리 3회 모두 실패 시 Map 계속 |
| Rate limit 회피 | `MaxConcurrency: 1` × Parallel 2 × `max_tokens 2048` = 4,096 ≤ 8,000 한도 | Anthropic Tier 1 분당 한도 (§5.2.2) |
| 결정성 캐싱 | `(symbol, as_of_date, stance, input_hash)` S3 키 | 같은 입력 재호출 시 LLM 호출 생략 |
| Schema 강제 | Pydantic `BullBearOpinion` + system prompt strict schema | LLM 응답 형식 위반 차단 |
| 추천 어휘 자동 가드 | `tests/test_bullbear_golden.py` `RECOMMENDATION_WORDS` 정규식 | Buy/Sell/Target 등 추천 표현 차단 |
| Sector 보강 | system prompt `Sector context` 섹션 + LLM 도메인 지식 | 6 sub-sector 검증 완료 (§10) |
| **응답 품질 자동 평가** | DeepEval G-Eval 3 criteria (evidence_grounded / risks_are_company_specific / signals_not_primary_evidence), judge = Sonnet 4.6 | criterion threshold 미만 발견 시 §11.5 baseline 비교 후 대응 |
| 비용 hard cap | (수동 대응) Anthropic Console / AWS Lambda 환경변수 disable | 월 $200 초과 (CHARTER §6) |

### 11.5 응답 품질 자동 평가 — DeepEval G-Eval baseline (2026-05-24 PoC)

LLM-as-judge 기반 hard rule 회귀 검증. 시스템 프롬프트의 hard rule 1·3·4 (Evidence-bound / Self-critique / Signals not as primary evidence) 를 G-Eval criteria 로 인코딩, Sonnet 4.6 judge 가 채점.

**기본 셋에서 제외한 항목**:
- hard rule #2 "No recommendations" → 기존 정규식 가드 [`RECOMMENDATION_WORDS`](../src/tests/test_bullbear_golden.py) 가 결정적으로 차단. PoC 골든 8건 전수 1.0/1.0 만점 — judge 호출이 새 시그널 0건 추가. 필요 시 [`build_no_recommendation_language`](../src/agents/bull_bear/evaluation/criteria.py) 를 명시 import 해 일회성 평가 가능 (예: regex 가드 회피 가능한 새 추천 표현 패턴 의심 시).
- hard rule #5 "JSON only" → Pydantic `BullBearOpinion` 검증이 모듈 경계 ([`agent._parse_opinion`](../src/agents/bull_bear/agent.py)) 에서 강제. judge 호출 추가는 비용만 증가.

**도구·비용**: DeepEval `>=2.0`, judge = `claude-sonnet-4-6`, 골든 8건 × 3 criteria ≈ **$0.37/회**. M3+ 운영 환산 (20종목 × 2 stance × 3 criteria × 주간) ≈ $11/월 — CHARTER §2.2 $200/월 상한 대비 5.5%.

**Baseline (2026-05-24, 골든 8건 × 3 criteria = 24 judge calls, 전수 통과)**:

| Criterion | Threshold | 8건 점수 분포 | 최저 | Margin |
|---|---|---|---|---|
| `evidence_grounded` | 0.8 | 0.9 × 4, 1.0 × 4 | 0.9 | +0.1 |
| `risks_are_company_specific` | 0.7 | 0.7 × 3, 0.8 × 2, 0.9 × 2, 1.0 × 1 | **0.7** | **0** |
| `signals_not_primary_evidence` | 0.8 | 0.8 × 1, 0.9 × 4, 1.0 × 3 | 0.8 | 0 |

baseline 리포트 전문 (judge reasoning 포함): [`tests/golden/bullbear/reports/deepeval_report.json`](../tests/golden/bullbear/reports/deepeval_report.json). 리포트는 골든 디렉토리 *하위* `reports/` 에 저장 — top-level 에 두면 [`test_bullbear_golden`](../src/tests/test_bullbear_golden.py) 의 `glob("*.json")` 이 리포트를 snapshot 으로 잘못 픽업.

**관찰**:
- `risks_are_company_specific` 가 가장 약한 차원 — 3건 (NVDA_bear, XOM_bear, XOM_bull) 이 임계값 정확히 동률. 공통 패턴 두 가지:
  - (a) 외부 정보 도입: `iPhone 17` (AAPL_bear), `Pioneer acquisition` (XOM 양쪽) — input 에 명시되지 않은 catalyst 를 risk 시나리오에 사용.
  - (b) Generic macro risk: `OPEC+ disruption`, `dollar strength`, `hyperscaler AI capex` 등 회사 메커니즘으로 연결 안 됨.
- `signals_not_primary_evidence` XOM_bear 0.8 동률 — argument 4 의 momentum framing 이 borderline 평가. 시스템 프롬프트의 의도된 동작 (JPM 골든 EV/EBITDA·FCF Yield 0회 인용, docs §10) 이 운영에서도 유지되는지 모니터링 차원.
- `evidence_grounded` 0.9 케이스 4건의 공통 감점: *derived calculation* (예: AAPL_bear `"29% premium"`, NVDA_bear `"growth rates 7.1%, 16.7%, 8.6%"`) — fabrication 이 아니라 input 수치에서 도출된 계산값. 시스템 프롬프트 룰의 회색 영역.

**임계값 정책**: M2 종료 직후 PoC 기준 보수적 유지. 4주 운영 데이터 누적 후 분포 보고 조정 — NVDA_bear/XOM 사례가 일관 반복되면 임계값 0.65 로 완화 또는 시스템 프롬프트 보강 (예: "key_risks_to_thesis 의 catalyst 는 input data 에서 도출 가능한 메커니즘으로 한정"). 단일 sample 8건은 통계적으로 적음.

**Judge family 단일성의 한계**: 본 baseline 은 judge 도 Sonnet 4.6 (Bull/Bear 본 호출과 동일 family) — self-preference bias 위험이 미측정. M3 진입 시점 cross-vendor (GPT-4o/Gemini) judge 로 1회 재평가 후 baseline 신뢰도 확정. 상세 결정 프레임 (Pattern A/B/C, ±0.05/±0.15 임계, Bedrock 전환과의 긴장) 은 [§10 "DeepEval judge 의 cross-vendor 다양성"](#10-미해결--다음-결정-필요) 항목 참조.

**fail 시 대응 흐름**:
1. judge reasoning (리포트의 `reason` 필드) 검토 — 어느 argument/risk 가 어떤 룰을 어떻게 위반했는지.
2. 동일 패턴이 2건 이상이면 시스템 프롬프트 보강 후보로 §10 미해결 항목에 등록.
3. 단발 fail 이면 LLM 응답 변동 가능성 — 다음 주 운영 결과로 재확인.

**실행**:
- 로컬 PoC: `PYTHONPATH=src ANTHROPIC_API_KEY=... .venv/bin/python scripts/run_bullbear_deepeval.py`
- pytest 게이트: `pytest -m deepeval` (개발자가 프롬프트 수정 후 회귀 확인)
- M3+ Lambda 자동화: [`src/agents/bull_bear/evaluation/`](../src/agents/bull_bear/evaluation/) 패키지를 그대로 import. lazy import 정책 (deepeval/anthropic 모두 함수 본문 import) 으로 cold start 무영향. ASL 확장 시 `BullBearMap` 후속에 `EvaluationMap` 추가 (MaxConcurrency=1, §5.2.2 rate limit 산정 동일 적용).

---

## 부록 A. 디렉토리 구조 (M2 마일스톤 ✅ 완전 종료)

CLAUDE.md "디렉토리 구조 (목표)"의 `src/agents/` 하위 — 이미 구현된 항목은 ✅ 표기.

```
src/agents/                              ✅ namespace package (CLAUDE.md 컨벤션 — __init__.py 없음)
├── bull_bear/
│   ├── schemas.py                       ✅ StockContext, BullBearOpinion 등 (§2.1, §2.2)
│   ├── mappers.py                       ✅ screened_to_context() 1:1 평탄화 (부록 B)
│   ├── context_builder.py               ✅ OHLCV/펀더멘털 → PriceSummary/Fundamentals (§2.1.1)
│   ├── agent.py                         ✅ 사다리 + 검증 + 로깅 + context_input_hash (§3, §4, §5, §6)
│   ├── anthropic_adapter.py             ✅ AnthropicSDKCaller — Lambda/스크립트가 사용 (§9 #6/#7)
│   └── lambda_core.py                   ✅ Lambda 공유 코어 — 캐시 hit/miss 분기 + S3 저장 (§9 #7)
└── prompts/
    ├── bull_system.md                   ✅ §3.2
    ├── bear_system.md                   ✅ §3.2
    └── bullbear_user.md                 ✅ §3.3 (placeholder 4개)

src/lambdas/                              ✅ Bull/Bear Lambda (§9 #7)
├── agent_bullbear_bull/
│   └── handler.py                       ✅ thin wrapper, stance="bull"
└── agent_bullbear_bear/
    └── handler.py                       ✅ thin wrapper, stance="bear"

src/common/                               (기존 자산 — 본 단계 #7-A 에 분기 fetcher 추가)
├── fmp_client.py                        ✅ get_income/cash_flow_statement_quarterly 추가
└── fundamentals.py                      ✅ fetch_income/cashflow_quarterly_with_cache 추가

scripts/
├── run_bullbear_golden.py               ✅ 골든 케이스 실행 — 4종목 fixture (§9 #6)
└── deploy_lambda.sh                     ✅ agents/ 패키징 추가 (#7 배포)

.github/workflows/
└── deploy-lambdas.yml                   ✅ src/agents/** path 트리거 추가 (#7 배포)

infra/step_functions/
└── screening_workflow.asl.json          ✅ BullBearMap (Map MaxConcurrency=1 + Parallel) + Catch 격리 (§9 #8)

tests/
├── golden/bullbear/                     ✅ {symbol}_{stance}.json 8개 (2026-04-30 첫 실행)
└── (src/tests/test_bullbear_*.py)       ✅ 단위 + 회귀 가드 (lambda_core 12, agent 29, prompts 11, ...)

EventBridge → Step Functions 트리거       ✅ 매주 월 06:00 ET cron (§9 #9, CHARTER §2.4)

⏳ 다음 단계 (본 문서 범위 밖):
   - M2 회고 (CHARTER §7 체크포인트) — 4주 운영 데이터 누적 후
   - M3 진입 — 시나리오 모델링 (`docs/03-scenario.md` 작성 시점)
   - 운영 모니터링 정책은 §11 참조
```

## 부록 B. 1단계와의 인터페이스 계약

[`docs/01-screening.md` 부록 A](01-screening.md#부록-a-bullbear와의-인터페이스-계약)와 짝을 이루는 본 단계 측 계약. **평탄화 매퍼**(`screened_to_context`)가 인터페이스 표면이다.

| `StockContext` 필드 | `ScreenedStock` 출처 | 변환 규칙 |
|---|---|---|
| `symbol`, `company_name`, `sector`, `sub_sector` | 동명 필드 | 1:1 |
| `as_of_date` | `ScreeningResult.as_of_date` | 매 호출에서 부모로부터 주입 |
| `composite_score` | 동명 필드 | 1:1 (`screening_score`로 표기 변경 X — 명칭 통일) |
| `momentum_z`, `value_z` | `factors.momentum_z`, `factors.value_z` | nested → flat |
| `pe_ttm`, `ev_ebitda`, `fcf_yield` | `factors.pe_ttm` 등 | nested → flat. **재호출 금지** (스크리닝과 동일 시점) |
| `peer_context` | 동명 필드 | 1:1, 사전 조립된 list[PeerComparable] 그대로 |
| `data_quality_flags` | 동명 필드 | 1:1 (단, LLM 프롬프트에는 미노출 — §2.1.2) |
| `run_id` | `ScreeningResult.run_id` | 매 호출에서 부모로부터 주입 |
| `screening_s3_key` | (없음) | Lambda 핸들러가 `screening/dt={...}/result.json` 경로 주입 |
| `price_summary` | (없음) | `context_builder`가 OHLCV 캐시에서 조립 |
| `fundamentals` | (없음) | `context_builder`가 FMP statements 캐시에서 조립 |

**가드**: `mappers.py`의 단위 테스트는 `ScreenedStock` 필드 셋과 `StockContext` 필드 셋을 dict 키로 비교해 매핑 누락을 방지한다. 스크리닝이 신규 필드를 추가하면 매퍼 테스트가 깨져 변경을 강제 인지하게 됨.

## 부록 C. 참고

- Anthropic JSON mode / Tool use (스키마 강제)
- 트윗 원본 영감: 종목당 30개 에이전트 → MVP는 의도적으로 2개 (CHARTER §3.3)
- 다음 문서: `docs/03-scenario.md` — Bull/Bear 출력을 입력으로 받는 시나리오 모델링 단계
