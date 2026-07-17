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
│   ├── engine/
│   │   └── orchestrator.py       # Expert-Judge 多轮循环调度器
│   ├── agent/
│   │   └── core.py               # Agent 核心（流式对话 + 工具调用）
│   ├── memory/
│   │   └── context.py            # 上下文存储 & tiktoken 自动压缩
│   ├── tools/                    # 工具库
│   │   ├── registry.py           # 工具注册表（自动生成 JSON Schema）
│   │   └── logger.py             # 日志捕获（sys.stdout Tee）
│   ├── skills/                   # 技能系统（按角色组织 .md 知识包）
│   │   ├── manager.py            # 技能管理器：索引 + 按需加载
│   │   ├── r_expert/             # 打磨专家技能
│   │   │   ├── _system.md        #   system prompt（自动加载）
│   │   │   ├── question_design.md#   驱动性问题设计
│   │   │   └── structure_check.md#   教案结构检查
│   │   └── r_judge/              # 评审专家技能
│   │       ├── _system.md        #   system prompt（自动加载）
│   │       └── evaluation_rubric.md# 评价量规（附录A）
│   ├── examples/
│   │   ├── inputs/               # 示例教案（命名: {学号}_{样本ID}.md）
│   │   │   ├── demo001_MATH01.md # 数学常规课 · 一元一次方程
│   │   │   ├── demo001_MATH02.md # 数学 PBL · 皮筋测力计
│   │   │   ├── demo001_CHN01.md  # 语文 PBL · 如果山水会说话
│   │   │   └── demo001_BIO01.md  # 生物 PBL · 防治校园飞絮
│   │   └── profile_minimal.yaml  # 最小学情配置（仅 student_id）
│   ├── outputs_practice/         # 磨课输出 (.gitignore)
│   └── logs/                     # 运行日志（时间戳命名）
├── report/                       # 技术报告 (.gitignore)
└── ai_collab/                    # AI 协作记录 (.gitignore)
```

## 运行流程

```
run.py
  │
  ├─ capture_output() → Tee sys.stdout 到 logs/{timestamp}.log
  │
  ├─ 读入 --lesson（教案 Markdown）和 --profile（YAML）
  │
  └─ Orchestrator.run()
       │
       ├─ Round 1:
       │   ├─ Expert 加载 skills/r_expert/ (system prompt + 全量技能注入)
       │   ├─ Expert 流式输出 → 只显示思考过程（---POLISHED--- 之前）
       │   ├─ Judge 加载 skills/r_judge/ → 按附录A六维度评分
       │   ├─ 总分 >= 85 或 提升 < 3分 → should_stop
       │   └─ 未达标 / 评审解析失败（重试1次）→ 进入 Round 2
       │
       ├─ Round N: ...（上限 5 轮）
       │
       └─ 输出:
            ├─ {学号}_{样本ID}_polished.md   ← 最终教案（循环结束统一展示）
            └─ {学号}_{样本ID}_process.json  ← 研讨过程记录

       └─ 自动运行 validate_submission.py 自查格式
            └─ 输出（capture_output 拦截）→ 写入日志
```

### 关键设计

| 组件 | 职责 |
|------|------|
| **Expert** | 接收教案原文或上轮反馈，输出改进后完整教案 Markdown |
| **Judge** | 按附录A六维度（A结构/B内容/C准确/D一致/E语言/F素养）评分，决定 should_stop |
| **Orchestrator** | 控制循环：总分 >= 85 或 提升 < 3 分 停止，上限5轮 |
| **SkillManager** | 从 skills/{role_id}/ 加载 _system.md + 全量技能注入 system prompt |
| **Agent** | 统一 LLM 通信层，支持流式/非流式两模式，可选工具调用 |
| **Logger** | Tee 包装 sys.stdout，自动保存到 logs/{时间戳}.log |

### 终止条件

- should_stop: true 当且仅当 **总分 >= 85** 或 **相比上一轮总分提升 < 3 分**
- Judge 评审 JSON 解析失败时自动重试 1 次，不因解析失败终止

## Skill 系统设计

skills/{role_id}/ 是角色的知识包目录：

```
skills/
├── r_expert/               ← 打磨专家
│   ├── _system.md          ← 自动加载为 system prompt
│   ├── question_design.md  ← 技能：驱动性问题设计
│   └── structure_check.md  ← 技能：教案结构检查
└── r_judge/                ← 评审专家
    ├── _system.md          ← 自动加载为 system prompt
    └── evaluation_rubric.md← 技能：评价量规（附录A）
```

- _system.md 自动加载为 Agent 的 system prompt
- 其他 .md 技能文件**全量注入**到 system prompt（文件小，无需工具调用）
- 新角色只需在 skills/ 下新建目录 + _system.md

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
# 编辑 .env:
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
| --lesson | 输入教案 Markdown 路径 |
| --profile | 学情描述 YAML（仅需 student_id 字段） |
| --out | 输出目录 |

- 退出码: 0 = 成功, 非零 = 失败
- 输出: {学号}_{样本ID}_polished.md + {学号}_{样本ID}_process.json
- 日志: code/logs/{时间戳}.log（全量终端输出）
- 运行结束后自动调用附录 D validate_submission.py 进行格式自查

## 核心能力

| 能力 | 实现位置 |
|------|---------|
| Expert-Judge 多轮迭代 | code/engine/orchestrator.py |
| Skill 知识包注入 | code/skills/manager.py + code/skills/{role_id}/ |
| 流式输出（实时显示思考过程） | code/agent/core.py -- chat_stream() |
| 日志全量记录 | code/tools/logger.py -- Tee stdout |
| 上下文管理与自动压缩 | code/memory/context.py -- tiktoken 计费 |
| 工具注册与调用 | code/tools/registry.py |
| 附录D 格式自查 | 自动调用 validate_submission.py |

## 示例教案

code/examples/inputs/ 下预置了 4 份教案（命名符合附录D规范）：

| 文件 | 课型 | 学科 |
|------|------|------|
| demo001_MATH01.md | 常规课 | 数学 . 一元一次方程 |
| demo001_MATH02.md | PBL 项目课 | 数学 . 皮筋测力计 |
| demo001_CHN01.md | PBL 项目课 | 语文 . 如果山水会说话 |
| demo001_BIO01.md | PBL 项目课 | 生物 . 防治校园飞絮 |

## 后续方向（创新点）

1. **多专家角色蒸馏** — 从金标准案例中归纳差异化专家角色，在 skills/ 下新建目录，Expert 扩展为多 Agent 圆桌研讨
2. **磨课过程可视化** — process.json 驱动的时间线/对话流展示
3. **迭代历史回溯** — 支持查看每轮打分趋势与修改轨迹