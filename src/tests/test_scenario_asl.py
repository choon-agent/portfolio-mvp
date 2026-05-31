"""Step Functions ASL 정의의 정적 구조 가드 (M3 #10).

설계 근거: docs/03-scenario.md §6.1, §6.3

AWS 배포 없이 ASL JSON 의 구조 drift 를 잡는다 — ScenarioMap 추가 + BullBearMap
체이닝 교정(G1)이 정확한지, M2 BullBearMap 의 기존 구조가 보존되는지(회귀 가드),
placeholder 셋이 deploy 스크립트와 일치하는지.

실제 실행(dry-run 5종목)은 AWS 자격증명 필요 — 본 테스트는 정의 층위만.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
ASL_PATH = ROOT / "infra" / "step_functions" / "screening_workflow.asl.json"


@pytest.fixture(scope="module")
def asl() -> dict:
    return json.loads(ASL_PATH.read_text(encoding="utf-8"))


# ---------- 전체 골격 ----------


def test_start_and_top_states(asl: dict) -> None:
    assert asl["StartAt"] == "RunScreening"
    assert set(asl["States"]) == {"RunScreening", "BullBearMap", "ScenarioMap"}


def test_chain_run_to_bullbear_to_scenario(asl: dict) -> None:
    assert asl["States"]["RunScreening"]["Next"] == "BullBearMap"
    assert asl["States"]["BullBearMap"]["Next"] == "ScenarioMap"
    assert asl["States"]["ScenarioMap"].get("End") is True


# ---------- G1: BullBearMap 체이닝 교정 ----------


def test_bullbear_resultpath_preserves_result(asl: dict) -> None:
    """ResultPath 가 없으면 Map 출력이 $.result 를 덮어써 ScenarioMap 의
    ItemsPath:$.result.selected 가 깨짐 (G1)."""
    bb = asl["States"]["BullBearMap"]
    assert bb["ResultPath"] == "$.bullbear_results"
    assert "End" not in bb  # End→Next 로 교체됨


def test_bullbear_m2_structure_preserved(asl: dict) -> None:
    """M2 회귀 가드 — BullBearMap 의 기존 구조(MaxConcurrency=1, Parallel,
    Catch→RecordItemFailure)가 유지되는지."""
    bb = asl["States"]["BullBearMap"]
    assert bb["MaxConcurrency"] == 1
    assert bb["ItemsPath"] == "$.result.selected"
    states = bb["ItemProcessor"]["States"]
    assert states["BullBearParallel"]["Type"] == "Parallel"
    assert states["BullBearParallel"]["Catch"][0]["Next"] == "RecordItemFailure"
    branch_starts = {b["StartAt"] for b in states["BullBearParallel"]["Branches"]}
    assert branch_starts == {"BullAgent", "BearAgent"}


# ---------- ScenarioMap (§6.1) ----------


def test_scenario_map_config(asl: dict) -> None:
    sm = asl["States"]["ScenarioMap"]
    assert sm["Type"] == "Map"
    assert sm["MaxConcurrency"] == 2  # G3 — single-stance, M2 동일 부하
    assert sm["ItemsPath"] == "$.result.selected"
    # ItemSelector: G4 — agent_scenario 가 Bull/Bear S3 키 재구성 (screening_s3_key 불요)
    sel = sm["ItemSelector"]
    assert set(sel) == {"screened_stock.$", "as_of_date.$", "run_id.$"}


def test_scenario_agent_task_catch_and_retry(asl: dict) -> None:
    """G2 — Parallel 없는 단일 Task 라 Catch/Retry 가 Task 직착."""
    states = asl["States"]["ScenarioMap"]["ItemProcessor"]["States"]
    assert set(states) == {"ScenarioAgent", "RecordScenarioFailure"}
    task = states["ScenarioAgent"]
    assert task["Type"] == "Task"
    assert task["Parameters"]["FunctionName"] == "<<SCENARIO_LAMBDA>>"
    # Catch → RecordScenarioFailure (Task 직착)
    assert task["Catch"][0]["ErrorEquals"] == ["States.ALL"]
    assert task["Catch"][0]["Next"] == "RecordScenarioFailure"
    # Lambda throttle retry (M2 와 동일 4종 에러)
    retry = task["Retry"][0]
    assert "Lambda.TooManyRequestsException" in retry["ErrorEquals"]
    assert retry["MaxAttempts"] == 3
    assert states["RecordScenarioFailure"]["Type"] == "Pass"


# ---------- placeholder (deploy 스크립트 정합) ----------


def test_placeholders_match_deploy_script() -> None:
    asl_txt = ASL_PATH.read_text(encoding="utf-8")
    placeholders = set(re.findall(r"<<(\w+)>>", asl_txt))
    assert placeholders == {
        "RUN_SCREENING_LAMBDA",
        "BULL_LAMBDA",
        "BEAR_LAMBDA",
        "SCENARIO_LAMBDA",
    }

    deploy = (ROOT / "scripts" / "deploy_step_functions.sh").read_text(encoding="utf-8")
    for ph in placeholders:
        assert f"<<{ph}>>" in deploy, f"deploy 스크립트에 {ph} 치환 누락"
