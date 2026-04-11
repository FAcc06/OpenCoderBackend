# OpenCoder LLM API 文档

## 🎯 概述

OpenCoder集成了基于OpenRouter的LLM功能，支持多种AI模型，提供智能标注建议、质量检查、数据分析和报告生成。

## 🔑 配置

### 环境变量

在 `.env` 文件中配置：

```env
# OpenRouter API密钥（必需）
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxx

# 默认模型（可选，推荐使用Claude 3.5 Haiku）
LLM_MODEL=anthropic/claude-3.5-haiku

# 报告生成模型（可选）
LLM_REPORT_MODEL=anthropic/claude-3.5-haiku

# LLM参数
LLM_TEMPERATURE=0.3
LLM_MAX_TOKENS=1000

# 应用URL（OpenRouter要求）
APP_URL=http://localhost:8000
```

### 获取OpenRouter API密钥

1. 访问 https://openrouter.ai/
2. 注册/登录账号
3. 前往 https://openrouter.ai/keys 创建API密钥
4. 复制密钥并添加到 `.env` 文件

---

## 📡 API端点

### 1. 标注建议 (Suggest Tags)

**端点：** `POST /api/projects/{project_id}/llm/suggest-tags`

**描述：** 使用LLM分析任务文本，自动建议合适的标签

**请求参数：**

```typescript
{
  task_text: string;           // 任务文本内容
  use_cache?: boolean;         // 是否使用缓存（默认true）
  model?: string;              // 指定模型（可选）
}
```

**请求示例：**

```bash
curl -X POST "http://localhost:8000/api/projects/65abc123/llm/suggest-tags?token=your-token" \
  -H "Content-Type: application/json" \
  -d '{
    "task_text": "这部电影非常精彩，演员表演很到位，剧情紧凑有趣",
    "use_cache": true
  }'
```

**响应示例：**

```json
{
  "suggestions": {
    "sentiment": ["positive"],
    "topic": ["entertainment", "movie"],
    "quality": ["high"]
  },
  "confidence": 0.92,
  "reasoning": "文本表达了对电影的积极评价，提到演员表演和剧情，情感倾向明显为正面",
  "model_used": "anthropic/claude-3.5-haiku",
  "cached": false
}
```

---

### 2. 批量预标注 (Batch Pre-annotate)

**端点：** `POST /api/projects/{project_id}/llm/batch-pre-annotate`

**描述：** 批量为多个任务生成标注建议

**请求参数：**

```typescript
{
  task_ids: string[];          // 任务ID列表
  model?: string;              // 指定模型（可选）
}
```

**请求示例：**

```bash
curl -X POST "http://localhost:8000/api/projects/65abc123/llm/batch-pre-annotate?token=your-token" \
  -H "Content-Type: application/json" \
  -d '{
    "task_ids": ["task1", "task2", "task3"]
  }'
```

**响应示例：**

```json
{
  "total": 3,
  "success": 3,
  "failed": 0,
  "results": [
    {
      "task_id": "task1",
      "status": "success",
      "suggestions": {...},
      "confidence": 0.88
    },
    ...
  ]
}
```

---

### 3. 质量检查 (Quality Check)

**端点：** `POST /api/projects/{project_id}/llm/quality-check`

**描述：** 使用LLM检查标注质量，识别潜在问题

**请求参数：**

```typescript
{
  annotation_id: string;       // 标注ID
}
```

**请求示例：**

```bash
curl -X POST "http://localhost:8000/api/projects/65abc123/llm/quality-check?token=your-token" \
  -H "Content-Type: application/json" \
  -d '{
    "annotation_id": "65xyz789"
  }'
```

**响应示例：**

```json
{
  "annotation_id": "65xyz789",
  "quality_score": 85,
  "is_correct": true,
  "issues": [
    "标注与文本情感略有不匹配"
  ],
  "suggestions": [
    "建议复查'sentiment'标签，文本可能有讽刺意味"
  ],
  "reasoning": "整体标注质量良好，但部分细节值得复查",
  "model_used": "anthropic/claude-3.5-haiku"
}
```

---

### 4. 周报生成 (Weekly Report)

**端点：** `POST /api/projects/{project_id}/llm/weekly-report`

**描述：** 生成项目周报，包含进度、团队表现、质量分析等

**请求参数：**

```typescript
{
  start_date: string;          // 开始日期 (YYYY-MM-DD)
  end_date: string;            // 结束日期 (YYYY-MM-DD)
  include_charts?: boolean;    // 是否包含图表数据（默认true）
  language?: string;           // 语言 zh/en（默认zh）
}
```

**请求示例：**

```bash
curl -X POST "http://localhost:8000/api/projects/65abc123/llm/weekly-report?token=your-token" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2024-01-15",
    "end_date": "2024-01-21",
    "include_charts": true,
    "language": "zh"
  }'
```

**响应示例：**

```json
{
  "report": {
    "title": "OpenCoder项目周报 (2024-01-15 至 2024-01-21)",
    "executive_summary": "本周完成250个标注任务，团队表现稳定，标注质量保持高水平",
    "sections": {
      "progress": {
        "summary": "本周完成率达到95%，超出预期",
        "key_metrics": [
          "完成标注：250个",
          "日均标注：35.7个",
          "参与成员：5人"
        ],
        "analysis": "项目进度良好，团队协作高效..."
      },
      "team_performance": {
        "summary": "团队整体表现优秀",
        "top_performers": ["张三", "李四"],
        "analysis": "张三本周完成80个标注，表现突出..."
      },
      "highlights": [
        "标注质量提升显著",
        "团队协作流畅",
        "无重大质量问题"
      ],
      "issues": [
        "周末标注量下降明显"
      ],
      "quality_insights": "标注一致性良好，质量稳定..."
    },
    "recommendations": [
      "建议增加周末排班",
      "可以考虑引入更多标注者",
      "定期进行质量复核"
    ],
    "conclusion": "整体进展顺利，建议继续保持当前节奏"
  },
  "metadata": {
    "generated_at": "2024-01-22T10:30:00Z",
    "period": {
      "start": "2024-01-15",
      "end": "2024-01-21"
    },
    "data_summary": {
      "total_annotations": 250,
      "team_size": 5,
      "daily_average": 35.7
    },
    "cost": 0.002341
  }
}
```

---

### 5. 项目总结报告 (Project Summary)

**端点：** `POST /api/projects/{project_id}/llm/project-summary`

**描述：** 生成全面的项目总结报告

**请求参数：**

```typescript
{
  include_team_performance?: boolean;    // 包含团队表现（默认true）
  include_quality_analysis?: boolean;    // 包含质量分析（默认true）
  include_recommendations?: boolean;     // 包含改进建议（默认true）
  language?: string;                     // 语言 zh/en（默认zh）
}
```

**请求示例：**

```bash
curl -X POST "http://localhost:8000/api/projects/65abc123/llm/project-summary?token=your-token" \
  -H "Content-Type: application/json" \
  -d '{
    "include_team_performance": true,
    "include_quality_analysis": true,
    "include_recommendations": true,
    "language": "zh"
  }'
```

**响应示例：**

```json
{
  "report": {
    "title": "OpenCoder项目总结报告",
    "executive_summary": "项目历时3个月，完成1500个标注任务，团队协作优秀，标注质量达到预期",
    "project_overview": {
      "description": "NLP数据标注项目",
      "duration": "2024-01-01 至 2024-03-31",
      "team_size": "8人"
    },
    "completion_stats": {
      "summary": "项目完成度98%，超出预期",
      "key_achievements": [
        "完成1500个高质量标注",
        "建立完善的标注流程",
        "培养专业标注团队"
      ],
      "metrics": [
        "完成率: 98%",
        "平均质量分: 92/100",
        "团队稳定性: 优秀"
      ]
    },
    "data_insights": {
      "patterns": [
        "正面评价占比60%，负面20%，中立20%",
        "标注难度呈逐步下降趋势"
      ],
      "trends": [
        "标注效率逐月提升",
        "质量稳定性增强"
      ],
      "surprises": [
        "中立评价比预期少",
        "周末标注量意外高"
      ]
    },
    "team_performance": {
      "summary": "团队整体表现优秀，协作流畅",
      "strengths": [
        "标注一致性高",
        "响应速度快",
        "主动性强"
      ],
      "areas_for_improvement": [
        "可以进一步提升复杂任务处理能力"
      ]
    },
    "quality_assessment": {
      "overall_quality": "优秀（92/100）",
      "consistency": "高度一致",
      "issues": [
        "少量边界情况判断不一致"
      ]
    },
    "recommendations": [
      "建立标注知识库",
      "定期团队培训",
      "引入peer review机制"
    ],
    "conclusion": "项目成功完成，为后续项目积累宝贵经验"
  },
  "metadata": {
    "generated_at": "2024-03-31T15:00:00Z",
    "project_name": "NLP标注项目",
    "data_summary": {
      "total_tasks": 1530,
      "total_annotations": 1500,
      "completion_rate": 98.04
    },
    "cost": 0.004521
  }
}
```

---

### 6. 标注数据分析 (Annotation Analysis)

**端点：** `POST /api/projects/{project_id}/llm/annotation-analysis`

**描述：** 深度分析标注数据，发现模式和洞察

**请求参数：**

```typescript
{
  analysis_type: "distribution" | "trends" | "quality" | "insights";
  start_date?: string;         // 开始日期（可选）
  end_date?: string;           // 结束日期（可选）
  coder_id?: string;           // 特定Coder ID（可选）
  tag_group?: string;          // 特定标签组（可选）
  language?: string;           // 语言（默认zh）
}
```

**分析类型说明：**

- `distribution`: 标签分布分析
- `trends`: 标注趋势分析
- `quality`: 质量一致性分析
- `insights`: 深度洞察分析

**请求示例：**

```bash
curl -X POST "http://localhost:8000/api/projects/65abc123/llm/annotation-analysis?token=your-token" \
  -H "Content-Type: application/json" \
  -d '{
    "analysis_type": "distribution",
    "start_date": "2024-01-01",
    "end_date": "2024-01-31",
    "language": "zh"
  }'
```

**响应示例：**

```json
{
  "analysis": {
    "type": "distribution",
    "summary": "标签分布较为均衡，正面情感标注占60%，负面20%，中立20%，符合预期分布",
    "key_findings": [
      {
        "title": "正面情感占主导",
        "description": "60%的任务被标注为正面情感，这反映了数据源的特点",
        "importance": "high"
      },
      {
        "title": "标签使用一致性高",
        "description": "不同Coder对相似文本的标注高度一致",
        "importance": "high"
      }
    ],
    "metrics": {
      "标签总数": "1500",
      "使用的标签类型": "12",
      "平均每任务标签数": "2.3"
    },
    "patterns": [
      "正面情感主要集中在产品评价类任务",
      "负面情感多出现在服务投诉类任务"
    ],
    "anomalies": [
      "极少数任务同时标注了正面和负面情感"
    ],
    "recommendations": [
      "可以增加中立类任务的样本量",
      "建议明确混合情感的标注规则"
    ]
  },
  "metadata": {
    "generated_at": "2024-01-31T12:00:00Z",
    "annotations_analyzed": 1500,
    "filters_applied": {
      "date_range": "2024-01-01 - 2024-01-31",
      "coder_id": null,
      "tag_group": null
    },
    "cost": 0.001234
  }
}
```

---

### 7. 获取可用模型 (Get Models)

**端点：** `GET /api/llm/models?token=your-token`

**描述：** 获取OpenRouter支持的可用模型列表

**响应示例：**

```json
{
  "models": [
    {
      "id": "anthropic/claude-3.5-haiku",
      "name": "Claude 3.5 Haiku",
      "provider": "Anthropic",
      "description": "快速且便宜，适合大量标注",
      "pricing": {
        "input": 0.80,
        "output": 4.00
      },
      "recommended": true
    },
    {
      "id": "openai/gpt-4o-mini",
      "name": "GPT-4o Mini",
      "provider": "OpenAI",
      "description": "性价比高，准确度好",
      "pricing": {
        "input": 0.15,
        "output": 0.60
      },
      "recommended": true
    }
  ],
  "default_model": "anthropic/claude-3.5-haiku"
}
```

---

### 8. LLM使用统计 (Usage Stats)

**端点：** `GET /api/projects/{project_id}/llm/usage-stats?token=your-token`

**描述：** 查看项目的LLM使用情况和成本

**响应示例：**

```json
{
  "total_calls": 523,
  "total_tokens": 456789,
  "total_cost": 0.234567,
  "by_operation": {
    "suggest_tags": {
      "calls": 450,
      "tokens": 380000,
      "cost": 0.180000
    },
    "weekly_report": {
      "calls": 4,
      "tokens": 50000,
      "cost": 0.030000
    },
    "quality_check": {
      "calls": 69,
      "tokens": 26789,
      "cost": 0.024567
    }
  },
  "by_model": {
    "anthropic/claude-3.5-haiku": {
      "calls": 500,
      "tokens": 440000,
      "cost": 0.220000
    },
    "openai/gpt-4o-mini": {
      "calls": 23,
      "tokens": 16789,
      "cost": 0.014567
    }
  }
}
```

---

## 💰 成本估算

### 模型定价（每百万tokens）

| 模型 | 输入价格 | 输出价格 | 推荐用途 |
|------|---------|---------|---------|
| Claude 3.5 Haiku | $0.80 | $4.00 | 标注建议、质量检查 |
| GPT-4o Mini | $0.15 | $0.60 | 通用任务 |
| Gemini Flash 1.5 | $0.075 | $0.30 | 批量处理 |
| Llama 3.1 8B | $0.055 | $0.055 | 简单任务 |

### 典型操作成本

| 操作 | 平均Token | 预估成本 |
|------|----------|---------|
| 标注建议 | 600 | $0.0003 |
| 质量检查 | 800 | $0.0004 |
| 周报生成 | 3000 | $0.0020 |
| 项目总结 | 5000 | $0.0035 |
| 数据分析 | 2500 | $0.0015 |

### 月度成本示例

假设：
- 每天100个标注建议
- 每周1次周报
- 每月1次项目总结
- 每月5次数据分析

**月度总成本：** 约 $10-15

---

## 🔧 集成示例

### 前端集成（React/TypeScript）

```typescript
// src/services/llm.ts
export const llmService = {
  // 获取标注建议
  async getSuggestion(projectId: string, taskText: string) {
    const response = await fetch(
      `${API_BASE_URL}/api/projects/${projectId}/llm/suggest-tags?token=${getToken()}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_text: taskText,
          use_cache: true
        })
      }
    );
    return response.json();
  },

  // 生成周报
  async generateWeeklyReport(projectId: string, startDate: string, endDate: string) {
    const response = await fetch(
      `${API_BASE_URL}/api/projects/${projectId}/llm/weekly-report?token=${getToken()}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          start_date: startDate,
          end_date: endDate,
          language: 'zh'
        })
      }
    );
    return response.json();
  },

  // 分析标注数据
  async analyzeAnnotations(projectId: string, analysisType: string) {
    const response = await fetch(
      `${API_BASE_URL}/api/projects/${projectId}/llm/annotation-analysis?token=${getToken()}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          analysis_type: analysisType,
          language: 'zh'
        })
      }
    );
    return response.json();
  }
};
```

### 在Coder页面使用

```tsx
// Coder.tsx
const handleGetAISuggestion = async () => {
  try {
    setLoadingSuggestion(true);
    const result = await llmService.getSuggestion(
      projectId,
      currentTask.payload.text
    );
    
    // 自动填充建议的标签
    setSelectedTags(result.suggestions);
    setAiConfidence(result.confidence);
    setAiReasoning(result.reasoning);
    
    toast.success(`AI建议已生成（置信度: ${(result.confidence * 100).toFixed(0)}%）`);
  } catch (error) {
    toast.error('获取AI建议失败');
  } finally {
    setLoadingSuggestion(false);
  }
};
```

### 在Dashboard使用

```tsx
// Dashboard.tsx
const generateWeeklyReport = async () => {
  try {
    setGeneratingReport(true);
    const result = await llmService.generateWeeklyReport(
      projectId,
      startDate,
      endDate
    );
    
    setReport(result.report);
    setShowReportModal(true);
  } catch (error) {
    toast.error('生成周报失败');
  } finally {
    setGeneratingReport(false);
  }
};
```

---

## 🛡️ 安全和最佳实践

### 1. API密钥安全

```bash
# ✅ 正确：存在.env文件
OPENROUTER_API_KEY=sk-or-v1-xxxxx

# ❌ 错误：硬编码在代码中
api_key = "sk-or-v1-xxxxx"  # 永远不要这样做！
```

### 2. 限流控制

后端已实现限流，但前端也应避免频繁调用：

```typescript
// 使用防抖
import { debounce } from 'lodash';

const getSuggestionDebounced = debounce(
  async () => await llmService.getSuggestion(...),
  500
);
```

### 3. 错误处理

```typescript
try {
  const result = await llmService.getSuggestion(...);
  if (result.confidence < 0.5) {
    toast.warning('AI建议置信度较低，请谨慎参考');
  }
} catch (error) {
  if (error.status === 429) {
    toast.error('请求过于频繁，请稍后再试');
  } else if (error.status === 500) {
    toast.error('AI服务暂时不可用');
  }
}
```

### 4. 成本控制

```typescript
// 在UI中显示成本
<div className="text-xs text-gray-500">
  本次查询成本: ${result.metadata.cost.toFixed(6)}
</div>

// 月度成本追踪
const usageStats = await llmService.getUsageStats(projectId);
<div>本月已使用: ${usageStats.total_cost.toFixed(2)}</div>
```

---

## 🧪 测试

### 使用curl测试

```bash
# 1. 测试标注建议
curl -X POST "http://localhost:8000/api/projects/YOUR_PROJECT_ID/llm/suggest-tags?token=YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"task_text": "这是一个测试文本"}'

# 2. 生成周报
curl -X POST "http://localhost:8000/api/projects/YOUR_PROJECT_ID/llm/weekly-report?token=YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "start_date": "2024-01-15",
    "end_date": "2024-01-21"
  }'

# 3. 查看使用统计
curl "http://localhost:8000/api/projects/YOUR_PROJECT_ID/llm/usage-stats?token=YOUR_TOKEN"
```

---

## 📊 监控和调试

### 查看日志

后端会记录所有LLM调用：

```python
print(f"✅ LLM suggestion generated (model: {model}, cost: ${cost:.6f})")
```

### 数据库记录

所有LLM使用都会记录到 `llm_usage` 集合：

```javascript
{
  _id: ObjectId("..."),
  user_id: "65abc123",
  project_id: "65xyz789",
  operation: "suggest_tags",
  model: "anthropic/claude-3.5-haiku",
  tokens_used: 650,
  cost: 0.000325,
  timestamp: ISODate("2024-01-20T10:30:00Z")
}
```

---

## 🚀 部署注意事项

### 环境变量

确保在生产环境设置：

```env
OPENROUTER_API_KEY=sk-or-v1-prod-xxxxx
APP_URL=https://your-production-domain.com
LLM_MODEL=anthropic/claude-3.5-haiku
```

### 性能优化

1. **启用缓存**：使用Redis替代内存缓存
2. **批量处理**：合并多个请求
3. **异步处理**：大任务使用后台队列

### 成本监控

1. 设置月度预算告警
2. 监控异常高频调用
3. 定期审查使用统计

---

## ❓ 常见问题

### Q: 为什么选择OpenRouter而不是直接用OpenAI？

A: OpenRouter的优势：
- 一个API访问多个模型
- 价格通常更便宜
- 自动failover
- 统一的使用追踪

### Q: 如何切换模型？

A: 三种方式：
1. 修改 `.env` 中的 `LLM_MODEL`
2. API请求中指定 `model` 参数
3. 调用 `/api/llm/models` 查看可用模型

### Q: 成本会失控吗？

A: 不会，因为：
- 后端实现了限流
- 缓存机制减少重复调用
- 使用成本追踪和告警
- 每次调用成本极低（$0.0003-0.004）

### Q: 支持本地模型吗？

A: 可以通过OpenRouter使用本地部署的模型，或修改代码直接调用本地API。

---

## 📚 相关资源

- [OpenRouter官网](https://openrouter.ai/)
- [OpenRouter文档](https://openrouter.ai/docs)
- [支持的模型列表](https://openrouter.ai/models)
- [定价信息](https://openrouter.ai/docs#models)
