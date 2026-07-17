import json, datetime, re
from typing import List, Dict, Tuple, Optional

from code.agent.core import Agent
from code.utils.prompt_loader import load_prompt


class PolishRound:
    """一次完整的 Expert→Judge 迭代记录。"""
    def __init__(self, round_num: int, lesson: str, feedback: Optional[str]):
        self.round = round_num
        self.lesson = lesson
        self.feedback = feedback  # Judge 的反馈（上一轮），第一轮为 None
        self.polished: Optional[str] = None
        self.judge_result: Optional[dict] = None

    def to_discussion(self) -> list:
        entries = []
        # Expert 发言
        expert_content = f"第{self.round}轮输出"
        entries.append({
            "round": self.round, "role_id": "r_expert",
            "content": expert_content, "refers_to": None
        })
        if self.feedback:
            entries[-1]["refers_to"] = f"r{self.round-1}:r_judge"
        # Judge 发言
        if self.judge_result:
            judge_content = (
                f"评分: A={self.judge_result['scores']['A']['score']}, "
                f"B={self.judge_result['scores']['B']['score']}, "
                f"C={self.judge_result['scores']['C']['score']}, "
                f"D={self.judge_result['scores']['D']['score']}, "
                f"E={self.judge_result['scores']['E']['score']}, "
                f"F={self.judge_result['scores']['F']['score']}, "
                f"总分={self.judge_result['total_score']}\n"
                f"反馈: {self.judge_result['overall_feedback']}"
            )
            entries.append({
                "round": self.round, "role_id": "r_judge",
                "content": judge_content,
                "refers_to": f"r{self.round}:r_expert"
            })
        return entries


class Orchestrator:
    """驱动 Expert-Judge 多轮迭代打磨的核心调度器。"""

    MAX_ITERATIONS = 5

    def __init__(self, model: Optional[str] = None):
        expert_prompt = load_prompt("expert_system")
        judge_prompt = load_prompt("judge_system")

        self.expert = Agent(name="教案打磨专家", role_prompt=expert_prompt)
        self.judge = Agent(name="评审专家", role_prompt=judge_prompt, use_tools=False)
        self.model = model

    def run(self, lesson: str, student_id: str, sample_id: str) -> Tuple[str, dict]:
        """执行多轮打磨循环，返回 (最终教案, process_dict)。"""
        rounds: List[PolishRound] = []
        current_lesson = lesson
        feedback = None

        print(f"\n启动 Expert-Judge 打磨循环（最多 {self.MAX_ITERATIONS} 轮）")
        print("=" * 60)

        for i in range(1, self.MAX_ITERATIONS + 1):
            pr = PolishRound(i, current_lesson, feedback)
            print(f"\n▶ 第 {i} 轮 — Expert 打磨中...\n")

            # ── Expert 打磨 ──
            expert_msg = f"请打磨以下教案：\n\n{current_lesson}"
            if feedback:
                expert_msg = f"以下是上一轮评审反馈，请据此改进教案：\n\n{feedback}\n\n---\n\n当前教案：\n\n{current_lesson}"

            polished_chunks = []
            for chunk in self.expert.chat_stream(expert_msg):
                print(chunk, end="", flush=True)
                polished_chunks.append(chunk)
            print()
            pr.polished = "".join(polished_chunks).strip()

            # ── Judge 评审 ──
            print(f"\n  Judge 评审中...")
            judge_prompt = f"请评审以下教案并返回 JSON：\n\n{pr.polished}"

            judge_response = self.judge.chat(judge_prompt)
            judge_data = self._parse_judge_response(judge_response)
            pr.judge_result = judge_data

            total = judge_data.get("total_score", 0)
            print(f"  总分: {total}/100 | should_stop: {judge_data.get('should_stop', False)}")
            print(f"  评分明细: A={judge_data['scores']['A']['score']}, B={judge_data['scores']['B']['score']}, "
                  f"C={judge_data['scores']['C']['score']}, D={judge_data['scores']['D']['score']}, "
                  f"E={judge_data['scores']['E']['score']}, F={judge_data['scores']['F']['score']}")

            rounds.append(pr)

            # ── 终止判断 ──
            if judge_data.get("should_stop", False):
                print(f"\n  ✓ 达到终止条件，打磨完成。")
                break

            # 准备下一轮输入
            current_lesson = pr.polished
            feedback = judge_data.get("overall_feedback", "")

        # ── 取最后一轮结果 ──
        final_round = rounds[-1]
        final_lesson = final_round.polished
        final_scores = final_round.judge_result["scores"] if final_round.judge_result else {}

        # ── 构建 process.json ──
        discussion = []
        modifications = []
        mod_count = 0

        for pr in rounds:
            discussion.extend(pr.to_discussion())
            if pr.judge_result:
                for dim_key in ['A', 'B', 'C', 'D', 'E', 'F']:
                    s = pr.judge_result['scores'].get(dim_key, {})
                    suggestion = s.get('suggestions', '')
                    if suggestion:
                        mod_count += 1
                        modifications.append({
                            "mod_id": f"M{mod_count:02d}",
                            "location": f"维度 {dim_key}",
                            "before_summary": f"第{pr.round}轮评审指出的问题",
                            "after_summary": suggestion[:100],
                            "source_role": "r_judge",
                            "rationale": suggestion[:200]
                        })

        if not modifications:
            modifications = [{
                "mod_id": "M01", "location": "全篇",
                "before_summary": "原始教案",
                "after_summary": f"第{len(rounds)}轮打磨后教案",
                "source_role": "r_expert",
                "rationale": f"经过{len(rounds)}轮 Expert-Judge 迭代打磨"
            }]

        process = {
            "meta": {
                "student_id": student_id,
                "sample_id": sample_id,
                "timestamp": datetime.datetime.now().astimezone().isoformat()
            },
            "roles": [
                {"role_id": "r_expert", "name": "教案打磨专家", "expertise": "教学设计评价与打磨"},
                {"role_id": "r_judge", "name": "评审专家", "expertise": "教案质量评审（附录A量规）"}
            ],
            "discussion": discussion,
            "modifications": modifications
        }

        return final_lesson, process

    def _parse_judge_response(self, response: Optional[str]) -> dict:
        """从 Judge 响应中解析 JSON。"""
        if not response:
            return self._fallback_judge_result("评审响应为空")

        # 尝试直接解析
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # 尝试从 ```json 块中提取
        match = re.search(r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```', response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试提取第一个 { }
        match = re.search(r'(\{.*\})', response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        return self._fallback_judge_result("评审 JSON 解析失败，请检查 Judge prompt 输出格式。")

    def _fallback_judge_result(self, reason: str) -> dict:
        max_scores = {"A": 10, "B": 15, "C": 20, "D": 15, "E": 10, "F": 30}
        return {
            "scores": {k: {"score": 0, "max": max_scores[k], "evidence": "", "suggestions": ""} for k in "ABCDEF"},
            "total_score": 0,
            "overall_feedback": reason,
            "should_stop": True
        }