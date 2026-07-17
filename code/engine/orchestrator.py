import json, datetime, re
from typing import List, Dict, Tuple, Optional

from code.agent.core import Agent


class PolishRound:
    def __init__(self, round_num: int, lesson: str, feedback: Optional[str]):
        self.round = round_num
        self.lesson = lesson
        self.feedback = feedback
        self.thinking: Optional[str] = None
        self.polished: Optional[str] = None
        self.judge_result: Optional[dict] = None

    def to_discussion(self) -> list:
        entries = []
        expert_content = f"【思考】\n{self.thinking or '(无)'}\n\n【输出】\n{self.polished or '(无)'}"
        entries.append({
            "round": self.round, "role_id": "r_expert",
            "content": expert_content, "refers_to": None
        })
        if self.feedback:
            entries[-1]["refers_to"] = f"r{self.round-1}:r_judge"
        if self.judge_result:
            s = self.judge_result['scores']
            judge_content = (
                f"评分: A={s['A']['score']}, B={s['B']['score']}, "
                f"C={s['C']['score']}, D={s['D']['score']}, "
                f"E={s['E']['score']}, F={s['F']['score']}, "
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
    """Expert-Judge 多轮迭代打磨调度器。"""

    MAX_ITERATIONS = 5

    def __init__(self, model: Optional[str] = None):
        self.expert = Agent(name="教案打磨专家", role_id="r_expert")
        self.judge = Agent(name="评审专家", role_id="r_judge", use_tools=False)
        self.model = model

    def run(self, lesson: str, student_id: str, sample_id: str) -> Tuple[str, dict]:
        rounds: List[PolishRound] = []
        current_lesson = lesson
        feedback = None

        print(f"\n启动 Expert-Judge 打磨循环（最多 {self.MAX_ITERATIONS} 轮）")
        print("=" * 60)

        for i in range(1, self.MAX_ITERATIONS + 1):
            pr = PolishRound(i, current_lesson, feedback)
            print(f"\n▶ 第 {i} 轮 — Expert 思考中...\n")

            expert_msg = f"请打磨以下教案：\n\n{current_lesson}"
            if feedback:
                expert_msg = f"以下是上一轮评审反馈，请据此改进教案：\n\n{feedback}\n\n---\n\n当前教案：\n\n{current_lesson}"

            # 静默收集完整响应，parse 后再展示思考部分
            full_chunks = []
            for chunk in self.expert.chat_stream(expert_msg):
                full_chunks.append(chunk)
            full = "".join(full_chunks)

            # 切分思考 + 教案：优先用 ---POLISHED--- 标记，其次用第一个 # 标题
            polished_marker = "---POLISHED---"
            if polished_marker in full:
                parts = full.split(polished_marker, 1)
                thinking = parts[0].strip()
                polished = parts[1].strip()
            else:
                # 兜底：找到第一个 "# " 一级标题，之前是思考，之后是教案
                heading_match = re.search(r'\n(# .+?)\n', full)
                if heading_match:
                    idx = heading_match.start()
                    thinking = full[:idx].strip()
                    polished = full[idx:].strip()
                else:
                    thinking = full
                    polished = full

            pr.thinking = thinking
            pr.polished = polished

            # 只展示思考过程
            print(thinking)

            # Judge 评审（失败重试1次）
            print(f"\n  Judge 评审中...")
            judge_prompt = f"请评审以下教案并返回 JSON：\n\n{pr.polished}"
            judge_response = self.judge.chat(judge_prompt)
            judge_data = self._parse_judge_response(judge_response)

            # 如果解析失败（0分fallback），重试一次
            if judge_data.get("total_score", 0) == 0 and not judge_response:
                print(f"  评审解析失败，重试...")
                judge_response = self.judge.chat(judge_prompt)
                judge_data = self._parse_judge_response(judge_response)

            pr.judge_result = judge_data

            total = judge_data.get("total_score", 0)
            print(f"  总分: {total}/100 | should_stop: {judge_data.get('should_stop', False)}")
            s = judge_data['scores']
            print(f"  评分明细: A={s['A']['score']}, B={s['B']['score']}, "
                  f"C={s['C']['score']}, D={s['D']['score']}, "
                  f"E={s['E']['score']}, F={s['F']['score']}")

            rounds.append(pr)

            if judge_data.get("should_stop", False):
                print(f"\n  ✓ 达到终止条件，打磨完成。")
                break

            current_lesson = pr.polished
            feedback = judge_data.get("overall_feedback", "")

        # 最终输出打磨后教案
        final_round = rounds[-1]
        final_lesson = final_round.polished
        print("\n" + "=" * 60)
        print("最终教案:\n")
        print(final_lesson)

        # 构建 process.json
        discussion = []
        modifications = []
        mod_count = 0

        for pr in rounds:
            discussion.extend(pr.to_discussion())
            if pr.thinking:
                for line in pr.thinking.split("\n"):
                    t = line.strip()
                    if not t:
                        continue
                    if re.match(r'^[\d•\-\*]+\s*[.、)）．]', t):
                        mod_count += 1
                        modifications.append({
                            "mod_id": f"M{mod_count:02d}",
                            "location": "全篇",
                            "before_summary": "原始教案相关部分",
                            "after_summary": re.sub(r'^[\d•\-\*]+[.、)）．\s]*', '', t)[:100],
                            "source_role": "r_expert",
                            "rationale": t[:200]
                        })
            if pr.judge_result:
                for dim_key in 'ABCDEF':
                    suggestion = pr.judge_result['scores'][dim_key].get('suggestions', '')
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
                "before_summary": "原始教案", "after_summary": f"第{len(rounds)}轮打磨后教案",
                "source_role": "r_expert",
                "rationale": f"经过{len(rounds)}轮 Expert-Judge 迭代打磨"
            }]

        process = {
            "meta": {
                "student_id": student_id, "sample_id": sample_id,
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
        if not response:
            return self._fallback_judge_result("评审响应为空")
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        match = re.search(r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```', response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r'(\{.*\})', response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return self._fallback_judge_result("评审 JSON 解析失败")

    def _fallback_judge_result(self, reason: str) -> dict:
        max_scores = {"A": 10, "B": 15, "C": 20, "D": 15, "E": 10, "F": 30}
        return {
            "scores": {k: {"score": 0, "max": max_scores[k], "evidence": "", "suggestions": ""} for k in "ABCDEF"},
            "total_score": 0, "overall_feedback": reason,
            "should_stop": False  # 不因解析失败而终止
        }