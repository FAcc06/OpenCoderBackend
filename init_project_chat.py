"""
初始化项目群聊
为每个项目创建一个默认的项目群聊对话
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

async def init_project_chats():
    """初始化项目群聊"""
    # 连接数据库
    mongodb_uri = os.getenv("MONGODB_URI")
    if not mongodb_uri:
        print("❌ MONGODB_URI not found in .env")
        return
    
    client = AsyncIOMotorClient(mongodb_uri)
    core_db = client.app_core
    
    print("🔍 查找所有项目...")
    projects = await core_db.projects.find().to_list(length=None)
    
    for project in projects:
        project_id = project["_id"]
        project_name = project.get("name", "Unknown")
        db_name = project.get("db_name")
        
        print(f"\n📁 项目: {project_name} (ID: {project_id})")
        
        if not db_name:
            print("   ⚠️  没有 db_name，跳过")
            continue
        
        project_db = client[db_name]
        
        # 获取项目所有成员（memberships，非 active shell）
        memberships = await core_db.project_memberships.find({
            "project_id": project_id,
            "status": "active",
        }).to_list(length=None)

        participant_ids = [m["user_id"] for m in memberships if m.get("user_id")]
        # Include owner if missing
        owner = project.get("owner_user_id")
        if owner and owner not in participant_ids and str(owner) not in [str(p) for p in participant_ids]:
            participant_ids.append(owner)

        if not participant_ids:
            print("   ⚠️  没有成员，跳过")
            continue
        print(f"   👥 找到 {len(participant_ids)} 个成员")
        
        # 检查是否已有项目群聊
        existing = await project_db.conversations.find_one({
            "type": "project_group"
        })
        
        if existing:
            print(f"   ✅ 项目群聊已存在 (ID: {existing['_id']})")
            
            # 更新参与者列表（可能有新成员加入）
            result = await project_db.conversations.update_one(
                {"_id": existing["_id"]},
                {"$set": {"participants": participant_ids, "updated_at": datetime.utcnow()}}
            )
            if result.modified_count > 0:
                print(f"   🔄 更新了参与者列表")
        else:
            # 创建新的项目群聊
            conversation = {
                "type": "project_group",
                "participants": participant_ids,
                "project_id": project_id,
                "name": f"{project_name} Team Chat",
                "last_message": None,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
            
            result = await project_db.conversations.insert_one(conversation)
            print(f"   ✅ 创建了项目群聊 (ID: {result.inserted_id})")
    
    print("\n✅ 初始化完成！")
    client.close()

if __name__ == "__main__":
    asyncio.run(init_project_chats())
