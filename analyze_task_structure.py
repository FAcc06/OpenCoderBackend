"""
获取真实的 Task 示例并分析数据结构和大小
"""
import os
import asyncio
import json
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from bson import ObjectId
from datetime import datetime
import sys

load_dotenv()

def json_serializer(obj):
    """处理 ObjectId 和 datetime 序列化"""
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

def calculate_size(data):
    """计算数据的实际大小（字节）"""
    json_str = json.dumps(data, default=json_serializer, ensure_ascii=False)
    return len(json_str.encode('utf-8'))

def format_size(bytes_size):
    """格式化字节大小"""
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.2f} KB"
    else:
        return f"{bytes_size / (1024 * 1024):.2f} MB"

async def analyze_tasks():
    """分析 Task 数据结构"""
    mongodb_uri = os.getenv("MONGODB_URI")
    if not mongodb_uri:
        print("❌ MONGODB_URI not found")
        return
    
    print("🔗 Connecting to MongoDB...\n")
    client = AsyncIOMotorClient(mongodb_uri)
    
    try:
        await client.admin.command('ping')
        print("✅ Connected!\n")
        
        # 获取项目数据库
        db = client['proj_68f828dde4b1c9270ec5e23b']
        
        # 获取不同类型的任务示例
        task_types = ['text', 'image', 'audio', 'video', 'bibliographic', 'pdf_document_coding']
        
        print("="*80)
        print("📋 TASK DATA STRUCTURE ANALYSIS")
        print("="*80)
        print()
        
        all_examples = []
        
        for task_type in task_types:
            print(f"\n{'='*80}")
            print(f"📌 Task Type: {task_type.upper()}")
            print(f"{'='*80}\n")
            
            # 查找该类型的任务
            task = await db.tasks.find_one({"task_type": task_type})
            
            if not task:
                print(f"⚠️  No tasks found for type: {task_type}\n")
                continue
            
            # 计算总大小
            total_size = calculate_size(task)
            
            # 美化输出
            task_json = json.dumps(task, default=json_serializer, indent=2, ensure_ascii=False)
            
            print("📄 Complete Task Document:")
            print("-" * 80)
            print(task_json)
            print("-" * 80)
            
            # 分析各部分大小
            print(f"\n📊 Size Breakdown:")
            print(f"{'Component':<30} {'Size':<15} {'Percentage':<15}")
            print("-" * 60)
            
            # 基础字段
            base_fields = {k: v for k, v in task.items() if k != 'payload'}
            base_size = calculate_size(base_fields)
            
            # Payload
            payload = task.get('payload', {})
            payload_size = calculate_size(payload)
            
            # Payload 子字段
            payload_breakdown = {}
            for key, value in payload.items():
                payload_breakdown[key] = calculate_size({key: value})
            
            # 输出
            print(f"{'Base Fields (metadata)':<30} {format_size(base_size):<15} {(base_size/total_size*100):.1f}%")
            print(f"{'Payload (total)':<30} {format_size(payload_size):<15} {(payload_size/total_size*100):.1f}%")
            
            if payload_breakdown:
                print(f"\n  Payload Components:")
                for key, size in sorted(payload_breakdown.items(), key=lambda x: x[1], reverse=True):
                    print(f"  {'  - ' + key:<28} {format_size(size):<15} {(size/total_size*100):.1f}%")
            
            print(f"\n{'TOTAL':<30} {format_size(total_size):<15} {'100.0%':<15}")
            
            # 保存示例
            all_examples.append({
                'task_type': task_type,
                'task_id': str(task['_id']),
                'total_size': total_size,
                'base_size': base_size,
                'payload_size': payload_size,
                'document': task
            })
            
            print()
        
        # 总结
        print("\n" + "="*80)
        print("📈 SUMMARY")
        print("="*80)
        print()
        print(f"{'Task Type':<25} {'Task ID':<30} {'Total Size':<15}")
        print("-" * 70)
        
        for example in all_examples:
            print(f"{example['task_type']:<25} {example['task_id']:<30} {format_size(example['total_size']):<15}")
        
        if all_examples:
            avg_size = sum(e['total_size'] for e in all_examples) / len(all_examples)
            print("-" * 70)
            print(f"{'Average':<25} {'-':<30} {format_size(avg_size):<15}")
        
        # 分析如何分割数据
        print("\n\n" + "="*80)
        print("🔧 DATA SPLITTING STRATEGIES")
        print("="*80)
        print()
        
        print("Strategy 1: Separate Collections")
        print("-" * 80)
        print("""
For each task, store in separate collections:

1. tasks (metadata only):
   - _id, task_type, title, status, tags, created_at, etc.
   - Size: ~200-300 B per task
   
2. task_payloads (content):
   - task_id (reference), payload data
   - Size: varies by type (500 B - 5 KB)
   
3. task_media_refs (media references):
   - task_id, drive_file_id, drive_download_url, mime_type
   - Size: ~100-200 B per task

Benefits:
✅ Faster metadata queries
✅ Payload loaded only when needed
✅ Media references can be lazy-loaded
✅ Easier to index and cache
""")
        
        print("\nStrategy 2: Embedded vs Referenced")
        print("-" * 80)
        print("""
Current (Embedded):
{
  "task_type": "text",
  "payload": {
    "text": "Very long text content...",
    "metadata": {...}
  }
}
Size: All in one document

Optimized (Referenced):
tasks collection:
{
  "_id": "123",
  "task_type": "text",
  "title": "...",
  "payload_ref": "payload_123"
}

payloads collection:
{
  "_id": "payload_123",
  "text": "Very long text content...",
  "metadata": {...}
}

Benefits:
✅ List/search queries don't load large content
✅ Content can be cached separately
✅ Easier to implement pagination
""")
        
        print("\nStrategy 3: Field-level Splitting")
        print("-" * 80)
        print("""
For large fields, use GridFS or external storage:

1. Small data (< 1 KB): Store in task document
   - text snippets, titles, IDs, URLs
   
2. Medium data (1-100 KB): Store in separate collection
   - abstracts, bibliographic metadata, annotations
   
3. Large data (> 100 KB): Store in GridFS or S3
   - full documents, PDFs, large text corpus
   
4. Binary/Media: Always in external storage
   - images, videos, audio → Google Drive
   - PDFs → Google Drive
""")
        
        # 分析当前项目数据
        print("\n\n" + "="*80)
        print("📊 CURRENT PROJECT DATA ANALYSIS")
        print("="*80)
        print()
        
        # 统计各类型数量和总大小
        pipeline = [
            {
                "$group": {
                    "_id": "$task_type",
                    "count": {"$sum": 1},
                    "avgSize": {"$avg": {"$bsonSize": "$$ROOT"}},
                    "totalSize": {"$sum": {"$bsonSize": "$$ROOT"}}
                }
            },
            {"$sort": {"totalSize": -1}}
        ]
        
        stats = await db.tasks.aggregate(pipeline).to_list(length=None)
        
        print(f"{'Task Type':<20} {'Count':<10} {'Avg Size':<15} {'Total Size':<15} {'% of Total':<12}")
        print("-" * 80)
        
        grand_total = sum(s['totalSize'] for s in stats)
        
        for stat in stats:
            task_type = stat['_id'] or 'undefined'
            count = stat['count']
            avg_size = stat['avgSize']
            total_size = stat['totalSize']
            percentage = (total_size / grand_total * 100) if grand_total > 0 else 0
            
            print(f"{task_type:<20} {count:<10} {format_size(avg_size):<15} {format_size(total_size):<15} {percentage:>6.1f}%")
        
        print("-" * 80)
        print(f"{'TOTAL':<20} {sum(s['count'] for s in stats):<10} {'-':<15} {format_size(grand_total):<15} {'100.0%':>7}")
        
        # 计算如果分割后能节省多少
        print("\n\n" + "="*80)
        print("💡 POTENTIAL SAVINGS WITH DATA SPLITTING")
        print("="*80)
        print()
        
        print("Scenario: List all tasks (for assignment/overview)")
        print("-" * 80)
        print()
        print("Current approach (load full documents):")
        print(f"  Total data loaded: {format_size(grand_total)}")
        print()
        print("With metadata-only collection:")
        estimated_metadata_size = sum(s['count'] for s in stats) * 250  # ~250B per metadata
        print(f"  Total data loaded: {format_size(estimated_metadata_size)}")
        print(f"  Savings: {format_size(grand_total - estimated_metadata_size)} ({((grand_total - estimated_metadata_size)/grand_total*100):.1f}%)")
        print()
        print("With lazy-loading payloads:")
        print(f"  Initial load: {format_size(estimated_metadata_size)}")
        print(f"  Per task detail: ~{format_size(grand_total / sum(s['count'] for s in stats))}")
        print()
        
    finally:
        client.close()
        print("\n✅ Analysis complete!")

if __name__ == "__main__":
    asyncio.run(analyze_tasks())
