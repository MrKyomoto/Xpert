# PBL 教案磨课系统 — 多 Expert 圆桌打磨

符合附录 D 提交与运行契约。基于 **3 位差异化专家 + 主持人 + Judge** 多轮迭代架构的教案智能打磨系统。

## 项目结构

```
.
├── .gitignore
├── .env.example                  # 环境变量模板（MAX_TOKENS=16000）
├── requirements.txt
├── code/                         # 代码工程
│   ├── run.py                    # 统一 CLI 入口（支持 --judge 仅评审模式）
│   ├── config.py                 # 环境变量 & 配置
│   ├── engine/
│   │   └── orchestrator.py       # 多 Expert 圆桌调度器
│   ├── agent/
│   │   └── core.py               # Agent 核心（非流式 chat + 工具调用 + load_skill）
│   ├── memory/
│   │   └── context.py            # 上下文存储 & tiktoken 自动压缩
│   ├── tools/                    # 工具库
│   │   ├── registry.py           # 工具注册表（自动生成 JSON Schema）
│   │   └── logger.py             # 日志捕获（sys.stdout Tee）
│   ├── skills/                   # 技能系统（按角色组织 .md 知识包）
│   │   ├── manager.py            # 技能管理器：索引 + 按需加载
│   │   ├── r_literacy/           # 素养导向教研员（预注入 + 按需加载）
│   │   ├── r_subject/            # 学科内容专家
│   │   ├── r_learner/            # 学情适配专家
│   │   ├── r_chair/              # 主持人（全量注入）
│   │   └── r_judge/              # 评审专家（单文件 _system.md 含完整量规）
│   ├── examples/
│   │   ├── inputs/               # 示例教案（{学号}_{样本ID}.md）
│   │   │   ├── demo001_MATH01.md
│   │   │   ├── demo001_MATH02.md
│   │   │   ├── demo001_CHN01.md
│   │   │   ├── demo001_BIO01.md
│   │   │   └── demo001_CHEM01.md
│   │   └── profile_minimal.yaml  # 学情配置（student_id 字段）
│   ├── outputs_practice/         # 磨课输出 (.gitignore)
│   └── logs/                     # 运行日志 (.gitignore)
├── report/                       # 技术报告 (.gitignore)
└── ai_collab/                    # AI 协作记录 (.gitignore)
```

## 运行模式

### 打磨模式（默认）

```bash
python code/run.py --lesson <path> --profile <path> --out <dir>
```

完整执行 3 专家圆桌 → 主持人合并 → Judge 评审的多轮迭代。

### 仅评审模式

```bash
python code/run.py --lesson <path> --profile <path> --out <dir> --judge
```

直接对输入教案打分，不打磨。输出评分明细到 `judge_{样本ID}.json`。

## 运行流程

```
run.py
  │
  ├─ capture_output() → Tee sys.stdout 到 logs/{timestamp}.log
  │
  ├─ 校验文件名 student_id 与 YAML 内 student_id 一致
  │
  ├─ (--judge 模式) 仅调用 Judge 打分 → 输出评分 → 退出
  │
  └─ Orchestrator.run()
       │
       ├─ Round 1:
       │   ├─ 3 位专家并行（ThreadPoolExecutor）非流式 chat()
       │   │   └─ 核心技能预注入 + load_skill 工具按需加载
       │   │   └─ temperature 根据上一轮总分动态调整
       │   ├─ 主持人汇总合并 → 冲突分析 + ---POLISHED--- + 教案
       │   │   └─ temperature 也动态调整，接收上一轮反馈和分数
       │   ├─ Judge 按附录 A 六维度评分（单文件 _system.md）
       │   ├─ 评分成功 → 判 should_stop（首轮强制继续）
       │   └─ 评分 0 分 → 最多重试 3 次，log 打出原始响应
       │
       ├─ Round N: ...（上限 5 轮）
       │
       └─ 输出:
            ├─ {学号}_{样本ID}_polished.md
            └─ {学号}_{样本ID}_process.json

       └─ 自动运行 validate_submission.py 格式自查
```

### 角色

| 角色 | role_id | 技能加载策略 |
|------|---------|-------------|
| 素养导向教研员 | r_literacy | 核心 3 技能预注入 + 其余按需加载 |
| 学科内容专家 | r_subject | 核心 3 技能预注入 + 其余按需加载 |
| 学情适配专家 | r_learner | 核心 3 技能预注入 + 其余按需加载 |
| 主持人 | r_chair | 全量注入 + 接收上一轮分数与反馈 |
| 评审专家 | r_judge | 单文件 _system.md，含完整六维度量规 |

## 动态 Temperature

**专家和主持人**均根据上一轮总分自动调整：

| 上一轮总分 | temperature | 修改策略 |
|-----------|-------------|---------|
| >= 90 | 0.3 | 保守微调 |
| 80–89 | 0.5 | 适度改进 |
| 70–79 | 0.7 | 正常输出 |
| < 70 | 0.9 | 大胆重构 |

第一轮无历史分数，默认 0.7。

## 终止条件

```python
if prev_total_score is None:
    should_stop = False        # 首轮不停
elif total >= 85 and (total - prev_total_score) < 2:
    should_stop = True         # 达标且涨不动
elif total < 85:
    should_stop = False        # 未达标必须继续
```

| 条件 | 结果 |
|------|------|
| 首轮 | 强制继续 |
| 总分 >= 85 且 提升 < 2 分 | 停止 |
| 总分 >= 85 且 提升 >= 2 分 | 继续 |
| 总分 < 85 | 继续 |
| 达到 5 轮上限 | 强制停止 |

## 重试机制

| 角色 | 重试策略 |
|------|---------|
| 专家 | API 失败最多重试 3 次（指数退避 1s/2s）；全失败输出占位意见 |
| Judge | 0 分时最多重试 3 次（间隔 1s）；log 打出原始响应用于调试 |

## 文件名规范

输入教案命名格式（附录 D）：

```
{student_id}_{sample_id}.md
```

- `student_id` 仅限字母数字，必须与 YAML 中 `student_id` 字段一致
- `sample_id` 字母数字开头，可含连字符

示例：`demo001_MATH01.md` → 输出 `demo001_MATH01_polished.md` + `demo001_MATH01_process.json`

## Skill 系统设计

加载策略：
- **预注入**：核心技能直接写入 system prompt（3-5 个文件）
- **索引**：其余技能只显示名称+描述，不加载内容
- **按需加载**：LLM 通过 load_skill 工具调用，将技能全文注入上下文

## 快速开始

```bash
# 1. 创建虚拟环境
python -m venv venv

# 2. 激活虚拟环境（Windows）
source venv/Scripts/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 API 密钥
cp .env.example .env
# 编辑 .env:
#   API_KEY=your_key
#   API_BASE=https://api.xxx.com/v1
#   MODEL=your-model
#   MAX_TOKENS=16000

# 5. 运行打磨
python code/run.py \
  --lesson code/examples/inputs/demo001_MATH01.md \
  --profile code/examples/profile_minimal.yaml \
  --out code/outputs_practice/
```

## 运行契约

```bash
python code/run.py --lesson <path> --profile <path> --out <dir>
python code/run.py --lesson <path> --profile <path> --out <dir> --judge
```

| 参数 | 说明 |
|------|------|
| --lesson | 输入教案 Markdown 路径 |
| --profile | 学情描述 YAML（需含 student_id 字段） |
| --out | 输出目录 |
| --judge | 仅评审模式（直接打分，不打磨） |

- 退出码: 0 = 成功, 非零 = 失败
- 输出: `{学号}_{样本ID}_polished.md` + `{学号}_{样本ID}_process.json`（或 `judge_{样本ID}.json`）
- 日志: `code/logs/{时间戳}.log`（全量终端输出，含每轮 temperature / 耗时 / 冲突分析 / Judge Debug）
- 运行结束后自动调用附录 D `validate_submission.py` 进行格式自查

## LOG 输出示例

```
╔══ 素养导向教研员 ═══ 16:51:41 （用时 20.0s, temperature=0.7）
── 修改意见 ──
1. 【位置】教学目标｜【问题】...
╔══ 主持人冲突分析 ═══
经分析三位专家的意见，存在以下冲突：
- r_literacy 建议增加探究环节...
...

最终教案 (总耗时 300s = 5.0 分钟):
```

## 核心能力

| 能力 | 实现位置 |
|------|---------|
| 多 Expert 圆桌并行打磨 | code/engine/orchestrator.py |
| Skill 按需加载（索引 + load_skill） | code/skills/manager.py + code/agent/core.py |
| 动态 temperature 自适应（专家 + 主持人） | code/engine/orchestrator.py |
| 3 次重试 + 占位输出（专家/Judge） | code/engine/orchestrator.py |
| 仅评审模式（--judge） | code/run.py |
| 文件名学号与 YAML 校验 | code/run.py — extract_filename_student_id() |
| 日志全量记录 | code/tools/logger.py — Tee stdout |
| 上下文管理与自动压缩 | code/memory/context.py — tiktoken 计费 |
| 工具注册与调用 | code/tools/registry.py |
| 附录D 格式自查 | 自动调用 validate_submission.py |