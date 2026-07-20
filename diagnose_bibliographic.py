import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import json

load_dotenv()

async def check_bibliographic_tasks():
    mongodb_uri = os.getenv("MONGODB_URI")
    client = AsyncIOMotorClient(mongodb_uri)

    project_id = "68f828dde4b1c9270ec5e23b"
    project_db = client[f"proj_{project_id}"]

    print("\n" + "="*80)
    print("BIBLIOGRAPHIC TASKS AUDIT")
    print("="*80 + "\n")

    # Check if there are any tasks with metadata.bibliographic
    print("1. Checking for tasks with metadata.bibliographic...")
    tasks_with_bib_meta = await project_db.tasks.count_documents({
        "payload.metadata.bibliographic": {"$exists": True}
    })
    print(f"   Found {tasks_with_bib_meta} tasks with metadata.bibliographic\n")

    # Check if there are any tasks with task_type='bibliographic'
    print("2. Checking for tasks with task_type='bibliographic'...")
    tasks_with_bib_type = await project_db.tasks.count_documents({
        "task_type": "bibliographic"
    })
    print(f"   Found {tasks_with_bib_type} tasks with task_type='bibliographic'\n")

    # Get sample bibliographic task if exists
    if tasks_with_bib_meta > 0:
        print("3. Sample bibliographic task with metadata:")
        sample = await project_db.tasks.find_one({
            "payload.metadata.bibliographic": {"$exists": True}
        })
        if sample:
            print(f"   Task ID: {sample['_id']}")
            print(f"   Title: {sample.get('title', 'N/A')}")
            print(f"   Task Type: {sample.get('task_type', 'N/A')}")
            print(f"   Payload keys: {list(sample.get('payload', {}).keys())}")
            print(f"   Metadata keys: {list(sample.get('payload', {}).get('metadata', {}).keys())}")
            
            bib_meta = sample.get('payload', {}).get('metadata', {}).get('bibliographic', {})
            print(f"\n   Bibliographic metadata:")
            print(f"   {json.dumps(bib_meta, indent=4)}")
            
            print(f"\n   Payload.text preview:")
            text = sample.get('payload', {}).get('text', '')
            print(f"   {text[:200]}...\n" if len(text) > 200 else f"   {text}\n")

    # Check all task types distribution
    print("4. Task types distribution:")
    pipeline = [
        {"$group": {
            "_id": "$task_type",
            "count": {"$sum": 1}
        }},
        {"$sort": {"count": -1}}
    ]
    types_dist = await project_db.tasks.aggregate(pipeline).to_list(None)
    for item in types_dist:
        task_type = item["_id"] or "null"
        count = item["count"]
        print(f"   {task_type}: {count}")

    # Check tasks with metadata field
    print("\n5. Tasks with metadata field:")
    tasks_with_metadata = await project_db.tasks.count_documents({
        "payload.metadata": {"$exists": True}
    })
    print(f"   Total: {tasks_with_metadata}")
    
    if tasks_with_metadata > 0:
        sample_meta = await project_db.tasks.find_one({
            "payload.metadata": {"$exists": True}
        })
        if sample_meta:
            meta_keys = list(sample_meta.get('payload', {}).get('metadata', {}).keys())
            print(f"   Sample metadata keys: {meta_keys}")

    # Check tasks with meta field (typo check)
    print("\n6. Tasks with 'meta' field (not 'metadata'):")
    tasks_with_meta = await project_db.tasks.count_documents({
        "payload.meta": {"$exists": True}
    })
    print(f"   Total: {tasks_with_meta}")

    client.close()

if __name__ == "__main__":
    asyncio.run(check_bibliographic_tasks())
