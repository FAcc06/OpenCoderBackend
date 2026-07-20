import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from bson import ObjectId

load_dotenv()

async def diagnose():
    mongodb_uri = os.getenv("MONGODB_URI")
    client = AsyncIOMotorClient(mongodb_uri)
    
    # 使用项目数据库
    project_id = "68f828dde4b1c9270ec5e23b"
    db = client[f"proj_{project_id}"]
    
    print("\n" + "="*60)
    print("诊断报告：Assignments 和 Tasks 状态")
    print("="*60 + "\n")
    
    # 1. 检查所有 assignments
    print("1. 所有 Assignments:")
    assignments = await db.assignments.find().to_list(length=None)
    print(f"   总数: {len(assignments)}")
    
    for i, assgn in enumerate(assignments, 1):
        print(f"\n   Assignment #{i}:")
        print(f"   - ID: {assgn['_id']}")
        print(f"   - Coder User ID: {assgn.get('coder_user_id')}")
        print(f"   - Task ID: {assgn.get('task_id')}")
        print(f"   - State: {assgn.get('state')}")
        print(f"   - Created: {assgn.get('created_at')}")
        
        # 查找对应的任务
        task = await db.tasks.find_one({"_id": assgn.get('task_id')})
        if task:
            print(f"   - Task Type: {task.get('task_type', 'N/A')}")
            print(f"   - Task Title: {task.get('title', 'N/A')[:50]}")
            print(f"   - Task Status: {task.get('status', 'N/A')}")
        else:
            print(f"   - ⚠️ Task not found!")
    
    # 2. 检查所有任务
    print("\n\n2. 所有 Tasks:")
    tasks = await db.tasks.find().to_list(length=None)
    print(f"   总数: {len(tasks)}")
    
    task_by_type = {}
    task_by_status = {}
    
    for task in tasks:
        task_type = task.get('task_type', 'unknown')
        status = task.get('status', 'unknown')
        
        task_by_type[task_type] = task_by_type.get(task_type, 0) + 1
        task_by_status[status] = task_by_status.get(status, 0) + 1
    
    print("\n   按类型分组:")
    for t_type, count in task_by_type.items():
        print(f"   - {t_type}: {count}")
    
    print("\n   按状态分组:")
    for status, count in task_by_status.items():
        print(f"   - {status}: {count}")
    
    # 3. 检查 Coder 用户
    print("\n\n3. 项目中的 Coder 用户:")
    core_db = client["app_core"]
    users = await core_db.users.find({"project_id": ObjectId(project_id)}).to_list(length=None)
    
    for user in users:
        print(f"\n   User:")
        print(f"   - ID: {user['_id']}")
        print(f"   - Email: {user.get('email')}")
        print(f"   - Name: {user.get('name')}")
        print(f"   - Role: {user.get('role')}")
        
        # 检查该用户的分配
        user_assignments = await db.assignments.find({"coder_user_id": user['_id']}).to_list(length=None)
        print(f"   - Assignments: {len(user_assignments)}")
        
        for assgn in user_assignments:
            print(f"     * State: {assgn.get('state')}, Task ID: {assgn.get('task_id')}")
    
    print("\n" + "="*60)
    print("诊断完成")
    print("="*60 + "\n")
    
    client.close()

if __name__ == "__main__":
    asyncio.run(diagnose())
