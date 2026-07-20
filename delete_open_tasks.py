import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

async def delete_open_tasks():
    """删除所有 open 状态的任务"""
    mongodb_uri = os.getenv("MONGODB_URI")
    client = AsyncIOMotorClient(mongodb_uri)
    
    # 检查 proj_123 数据库
    db = client["proj_123"]
    
    print("\n" + "=" * 80)
    print("🗑️  删除待办任务 (status: open)")
    print("=" * 80)
    
    # 先统计数量
    open_count = await db.tasks.count_documents({"status": "open"})
    
    print(f"\n📊 当前统计:")
    print(f"   - 待办任务 (open) 数量: {open_count}")
    
    if open_count == 0:
        print("\n✅ 没有待办任务需要删除")
        client.close()
        return
    
    # 询问确认
    print(f"\n⚠️  警告: 即将删除 {open_count} 个待办任务")
    print("   这个操作不可逆！")
    
    confirm = input("\n是否继续？输入 'YES' 确认删除: ")
    
    if confirm != "YES":
        print("\n❌ 取消删除操作")
        client.close()
        return
    
    # 执行删除
    print("\n🗑️  正在删除...")
    result = await db.tasks.delete_many({"status": "open"})
    
    print(f"\n✅ 删除完成!")
    print(f"   - 已删除 {result.deleted_count} 个任务")
    
    # 验证结果
    remaining_open = await db.tasks.count_documents({"status": "open"})
    total_tasks = await db.tasks.count_documents({})
    
    print(f"\n📊 删除后统计:")
    print(f"   - 剩余待办任务 (open): {remaining_open}")
    print(f"   - 数据库中总任务数: {total_tasks}")
    
    print("\n" + "=" * 80 + "\n")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(delete_open_tasks())
