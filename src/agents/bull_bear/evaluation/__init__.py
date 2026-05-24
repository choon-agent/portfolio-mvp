"""Bull/Bear 에이전트 응답 품질 평가 — DeepEval G-Eval 기반.

설계 근거: docs/02-bull-bear.md §11 (응답 품질 회귀 모니터링) — *프롬프트 회귀
게이트* 와 *운영 모니터링* 두 용도에 공통으로 쓸 평가 자산을 단일 모듈에 둠.

이 패키지의 책임:
- criteria.py : 시스템 프롬프트의 hard rule 을 G-Eval criteria 로 인코딩
                (Single source of truth — pytest / Lambda / 수동 스크립트가
                 동일 정의를 import)
- adapters.py : AnthropicJudge (DeepEval 의 judge LLM 어댑터) + 골든 스냅샷
                → LLMTestCase 변환

import 정책 (deepeval 미설치 환경 보호):
- 본 패키지의 모든 deepeval import 는 *함수 본문* 에서 lazy 로 발생한다.
- 따라서 deepeval 가 없어도 agents.bull_bear 의 다른 모듈 (schemas/agent/
  context_builder/lambda_core) 의 import 와 단위 테스트는 영향 없음.
- Lambda 번들에 본 패키지가 포함되더라도, 호출되지 않으면 cold start 실패 X.

비용 (judge = Sonnet 4.6, 기본 셋 3 criteria — docs §11.5 baseline 기준):
- 1 스냅샷 × 3 criteria ≈ $0.045
- 골든 8건 전수 평가 ≈ $0.37
- M3+ 운영 (20종목 × 2 stance × 3 criteria 주간) ≈ $11/월
"""
