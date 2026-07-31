# CLAUDE.md

- 需求文档为 PBL 课题提供的 Word 文档（含附录A量规、附录B案例、附录D契约）
- 关键提示词和设计决策记录在 `note.md` 和 `prompts/key_prompts.md` 中

## 关键设计决策

| 决策 | 结论 | 原因 |
|------|------|------|
| Judge prompt 架构 | 单文件全量注入 | 多文件拆分导致评分不一致 |
| 专家调用方式 | 非流式 chat() | 线程池并行 + 支持 load_skill 工具调用 |
| 容错策略 | 3次重试 + 占位输出 | 单专家失败不应崩全局流程 |
| 版本控制 | 最佳版本回滚 | 多轮迭代必然出现分数震荡 |
| 学情适配 | 暂不实现 | profile仅读student_id用于校验 |

## 目录说明

- `code/` — 核心源代码
- `code/skills/` — 各角色 prompt 文件包
- `code/engine/orchestrator.py` — 圆桌调度器（核心编排逻辑）
- `outputs_practice/` — 公开练习三元组的磨课输出
- `report/` — 技术报告与答辩PPT
- `code/logs/` — 运行日志（保留历史迭代记录）