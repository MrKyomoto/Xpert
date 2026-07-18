import json, datetime, re, concurrent.futures
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field

from code.agent.core import Agent


# ── 角色定义（附录 D 示例） ──
EXPERT_ROLES = [
    ("r_literacy", "素养导向教研员"),
    ("r_subject",  "学科内容专家"),
    ("r_learner",  "学情适配专家"),
]
CHAIR_ROLE = ("r_chair", "主持人")
JUDGE_ROLE = ("r_judge", "评审专家")


@dataclass
class ExpertOpinion:
    role_id: str
    name: str
    thinking: str      # 分析思考文本
    opinions_text: str # ---OPINION--- 之后的意见列表
    refers_to: Optional[str] = None  # 引用上一轮的角色意见


@dataclass
class RoundRecord:
    round: int
    opinions: List[ExpertOpinion] = field(default_factory=list)
    chair_thinking: str = ""
    polished: str = ""
    judge_result: Optional[dict] = None
    prev_feedback: str = ""

    def to_discussion(self, round_num: int) -> list:
        entries = []
        # 每位专家的发言（含互评引用）
        for op in self.opinions:
            entry = {
                "round": round_num,
                "role_id": op.role_id,
                "content": f"【思考】\n{op.thinking}\n\n【意见】\n{op.opinions_text}",
                "refers_to": op.refers_to,
            }
            entries.append(entry)
        # 主持人发言
        if self.chair_thinking:
            entries.append({
                "round": round_num,
                "role_id": CHAIR_ROLE[0],
                "content": f"【合并与裁决】\n{self.chair_thinking}",
                "refers_to": [op.role_id for op in self.opinions],
            })
        # Judge 发言
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
                "round": round_num,
                "role_id": JUDGE_ROLE[0],
                "content": judge_content,
                "refers_to": CHAIR_ROLE[0],
            })
        return entries


class Orchestrator:
    """多 Expert 圆桌磨课调度器。"""

    MAX_ITERATIONS = 5
    MAX_OPINION_RETRY = 2  # 每轮专家拉取意见失败后的重试次数
    OPINION_MARKER = "---OPINION---"
    POLISHED_MARKER = "---POLISHED---"

    def __init__(self, model: Optional[str] = None):
        # 创建所有 Agent
        self.experts: Dict[str, Agent] = {}
        for rid, rname in EXPERT_ROLES:
            self.experts[rid] = Agent(name=rname, role_id=rid)
        self.chair = Agent(name=CHAIR_ROLE[1], role_id=CHAIR_ROLE[0])
        self.judge = Agent(name=JUDGE_ROLE[1], role_id=JUDGE_ROLE[0], use_tools=False)
        self.model = model

    def run(self, lesson: str, student_id: str, sample_id: str) -> Tuple[str, dict]:
        records: List[RoundRecord] = []
        current_lesson = lesson
        prev_feedback = ""
        prev_total_score: Optional[int] = None
        # 上一轮各专家的意见文本（用于互评引用）
        prev_opinions_text: Dict[str, str] = {}

        print(f"\n启动多 Expert 圆桌打磨（最多 {self.MAX_ITERATIONS} 轮）")
        print("=" * 60)

        for round_i in range(1, self.MAX_ITERATIONS + 1):
            rec = RoundRecord(round=round_i)
            rec.prev_feedback = prev_feedback
            print(f"\n▶ 第 {round_i} 轮 — 圆桌研讨\n")

            # ── Step 1: 各专家并行输出意见（线程池）──
            def run_expert(rid: str, rname: str) -> ExpertOpinion:
                expert = self.experts[rid]
                msg = f"请分析以下教案：\n\n{current_lesson}"
                if prev_feedback:
                    msg += f"\n\n上一轮评审反馈：\n{prev_feedback}"
                if prev_opinions_text:
                    refs = "\n".join(
                        f"({k} 的意见) {v[:200]}..."
                        for k, v in prev_opinions_text.items() if k != rid
                    )
                    if refs:
                        msg += f"\n\n其他专家的意见供参考：\n{refs}"
                # 线程内静默收集
                chunks = []
                for chunk in expert.chat_stream(msg):
                    chunks.append(chunk)
                full = "".join(chunks)
                thinking, opinions_text = self._parse_expert_response(full)
                return ExpertOpinion(
                    role_id=rid, name=rname,
                    thinking=thinking, opinions_text=opinions_text,
                    refers_to=f"r{round_i-1}:{list(prev_opinions_text.keys())[0]}" if prev_opinions_text and round_i > 1 else None,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(EXPERT_ROLES)) as pool:
                futures = [pool.submit(run_expert, rid, rname) for rid, rname in EXPERT_ROLES]
                for f in concurrent.futures.as_completed(futures):
                    op = f.result()
                    rec.opinions.append(op)
                    print(f"  ✓ 收到 {op.name} 意见 ({len(op.opinions_text)} 字): {op.thinking[:120]}...")
            # 保持角色顺序
            rec.opinions.sort(key=lambda o: [r[0] for r in EXPERT_ROLES].index(o.role_id))

            # ── Step 2: 主持人汇总合并 ──
            print(f"\n  [{CHAIR_ROLE[1]}] 汇总合并中...")
            chair_msg = "以下是多位专家对同一教案的修改意见，请进行冲突检测、合并，并输出打磨后的完整教案。\n\n"
            for op in rec.opinions:
                chair_msg += f"--- {op.name} ({op.role_id}) ---\n{op.opinions_text}\n\n"
            chair_msg += "\n当前教案原文：\n\n" + current_lesson

            chair_full = self._stream_and_collect(self.chair, chair_msg)

            # Chair 的输出格式：思考 → ---POLISHED--- → 教案
            if self.POLISHED_MARKER in chair_full:
                parts = chair_full.split(self.POLISHED_MARKER, 1)
                rec.chair_thinking = parts[0].strip()
                rec.polished = parts[1].strip()
            else:
                heading_match = re.search(r'\n(# .+?)\n', chair_full)
                if heading_match:
                    idx = heading_match.start()
                    rec.chair_thinking = chair_full[:idx].strip()
                    rec.polished = chair_full[idx:].strip()
                else:
                    rec.chair_thinking = chair_full
                    rec.polished = chair_full

            print(f"    ✓ 合并完成")

            # ── Step 3: 输出合并后教案 ──
            print(f"\n  合并后教案预览（前200字）:")
            print(f"    {rec.polished[:200]}...")

            # ── Step 4: Judge 评审 ──
            print(f"\n  [{JUDGE_ROLE[1]}] 评审中...")
            prev_score_note = f"\n\n上一轮总分: {prev_total_score}/100。如果本轮提升不足 2 分，请将 should_stop 设为 true。" if prev_total_score is not None else ""
            judge_prompt = f"请评审以下教案并返回 JSON：{prev_score_note}\n\n{rec.polished}"
            judge_response = self.judge.chat(judge_prompt)
            judge_data = self._parse_judge_response(judge_response)

            if judge_data.get("total_score", 0) == 0 and not judge_response:
                print(f"  评审解析失败，重试...")
                judge_response = self.judge.chat(judge_prompt)
                judge_data = self._parse_judge_response(judge_response)

            rec.judge_result = judge_data

            total = judge_data.get("total_score", 0)
            # 代码层强制的终止条件：总分 >= 85 且提升不足 2 分才停
            should_stop = judge_data.get("should_stop", False)
            if total >= 85 and prev_total_score is not None and (total - prev_total_score) < 2:
                should_stop = True
            elif total < 85:
                should_stop = False  # 未达标必须继续
            print(f"  总分: {total}/100 (上一轮: {prev_total_score if prev_total_score is not None else '-'}) | should_stop: {should_stop}")
            s = judge_data['scores']
            print(f"  评分明细: A={s['A']['score']}, B={s['B']['score']}, "
                  f"C={s['C']['score']}, D={s['D']['score']}, "
                  f"E={s['E']['score']}, F={s['F']['score']}")

            records.append(rec)

            # 更新上一轮的意见（供下一轮引用）
            prev_opinions_text = {op.role_id: op.opinions_text for op in rec.opinions}
            prev_total_score = total

            if should_stop:
                print(f"\n  ✓ 达到终止条件，打磨完成。")
                break

            current_lesson = rec.polished
            prev_feedback = judge_data.get("overall_feedback", "")

        # ── 最终输出 ──
        final_rec = records[-1]
        final_lesson = final_rec.polished
        print("\n" + "=" * 60)
        print("最终教案:\n")
        print(final_lesson)

        # ── 构建 process.json ──
        discussion = []
        modifications = []
        mod_count = 0

        for rec in records:
            discussion.extend(rec.to_discussion(rec.round))
            # 从专家意见提取 modifications
            for op in rec.opinions:
                for line in op.opinions_text.split("\n"):
                    t = line.strip()
                    if re.match(r'^[\d•\-\*]+[.、)）．]', t):
                        mod_count += 1
                        modifications.append({
                            "mod_id": f"M{mod_count:02d}",
                            "location": "全篇",
                            "before_summary": "原始教案相关部分",
                            "after_summary": re.sub(r'^[\d•\-\*]+[.、)）．\s]*', '', t)[:100],
                            "source_role": op.role_id,
                            "rationale": t[:200],
                        })
            # 从 Judge 评分提取
            if rec.judge_result:
                for dim_key in 'ABCDEF':
                    suggestion = rec.judge_result['scores'][dim_key].get('suggestions', '')
                    if suggestion:
                        mod_count += 1
                        modifications.append({
                            "mod_id": f"M{mod_count:02d}",
                            "location": f"维度 {dim_key}",
                            "before_summary": f"第{rec.round}轮评审指出的问题",
                            "after_summary": suggestion[:100],
                            "source_role": JUDGE_ROLE[0],
                            "rationale": suggestion[:200],
                        })

        if not modifications:
            modifications = [{
                "mod_id": "M01", "location": "全篇",
                "before_summary": "原始教案", "after_summary": f"第{len(records)}轮打磨后教案",
                "source_role": CHAIR_ROLE[0],
                "rationale": f"经过{len(records)}轮多 Expert 圆桌打磨",
            }]

        all_roles = [
            {"role_id": rid, "name": rname, "expertise": "教学设计专家"}
            for rid, rname in EXPERT_ROLES
        ] + [
            {"role_id": CHAIR_ROLE[0], "name": CHAIR_ROLE[1], "expertise": "主持与冲突合并"},
            {"role_id": JUDGE_ROLE[0], "name": JUDGE_ROLE[1], "expertise": "教案质量评审（附录A量规）"},
        ]

        process = {
            "meta": {
                "student_id": student_id, "sample_id": sample_id,
                "timestamp": datetime.datetime.now().astimezone().isoformat(),
            },
            "roles": all_roles,
            "discussion": discussion,
            "modifications": modifications,
        }

        return final_lesson, process

    # ── 辅助方法 ──

    def _stream_and_collect(self, agent: Agent, message: str) -> str:
        """流式收集完整响应并展示思考部分。"""
        chunks = []
        for chunk in agent.chat_stream(message):
            chunks.append(chunk)
        full = "".join(chunks)
        # 只打印思考部分（---OPINION--- 或 ---POLISHED--- 之前）
        for marker in [self.OPINION_MARKER, self.POLISHED_MARKER]:
            if marker in full:
                print(full.split(marker)[0].strip())
                return full
        print(full[:300] + "...")
        return full

    def _parse_expert_response(self, full: str) -> Tuple[str, str]:
        """从专家响应中切分 thinking 和 opinions_text。"""
        if self.OPINION_MARKER in full:
            parts = full.split(self.OPINION_MARKER, 1)
            return parts[0].strip(), parts[1].strip()
        # 没有标记：前面是思考，后面是意见
        lines = full.strip().split("\n")
        mid = len(lines) // 2
        return "\n".join(lines[:mid]).strip(), "\n".join(lines[mid:]).strip()

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
            "total_score": 0, "overall_feedback": reason, "should_stop": False,
        }