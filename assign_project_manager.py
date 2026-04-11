"""
手动分配项目管理员
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import os
from dotenv import load_dotenv

load_dotenv()

async def assign_manager():
    """分配项目管理员"""
    # 连接数据库
    mongodb_uri = os.getenv("MONGODB_URI")
    if not mongodb_uri:
        print("❌ MONGODB_URI not found in .env")
        return
    
    client = AsyncIOMotorClient(mongodb_uri)
    core_db = client.app_core
    
    print("👥 查找所有用户...")
    users = await core_db.users.find().to_list(length=None)
    
    if not users:
        print("❌ 没有找到任何用户")
        return
    
    print(f"\n找到 {len(users)} 个用户：")
    for i, user in enumerate(users, 1):
        print(f"{i}. {user.get('name', 'Unknown')} ({user.get('email')})")
        print(f"   ID: {user['_id']}")
        print(f"   Role: {user.get('role', 'None')}")
        print(f"   Project ID: {user.get('project_id', 'None')}")
        print()
    
    print("\n📁 查找所有项目...")
    projects = await core_db.projects.find().to_list(length=None)
    
    if not projects:
        print("❌ 没有找到任何项目")
        return
    
    print(f"\n找到 {len(projects)} 个项目：")
    for i, project in enumerate(projects, 1):
        print(f"{i}. {project.get('name', 'Unknown')}")
        print(f"   ID: {project['_id']}")
        print(f"   Owner ID: {project.get('owner_id', 'None')}")
        print()
    
    # 自动分配：使用第一个用户作为第一个项目的 owner
    if users and projects:
        first_user = users[0]
        
        print(f"\n🔧 自动分配：")
        print(f"   将 {first_user.get('name')} 设为所有项目的管理员")
        
        for project in projects:
            result = await core_db.projects.update_one(
                {"_id": project["_id"]},
                {"$set": {"owner_id": first_user["_id"]}}
            )
            
            if result.modified_count > 0:
                print(f"   ✅ 项目 '{project.get('name')}' 已更新")
        
        print("\n✅ 完成！")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(assign_manager())
