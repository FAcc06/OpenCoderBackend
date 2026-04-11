# 🚀 LLM功能快速开始

## 1️⃣ 获取OpenRouter API密钥

1. 访问 https://openrouter.ai/
2. 注册/登录账号
3. 前往 https://openrouter.ai/keys
4. 点击 "Create Key"
5. 复制生成的密钥（格式：`sk-or-v1-xxxxxxxxxx`）

## 2️⃣ 配置环境变量

在 `OpenCoderBackend/.env` 文件中添加：

```env
OPENROUTER_API_KEY=sk-or-v1-你的密钥
LLM_MODEL=anthropic/claude-3.5-haiku
LLM_TEMPERATURE=0.3
LLM_MAX_TOKENS=1000
APP_URL=http://localhost:8000
```

## 3️⃣ 启动后端

```bash
cd OpenCoderBackend
python main.py
```

看到以下输出表示成功：

```
[OK] Connected to MongoDB Atlas
INFO:     Application startup complete.
```

## 4️⃣ 测试API

### 方法1：使用curl

```bash
# 替换 YOUR_PROJECT_ID 和 YOUR_TOKEN
curl -X POST "http://localhost:8000/api/projects/YOUR_PROJECT_ID/llm/suggest-tags?token=YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_text": "这部电影非常精彩，值得一看！"
  }'
```

### 方法2：使用FastAPI文档

1. 访问 http://localhost:8000/docs
2. 找到 "llm" 标签下的接口
3. 点击 "Try it out"
4. 输入参数并点击 "Execute"

## 5️⃣ 前端集成示例

### 在Coder页面添加"AI建议"按钮

```tsx
// Coder.tsx
const [aiSuggestion, setAiSuggestion] = useState(null);
const [loadingSuggestion, setLoadingSuggestion] = useState(false);

const handleGetAISuggestion = async () => {
  setLoadingSuggestion(true);
  try {
    const response = await fetch(
      `${apiBaseUrl}/api/projects/${projectId}/llm/suggest-tags?token=${token}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_text: currentTask.payload.text,
          use_cache: true
        })
      }
    );
    
    const result = await response.json();
    
    // 自动填充建议的标签
    setSelectedTags(result.suggestions);
    setAiSuggestion(result);
    
    toast.success(`AI建议已生成（置信度: ${(result.confidence * 100).toFixed(0)}%）`);
  } catch (error) {
    toast.error('获取AI建议失败');
  } finally {
    setLoadingSuggestion(false);
  }
};

// 在UI中添加按钮
<Button 
  onClick={handleGetAISuggestion} 
  disabled={loadingSuggestion}
  variant="outline"
>
  {loadingSuggestion ? (
    <>
      <Loader2 className="h-4 w-4 animate-spin mr-2" />
      生成中...
    </>
  ) : (
    <>
      <Sparkles className="h-4 w-4 mr-2" />
      获取AI建议
    </>
  )}
</Button>

{/* 显示AI建议 */}
{aiSuggestion && (
  <div className="mt-4 p-4 bg-blue-50 rounded-lg border border-blue-200">
    <div className="flex items-center gap-2 mb-2">
      <Sparkles className="h-4 w-4 text-blue-600" />
      <span className="font-medium text-blue-900">AI建议</span>
      <Badge variant="secondary">
        置信度: {(aiSuggestion.confidence * 100).toFixed(0)}%
      </Badge>
    </div>
    <p className="text-sm text-blue-700">{aiSuggestion.reasoning}</p>
  </div>
)}
```

### 在Dashboard添加"生成周报"按钮

```tsx
// Dashboard.tsx
const [report, setReport] = useState(null);
const [generatingReport, setGeneratingReport] = useState(false);

const generateWeeklyReport = async () => {
  setGeneratingReport(true);
  try {
    const endDate = new Date().toISOString().split('T')[0];
    const startDate = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)
      .toISOString().split('T')[0];
    
    const response = await fetch(
      `${apiBaseUrl}/api/projects/${projectId}/llm/weekly-report?token=${token}`,
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
    
    const result = await response.json();
    setReport(result.report);
    
    toast.success('周报生成成功');
  } catch (error) {
    toast.error('生成周报失败');
  } finally {
    setGeneratingReport(false);
  }
};

// 添加按钮
<Button onClick={generateWeeklyReport} disabled={generatingReport}>
  {generatingReport ? (
    <>
      <Loader2 className="h-4 w-4 animate-spin mr-2" />
      生成中...
    </>
  ) : (
    <>
      <FileText className="h-4 w-4 mr-2" />
      生成周报
    </>
  )}
</Button>

{/* 显示报告 */}
{report && (
  <Card className="mt-6">
    <CardHeader>
      <CardTitle>{report.title}</CardTitle>
      <CardDescription>{report.executive_summary}</CardDescription>
    </CardHeader>
    <CardContent>
      {/* 渲染报告内容 */}
      <div className="space-y-4">
        <div>
          <h3 className="font-semibold mb-2">📊 进度统计</h3>
          <p>{report.sections.progress.summary}</p>
          <ul className="list-disc pl-5 mt-2">
            {report.sections.progress.key_metrics.map((metric, i) => (
              <li key={i}>{metric}</li>
            ))}
          </ul>
        </div>
        
        <div>
          <h3 className="font-semibold mb-2">✨ 亮点</h3>
          <ul className="list-disc pl-5">
            {report.sections.highlights.map((highlight, i) => (
              <li key={i}>{highlight}</li>
            ))}
          </ul>
        </div>
        
        <div>
          <h3 className="font-semibold mb-2">💡 建议</h3>
          <ul className="list-disc pl-5">
            {report.recommendations.map((rec, i) => (
              <li key={i}>{rec}</li>
            ))}
          </ul>
        </div>
      </div>
    </CardContent>
  </Card>
)}
```

## 6️⃣ 常用接口速查

| 功能 | 端点 | 用途 |
|------|------|------|
| 标注建议 | `POST /api/projects/{id}/llm/suggest-tags` | Coder标注时获取AI建议 |
| 批量预标注 | `POST /api/projects/{id}/llm/batch-pre-annotate` | Manager上传任务后批量生成建议 |
| 质量检查 | `POST /api/projects/{id}/llm/quality-check` | Manager检查标注质量 |
| 周报生成 | `POST /api/projects/{id}/llm/weekly-report` | Dashboard生成周报 |
| 项目总结 | `POST /api/projects/{id}/llm/project-summary` | 项目结束时生成总结 |
| 数据分析 | `POST /api/projects/{id}/llm/annotation-analysis` | 分析标注数据 |
| 使用统计 | `GET /api/projects/{id}/llm/usage-stats` | 查看LLM使用情况和成本 |

## 7️⃣ 成本参考

使用默认模型（Claude 3.5 Haiku）：

- 标注建议：约 $0.0003/次
- 质量检查：约 $0.0004/次
- 周报生成：约 $0.002/次
- 项目总结：约 $0.0035/次

**预估月度成本：** $10-20（假设每天100次标注建议 + 每周1次周报）

## 8️⃣ 常见问题

### Q: 如何查看我的API密钥余额？

A: 访问 https://openrouter.ai/credits

### Q: 调用失败怎么办？

A: 检查：
1. API密钥是否正确配置
2. 后端日志中的错误信息
3. OpenRouter服务状态：https://status.openrouter.ai/

### Q: 如何更换模型？

A: 修改 `.env` 中的 `LLM_MODEL`，例如：
- `anthropic/claude-3.5-haiku` (推荐，便宜快速)
- `openai/gpt-4o-mini` (OpenAI，质量好)
- `google/gemini-flash-1.5` (Google，速度快)

完整模型列表：https://openrouter.ai/models

### Q: 如何限制成本？

A: 在OpenRouter后台设置：
1. 月度预算上限
2. 单次调用上限
3. 告警通知

## 9️⃣ 下一步

- 📚 查看完整文档：`LLM_API_DOCUMENTATION.md`
- 🧪 测试所有接口：访问 http://localhost:8000/docs
- 💡 自定义Prompt：修改 `routers/llm.py` 中的提示词
- 🎨 优化UI：根据业务需求调整前端展示

## 🆘 获取帮助

- OpenRouter文档：https://openrouter.ai/docs
- 项目Issues：提交问题和建议
- 社区讨论：Discord/Slack频道

---

**祝您使用愉快！🎉**
