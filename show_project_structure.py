"""
显示项目结构和数据存储位置
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

async def show_project_structure():
    """显示项目结构"""
    # 连接数据库
    mongodb_uri = os.getenv("MONGODB_URI")
    if not mongodb_uri:
        print("❌ MONGODB_URI not found in .env")
        return
    
    client = AsyncIOMotorClient(mongodb_uri)
    core_db = client.app_core
    
    print("=" * 100)
    print("🏢 项目结构和数据存储位置")
    print("=" * 100)
    
    # 获取所有用户
    users = await core_db.users.find().to_list(length=None)
    
    # 获取所有项目
    projects = await core_db.projects.find().to_list(length=None)
    
    for project in projects:
        project_id = str(project["_id"])
        project_name = project.get("name", "Unknown")
        slug = project.get("slug")
        db_name = project.get("db_name")
        owner_id = project.get("owner_id")
        
        print(f"\n\n📁 项目: {project_name}")
        print("=" * 100)
        print(f"   🔑 项目ID (ObjectId):  {project_id}")
        print(f"   🏷️  项目Slug:           {slug}")
        print(f"   💾 数据库名称:          {db_name}")
        
        # 显示项目所有者
        if owner_id:
            owner = await core_db.users.find_one({"_id": ObjectId(owner_id)})
            if owner:
                print(f"   👤 项目管理员:          {owner.get('name')} ({owner.get('email')})")
            else:
                print(f"   👤 项目管理员:          ID {owner_id} (用户不存在)")
        else:
            print(f"   👤 项目管理员:          未设置")
        
        # 显示团队成员
        print(f"\n   👥 团队成员:")
        project_users = [u for u in users if str(u.get('project_id')) == project_id]
        if project_users:
            for user in project_users:
                role = user.get('role', 'unknown')
                print(f"      - {user.get('name')} ({user.get('email')}) - {role}")
        else:
            print(f"      无团队成员")
        
        # 检查数据库中的数据
        if db_name:
            print(f"\n   📊 数据库统计 ({db_name}):")
            project_db = client[db_name]
            
            # 统计各个collection
            collections = await project_db.list_collection_names()
            if collections:
                for collection_name in ['tasks', 'annotations', 'assignments', 'tag_groups']:
                    if collection_name in collections:
                        count = await project_db[collection_name].count_documents({})
                        print(f"      - {collection_name}: {count} 条")
                        
                        # 如果有annotation，显示详细信息
                        if collection_name == 'annotations' and count > 0:
                            annotations = await project_db[collection_name].find().to_list(length=None)
                            
                            # 按coder统计
                            coder_stats = {}
                            date_stats = {}
                            
                            for ann in annotations:
                                coder_id = str(ann.get('coder_user_id'))
                                if coder_id not in coder_stats:
                                    coder_stats[coder_id] = 0
                                coder_stats[coder_id] += 1
                                
                                # 统计日期
                                created_at = ann.get('completed_at') or ann.get('created_at')
                                if created_at:
                                    date_str = created_at.strftime("%Y-%m-%d")
                                    if date_str not in date_stats:
                                        date_stats[date_str] = 0
                                    date_stats[date_str] += 1
                            
                            print(f"\n      📈 标注详情:")
                            print(f"         按用户统计:")
                            for coder_id, count in coder_stats.items():
                                coder = await core_db.users.find_one({"_id": ObjectId(coder_id)})
                                coder_name = coder.get('name', 'Unknown') if coder else 'Unknown'
                                print(f"            - {coder_name}: {count} 个")
                            
                            print(f"\n         按日期统计:")
                            for date, count in sorted(date_stats.items()):
                                print(f"            - {date}: {count} 个")
                            
                            # 显示日期范围
                            valid_dates = [a.get('completed_at') or a.get('created_at') for a in annotations if a.get('completed_at') or a.get('created_at')]
                            if valid_dates:
                                min_date = min(valid_dates)
                                max_date = max(valid_dates)
                                print(f"\n         📅 时间范围: {min_date.strftime('%Y-%m-%d')} 到 {max_date.strftime('%Y-%m-%d')}")
            else:
                print(f"      (数据库为空)")
        
        print(f"\n   🔗 前端访问地址:")
        print(f"      Manager Dashboard: /project-manager/dashboard")
        print(f"      需要使用项目ID: {project_id}")
    
    print("\n" + "=" * 100)
    print("\n📝 总结:")
    print(f"   - 总共 {len(projects)} 个项目")
    print(f"   - 总共 {len(users)} 个用户")
    
    # 显示没有分配项目的用户
    unassigned_users = [u for u in users if not u.get('project_id')]
    if unassigned_users:
        print(f"\n   ⚠️  未分配项目的用户:")
        for user in unassigned_users:
            print(f"      - {user.get('name')} ({user.get('email')})")
    
    print("\n" + "=" * 100)
    client.close()

if __name__ == "__main__":
    asyncio.run(show_project_structure())
