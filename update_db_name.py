import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from bson import ObjectId

load_dotenv()

async def update_db_name():
    mongodb_uri = os.getenv("MONGODB_URI")
    client = AsyncIOMotorClient(mongodb_uri)
    
    core_db = client["app_core"]
    
    print("\n" + "="*60)
    print("更新项目的 db_name 字段")
    print("="*60 + "\n")
    
    project_id = "68f828dde4b1c9270ec5e23b"
    new_db_name = f"proj_{project_id}"
    
    # 更新项目文档
    result = await core_db.projects.update_one(
        {"_id": ObjectId(project_id)},
        {"$set": {"db_name": new_db_name}}
    )
    
    print(f"更新结果:")
    print(f"  匹配的文档数: {result.matched_count}")
    print(f"  修改的文档数: {result.modified_count}")
    
    # 验证更新
    project = await core_db.projects.find_one({"_id": ObjectId(project_id)})
    if project:
        print(f"\n验证:")
        print(f"  项目名称: {project.get('name')}")
        print(f"  新的 db_name: {project.get('db_name')}")
        
        if project.get('db_name') == new_db_name:
            print(f"\n✅ db_name 已成功更新为: {new_db_name}")
        else:
            print(f"\n❌ 更新失败")
    
    print("\n" + "="*60)
    
    client.close()

if __name__ == "__main__":
    asyncio.run(update_db_name())
