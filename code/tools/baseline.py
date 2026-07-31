#!/usr/bin/env python3
"""
baseline.py — 单 Agent 基线版（与多 Agent 对照实验）

用法:
    python code/tools/baseline.py --lesson code/examples/inputs/demo001_MATH01.md \\
                                  --profile code/examples/profile_minimal.yaml \\
                                  --out outputs_baseline/

对照: 同一输入，单 Agent 一把改写 vs 多 Agent 圆桌打磨，对比分数差异。
"""
import sys, os, json, argparse, datetime, re, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv
load_dotenv()
from code.agent.core import Agent
from code.config import config

def parse_args():
    p = argparse.ArgumentParser(description="单 Agent 基线版")
    p.add_argument("--lesson", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    return p.parse_args()

def main():
    args = parse_args()
    lesson = open(args.lesson, "r", encoding="utf-8").read()
    sample_id = os.path.splitext(os.path.basename(args.lesson))[0]
    m = re.match(r'^[A-Za-z0-9]+_(.*)$', sample_id)
    if m: sample_id = m.group(1)
    student_id = "demo001"

    os.makedirs(args.out, exist_ok=True)

    # ── Step 1: 单 Agent 改写 ──
    print("▶ 单 Agent 改写中...")
    t0 = time.monotonic()
    agent = Agent(name="教案打磨专家", role_id="r_expert", use_tools=False)
    prompt = (
        "你是一位资深的教案打磨专家。请根据以下教案，结合高质量教学设计方法论，"
        "对教案进行全面优化打磨。保留原始教案的核心知识点和结构，"
        "在教学目标行为化、活动可执行性、内容准确性、一致性、素养导向等方面进行提升。"
        "直接输出打磨后的完整教案 Markdown。\n\n"
        f"{lesson}"
    )
    polished = agent.chat(prompt)
    elapsed = time.monotonic() - t0
    print(f"  用时 {elapsed:.1f}s\n")

    # ── Step 2: Judge 评审 ──
    print("▶ Judge 评审中...")
    t0 = time.monotonic()
    judge = Agent(name="评审专家", role_id="r_judge", use_tools=False)
    judge_prompt = f"请评审以下教案并返回 JSON：\n\n{polished}"
    judge_response = judge.chat(judge_prompt)
    try:
        judge_data = json.loads(judge_response)
    except:
        match = re.search(r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```', judge_response, re.DOTALL)
        judge_data = json.loads(match.group(1)) if match else {"total_score": 0, "scores": {}}
    elapsed = time.monotonic() - t0
    total = judge_data.get("total_score", 0)
    s = judge_data.get("scores", {})
    print(f"  用时 {elapsed:.1f}s")
    print(f"  总分: {total}/100")
    print(f"  评分明细: A={s.get('A',{}).get('score',0)}, B={s.get('B',{}).get('score',0)}, "
          f"C={s.get('C',{}).get('score',0)}, D={s.get('D',{}).get('score',0)}, "
          f"E={s.get('E',{}).get('score',0)}, F={s.get('F',{}).get('score',0)}")
    print(f"  评语: {judge_data.get('overall_feedback', '')[:200]}\n")

    # ── 输出 ──
    prefix = f"{student_id}_{sample_id}"
    md_path = os.path.join(args.out, f"{prefix}_polished.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(polished)

    process = {
        "meta": {
            "student_id": student_id, "sample_id": sample_id,
            "timestamp": datetime.datetime.now().astimezone().isoformat(),
            "mode": "baseline_single_agent",
            "total_score": total,
        },
        "roles": [
            {"role_id": "r_expert", "name": "教案打磨专家", "expertise": "教案打磨与改写"},
            {"role_id": "r_judge", "name": "评审专家", "expertise": "教案质量评审"},
        ],
        "discussion": [],
        "modifications": [],
    }
    js_path = os.path.join(args.out, f"{prefix}_process.json")
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(process, f, ensure_ascii=False, indent=2)

    print(f"  → {md_path}")
    print(f"  → {js_path}")

if __name__ == "__main__":
    main()