# PBL 教案磨课多智能体系统 — 单 Agent 基线

符合附录 D 提交与运行契约的 MVP 实现。

## 项目结构

```
.
├── run.py                     # 统一 CLI 入口
├── requirements.txt
├── .env.example
├── code/                      # 代码工程
│   ├── config.py              # 环境变量 & 配置
│   ├── agent/core.py          # Agent 核心（对话循环 + 工具调用）
│   ├── memory/context.py      # 上下文存储 & 自动压缩
│   ├── tools/registry.py      # 工具注册表（自动生成 JSON Schema）
│   └── skills/
│       ├── manager.py         # 技能按需加载
│       └── file_operations.py # 文件读写工具
├── examples/inputs/           # 示例教案（开发调试用）
├── outputs_practice/          # 磨课输出
├── report/                    # 技术报告
├── ai_collab/prompts/         # AI 协作记录
└── logs/                      # 运行日志
```

## 运行

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入 API_KEY
python run.py --lesson examples/inputs/常规_数学_一元一次方程.md --profile examples/profile_minimal.yaml --out outputs_practice/
```

## 运行契约

```bash
python run.py --lesson <path> --profile <path> --out <dir>
```
- 退出码: 0 = 成功, 非零 = 失败
- 输出: `<sample_id>_polished.md` + `<sample_id>_process.json` (含时间戳)
- `--profile` 仅需 `student_id` 字段