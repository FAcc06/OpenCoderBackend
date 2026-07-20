import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

async def migrate_data():
    mongodb_uri = os.getenv("MONGODB_URI")
    client = AsyncIOMotorClient(mongodb_uri)
    
    # 源数据库和目标数据库
    source_db = client["proj_123"]
    target_db = client["proj_68f828dde4b1c9270ec5e23b"]
    
    print("\n" + "="*60)
    print("数据迁移：proj_123 -> proj_68f828dde4b1c9270ec5e23b")
    print("="*60 + "\n")
    
    # 1. 迁移 tasks
    print("1. 迁移 Tasks...")
    tasks = await source_db.tasks.find().to_list(length=None)
    print(f"   找到 {len(tasks)} 个任务")
    
    if tasks:
        # 先清空目标数据库的 tasks（如果有的话）
        await target_db.tasks.delete_many({})
        # 插入所有任务
        result = await target_db.tasks.insert_many(tasks)
        print(f"   ✅ 成功迁移 {len(result.inserted_ids)} 个任务")
    
    # 2. 迁移 assignments
    print("\n2. 迁移 Assignments...")
    assignments = await source_db.assignments.find().to_list(length=None)
    print(f"   找到 {len(assignments)} 个分配")
    
    if assignments:
        # 先清空目标数据库的 assignments
        await target_db.assignments.delete_many({})
        # 插入所有分配
        result = await target_db.assignments.insert_many(assignments)
        print(f"   ✅ 成功迁移 {len(result.inserted_ids)} 个分配")
    
    # 3. 迁移 annotations
    print("\n3. 迁移 Annotations...")
    annotations = await source_db.annotations.find().to_list(length=None)
    print(f"   找到 {len(annotations)} 个标注")
    
    if annotations:
        await target_db.annotations.delete_many({})
        result = await target_db.annotations.insert_many(annotations)
        print(f"   ✅ 成功迁移 {len(result.inserted_ids)} 个标注")
    
    # 4. 迁移 tag_groups
    print("\n4. 迁移 Tag Groups...")
    tag_groups = await source_db.tag_groups.find().to_list(length=None)
    print(f"   找到 {len(tag_groups)} 个标签组")
    
    if tag_groups:
        await target_db.tag_groups.delete_many({})
        result = await target_db.tag_groups.insert_many(tag_groups)
        print(f"   ✅ 成功迁移 {len(result.inserted_ids)} 个标签组")
    
    # 5. 验证迁移结果
    print("\n5. 验证迁移结果...")
    target_tasks = await target_db.tasks.count_documents({})
    target_assignments = await target_db.assignments.count_documents({})
    target_annotations = await target_db.annotations.count_documents({})
    target_tag_groups = await target_db.tag_groups.count_documents({})
    
    print(f"   目标数据库统计:")
    print(f"   - Tasks: {target_tasks}")
    print(f"   - Assignments: {target_assignments}")
    print(f"   - Annotations: {target_annotations}")
    print(f"   - Tag Groups: {target_tag_groups}")
    
    print("\n" + "="*60)
    print("迁移完成！")
    print("="*60 + "\n")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(migrate_data())
