#!/usr/bin/env python3
"""
run.py — 统一 CLI 入口 (Appendix D 运行契约)
Usage: python run.py --lesson <path> --profile <path> --out <dir>
Exit code: 0 = success, 非零 = fail
"""
import argparse
import json
import os
import re
import subprocess
import sys

import yaml
if sys.stdout.encoding and sys.stdout.encoding.upper() in ('GBK', 'GB2312', 'CP936'):
    sys.stdout.reconfigure(errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()
from code.engine.orchestrator import Orchestrator
from code.tools.logger import capture_output

def parse_args():
    p = argparse.ArgumentParser(description="PBL 教案磨课系统 — Expert-Judge 闭环")
    p.add_argument("--lesson", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--out", required=True)
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
    profile = parse_profile(profile_text)
    value = profile.get("student_id")
    if value is not None:
        val = str(value).strip()
        if val and re.fullmatch(r"[A-Za-z0-9]+", val):
            return val
    return "unknown"


def parse_profile(profile_text):
    try:
        data = yaml.safe_load(profile_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"profile YAML 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("profile YAML 顶层必须是对象")
    return data

def write_outputs(out_dir, student_id, sample_id, polished, process):
    os.makedirs(out_dir, exist_ok=True)
    prefix = f"{student_id}_{sample_id}"
    md = os.path.join(out_dir, f"{prefix}_polished.md")
    with open(md, "w", encoding="utf-8") as f: f.write(polished)
    js = os.path.join(out_dir, f"{prefix}_process.json")
    with open(js, "w", encoding="utf-8") as f: json.dump(process, f, ensure_ascii=False, indent=2)
    print(f"\n  → {md}\n  → {js}")

def main():
    rc = 1
    with capture_output() as log_path:
        try:
            rc = _main()
        except Exception as exc:
            print(f"FATAL: {exc}", file=sys.stderr)
            rc = 1
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
    profile = parse_profile(profile_text)
    sample_id = extract_sample_id(args.lesson)
    student_id = parse_student_id(profile_text)

    # ── Expert-Judge 多轮打磨 ──
    orchestrator = Orchestrator()
    polished, process = orchestrator.run(
        lesson,
        student_id,
        sample_id,
        profile=profile,
    )

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
