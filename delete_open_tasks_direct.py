import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

async def delete_open_tasks_direct():
    """直接删除所有 open 状态的任务（无需确认）"""
    mongodb_uri = os.getenv("MONGODB_URI")
    client = AsyncIOMotorClient(mongodb_uri)
    
    # 检查 proj_123 数据库
    db = client["proj_123"]
    
    print("\n" + "=" * 80)
    print("🗑️  删除待办任务 (status: open)")
    print("=" * 80)
    
    # 先统计数量
    open_count = await db.tasks.count_documents({"status": "open"})
    
    print(f"\n📊 删除前统计:")
    print(f"   - 待办任务 (open) 数量: {open_count}")
    
    if open_count == 0:
        print("\n✅ 没有待办任务需要删除")
        client.close()
        return
    
    # 直接执行删除
    print(f"\n🗑️  正在删除 {open_count} 个待办任务...")
    result = await db.tasks.delete_many({"status": "open"})
    
    print(f"\n✅ 删除完成!")
    print(f"   - 已删除 {result.deleted_count} 个任务")
    
    # 验证结果
    remaining_open = await db.tasks.count_documents({"status": "open"})
    total_tasks = await db.tasks.count_documents({})
    done_tasks = await db.tasks.count_documents({"status": "done"})
    
    print(f"\n📊 删除后统计:")
    print(f"   - 剩余待办任务 (open): {remaining_open}")
    print(f"   - 已完成任务 (done): {done_tasks}")
    print(f"   - 数据库中总任务数: {total_tasks}")
    
    print("\n" + "=" * 80 + "\n")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(delete_open_tasks_direct())
