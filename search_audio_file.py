import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

async def search_audio_file():
    """搜索特定的音频文件"""
    mongodb_uri = os.getenv("MONGODB_URI")
    client = AsyncIOMotorClient(mongodb_uri)
    
    filename = "file_example_MP3_700KB.mp3"
    
    print(f"\n🔍 搜索音频文件: {filename}\n")
    print("=" * 80)
    
    # 搜索所有项目数据库
    project_dbs = ["proj_123", "proj_test", "proj_demo"]
    
    found = False
    
    for db_name in project_dbs:
        db = client[db_name]
        
        # 搜索包含此文件名的任务
        # 可能在 title 或 payload.audio.original_filename 中
        tasks = await db.tasks.find({
            "$or": [
                {"title": {"$regex": filename, "$options": "i"}},
                {"payload.audio.original_filename": {"$regex": filename, "$options": "i"}}
            ]
        }).to_list(length=None)
        
        if tasks:
            found = True
            print(f"\n✅ 在 {db_name} 数据库中找到 {len(tasks)} 个任务:\n")
            
            for task in tasks:
                print(f"   任务 ID: {task['_id']}")
                print(f"   标题: {task.get('title', 'N/A')}")
                print(f"   任务类型: {task.get('task_type', 'N/A')}")
                print(f"   状态: {task.get('status', 'N/A')}")
                print(f"   创建时间: {task.get('created_at', 'N/A')}")
                
                # 显示音频信息
                if 'payload' in task and 'audio' in task['payload']:
                    audio = task['payload']['audio']
                    print(f"   音频信息:")
                    print(f"      - 文件名: {audio.get('original_filename', 'N/A')}")
                    print(f"      - 文件大小: {audio.get('file_size', 0) / 1024:.2f} KB")
                    print(f"      - MIME 类型: {audio.get('mime_type', 'N/A')}")
                    print(f"      - Google Drive ID: {audio.get('drive_file_id', 'N/A')}")
                    print(f"      - Drive URL: {audio.get('drive_file_url', 'N/A')}")
                    print(f"      - 上传时间: {audio.get('uploaded_at', 'N/A')}")
                
                print()
    
    if not found:
        print(f"\n❌ 未找到包含 '{filename}' 的任务")
        print("\n可能的原因:")
        print("1. 文件尚未上传")
        print("2. 上传过程中出错")
        print("3. 文件在其他项目数据库中")
        
        # 列出所有音频任务
        print("\n" + "=" * 80)
        print("📋 所有音频/视频任务列表:\n")
        
        for db_name in project_dbs:
            db = client[db_name]
            media_tasks = await db.tasks.find({
                "task_type": {"$in": ["audio", "video"]}
            }).to_list(length=100)
            
            if media_tasks:
                print(f"\n📂 {db_name}:")
                for task in media_tasks:
                    task_type = task.get('task_type', 'N/A')
                    title = task.get('title', 'N/A')
                    created = task.get('created_at', 'N/A')
                    print(f"   [{task_type.upper()}] {title} (创建于 {created})")
    
    print("\n" + "=" * 80)
    client.close()

if __name__ == "__main__":
    asyncio.run(search_audio_file())
