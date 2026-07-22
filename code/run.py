#!/usr/bin/env python3
"""
run.py — 统一 CLI 入口 (Appendix D 运行契约)
Usage: python run.py --lesson <path> --profile <path> --out <dir>
Exit code: 0 = success, 非零 = fail
"""
import sys, os, json, argparse, datetime, re, subprocess, time
if sys.stdout.encoding and sys.stdout.encoding.upper() in ('GBK', 'GB2312', 'CP936'):
    sys.stdout.reconfigure(errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()
from code.engine.orchestrator import Orchestrator
from code.agent.core import Agent
from code.tools.logger import capture_output

def parse_args():
    p = argparse.ArgumentParser(description="PBL 教案磨课系统")
    p.add_argument("--lesson", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--judge", action="store_true", help="仅评审模式：直接打分不打磨")
    return p.parse_args()

def read_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def extract_sample_id(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    for suf in ["_polished", "_process"]:
        if stem.endswith(suf):
            stem = stem[:-len(suf)]
    m = re.match(r'^[A-Za-z0-9]+_(.*)$', stem)
    if m: stem = m.group(1)
    safe = re.sub(r'[^A-Za-z0-9-]', '', stem)
    return safe or "lesson"

def parse_student_id(profile_text):
    for line in profile_text.splitlines():
        if line.strip().startswith("student_id:"):
            val = line.split(":", 1)[1].strip().strip('"').strip("'")
            if val and re.match(r'^[A-Za-z0-9]+$', val):
                return val
    return "unknown"

def extract_filename_student_id(path):
    """从文件名提取 student_id 前缀（{student_id}_{sample_id}.md）。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r'^([A-Za-z0-9]+)_.*$', stem)
    return m.group(1) if m else None

def write_outputs(out_dir, student_id, sample_id, polished, process):
    os.makedirs(out_dir, exist_ok=True)
    prefix = f"{student_id}_{sample_id}"
    md = os.path.join(out_dir, f"{prefix}_polished.md")
    with open(md, "w", encoding="utf-8") as f: f.write(polished)
    js = os.path.join(out_dir, f"{prefix}_process.json")
    with open(js, "w", encoding="utf-8") as f: json.dump(process, f, ensure_ascii=False, indent=2)
    print(f"\n  → {md}\n  → {js}")

def write_judge_output(out_dir, sample_id, judge_data):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"judge_{sample_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(judge_data, f, ensure_ascii=False, indent=2)
    print(f"\n  → {path}")

def main():
    with capture_output() as log_path:
        rc = _main()
    print(f"\nDone. Exit code {rc}")
    print(f"日志已保存: {log_path}")
    sys.exit(rc)


def _main():
    args = parse_args()
    if not os.getenv("API_KEY") and not os.getenv("OPENAI_API_KEY"):
        print("FATAL: API_KEY not set", file=sys.stderr); return 1
    if not os.path.exists(args.lesson): print("FATAL: lesson not found", file=sys.stderr); return 1
    if not os.path.exists(args.profile): print("FATAL: profile not found", file=sys.stderr); return 1

    lesson = read_file(args.lesson)
    profile_text = read_file(args.profile)
    sample_id = extract_sample_id(args.lesson)
    student_id = parse_student_id(profile_text)
    filename_sid = extract_filename_student_id(args.lesson)
    if filename_sid and student_id != "unknown" and filename_sid != student_id:
        print(f"FATAL: 文件名学号 '{filename_sid}' 与 YAML 内 student_id '{student_id}' 不一致", file=sys.stderr)
        return 1

    # ── 仅评审模式 ──
    if getattr(args, 'judge', False):
        print(f"\n仅评审模式: {os.path.basename(args.lesson)}")
        print("=" * 50)
        judge = Agent(name="评审专家", role_id="r_judge", use_tools=False)
        judge_prompt = f"请评审以下教案并返回 JSON：\n\n{lesson}"
        t0 = time.monotonic()
        judge_response = judge.chat(judge_prompt)
        import json as _json
        try:
            data = _json.loads(judge_response)
        except:
            match = re.search(r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```', judge_response, re.DOTALL)
            data = _json.loads(match.group(1)) if match else {"total_score": 0}
        elapsed = time.monotonic() - t0
        s = data.get("scores", {})
        total = data.get("total_score", 0)
        print(f"\n评分用时: {elapsed:.1f}s")
        print(f"总分: {total}/100")
        print(f"评分明细: A={s.get('A',{}).get('score',0)}, B={s.get('B',{}).get('score',0)}, "
              f"C={s.get('C',{}).get('score',0)}, D={s.get('D',{}).get('score',0)}, "
              f"E={s.get('E',{}).get('score',0)}, F={s.get('F',{}).get('score',0)}")
        print(f"评语: {data.get('overall_feedback', '')}")
        # 也写一份到输出目录
        write_judge_output(args.out, sample_id, data)
        return 0

    # ── Expert-Judge 多轮打磨 ──
    orchestrator = Orchestrator()
    polished, process = orchestrator.run(lesson, student_id, sample_id)

    write_outputs(args.out, student_id, sample_id, polished, process)

    # ── 自动运行附录 D 自查脚本 ──
    validate_script = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "PBL多智能体实践课题", "附录", "附录D_提交与运行契约", "validate_submission.py"
    )
    if os.path.exists(validate_script):
        print("\n" + "=" * 50)
        print("运行格式自检...")
        result = subprocess.run(
            [sys.executable, validate_script, os.path.abspath(args.out)],
            capture_output=True, encoding="utf-8", errors="replace"
        )
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        if result.returncode != 0:
            return 1

    return 0

if __name__ == "__main__":
    main()