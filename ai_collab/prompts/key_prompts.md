# 系统提示词（System Prompts）

## 专家角色 prompt 设计

### 素养导向教研员（r_literacy）
**文件**: `code/skills/r_literacy/_system.md`

### 学科内容专家（r_subject）
**文件**: `code/skills/r_subject/_system.md`

### 学情适配专家（r_learner）
**文件**: `code/skills/r_learner/_system.md`

上述三个角色均采用统一的 prompt 框架（角色定位 + 核心职责 + 评价维度 + 边界声明），差异化体现在 role_id 和具体的 skill 文件内容中。

### 主持人（r_chair）
**文件**: `code/skills/r_chair/_system.md`

### 评审专家（r_judge）
**文件**: `code/skills/r_judge/_system.md`
**设计特点**: 单文件全量注入，不拆分技能，确保评分一致性。

---

## 编排层 prompt（代码内注入）

### 专家每轮输入（orchestrator.py:138-148）
```
请分析以下教案：
{current_lesson}

上一轮评审反馈：
{prev_feedback}

上一轮总分: {prev_total_score}/100。
请根据当前分数调整修改力度：高分（≥85）建议只做微调，低分（<85）可大胆提出结构性改进。

其他专家的意见供参考：
{r_literacy} ...
{r_subject} ...
{r_learner} ...
```

### Chair 每轮输入（orchestrator.py:199-208）
```
以下是多位专家对同一教案的修改意见，请进行冲突检测、合并，并输出打磨后的完整教案。

{各专家意见}

上一轮总分: {prev_total_score}/100。高分（≥85）建议保守微调，低分（<85）可大胆重构。
上一轮评审反馈: {prev_feedback}

当前教案原文：
{current_lesson}
```

### Judge 每轮输入（orchestrator.py:240）
```
请评审以下教案并返回 JSON：
上一轮总分: {prev_total_score}/100。如果本轮提升不足2分，请将should_stop设为true。

{rec.polished}
```