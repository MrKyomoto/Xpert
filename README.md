# 教案智能磨课系统 —— 多智能体圆桌研讨

基于 **3 位差异化专家 + 主持人 + Judge** 多轮迭代架构的教案智能打磨系统。符合附录 D 提交与运行契约。

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv venv
# Windows
source venv/Scripts/activate
# Linux/macOS
# source venv/bin/activate
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env        # 根据自己的实际 api(走的是openai格式) 和 模型填入 API_KEY 以及 MODEL

# 3. 打磨模式
python code/run.py --lesson code/examples/inputs/demo001_MATH01.md --profile code/examples/profile_minimal.yaml --out outputs/

# 4. 仅评审模式（直接打分不打磨）
python code/run.py --lesson code/examples/inputs/demo001_MATH01.md --profile code/examples/profile_minimal.yaml --out outputs/

# 3. 仅评审模式（直接打分不打磨）
python code/run.py --lesson code/examples/inputs/demo001_MATH01.md --profile code/examples/profile_minimal.yaml --out outputs/ --judge

# 4. 生成可视化报告
python code/tools/viz.py outputs/demo001_MATH01_process.json
```

### ⚠️ 关于 profile（学情描述）文件

本系统当前版本**未实现个性化学情适配**，profile YAML 中仅 `student_id` 字段被实际使用（用于文件名校验）。其他字段（如学情描述、班级信息等）已预留但暂不参与磨课逻辑。

```yaml
student_id: "demo001"   # 唯一生效字段，须与教案文件名前缀一致
# 其余字段预留，当前版本不参与处理
```

---

## 项目结构

```
.
├── .gitignore
├── .env.example
├── requirements.txt
├── 测试命令.md
├── code/
│   ├── run.py                    # 统一 CLI 入口（--judge 仅评审模式）
│   ├── config.py                 # 配置（MAX_TOKENS=16000）
│   ├── agent/core.py             # Agent 基类（非流式 chat + load_skill）
│   ├── engine/orchestrator.py    # 圆桌调度器（并行 + 重试 + 温度控制）
│   ├── memory/context.py         # 上下文管理（tiktoken 压缩）
│   ├── skills/                   # 技能知识包（.md，不硬编码）
│   │   ├── manager.py            # 技能管理器（索引 + 按需加载）
│   │   ├── r_literacy/           # 素养导向教研员
│   │   ├── r_subject/            # 学科内容专家
│   │   ├── r_learner/            # 学情适配专家
│   │   ├── r_chair/              # 主持人
│   │   └── r_judge/              # 评审专家（含 evaluation_rubric）
│   ├── tools/
│   │   ├── registry.py           # 工具注册表
│   │   ├── logger.py             # 日志捕获（Tee）
│   │   └── viz.py                # process.json → HTML 可视化
│   └── examples/inputs/          # 示例教案（16 份）
├── outputs_practice/             # 磨课输出（公开练习三元组产物）
├── report/                       # 技术报告
├── ai_collab/                    # AI 协作记录
└── logs/                         # 运行日志
```

## 角色

| 角色 | role_id | 职责 |
|------|---------|------|
| 素养导向教研员 | r_literacy | 评价 F 维度（素养导向性） |
| 学科内容专家 | r_subject | 评价 C/E 维度（准确性与逻辑性） |
| 学情适配专家 | r_learner | 评价 A/B/D 维度（结构/内容/一致性）|
| 主持人 | r_chair | 冲突检测、仲裁、合并教案 |
| 评审专家 | r_judge | 六维度评分、决定 should_stop |

## 特点

- **多线程并行**：3 位专家通过 ThreadPoolExecutor 并行调用
- **动态 temperature**：根据上一轮总分自动调整（≥90 用 0.3，<70 用 0.9）
- **3 次重试**：专家和 Judge 失败时自动重试，不崩流程
- **Skill 系统**：prompt 全外置 .md，支持预注入 + 按需加载 + load_skill
- **最佳版本回滚**：每轮跟踪历史最高分，分数下降时自动回滚到最佳版本，避免越改越差
- **日志**：logs/{时间戳}.log 记录每轮思考、评分、温度、耗时
- **可视化**：viz.py 将 process.json 转为含图表和时间线的 HTML 报告
- **仅评审模式**：--judge 参数直接打分不打磨

## 终止条件

| 条件 | 结果 |
|------|------|
| 首轮 | 强制继续 |
| 总分 >= 85 且 提升 < 2 分 | 停止 |
| 总分 < 85 | 继续 |
| 达到 5 轮上限 | 强制停止 |
| 本轮评分 < 历史最高分 | 自动回滚到历史最佳版本，继续打磨 |
| 连续 2 次回滚后仍下降 | 强制终止，取历史最高分版本 |

## 核心能力

| 能力 | 实现位置 |
|------|---------|
| 多 Expert 圆桌并行打磨 | code/engine/orchestrator.py |
| Skill 按需加载 | code/skills/manager.py + agent/core.py |
| 动态 temperature 自适应 | code/engine/orchestrator.py |
| 3 次重试 + 占位输出 | code/engine/orchestrator.py |
| 仅评审模式（--judge） | code/run.py |
| 文件名学号与 YAML 校验 | code/run.py |
| 日志全量记录 | code/tools/logger.py |
| 过程可视化 HTML | code/tools/viz.py |
| 附录D 格式自查 | 自动调用 validate_submission.py |