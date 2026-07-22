import json, datetime, re, concurrent.futures, time
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
    thinking: str
    opinions_text: str
    elapsed: float = 0.0
    temperature: float = 0.7
    refers_to: Optional[str] = None


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
    MAX_ROLLBACKS = 2      # 累计回滚次数上限
    OPINION_MARKER = "---OPINION---"
    POLISHED_MARKER = "---POLISHED---"

    def __init__(self, model: Optional[str] = None):
        # 创建所有 Agent
        # 专家：核心技能预注入 + 其余技能按需加载
        self.experts: Dict[str, Agent] = {}
        for rid, rname in EXPERT_ROLES:
            self.experts[rid] = Agent(
                name=rname, role_id=rid,
                preload_skills=["responsibility", "rubric_mapping", "knowledge_boundary"]
            )
        # 主持人 + Judge：全量注入，不做按需加载
        self.chair = Agent(
            name=CHAIR_ROLE[1], role_id=CHAIR_ROLE[0], use_tools=False,
            preload_skills=["responsibility", "knowledge_boundary", "conflict_resolution"]
        )
        self.judge = Agent(
            name=JUDGE_ROLE[1], role_id=JUDGE_ROLE[0], use_tools=False
        )
        self.model = model

    def run(self, lesson: str, student_id: str, sample_id: str) -> Tuple[str, dict]:
        records: List[RoundRecord] = []
        current_lesson = lesson
        prev_feedback = ""
        prev_total_score: Optional[int] = None
        prev_opinions_text: Dict[str, str] = {}
        run_start = time.monotonic()

        # ── 最佳版本追踪变量 ──
        best_lesson = lesson
        best_score = 0
        best_round = 0
        best_opinions_text: Dict[str, str] = {}
        best_feedback = ""
        rollback_count = 0
        is_rollback_recovery = False

        print(f"\n启动多 Expert 圆桌打磨（最多 {self.MAX_ITERATIONS} 轮）")
        print("=" * 60)

        for round_i in range(1, self.MAX_ITERATIONS + 1):
            # 每轮开始时保存各 Agent 上下文快照
            for agent in [*self.experts.values(), self.chair, self.judge]:
                agent.context.save_checkpoint()

            rec = RoundRecord(round=round_i)
            rec.prev_feedback = prev_feedback
            print(f"\n▶ 第 {round_i} 轮 — 圆桌研讨\n")

            # ── Step 1: 各专家并行输出意见（线程池，失败自动重试）──
            def run_expert(rid: str, rname: str) -> ExpertOpinion:
                t0 = time.monotonic()
                expert = self.experts[rid]
                msg = f"请分析以下教案：\n\n{current_lesson}"
                if prev_feedback:
                    msg += f"\n\n上一轮评审反馈：\n{prev_feedback}"
                if prev_total_score is not None:
                    msg += f"\n\n上一轮总分: {prev_total_score}/100。请根据当前分数调整修改力度：高分（≥85）建议只做微调，低分（<85）可大胆提出结构性改进。"
                if prev_opinions_text:
                    refs = "\n".join(
                        f"({k} 的意见) {v[:200]}..."
                        for k, v in prev_opinions_text.items() if k != rid
                    )
                    if refs:
                        msg += f"\n\n其他专家的意见供参考：\n{refs}"
                # 非流式调用（支持 load_skill 工具调用）
                temp = self._compute_temperature(prev_total_score, recovery=is_rollback_recovery)
                if is_rollback_recovery:
                    msg += self._rollback_notice()
                last_err = None
                for attempt in range(3):
                    try:
                        full = expert.chat(msg, temperature=temp)
                        thinking, opinions_text = self._parse_expert_response(full)
                        elapsed = time.monotonic() - t0
                        return ExpertOpinion(
                            role_id=rid, name=rname,
                            thinking=thinking, opinions_text=opinions_text,
                            elapsed=elapsed, temperature=temp,
                            refers_to=f"r{round_i-1}:{list(prev_opinions_text.keys())[0]}" if prev_opinions_text and round_i > 1 else None,
                        )
                    except Exception as e:
                        last_err = e
                        print(f"  [{rname}] 第{attempt+1}次调用失败: {type(e).__name__}")
                        if attempt < 2:
                            time.sleep(2 ** attempt)
                # 3 次都失败，输出占位意见（符合 ---OPINION--- 解析格式）
                print(f"  [{rname}] 3次重试均失败，本轮跳过")
                return ExpertOpinion(
                    role_id=rid, name=rname,
                    thinking=f"（API 调用 3 次重试均失败: {last_err}）",
                    opinions_text=f"---OPINION---\n1. 【无法提供意见】该专家本轮因 API 调用失败无法输出修改建议",
                    elapsed=time.monotonic() - t0,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=len(EXPERT_ROLES)) as pool:
                futures = [pool.submit(run_expert, rid, rname) for rid, rname in EXPERT_ROLES]
                for f in concurrent.futures.as_completed(futures):
                    op = f.result()
                    rec.opinions.append(op)
                    print(f"╔══ {op.name} ═══ {datetime.datetime.now().strftime('%H:%M:%S')} （用时 {op.elapsed:.1f}s, temperature={op.temperature}）")
                    print(op.thinking)
                    if op.opinions_text.strip():
                        print(f"── 修改意见 ──")
                        print(op.opinions_text)
                    else:
                        print(f"── 无修改意见 ──")
            # 保持角色顺序
            rec.opinions.sort(key=lambda o: [r[0] for r in EXPERT_ROLES].index(o.role_id))

            # ── Step 2: 主持人汇总合并 ──
            print(f"\n  [{CHAIR_ROLE[1]}] 汇总合并中...")
            t0 = time.monotonic()
            # Chair 的 temperature 也动态调整
            chair_temp = self._compute_temperature(prev_total_score, recovery=is_rollback_recovery)
            chair_msg = "以下是多位专家对同一教案的修改意见，请进行冲突检测、合并，并输出打磨后的完整教案。\n\n"
            for op in rec.opinions:
                chair_msg += f"--- {op.name} ({op.role_id}) ---\n{op.opinions_text}\n\n"
            if prev_total_score is not None:
                chair_msg += f"上一轮总分: {prev_total_score}/100。高分（≥85）建议保守微调，低分（<85）可大胆重构。\n"
            if prev_feedback:
                chair_msg += f"上一轮评审反馈: {prev_feedback}\n\n"
            if is_rollback_recovery:
                chair_msg += self._rollback_notice()
            chair_msg += "\n当前教案原文：\n\n" + current_lesson

            chair_full = self.chair.chat(chair_msg, temperature=chair_temp)
            print(f"  [{CHAIR_ROLE[1]}] 用时 {time.monotonic() - t0:.1f}s (temperature={chair_temp})")

            # Chair 的输出格式：思考 → ---POLISHED--- → 教案
            if self.POLISHED_MARKER in chair_full:
                parts = chair_full.split(self.POLISHED_MARKER, 1)
                rec.chair_thinking = parts[0].strip()
                rec.polished = parts[1].strip()
                print(f"╔══ 主持人冲突分析 ═══")
                print(rec.chair_thinking)
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

            # ── Step 4: Judge 评审（失败重试 3 次）──
            print(f"\n  [{JUDGE_ROLE[1]}] 评审中...")
            t0 = time.monotonic()
            prev_score_note = f"\n\n上一轮总分: {prev_total_score}/100。如果本轮提升不足 2 分，请将 should_stop 设为 true。" if prev_total_score is not None else ""
            judge_prompt = f"请评审以下教案并返回 JSON：{prev_score_note}\n\n{rec.polished}"
            judge_data = None
            judge_last_err = ""
            for attempt in range(3):
                judge_response = self.judge.chat(judge_prompt)
                judge_data = self._parse_judge_response(judge_response)
                if judge_data.get("total_score", 0) != 0:
                    break
                judge_last_err = (judge_response or "")[:300]
                if attempt < 2:
                    print(f"  [DEBUG] Judge 返回 0 分（第{attempt+1}次），原始响应前300字: {judge_last_err}")
                    print(f"  重试...")
                    time.sleep(1)
            if judge_data is None or judge_data.get("total_score", 0) == 0:
                judge_data = self._fallback_judge_result(f"Judge 3 次重试均返回 0 分: {judge_last_err}")

            rec.judge_result = judge_data

            total = judge_data.get("total_score", 0)
            print(f"  评审用时 {time.monotonic() - t0:.1f}s")
            # 代码层强制的终止条件
            should_stop = judge_data.get("should_stop", False)
            if prev_total_score is None:
                should_stop = False  # 首轮不停
            elif total >= 85 and (total - prev_total_score) < 2:
                should_stop = True   # 达标且涨不动
            elif total < 85:
                should_stop = False  # 未达标必须继续
            print(f"  总分: {total}/100 (上一轮: {prev_total_score if prev_total_score is not None else '-'}) | should_stop: {should_stop}")
            s = judge_data['scores']
            print(f"  评分明细: A={s['A']['score']}, B={s['B']['score']}, "
                  f"C={s['C']['score']}, D={s['D']['score']}, "
                  f"E={s['E']['score']}, F={s['F']['score']}")

            records.append(rec)

            # ── 最佳版本追踪 & 回滚判断 ──
            is_rollback_recovery = False

            if best_score == 0:
                best_lesson = rec.polished
                best_score = total
                best_round = round_i
                best_opinions_text = {op.role_id: op.opinions_text for op in rec.opinions}
                best_feedback = judge_data.get("overall_feedback", "")

            elif total > best_score:
                best_lesson = rec.polished
                best_score = total
                best_round = round_i
                best_opinions_text = {op.role_id: op.opinions_text for op in rec.opinions}
                best_feedback = judge_data.get("overall_feedback", "")

            elif total < best_score:
                rollback_count += 1
                if rollback_count > self.MAX_ROLLBACKS:
                    print(f"  ⚠ 回滚 {self.MAX_ROLLBACKS} 次已达上限，强制终止。取第 {best_round} 轮（{best_score} 分）。")
                    rec.polished = best_lesson
                    break

                print(f"  ⚠ 第 {round_i} 轮评分 {total} < 历史最高 {best_score}（第 {best_round} 轮）→ 回滚")
                print(f"    教案版本 → 第 {best_round} 轮, Agent 上下文 → 本轮开始前。回滚 {rollback_count}/{self.MAX_ROLLBACKS}")

                current_lesson = best_lesson
                for agent in [*self.experts.values(), self.chair, self.judge]:
                    agent.context.restore_checkpoint()

                prev_total_score = best_score
                prev_feedback = best_feedback
                prev_opinions_text = best_opinions_text
                is_rollback_recovery = True
                rec.judge_result["_rollback"] = {
                    "triggered": True, "rollback_count": rollback_count,
                    "score_dropped_to": total, "restored_to_round": best_round,
                    "restored_score": best_score,
                }
                continue

            # 非回滚轮的正常推进
            if should_stop:
                print(f"\n  ✓ 达到终止条件，打磨完成。")
                break

            current_lesson = rec.polished
            prev_feedback = judge_data.get("overall_feedback", "")
            prev_opinions_text = {op.role_id: op.opinions_text for op in rec.opinions}
            prev_total_score = total

        # ── 最终输出（取历史最佳版本）──
        final_lesson = best_lesson
        run_elapsed = time.monotonic() - run_start
        print("\n" + "=" * 60)
        print(f"最终教案 (总耗时 {run_elapsed:.0f}s = {run_elapsed/60:.1f} 分钟):\n")
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
                "best_score": best_score,
                "best_round": best_round,
                "total_rollbacks": rollback_count,
            },
            "roles": all_roles,
            "discussion": discussion,
            "modifications": modifications,
        }

        return final_lesson, process

    # ── 辅助方法 ──

    @staticmethod
    def _compute_temperature(score: Optional[int], recovery: bool = False) -> float:
        """根据上一轮总分计算 temperature。recovery=True 时降一档。"""
        if score is None:
            base = 0.7
        elif score >= 90:
            base = 0.3
        elif score >= 80:
            base = 0.5
        elif score >= 70:
            base = 0.7
        else:
            base = 0.9
        if recovery:
            downgrade = {0.3: 0.15, 0.5: 0.3, 0.7: 0.5, 0.9: 0.7}
            return downgrade.get(base, base)
        return base

    def _rollback_notice(self) -> str:
        return (
            f"\n\n⚠️ 上一轮修改已被回滚：评分从 {self.best_score} 降至本轮分数，"
            f"教案已恢复至第 {self.best_round} 轮的最高分版本。\n"
            f"本轮请仅针对 Judge 反馈中的低分维度做小步幅修改，避免大范围重构。"
            f"（回滚 {self.rollback_count}/{self.MAX_ROLLBACKS}）"
        )

    def _parse_expert_response(self, full: str) -> Tuple[str, str]:
        """从专家响应中切分 thinking 和 opinions_text。"""
        if self.OPINION_MARKER in full:
            parts = full.split(self.OPINION_MARKER, 1)
            return parts[0].strip(), parts[1].strip()
        # 没有标记：前面是思考，后面是意见
        lines = full.strip().split("\n")
        mid = len(lines) // 2
        return "\n".join(lines[:mid]).strip(), "\n".join(lines[mid:]).strip()

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        """从文本中提取第一个完整 JSON 对象（用花括号计数，处理嵌套）。"""
        start = text.find('{')
        if start == -1:
            return None
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return None

    def _parse_judge_response(self, response: Optional[str]) -> dict:
        if not response:
            return self._fallback_judge_result("评审响应为空")
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass
        # 先尝试从 ```json 代码块中提取
        match = re.search(r'```(?:json)?\s*\n?(.+?)\n?```', response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        # 用花括号计数提取完整 JSON（处理嵌套）
        extracted = self._extract_json(response)
        if extracted:
            try:
                return json.loads(extracted)
            except json.JSONDecodeError:
                pass
        return self._fallback_judge_result("评审 JSON 解析失败")

    def _fallback_judge_result(self, reason: str) -> dict:
        max_scores = {"A": 10, "B": 15, "C": 20, "D": 15, "E": 10, "F": 30}
        return {
            "scores": {k: {"score": 0, "max": max_scores[k], "evidence": "", "suggestions": ""} for k in "ABCDEF"},
            "total_score": 0, "overall_feedback": reason, "should_stop": False,
        }