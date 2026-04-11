# Consensus 功能修复报告
生成时间: 2026-04-11

## 🐛 问题诊断

### 用户问题
Consensus 页面没有显示任何冲突，即使数据库中有多个 coder 对同一 task 的不同标注。

### 根本原因
**数据格式不匹配！**

- **数据库存储格式** (List/Array):
  ```python
  [
    {'group_id': 'sentiment_1761100212558', 'option_ids': ['Positive']},
    {'group_id': 'support_system_1761100298583', 'option_ids': ['School']}
  ]
  ```

- **`compare_labels()` 函数期望的格式** (String):
  ```python
  "sentiment_123:Positive;support_system_456:School"
  ```

### 检查结果

✅ **数据库中有 10 个冲突的 tasks**:
- 每个 task 都有 2-3 个不同 coder 的标注
- Labels 确实不同（例如: Positive vs Negative）
- 当前用户参与了所有 10 个冲突 tasks

❌ **但 API 返回 0 个冲突**:
- `compare_labels()` 无法解析 List 格式
- 所有比较都失败，导致 `has_conflict: False`

## ✅ 解决方案

### 修改的文件
`routers/consensus.py` - `compare_labels()` 函数

### 关键修改

1. **支持 List 格式**（主要格式）:
   ```python
   if isinstance(labels, list):
       for item in labels:
           group_id = item.get('group_id', '')
           option_ids = item.get('option_ids', [])
           result[group_id] = sorted(option_ids)
   ```

2. **保持 String 格式兼容**（向后兼容）:
   ```python
   if isinstance(labels, str):
       parts = labels.split(';')
       for part in parts:
           # 原有逻辑...
   ```

3. **移除时间戳后缀**:
   ```python
   # sentiment_1761100212558 → sentiment
   base_group_id = '_'.join(group_id.split('_')[:-1])
   ```

4. **支持多选选项**:
   ```python
   result[group_id] = sorted(option_ids)  # 排序后比较
   ```

### 测试结果

✅ **测试 1 - List 格式 (不同)**:
```python
labels1 = [{'group_id': 'sentiment_...', 'option_ids': ['Positive']}]
labels2 = [{'group_id': 'sentiment_...', 'option_ids': ['Negative']}]
# Result: has_conflict = True, conflict_count = 2 ✅
```

✅ **测试 2 - List 格式 (相同)**:
```python
labels1 = labels1
labels2 = labels1
# Result: has_conflict = False ✅
```

✅ **测试 3 - String 格式 (向后兼容)**:
```python
labels1 = "sentiment_123:Positive"
labels2 = "sentiment_123:Negative"
# Result: has_conflict = True ✅
```

## 📊 预期结果

修复后，Consensus 页面应该显示：
- ✅ **10 个冲突的 tasks**
- ✅ 每个 task 显示不同 coders 的 labels
- ✅ 高亮显示哪些 tag groups 有冲突
- ✅ 允许 coders 一起讨论并提交 consensus

## 🔄 后续步骤

1. ✅ 后端已自动重新加载（--reload 模式）
2. 🔄 刷新前端浏览器，访问 Consensus 页面
3. ✅ 应该看到 10 个冲突 tasks

## 📝 其他发现

- ✅ 所有数据库调用都正确使用 `get_project_db()`
- ✅ 前端 `is_me` 逻辑完全正确
- ✅ 数据库架构设计合理
- 🔧 修复了 `llm_v2.py` 中缺少 `await` 的 bug

## ✨ 总结

**问题**: 数据格式不匹配导致 consensus 检测失败  
**修复**: 重写 `compare_labels()` 支持 List 和 String 两种格式  
**结果**: Consensus 功能现在应该正常工作！✅
