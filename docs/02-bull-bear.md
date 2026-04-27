# 02. Bull/Bear 에이전트 설계

> **단계**: 2단계 — Bull/Bear 리서치 (LLM 핵심 사용 지점)
> **상위 문서**: [`CHARTER.md`](../CHARTER.md), [`CLAUDE.md`](../CLAUDE.md)
> **버전**: v0.1 (2026-04-27 초안)
> **상태**: 설계 단계 (M1 마일스톤 — 미구현)

---

## 0. TL;DR

스크리닝 통과 종목(상위 15~20개)에 대해 **Bull 에이전트**와 **Bear 에이전트**를 각각 1회 호출해 매수·매도 근거를 독립적으로 생성한다. 두 출력은 다음 단계(시나리오 모델링)에 입력으로 전달된다.

- 모델: **Sonnet 4.6** (Haiku 4.5 폴백)
- 호출량: 종목 15개 × 2 에이전트 × 주 1회 = **주 30회 / 월 ~120회**
- 예상 비용: **월 $25~$50** (CHARTER §3.3 예산 내)
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

스크리닝 통과 종목 1개에 대해 호출 직전에 조립한다. **FMP 직접 호출 금지** — 캐싱 계층(`src/common/fmp_client.py`)을 통해서만.

| 필드 | 타입 | 출처 | 비고 |
|---|---|---|---|
| `symbol` | str | 스크리닝 결과 | |
| `company_name` | str | `Constituent` | |
| `sector` / `sub_sector` | str | `Constituent` | |
| `as_of_date` | date | 호출 시점 | 리밸런싱 기준일 (월요일 프리마켓) |
| `price_summary` | obj | OHLCV 캐시 | 직전 1Y 수익률, 52w high/low 대비, 베타 |
| `fundamentals` | obj | FMP statements | 매출·EPS·FCF의 직전 4분기 + 5Y CAGR |
| `valuation` | obj | FMP ratios | P/E, P/S, EV/EBITDA, FCF yield (섹터 중앙값과 함께) |
| `peer_context` | obj | 스크리닝 결과 | 같은 sub_sector 상위 5개 멀티플 비교 |
| `screening_score` | float | 1단계 출력 | 팩터 점수 (참고용 — Bull/Bear는 직접 사용 안 함) |

**원칙**: 컨텍스트 토큰을 8K 이하로 묶는다 (Sonnet 입력 단가 절감). 표 데이터는 Markdown table로 직렬화.

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

### 3.3 사용자 프롬프트
`StockContext`를 Markdown 표 형태로 직렬화 + 마지막에 "이 데이터를 근거로 {bull|bear} 관점의 의견을 작성하라" 한 줄.

---

## 4. 오케스트레이션

### 4.1 호출 패턴
1주 1회, 월요일 프리마켓:

```
스크리닝 결과 (15~20 종목)
        │
        ▼
[StepFunctions Map state, MaxConcurrency=5]
   ├─ Lambda: bull_agent(symbol_i)   ─┐
   └─ Lambda: bear_agent(symbol_i)   ─┴─→ S3 (opinions/yyyy-mm-dd/{symbol}_{stance}.json)
```

- **Bull/Bear는 서로 독립** → 동일 종목 내에서도 병렬 호출 가능
- **종목 간**도 병렬 (`MaxConcurrency=5`로 Anthropic rate limit 보호)
- 한 종목의 한쪽이 실패해도 다른 종목/스탠스에 영향 없음
- 1주 1회 배치이므로 Lambda 동시성 부담 없음

### 4.2 구성 요소 매핑 (CHARTER §3.4 기존 자산 재사용)
- **EventBridge**: 매주 월요일 06:00 ET 트리거
- **Step Functions**: 스크리닝 → Bull/Bear Map → 시나리오 → 최적화 → 리밸런서
- **Lambda**: 종목당 1개 인스턴스, Bull/Bear는 별도 Lambda (모델·프롬프트 격리)
- **S3 레이아웃**: `s3://{bucket}/agents/bullbear/dt={yyyy-mm-dd}/symbol={SYM}/stance={bull|bear}.json`
- **Athena**: S3 출력에 외부 테이블 연결 → 의사결정 로그 사후 분석 (CHARTER 2순위)

---

## 5. 모델 선택과 비용

### 5.1 모델 정책 (CLAUDE.md 준수)
- **기본**: `claude-sonnet-4-6`
- **폴백**: `claude-haiku-4-5-20251001` (1차 검증 실패 시에만)
- **금지**: Opus 무근거 사용

### 5.2 토큰·비용 추정 (단일 호출)

| 항목 | 추정 |
|---|---|
| 시스템 프롬프트 | ~600 tok |
| 사용자 컨텍스트 (StockContext) | ~3,500 tok |
| 출력 (JSON) | ~600 tok |
| **합계** | 입력 ~4,100 / 출력 ~600 |

Sonnet 4.6 가격 가정 (2026-04 시점): 입력 $3/1M, 출력 $15/1M
- 호출당 비용: 4,100 × $3/1M + 600 × $15/1M ≈ **$0.021**
- **단일 호출 $1 상한 (CLAUDE.md)** 대비 50배 여유

### 5.3 월 비용 추정

| 시나리오 | 종목 | 주간 호출 | 월 호출 | 월 비용 |
|---|---|---|---|---|
| MVP 기본 (15종목) | 15 | 30 | 120 | ~$2.6 |
| 상한 (20종목) | 20 | 40 | 160 | ~$3.4 |
| 재시도 +20% 가정 | 20 | 48 | 192 | ~$4.0 |

**여유분이 큰 이유**: 2단계 자체보다 3단계 시나리오 모델링 비용이 더 무거울 것으로 예상. CHARTER §3.3의 "월 $30~$80" 추정의 대부분은 3단계 몫. 본 단계는 **월 $5 미만**으로 운영 가능 → v2에서 Debate 패턴(에이전트 수 5~10배) 실험 여력 확보.

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

## 9. 구현 순서 (M1 마일스톤)

> CLAUDE.md "현재 단계 M0" 다음 항목들. 각 단계는 별도 커밋. LLM 호출 추가 커밋은 메시지에 비용 추정 명시.

1. **schemas.py 작성** — `StockContext`, `Argument`, `BullBearOpinion` Pydantic 모델 + 단위 테스트
2. **context_builder.py** — FMP 캐시 → `StockContext` 변환 (LLM 호출 없음)
3. **프롬프트 파일 3종** — `bull_system.md`, `bear_system.md`, `bullbear_user.md`
4. **agent.py 골격** — Anthropic SDK 호출, JSON mode, Pydantic 검증, 재시도/폴백, 로깅 (`FakeAnthropicClient`로 단위 테스트)
5. **golden 케이스 1개** — AAPL 실제 호출 1회, 출력 검토, 스냅샷 저장
6. **Lambda 핸들러** — `src/lambdas/agent_bullbear/handler.py`
7. **Step Functions Map state** — 5종목으로 dry run
8. **15종목 주간 배치 첫 실행** — 비용·실패율 기록 → M1 회고

---

## 10. 미해결 / 다음 결정 필요

- [ ] FMP statements 캐시 TTL이 현재 90일 — Bull/Bear는 분기 발표 직후 호출이 잦을 수 있어 **이벤트 기반 캐시 무효화** 도입 여부
- [ ] `key_risks_to_thesis`의 출력이 실제로 단일 호출 편향 보완 효과가 있는지 — M1 종료 시 골든 케이스로 평가
- [ ] v2 Debate 패턴 실험 시점 (M2 후반? M3?)
- [ ] 의견 출력의 한국어 vs 영어 — 일관성 차원에서 영어가 무난하지만 산출물(블로그) 관점에서 한국어 이점 존재 → M1 골든 케이스에서 양쪽 비교 후 결정

---

## 부록 A. 디렉토리 변경 제안

CLAUDE.md "디렉토리 구조 (목표)"에 이미 명시된 `src/agents/`를 다음과 같이 시작:

```
src/agents/
├── __init__.py
├── bull_bear/
│   ├── __init__.py
│   ├── agent.py
│   ├── context_builder.py
│   └── schemas.py
└── prompts/
    ├── bull_system.md
    ├── bear_system.md
    └── bullbear_user.md

src/lambdas/
└── agent_bullbear/
    └── handler.py
```

## 부록 B. 참고

- Anthropic JSON mode / Tool use (스키마 강제)
- 트윗 원본 영감: 종목당 30개 에이전트 → MVP는 의도적으로 2개 (CHARTER §3.3)
- 다음 문서: `docs/03-scenario.md` — Bull/Bear 출력을 입력으로 받는 시나리오 모델링 단계
