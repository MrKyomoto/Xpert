#!/usr/bin/env python3
"""
run.py — 统一 CLI 入口 (Appendix D 运行契约)
Usage: python run.py --lesson <path> --profile <path> --out <dir>
Exit code: 0 = success, 非零 = fail
"""
import sys, os, json, argparse, traceback, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()
from code.agent.core import Agent

def parse_args():
    p = argparse.ArgumentParser(description="PBL 教案磨课系统 — 单 Agent 基线")
    p.add_argument("--lesson", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    return p.parse_args()

def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def extract_id(path):
    name = os.path.basename(path)
    for s in ["_polished.md", "_process.json", ".md", ".json", ".yaml", ".yml"]:
        if name.endswith(s): return name[:-len(s)]
    return name

def write_outputs(out_dir, sample_id, polished, process):
    os.makedirs(out_dir, exist_ok=True)
    md = os.path.join(out_dir, f"{sample_id}_polished.md")
    with open(md, "w", encoding="utf-8") as f: f.write(polished)
    js = os.path.join(out_dir, f"{sample_id}_process.json")
    with open(js, "w", encoding="utf-8") as f: json.dump(process, f, ensure_ascii=False, indent=2)
    print(f"  → {md}\n  → {js}")

def main():
    args = parse_args()
    if not os.getenv("API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("FATAL: API_KEY not set", file=sys.stderr); sys.exit(1)
    if not os.path.exists(args.lesson): print("FATAL: lesson not found", file=sys.stderr); sys.exit(1)
    if not os.path.exists(args.profile): print("FATAL: profile not found", file=sys.stderr); sys.exit(1)

    sample_id = extract_id(args.lesson)
    lesson = read_file(args.lesson)
    profile = read_file(args.profile)  # minimal: {"student_id": "..."}

    # ── 单 Agent 基线 ──
    agent = Agent(name="教学设计专家", role_prompt="你是一位资深的教学设计专家，精通教学设计评价与打磨。请审阅教案，输出分析及打磨后的完整教案 Markdown。")
    try:
        response = agent.chat(f"请审阅并打磨以下教案：\n\n{lesson}")
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr); traceback.print_exc(file=sys.stderr); sys.exit(1)

    parts = response.split("---", 1)
    analysis = parts[0].strip()
    polished = parts[1].strip() if len(parts) > 1 else response

    process = {
        "meta": {
            "student_id": extract_id(args.profile),
            "sample_id": sample_id,
            "timestamp": datetime.datetime.now().astimezone().isoformat()
        },
        "roles": [{"role_id": "r_design", "name": "教学设计专家", "expertise": "教学设计评价与打磨"}],
        "discussion": [{"round": 1, "role_id": "r_design", "content": analysis, "refers_to": None}],
        "modifications": [{
            "mod_id": "M01", "location": "全篇",
            "before_summary": "原始教案", "after_summary": "打磨后教案",
            "source_role": "r_design", "rationale": analysis[:200] + "..."
        }]
    }

    write_outputs(args.out, sample_id, polished, process)
    sys.exit(0)

if __name__ == "__main__":
    main()