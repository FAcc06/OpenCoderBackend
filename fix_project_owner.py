"""
修复项目的 owner_id
确保所有项目都有正确的管理员
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import os
from dotenv import load_dotenv

load_dotenv()

async def fix_project_owners():
    """修复项目的 owner_id"""
    # 连接数据库
    mongodb_uri = os.getenv("MONGODB_URI")
    if not mongodb_uri:
        print("❌ MONGODB_URI not found in .env")
        return
    
    client = AsyncIOMotorClient(mongodb_uri)
    core_db = client.app_core
    
    print("🔍 检查所有项目...")
    
    # 获取所有项目
    projects = await core_db.projects.find().to_list(length=None)
    
    if not projects:
        print("❌ 没有找到任何项目")
        return
    
    print(f"📊 找到 {len(projects)} 个项目\n")
    
    for project in projects:
        project_id = project["_id"]
        project_name = project.get("name", "Unknown")
        current_owner_id = project.get("owner_id")
        
        print(f"📁 项目: {project_name}")
        print(f"   ID: {project_id}")
        print(f"   当前 owner_id: {current_owner_id}")
        
        if current_owner_id:
            # 验证 owner 是否存在
            owner = await core_db.users.find_one({"_id": ObjectId(current_owner_id)})
            if owner:
                print(f"   ✅ Owner 存在: {owner.get('name')} ({owner.get('email')})")
            else:
                print(f"   ⚠️  Owner ID 无效，需要重新分配")
                current_owner_id = None
        
        if not current_owner_id:
            # 没有 owner，查找 role 为 project-manager 且 project_id 匹配的用户
            print(f"   🔍 查找合适的 Manager...")
            
            # 方法1：查找 project_id 匹配且 role 为 project-manager 的用户
            manager = await core_db.users.find_one({
                "project_id": project_id,
                "role": "project-manager"
            })
            
            if not manager:
                # 方法2：查找创建该项目的用户（通过 created_by 字段，如果有）
                if project.get("created_by"):
                    manager = await core_db.users.find_one({"_id": ObjectId(project.get("created_by"))})
            
            if not manager:
                # 方法3：查找任何有 project-manager role 的用户
                manager = await core_db.users.find_one({"role": "project-manager"})
            
            if manager:
                print(f"   ✅ 找到 Manager: {manager.get('name')} ({manager.get('email')})")
                print(f"   🔧 更新 owner_id...")
                
                result = await core_db.projects.update_one(
                    {"_id": project_id},
                    {"$set": {"owner_id": manager["_id"]}}
                )
                
                if result.modified_count > 0:
                    print(f"   ✅ 成功更新！")
                else:
                    print(f"   ⚠️  更新失败")
            else:
                print(f"   ❌ 未找到合适的 Manager")
                print(f"   💡 建议：手动指定一个用户作为该项目的管理员")
        
        print()
    
    print("✅ 检查完成！")
    client.close()

if __name__ == "__main__":
    asyncio.run(fix_project_owners())
