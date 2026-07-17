# PBL 教案磨课系统 — Expert-Judge 多轮闭环

符合附录 D 提交与运行契约，基于 **Expert（打磨专家）+ Judge（评审专家）** 多轮迭代架构的教案智能打磨系统。

## 项目结构

```
.
├── .gitignore
├── .env.example                  # 环境变量模板
├── requirements.txt
├── code/                         # 代码工程
│   ├── run.py                    # 统一 CLI 入口
│   ├── config.py                 # 环境变量 & 配置
│   ├── prompts/                  # Prompt 文件（.md，按需加载，不硬编码）
│   │   ├── expert_system.md      # 打磨专家 system prompt
│   │   └── judge_system.md       # 评审专家 system prompt（含附录A量规）
│   ├── engine/
│   │   └── orchestrator.py       # Expert-Judge 多轮循环调度器
│   ├── agent/
│   │   └── core.py               # Agent 核心（流式对话 + 工具调用）
│   ├── memory/
│   │   └── context.py            # 上下文存储 & tiktoken 自动压缩
│   ├── tools/
│   │   └── registry.py           # 工具注册表（自动生成 JSON Schema）
│   ├── skills/
│   │   ├── manager.py            # 技能按需加载
│   │   └── file_operations.py    # 文件读写工具
│   ├── utils/
│   │   └── prompt_loader.py      # 从 prompts/*.md 按需加载 prompt
│   ├── examples/
│   │   ├── inputs/               # 示例教案（命名: {学号}_{样本ID}.md）
│   │   │   ├── demo001_MATH01.md # 数学常规课
│   │   │   ├── demo001_MATH02.md # 数学 PBL 项目课
│   │   │   ├── demo001_CHN01.md  # 语文 PBL 项目课
│   │   │   └── demo001_BIO01.md  # 生物 PBL 项目课
│   │   └── profile_minimal.yaml  # 最小学情配置（仅 student_id）
│   └── outputs_practice/         # 磨课输出 (.gitignore)
├── report/                       # 技术报告 (.gitignore)
├── ai_collab/prompts/            # AI 协作记录 (.gitignore)
└── logs/                         # 运行日志 (.gitignore)
```

## 运行流程

### 整体架构

```
run.py
  │
  ├─ 读入 --lesson（教案 Markdown）和 --profile（YAML）
  │
  └─ Orchestrator.run()
       │
       ├─ Round 1:
       │   ├─ Expert（打磨专家）流式输出打磨后教案
       │   ├─ Judge（评审专家）按附录A六维度打分
       │   ├─ 总分 ≥ 85 或 提升 < 3分 → should_stop
       │   └─ 未达标 → 进入 Round 2
       │
       ├─ Round 2:
       │   ├─ Expert 收到 Judge 反馈，针对性改进
       │   ├─ Judge 重新评分
       │   └─ 达标或分数不增长 → 停止
       │
       ├─ Round N: ...（上限 5 轮）
       │
       └─ 输出:
            ├─ {学号}_{样本ID}_polished.md   ← 最终教案
            └─ {学号}_{样本ID}_process.json  ← 研讨过程记录
                 ├─ meta: 学号/样本ID/时间戳
                 ├─ roles: r_expert, r_judge
                 ├─ discussion: 每轮发言（含评分与反馈）
                 └─ modifications: 修改条目（来源Judge评分建议）
```

### 关键设计

| 组件 | 职责 |
|------|------|
| **Expert** | 接收教案原文或上轮反馈，输出改进后完整教案 Markdown |
| **Judge** | 按附录A六维度（A结构/B内容/C准确/D一致/E语言/F素养）评分，决定 `should_stop` |
| **Orchestrator** | 控制循环终止条件：**总分 ≥ 85 或 提升 < 3 分** |
| **prompt_loader** | 从 `prompts/*.md` 加载 system prompt，支持 `{{变量}}` 替换 |
| **Agent** | 统一 LLM 通信层，支持流式/非流式两模式，可选工具调用 |

### 终止条件（定义在 `prompts/judge_system.md`）

- `should_stop: true` 当且仅当 **总分 ≥ 85** 或 **相比上一轮总分提升 < 3 分**
- 否则继续迭代，最多 5 轮

## 快速开始

```bash
# 1. 创建虚拟环境
python -m venv venv

# 2. 激活虚拟环境
# Windows:
source venv/Scripts/activate
# Linux/Mac:
# source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置 API 密钥
cp .env.example .env
# 编辑 .env，填入:
#   API_KEY=your_key
#   API_BASE=https://api.xxx.com/v1
#   MODEL=your-model

# 5. 运行打磨
python code/run.py \
  --lesson code/examples/inputs/demo001_MATH01.md \
  --profile code/examples/profile_minimal.yaml \
  --out code/outputs_practice/
```

## 运行契约

```bash
python code/run.py --lesson <path> --profile <path> --out <dir>
```

| 参数 | 说明 |
|------|------|
| `--lesson` | 输入教案 Markdown 路径 |
| `--profile` | 学情描述 YAML（仅需 `student_id` 字段） |
| `--out` | 输出目录 |

- 退出码: `0` = 成功, 非零 = 失败
- 输出: `{学号}_{样本ID}_polished.md` + `{学号}_{样本ID}_process.json`
- 运行结束后自动调用附录 D `validate_submission.py` 进行格式自查

## 核心能力

| 能力 | 实现位置 |
|------|---------|
| Expert-Judge 多轮迭代 | `code/engine/orchestrator.py` |
| Prompt 外置 .md 按需加载 | `code/utils/prompt_loader.py` + `code/prompts/` |
| 流式输出（实时显示思考过程） | `code/agent/core.py` — `chat_stream()` |
| 上下文管理与自动压缩 | `code/memory/context.py` — tiktoken 计费 |
| 工具注册与调用 | `code/tools/registry.py` |
| 附录D 格式自查 | 自动调用 `validate_submission.py` |

## 示例教案

`code/examples/inputs/` 下预置了 4 份教案（命名符合附录D规范）：

| 文件 | 课型 | 学科 |
|------|------|------|
| `demo001_MATH01.md` | 常规课 | 数学 · 一元一次方程 |
| `demo001_MATH02.md` | PBL 项目课 | 数学 · 皮筋测力计 |
| `demo001_CHN01.md` | PBL 项目课 | 语文 · 如果山水会说话 |
| `demo001_BIO01.md` | PBL 项目课 | 生物 · 防治校园飞絮 |

## 后续方向（创新点）

1. **多专家角色蒸馏** — 从金标准案例中归纳多个差异化专家角色，Expert 扩展为多 Agent 圆桌研讨
2. **磨课过程可视化** — process.json 驱动的时间线/对话流展示
3. **迭代历史回溯** — 支持查看每轮打分趋势与修改轨迹