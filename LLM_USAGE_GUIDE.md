# 🚀 OpenCoder LLM 使用指南

## 📌 概述

OpenCoder集成了基于OpenRouter的LLM功能，提供三个核心接口：
1. **周报生成** - 自动生成项目周报
2. **月报生成** - 自动生成项目月报  
3. **智能标注** - AI辅助标注建议

### ✨ 核心特性

- ✅ **Prompt外部化** - 存储在txt文件，易修改
- ✅ **数据解耦** - 从其他端口获取数据
- ✅ **统一输出** - 固定JSON格式，前端易解析
- ✅ **灵活配置** - 支持多种LLM模型

---

## 🔧 快速开始

### 1. 配置环境变量

在 `.env` 文件中添加：

```env
# OpenRouter配置（必需）
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxx

# LLM参数（可选）
LLM_MODEL=anthropic/claude-3.5-haiku
LLM_TEMPERATURE=0.3
LLM_MAX_TOKENS=2000
APP_URL=http://localhost:8000
```

### 2. 获取OpenRouter API密钥

1. 访问 https://openrouter.ai/
2. 注册/登录账号
3. 前往 https://openrouter.ai/keys
4. 创建API密钥
5. 复制密钥到 `.env`

### 3. 启动后端

```bash
cd OpenCoderBackend
python main.py
```

### 4. 测试接口

访问 http://localhost:8000/docs

在 `llm` 标签下找到三个接口并测试。

---

## 📡 API接口详解

### 1️⃣ 周报生成

**端点：** `POST /api/llm/weekly-report`

**请求体：**

```json
{
  "project_id": "65abc123...",
  "start_date": "2024-01-15",
  "end_date": "2024-01-21",
  "model": "anthropic/claude-3.5-haiku"  // 可选
}
```

**响应（固定格式）：**

```json
{
  "success": true,
  "report": {
    "title": "项目周报 (2024-01-15 至 2024-01-21)",
    "period": "2024-01-15 至 2024-01-21",
    "summary": "本周完成250个标注任务...",
    "sections": {
      "progress": {
        "total_annotations": 250,
        "completion_rate": 95.5,
        "description": "本周进度良好..."
      },
      "team_performance": {
        "members": [
          {"name": "张三", "annotations": 80, "performance": "优秀"}
        ],
        "top_performer": "张三",
        "description": "团队表现优秀"
      },
      "highlights": ["亮点1", "亮点2"],
      "issues": ["问题1"],
      "recommendations": ["建议1", "建议2"]
    },
    "conclusion": "整体进展顺利"
  },
  "metadata": {
    "generated_at": "2024-01-22T10:00:00Z",
    "model_used": "anthropic/claude-3.5-haiku",
    "cost": 0.002
  }
}
```

---

### 2️⃣ 月报生成

**端点：** `POST /api/llm/monthly-report`

**请求体：**

```json
{
  "project_id": "65abc123...",
  "year": 2024,
  "month": 1,
  "model": "anthropic/claude-3.5-haiku"  // 可选
}
```

**响应（固定格式）：**

```json
{
  "success": true,
  "report": {
    "title": "2024年1月项目月报",
    "period": "2024年1月",
    "summary": "本月完成1200个标注任务...",
    "sections": {
      "monthly_progress": {...},
      "weekly_breakdown": [...],
      "team_performance": {...},
      "quality_metrics": {...},
      "achievements": [...],
      "challenges": [...],
      "next_month_plan": [...]
    },
    "conclusion": "1月份进展顺利"
  },
  "metadata": {
    "generated_at": "2024-02-01T10:00:00Z",
    "model_used": "anthropic/claude-3.5-haiku",
    "cost": 0.003
  }
}
```

---

### 3️⃣ 智能标注

**端点：** `POST /api/llm/annotate`

**请求体：**

```json
{
  "sentence": "这部电影非常精彩，值得一看！",
  "tag_groups": [
    {
      "group_id": "sentiment",
      "group_name": "情感倾向",
      "type": "single",
      "options": [
        {"value": "positive", "label": "正面"},
        {"value": "negative", "label": "负面"},
        {"value": "neutral", "label": "中立"}
      ]
    },
    {
      "group_id": "topic",
      "group_name": "话题分类",
      "type": "multi",
      "options": [
        {"value": "entertainment", "label": "娱乐"},
        {"value": "movie", "label": "电影"}
      ]
    }
  ],
  "model": "anthropic/claude-3.5-haiku"  // 可选
}
```

**tag_groups 说明：**

- 从项目的标签配置接口获取：`GET /api/projects/{id}/tag-groups`
- `type: "single"` - 单选
- `type: "multi"` - 多选

**响应（固定格式）：**

```json
{
  "success": true,
  "annotation": {
    "sentence": "这部电影非常精彩，值得一看！",
    "labels": [
      {
        "group_id": "sentiment",
        "group_name": "情感倾向",
        "selected": ["positive"],
        "confidence": 0.95
      },
      {
        "group_id": "topic",
        "group_name": "话题分类",
        "selected": ["entertainment", "movie"],
        "confidence": 0.88
      }
    ],
    "overall_confidence": 0.92,
    "reasoning": "文本明确表达了对电影的正面评价..."
  },
  "metadata": {
    "generated_at": "2024-01-20T10:00:00Z",
    "model_used": "anthropic/claude-3.5-haiku",
    "cost": 0.0003
  }
}
```

---

## 📝 修改Prompt模板

### 模板位置

```
OpenCoderBackend/
└── prompts/
    ├── weekly_report.txt
    ├── monthly_report.txt
    └── annotation.txt
```

### 修改步骤

1. **打开对应的txt文件**
2. **编辑Prompt内容**
3. **保存文件**
4. **无需重启** - 修改立即生效！

### 可用变量

#### weekly_report.txt
- `{project_name}` - 项目名称
- `{period}` - 报告周期
- `{total_annotations}` - 总标注数
- `{total_tasks}` - 总任务数
- `{completed_tasks}` - 已完成任务数
- `{team_members}` - 团队成员（已格式化）
- `{annotations_by_date}` - 每日标注（已格式化）

#### monthly_report.txt
- `{project_name}` - 项目名称
- `{year}` - 年份
- `{month}` - 月份
- `{total_annotations}` - 总标注数
- `{total_tasks}` - 总任务数
- `{completed_tasks}` - 已完成任务数
- `{team_members}` - 团队成员（已格式化）

#### annotation.txt
- `{sentence}` - 待标注句子
- `{tag_groups}` - 标签组（已格式化）
- `{num_groups}` - 标签组数量

---

## 🎨 前端集成

### 周报生成按钮

```tsx
const generateWeeklyReport = async () => {
  try {
    setGenerating(true);
    
    const endDate = new Date().toISOString().split('T')[0];
    const startDate = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)
      .toISOString().split('T')[0];
    
    const response = await fetch(
      `${apiBaseUrl}/api/llm/weekly-report?token=${token}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          start_date: startDate,
          end_date: endDate
        })
      }
    );
    
    const result = await response.json();
    if (result.success) {
      setReport(result.report);
      toast.success('周报生成成功');
    }
  } catch (error) {
    toast.error('生成周报失败');
  } finally {
    setGenerating(false);
  }
};

<Button onClick={generateWeeklyReport}>
  生成周报
</Button>
```

### AI标注建议按钮

```tsx
const getAISuggestion = async () => {
  try {
    setLoadingSuggestion(true);
    
    // 1. 获取标签组配置
    const tagGroupsResponse = await fetch(
      `${apiBaseUrl}/api/projects/${projectId}/tag-groups?token=${token}`
    );
    const tagGroups = await tagGroupsResponse.json();
    
    // 2. 调用标注接口
    const response = await fetch(
      `${apiBaseUrl}/api/llm/annotate?token=${token}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sentence: currentTask.payload.text,
          tag_groups: tagGroups.map(g => ({
            group_id: g.group_id,
            group_name: g.name,
            type: g.type,
            options: g.options
          }))
        })
      }
    );
    
    const result = await response.json();
    if (result.success) {
      // 3. 自动填充标签
      const suggestions = {};
      result.annotation.labels.forEach(label => {
        suggestions[label.group_id] = label.selected;
      });
      setSelectedTags(suggestions);
      
      toast.success(
        `AI建议已生成（置信度: ${(result.annotation.overall_confidence * 100).toFixed(0)}%）`
      );
    }
  } catch (error) {
    toast.error('获取AI建议失败');
  } finally {
    setLoadingSuggestion(false);
  }
};

<Button onClick={getAISuggestion}>
  获取AI建议
</Button>
```

---

## 💰 成本估算

使用默认模型（Claude 3.5 Haiku）：

| 操作 | 预估成本 |
|------|---------|
| 周报生成 | ~$0.002 |
| 月报生成 | ~$0.003 |
| 单次标注 | ~$0.0003 |

**月度成本示例：**
- 每周1次周报：$0.008/月
- 每月1次月报：$0.003/月
- 每天100次标注：$0.90/月

**总计：** 约 $1-2/月 ✨

---

## 🔧 高级配置

### 切换LLM模型

**方法1：修改默认模型**

```env
# .env
LLM_MODEL=openai/gpt-4o-mini
```

**方法2：请求中指定**

```json
{
  "project_id": "...",
  "model": "openai/gpt-4o-mini"  // 覆盖默认模型
}
```

### 支持的模型

- `anthropic/claude-3.5-haiku` （推荐，便宜快速）
- `openai/gpt-4o-mini` （OpenAI，质量好）
- `google/gemini-flash-1.5` （Google，速度快）
- `meta-llama/llama-3.1-8b-instruct` （开源，最便宜）

完整列表：https://openrouter.ai/models

---

## 📚 相关文档

- [完整API文档](LLM_API_DOCUMENTATION.md)
- [快速开始](LLM_QUICK_START.md)
- [OpenRouter官网](https://openrouter.ai/)

---

## ❓ 常见问题

### Q: 修改Prompt后需要重启服务吗？

A: **不需要！** Prompt从txt文件实时读取，修改后立即生效。

### Q: 如何查看API密钥余额？

A: 访问 https://openrouter.ai/credits

### Q: 调用失败怎么办？

A: 检查：
1. API密钥是否正确配置
2. 后端日志中的错误信息
3. OpenRouter服务状态

### Q: 如何控制成本？

A: 
1. 在OpenRouter后台设置月度预算上限
2. 使用更便宜的模型（如Llama）
3. 启用缓存减少重复调用

---

**祝您使用愉快！🎉**
