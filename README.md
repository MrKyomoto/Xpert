# PBL 教案圆桌磨课系统

基于三类领域专家、主持人和独立评审的多轮闭环教案磨课系统，兼容附录 D 的提交与运行契约。

## Pipeline

1. 从学情 YAML、样本编号和教案标题识别课型、学科与学段。
2. 三位专家并行审阅：
   - `r_literacy`：F 素养导向与 D5 学情适配；
   - `r_subject`：C 内容准确性与 E 语言逻辑性；
   - `r_learner`：A/B/D 结构、任务链与一致性。
3. `r_chair` 对全部结构化提案逐条接受、合并或拒绝，记录冲突并生成完整教案。
4. `r_judge` 按 A—F 六维量规独立评分。
5. 首轮强制继续；后续仅当总分不低于 85 且相对上一轮提升小于 2 分时停止，最多 5 轮。

每次运行都会重新创建 Agent，避免对话或 PBL 案例知识跨任务泄漏。常规课只加载职责、边界和量规卡；PBL 课才额外加载脱敏的案例方法卡。

## Skills

```text
code/skills/
├── manager.py
├── r_literacy/
│   ├── _system.md
│   ├── responsibility.md
│   ├── knowledge_boundary.md
│   ├── rubric_mapping.md
│   └── case_knowledge.md
├── r_subject/       # 同上四类知识卡
├── r_learner/       # 同上四类知识卡
├── r_chair/
│   ├── _system.md
│   ├── responsibility.md
│   ├── knowledge_boundary.md
│   └── conflict_resolution.md
└── r_judge/
    ├── _system.md
    ├── responsibility.md
    ├── knowledge_boundary.md
    ├── evaluation_rubric.md
    └── scoring_rules.md
```

`_system.md` 只定义身份、任务、原则和输出协议。其他 Markdown 卡由编排器按角色和课型显式加载；文件名、SHA-256 摘要、加载原因、实际引用和跨角色引用均写入 `process.json`。

## 安装与配置

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 中配置 `API_KEY` 或 `OPENAI_API_KEY`。可选配置包括 `API_BASE`、`MODEL`、`MAX_TOKENS`、`API_TIMEOUT`、`API_RETRIES`、`MAX_CONTEXT_TOKENS` 和 `COMPRESSION_THRESHOLD`。

## 运行

```bash
python code/run.py \
  --lesson code/examples/inputs/demo001_MATH01.md \
  --profile code/examples/profile_minimal.yaml \
  --out code/outputs_practice
```

输入教案应为 UTF-8 Markdown；学情文件应为顶层对象的 YAML，并建议提供 `student_id`、`subject`、`grade` 和 `lesson_type`。`lesson_type` 可用 `regular` 或 `pbl`，显式值优先于标题启发式识别。

运行产生：

- `{student_id}_{sample_id}_polished.md`：完整终稿；
- `{student_id}_{sample_id}_process.json`：角色、讨论、提案裁决、修改、冲突、Skill 清单和评分历史；
- `code/logs/{timestamp}.log`：stdout 与 stderr 的完整日志。

日志使用微秒时间戳和排他创建，不会截断已有日志。运行结束会自动调用附录 D 校验脚本，校验失败时返回非零退出码。

## 测试

```bash
python -m pytest -q
python -m compileall -q code tests
python "PBL多智能体实践课题/附录/附录D_提交与运行契约/validate_submission.py" \
  "PBL多智能体实践课题/附录/附录D_提交与运行契约/_selftest/sample_ok"
```

测试覆盖按需加载与 PBL 隔离、示例课型识别、YAML 解析、停止规则、结构化意见与裁决解析、日志防覆盖和模拟圆桌端到端流程。
