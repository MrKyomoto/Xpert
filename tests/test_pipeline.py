import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

from code.agent.core import Agent
from code.engine import orchestrator as orchestrator_module
from code.engine.orchestrator import Orchestrator
from code.run import parse_profile, parse_student_id
from code.skills.manager import SkillManager
from code.tools import logger


ROOT = Path(__file__).resolve().parents[1]


def test_skill_manager_indexes_modular_cards():
    expected = {
        "r_literacy": {
            "responsibility",
            "knowledge_boundary",
            "rubric_mapping",
            "case_knowledge",
        },
        "r_subject": {
            "responsibility",
            "knowledge_boundary",
            "rubric_mapping",
            "case_knowledge",
        },
        "r_learner": {
            "responsibility",
            "knowledge_boundary",
            "rubric_mapping",
            "case_knowledge",
        },
        "r_chair": {
            "responsibility",
            "knowledge_boundary",
            "conflict_resolution",
        },
        "r_judge": {
            "responsibility",
            "knowledge_boundary",
            "evaluation_rubric",
            "scoring_rules",
        },
    }
    for role_id, required in expected.items():
        manager = SkillManager(role_id)
        assert required.issubset(set(manager.available_names))
        assert "_system" not in manager.available_names
        for name in required:
            assert manager.require_skill(name).strip()
            assert re.fullmatch(r"[0-9a-f]{64}", manager.skill_digest(name))


def test_agent_rebuilds_prompt_without_pbl_skill_leakage():
    agent = Agent(
        name="测试专家",
        role_id="r_literacy",
        use_tools=False,
        client=object(),
    )
    regular = ["responsibility", "knowledge_boundary", "rubric_mapping"]
    agent.configure_skills(regular + ["case_knowledge"])
    assert "### Skill: case_knowledge" in agent.context.messages[0]["content"]

    agent.configure_skills(regular)
    prompt = agent.context.messages[0]["content"]
    assert "### Skill: case_knowledge" not in prompt
    assert agent.get_loaded_skills() == regular
    assert len(agent.context.messages) == 1


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("demo001_MATH01.md", "regular"),
        ("demo001_MATH02.md", "pbl"),
        ("demo001_CHN01.md", "pbl"),
        ("demo001_BIO01.md", "pbl"),
    ],
)
def test_lesson_type_detection_on_examples(filename, expected):
    lesson = (ROOT / "code/examples/inputs" / filename).read_text(encoding="utf-8")
    context = Orchestrator._detect_lesson_context(
        lesson,
        filename.split("_", 1)[1].split(".", 1)[0],
        {},
    )
    assert context["lesson_type"] == expected
    assert context["detection"][f"{expected}_score"] > 0


def test_profile_override_and_yaml_parsing():
    text = 'student_id: "demo123"\nlesson_type: regular\nsubject: 数学\ngrade: 七年级\n'
    profile = parse_profile(text)
    assert parse_student_id(text) == "demo123"
    context = Orchestrator._detect_lesson_context(
        "# 项目简介\n## 项目目标\n",
        "UNKNOWN",
        profile,
    )
    assert context["lesson_type"] == "regular"
    assert context["subject"] == "数学"
    assert context["stage"] == "义务教育"
    assert context["detection"]["source"] == "profile_override"


def test_stop_rule_truth_table():
    assert Orchestrator._compute_should_stop(99, None)[0] is False
    assert Orchestrator._compute_should_stop(84, 84)[0] is False
    assert Orchestrator._compute_should_stop(87, 85)[0] is False
    assert Orchestrator._compute_should_stop(86.5, 85)[0] is True
    assert Orchestrator._compute_should_stop(85, 86)[0] is True


def test_structured_opinion_and_judge_parsing():
    opinions = (
        "1. 【位置】教学目标1｜【问题】动词不可观察｜"
        "【依据】rubric_mapping: F1｜【建议】改为可观察行为\n"
        "2. 【位置】教学过程｜【问题】缺少检核｜"
        "【依据】responsibility: F6｜【建议】增加退出卡"
    )
    parsed = Orchestrator._parse_opinion_items(
        opinions,
        "r_literacy",
        ["responsibility", "knowledge_boundary", "rubric_mapping"],
    )
    assert len(parsed) == 2
    assert parsed[0]["location"] == "教学目标1"
    assert parsed[0]["skills_used"] == ["rubric_mapping"]

    scores = {
        key: {
            "score": maximum,
            "max": maximum,
            "evidence": f"{key} 有明确原文证据",
            "suggestions": "",
        }
        for key, maximum in Orchestrator.SCORE_MAX.items()
    }
    result = Orchestrator._parse_judge_response(
        json.dumps(
            {
                "scores": scores,
                "total_score": 1,
                "overall_feedback": "结构与证据完整。",
                "should_stop": True,
            },
            ensure_ascii=False,
        )
    )
    assert result["total_score"] == 100
    assert result["reported_total_score"] == 1
    assert result["should_stop"] is False


def test_expert_parser_accepts_harmless_marker_hyphen_variation():
    thinking, opinions = Orchestrator()._parse_expert_response(
        "分析内容\n----OPINION---\n1. 【位置】目标｜【问题】笼统｜"
        "【依据】rubric_mapping: F1｜【建议】改写"
    )
    assert thinking == "分析内容"
    assert opinions.startswith("1. 【位置】")

    repeated = Orchestrator._parse_opinion_items(
        "---OPINION---\n1. 【位置】目标｜【问题】笼统｜"
        "【依据】rubric_mapping: F1｜【建议】改写",
        "r_literacy",
        ["rubric_mapping"],
    )
    assert len(repeated) == 1


def test_opinion_basis_must_reference_a_loaded_skill():
    with pytest.raises(ValueError, match="未引用当前已加载 Skill"):
        Orchestrator._parse_opinion_items(
            "1. 【位置】目标｜【问题】不可观察｜【依据】根据相关要求｜【建议】改写",
            "r_literacy",
            ["responsibility", "knowledge_boundary", "rubric_mapping"],
        )


def test_chair_parser_requires_complete_proposal_coverage():
    proposals = [
        {"proposal_id": "P001", "source_role": "r_literacy"},
        {"proposal_id": "P002", "source_role": "r_subject"},
    ]
    decisions = [
        {
            "proposal_ids": ["P001", "P002"],
            "status": "merged",
            "location": "教学目标",
            "before_summary": "目标笼统",
            "after_summary": "目标行为化并校正知识",
            "source_roles": ["r_literacy", "r_subject"],
            "rationale": "合并两条互补证据",
        }
    ]
    response = (
        "---DECISIONS---\n"
        + json.dumps(decisions, ensure_ascii=False)
        + "\n---CONFLICTS---\n[]\n---POLISHED---\n# 教案\n\n## 教学目标\n内容"
    )
    _, parsed, conflicts, polished = Orchestrator._parse_chair_response(
        response,
        {"P001", "P002"},
        proposals,
    )
    assert parsed[0]["status"] == "merged"
    assert conflicts == []
    assert polished.startswith("# 教案")


def test_polished_structure_requires_appendix_d_process_heading():
    Orchestrator._validate_polished_structure(
        "# 教案\n\n## 项目任务链\n\n### 活动1\n内容", "pbl"
    )
    with pytest.raises(ValueError, match="过程/任务类标题"):
        Orchestrator._validate_polished_structure(
            "# 教案\n\n## 项目策划\n\n### 活动1\n内容", "pbl"
        )


def test_logger_never_overwrites_and_captures_stderr(tmp_path, monkeypatch):
    monkeypatch.setattr(logger, "LOG_DIR", str(tmp_path))
    sentinel = tmp_path / "existing.log"
    sentinel.write_text("historical", encoding="utf-8")
    old_digest = hashlib.sha256(sentinel.read_bytes()).hexdigest()

    paths = []
    for index in range(2):
        with logger.capture_output("test") as path:
            print(f"stdout-{index}")
            print(f"stderr-{index}", file=sys.stderr)
        paths.append(Path(path))

    assert paths[0] != paths[1]
    assert all(path.exists() and path.stat().st_size > 0 for path in paths)
    assert "stderr-0" in paths[0].read_text(encoding="utf-8")
    assert hashlib.sha256(sentinel.read_bytes()).hexdigest() == old_digest


class FakeAgent:
    def __init__(self, name, role_id="", **kwargs):
        self.name = name
        self.role_id = role_id
        self.loaded = []
        self.calls = 0

    def configure_skills(self, names):
        self.loaded = list(names)
        return self.loaded

    def get_loaded_skills(self):
        return list(self.loaded)

    def get_skill_manifest(self):
        return [
            {"name": name, "sha256": "0" * 64}
            for name in self.loaded
        ]

    def chat_stream(self, message):
        self.calls += 1
        if self.role_id.startswith("r_") and self.role_id not in {"r_chair", "r_judge"}:
            response = (
                f"分析 {self.role_id} 的职责，并回应上一轮相关意见。\n"
                "---OPINION---\n"
                "1. 【位置】教学目标｜【问题】表述仍可更具体｜"
                "【依据】rubric_mapping: 对应量规｜【建议】改为可观察行为"
            )
        elif self.role_id == "r_chair":
            pairs = re.findall(
                r'"source_role":\s*"([^"]+)".*?"proposal_id":\s*"(P\d{3})"',
                message,
                flags=re.DOTALL,
            )
            if not pairs:
                pairs = [
                    (role, proposal_id)
                    for proposal_id, role in re.findall(
                        r'"proposal_id":\s*"(P\d{3})".*?"source_role":\s*"([^"]+)"',
                        message,
                        flags=re.DOTALL,
                    )
                ]
            ids = list(dict.fromkeys(proposal_id for _, proposal_id in pairs))
            roles = list(dict.fromkeys(role for role, _ in pairs))
            decision = {
                "proposal_ids": ids,
                "status": "merged",
                "location": "教学目标",
                "before_summary": "目标较笼统",
                "after_summary": "目标改为可观察行为",
                "source_roles": roles,
                "rationale": "合并专家量规证据",
            }
            response = (
                "---DECISIONS---\n"
                + json.dumps([decision], ensure_ascii=False)
                + "\n---CONFLICTS---\n[]\n---POLISHED---\n"
                + "# 测试教案\n\n## 教学目标\n学生能完成并解释任务。\n\n"
                + "## 教学过程\n学生分组完成任务并提交退出卡。"
            )
        else:
            raise AssertionError("unexpected streaming role")
        yield response

    def chat(self, message, response_format=None):
        self.calls += 1
        total = 86 if self.calls == 1 else 87
        raw_scores = {"A": 9, "B": 13, "C": 18, "D": 13, "E": 9, "F": total - 62}
        return json.dumps(
            {
                "scores": {
                    key: {
                        "score": score,
                        "max": Orchestrator.SCORE_MAX[key],
                        "evidence": f"{key}：教案有明确原文证据",
                        "suggestions": "继续细化",
                    }
                    for key, score in raw_scores.items()
                },
                "total_score": total,
                "overall_feedback": "继续细化目标和检核。",
                "should_stop": False,
            },
            ensure_ascii=False,
        )


def test_mocked_round_table_end_to_end(monkeypatch):
    monkeypatch.setattr(orchestrator_module, "Agent", FakeAgent)
    lesson = "# 原教案\n\n## 教学目标\n理解内容。\n\n## 教学过程\n教师讲解。"
    polished, process = Orchestrator(model="fake").run(
        lesson,
        "demo001",
        "MATH01",
        profile={"student_id": "demo001", "grade": "七年级", "基础": "较弱"},
    )
    assert polished.startswith("# 测试教案")
    assert len(process["quality_control"]["score_history"]) == 2
    assert process["quality_control"]["score_history"][-1]["should_stop"] is True
    assert process["modifications"]
    assert process["knowledge_distillation"]["lesson_type"] == "regular"
    assert all(
        "case_knowledge" not in names
        for names in process["knowledge_distillation"]["loaded_skills"].values()
    )
    round_two_experts = [
        item
        for item in process["discussion"]
        if item["round"] == 2 and item["role_id"] in dict(orchestrator_module.EXPERT_ROLES)
    ]
    assert round_two_experts
    assert all(item["refers_to"] for item in round_two_experts)
