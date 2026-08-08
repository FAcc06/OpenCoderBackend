from fastapi import APIRouter, HTTPException
from typing import List, Optional
from bson import ObjectId
from datetime import datetime

from database import get_core_db, get_project_db
from models import User, Project, Task, Annotation, TagGroup, PaginatedResponse

router = APIRouter()

# 模拟当前用户（用于测试）
CURRENT_USER = {
    "id": "507f1f77bcf86cd799439011",
    "email": "test@example.com",
    "name": "测试用户",
    "role": "manager",
    "project_id": "507f1f77bcf86cd799439012"
}

@router.get("/users/me")
async def get_current_user():
    """获取当前用户信息（无需认证）"""
    return CURRENT_USER

@router.get("/projects")
async def get_projects():
    """获取项目列表（无需认证）"""
    try:
        core_db = get_core_db()
        if core_db is None:
            return {"message": "数据库未连接", "projects": []}
        
        projects = await core_db.projects.find().to_list(length=None)
        # 转换ObjectId为字符串
        for project in projects:
            if '_id' in project:
                project['_id'] = str(project['_id'])
            if 'owner_user_id' in project:
                project['owner_user_id'] = str(project['owner_user_id'])
        
        return {"projects": projects, "total": len(projects)}
    except Exception as e:
        return {"error": str(e), "projects": []}

@router.post("/projects")
async def create_project(project_data: dict):
    """创建项目（无需认证）"""
    try:
        core_db = get_core_db()
        if core_db is None:
            raise HTTPException(status_code=500, detail="数据库未连接")
        
        # 生成唯一的slug
        base_slug = project_data.get("slug", f"project-{ObjectId()}")
        slug = base_slug
        counter = 1
        
        # 检查slug是否已存在，如果存在则添加数字后缀
        while await core_db.projects.find_one({"slug": slug}):
            slug = f"{base_slug}-{counter}"
            counter += 1
        
        project = {
            "_id": ObjectId(),
            "name": project_data.get("name", "新项目"),
            "slug": slug,
            "owner_user_id": ObjectId(CURRENT_USER["id"]),
            "db_name": f"proj_{slug}",
            "cluster_uri": None,
            "status": "active",
            "tags": project_data.get("tags", []),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = await core_db.projects.insert_one(project)
        project["_id"] = str(result.inserted_id)
        project["owner_user_id"] = str(project["owner_user_id"])
        
        return {"message": "项目创建成功", "project": project}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建项目失败: {str(e)}")

@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    """获取项目详情（无需认证）"""
    try:
        core_db = get_core_db()
        if core_db is None:
            raise HTTPException(status_code=500, detail="数据库未连接")
        
        project = await core_db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        
        # 转换ObjectId为字符串
        project['_id'] = str(project['_id'])
        project['owner_user_id'] = str(project['owner_user_id'])
        
        return {"project": project}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取项目失败: {str(e)}")

@router.get("/projects/{project_id}/tasks")
async def get_tasks(project_id: str, status: Optional[str] = None, page: int = 1, limit: int = 10):
    """获取任务列表（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        query_filter = {}
        if status:
            query_filter["status"] = status
        
        total = await project_db.tasks.count_documents(query_filter)
        skip = (page - 1) * limit
        tasks = await project_db.tasks.find(query_filter).skip(skip).limit(limit).to_list(length=None)
        
        # 转换ObjectId为字符串
        for task in tasks:
            if '_id' in task:
                task['_id'] = str(task['_id'])
            if 'created_by' in task:
                task['created_by'] = str(task['created_by'])
        
        return {
            "tasks": tasks,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit
        }
    except Exception as e:
        return {"error": str(e), "tasks": [], "total": 0}

@router.post("/projects/{project_id}/tasks/bulk")
async def create_tasks_bulk(project_id: str, tasks_data: dict):
    """批量创建任务（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        tasks = []
        for task_data in tasks_data.get("tasks", []):
            task = {
                "_id": ObjectId(),
                "title": task_data.get("title", "新任务"),
                "payload": task_data.get("payload", {"text": "", "url": None, "meta": {}}),
                "status": "open",
                "tags": task_data.get("tags", []),
                "created_by": ObjectId(CURRENT_USER["id"]),
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
            tasks.append(task)
        
        if tasks:
            result = await project_db.tasks.insert_many(tasks)
            for i, task in enumerate(tasks):
                task["_id"] = str(result.inserted_ids[i])
                task["created_by"] = str(task["created_by"])
        
        return {"message": f"成功创建 {len(tasks)} 个任务", "tasks": tasks}
    except Exception as e:
        return {"error": str(e), "tasks": []}

@router.get("/projects/{project_id}/tag-groups")
async def get_tag_groups(project_id: str):
    """获取标签组列表（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        tag_groups = await project_db.tag_groups.find({"active": True}).sort("order", 1).to_list(length=None)
        
        # 转换ObjectId为字符串
        for group in tag_groups:
            if '_id' in group:
                group['_id'] = str(group['_id'])
        
        return {"tag_groups": tag_groups, "total": len(tag_groups)}
    except Exception as e:
        return {"error": str(e), "tag_groups": [], "total": 0}

@router.post("/projects/{project_id}/tag-groups")
async def create_tag_group(project_id: str, tag_group_data: dict):
    """创建标签组（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        tag_group = {
            "_id": ObjectId(),
            "group_id": tag_group_data.get("group_id", f"g_{ObjectId()}"),
            "name": tag_group_data.get("name", "新标签组"),
            "description": tag_group_data.get("description", ""),
            "type": tag_group_data.get("type", "single"),
            "required": tag_group_data.get("required", False),
            "order": tag_group_data.get("order", 1),
            "active": True,
            "options": tag_group_data.get("options", []),
            "constraints": tag_group_data.get("constraints", {"mutex_with_groups": [], "requires_groups": []}),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = await project_db.tag_groups.insert_one(tag_group)
        tag_group["_id"] = str(result.inserted_id)
        
        return {"message": "标签组创建成功", "tag_group": tag_group}
    except Exception as e:
        return {"error": str(e)}

@router.get("/projects/{project_id}/board")
async def get_board(project_id: str):
    """获取看板视图（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        # 获取任务
        tasks = await project_db.tasks.find().to_list(length=None)
        # 转换ObjectId为字符串
        for task in tasks:
            if '_id' in task:
                task['_id'] = str(task['_id'])
            if 'created_by' in task:
                task['created_by'] = str(task['created_by'])
        
        # 获取分配
        assignments = await project_db.assignments.find().to_list(length=None)
        for assignment in assignments:
            if '_id' in assignment:
                assignment['_id'] = str(assignment['_id'])
            if 'task_id' in assignment:
                assignment['task_id'] = str(assignment['task_id'])
            if 'coder_user_id' in assignment:
                assignment['coder_user_id'] = str(assignment['coder_user_id'])
        
        # 获取标注
        annotations = await project_db.annotations.find().to_list(length=None)
        for annotation in annotations:
            if '_id' in annotation:
                annotation['_id'] = str(annotation['_id'])
            if 'task_id' in annotation:
                annotation['task_id'] = str(annotation['task_id'])
            if 'coder_user_id' in annotation:
                annotation['coder_user_id'] = str(annotation['coder_user_id'])
        
        # 按状态分组任务
        tasks_by_status = {
            "open": [t for t in tasks if t.get("status") == "open"],
            "assigned": [t for t in tasks if t.get("status") == "assigned"],
            "in_progress": [t for t in tasks if t.get("status") == "in_progress"],
            "done": [t for t in tasks if t.get("status") == "done"]
        }
        
        return {
            "board": {
                "tasks_by_status": tasks_by_status,
                "total_tasks": len(tasks),
                "assignments": assignments,
                "annotations": annotations
            }
        }
    except Exception as e:
        return {"error": str(e), "board": {"tasks_by_status": {}, "total_tasks": 0}}

@router.get("/projects/{project_id}/members")
async def get_members(project_id: str):
    """获取项目成员（无需认证）— from project_memberships"""
    try:
        core_db = get_core_db()
        if core_db is None:
            return {"members": [], "total": 0}
        
        project = await core_db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            return {"error": "项目不存在", "members": []}

        from services.membership_service import list_project_member_users
        users = await list_project_member_users(core_db, ObjectId(project_id))
        members = []
        for u in users:
            members.append({
                "_id": u["id"],
                "email": u.get("email"),
                "name": u.get("name"),
                "role": u.get("role"),
                "roles": u.get("roles", []),
                "avatar_url": u.get("avatar_url"),
                "project_id": u.get("project_id"),
            })
        
        return {"members": members, "total": len(members)}
    except Exception as e:
        return {"error": str(e), "members": [], "total": 0}

# 添加缺失的API端点

@router.get("/projects/{project_id}/tasks/{task_id}")
async def get_task(project_id: str, task_id: str):
    """获取单个任务详情（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        task = await project_db.tasks.find_one({"_id": ObjectId(task_id)})
        
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        
        # 转换ObjectId为字符串
        task['_id'] = str(task['_id'])
        if 'created_by' in task:
            task['created_by'] = str(task['created_by'])
        
        return {"task": task}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取任务失败: {str(e)}")

@router.post("/projects/{project_id}/tasks")
async def create_task(project_id: str, task_data: dict):
    """创建单个任务（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        task = {
            "_id": ObjectId(),
            "title": task_data.get("title", "新任务"),
            "payload": task_data.get("payload", {"text": "", "url": None, "meta": {}}),
            "status": "open",
            "tags": task_data.get("tags", []),
            "created_by": ObjectId(CURRENT_USER["id"]),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = await project_db.tasks.insert_one(task)
        task["_id"] = str(result.inserted_id)
        task["created_by"] = str(task["created_by"])
        
        return {"message": "任务创建成功", "task": task}
    except Exception as e:
        return {"error": str(e)}

@router.put("/projects/{project_id}/tasks/{task_id}")
async def update_task(project_id: str, task_id: str, task_data: dict):
    """更新任务（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        update_data = {
            "updated_at": datetime.utcnow()
        }
        
        # 只更新提供的字段
        if "title" in task_data:
            update_data["title"] = task_data["title"]
        if "payload" in task_data:
            update_data["payload"] = task_data["payload"]
        if "status" in task_data:
            update_data["status"] = task_data["status"]
        if "tags" in task_data:
            update_data["tags"] = task_data["tags"]
        
        result = await project_db.tasks.update_one(
            {"_id": ObjectId(task_id)},
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="任务不存在")
        
        # 获取更新后的任务
        task = await project_db.tasks.find_one({"_id": ObjectId(task_id)})
        task['_id'] = str(task['_id'])
        if 'created_by' in task:
            task['created_by'] = str(task['created_by'])
        
        return {"message": "任务更新成功", "task": task}
    except Exception as e:
        return {"error": str(e)}

@router.delete("/projects/{project_id}/tasks/{task_id}")
async def delete_task(project_id: str, task_id: str):
    """删除任务（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        result = await project_db.tasks.delete_one({"_id": ObjectId(task_id)})
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="任务不存在")
        
        return {"message": "任务删除成功"}
    except Exception as e:
        return {"error": str(e)}

@router.get("/projects/{project_id}/tag-groups/{group_id}")
async def get_tag_group(project_id: str, group_id: str):
    """获取单个标签组详情（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        tag_group = await project_db.tag_groups.find_one({"group_id": group_id})
        
        if not tag_group:
            raise HTTPException(status_code=404, detail="标签组不存在")
        
        # 转换ObjectId为字符串
        tag_group['_id'] = str(tag_group['_id'])
        
        return {"tag_group": tag_group}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取标签组失败: {str(e)}")

@router.put("/projects/{project_id}/tag-groups/{group_id}")
async def update_tag_group(project_id: str, group_id: str, tag_group_data: dict):
    """更新标签组（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        update_data = {
            "updated_at": datetime.utcnow()
        }
        
        # 只更新提供的字段
        if "name" in tag_group_data:
            update_data["name"] = tag_group_data["name"]
        if "description" in tag_group_data:
            update_data["description"] = tag_group_data["description"]
        if "type" in tag_group_data:
            update_data["type"] = tag_group_data["type"]
        if "required" in tag_group_data:
            update_data["required"] = tag_group_data["required"]
        if "order" in tag_group_data:
            update_data["order"] = tag_group_data["order"]
        if "active" in tag_group_data:
            update_data["active"] = tag_group_data["active"]
        if "options" in tag_group_data:
            update_data["options"] = tag_group_data["options"]
        if "constraints" in tag_group_data:
            update_data["constraints"] = tag_group_data["constraints"]
        
        result = await project_db.tag_groups.update_one(
            {"group_id": group_id},
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="标签组不存在")
        
        # 获取更新后的标签组
        tag_group = await project_db.tag_groups.find_one({"group_id": group_id})
        tag_group['_id'] = str(tag_group['_id'])
        
        return {"message": "标签组更新成功", "tag_group": tag_group}
    except Exception as e:
        return {"error": str(e)}

@router.delete("/projects/{project_id}/tag-groups/{group_id}")
async def delete_tag_group(project_id: str, group_id: str):
    """删除标签组（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        # 软删除：将active设为False
        result = await project_db.tag_groups.update_one(
            {"group_id": group_id},
            {"$set": {"active": False, "updated_at": datetime.utcnow()}}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="标签组不存在")
        
        return {"message": "标签组删除成功"}
    except Exception as e:
        return {"error": str(e)}

@router.get("/projects/{project_id}/annotations")
async def get_annotations(project_id: str, task_id: Optional[str] = None, coder_user_id: Optional[str] = None):
    """获取标注列表（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        query_filter = {}
        if task_id:
            query_filter["task_id"] = ObjectId(task_id)
        if coder_user_id:
            query_filter["coder_user_id"] = ObjectId(coder_user_id)
        
        annotations = await project_db.annotations.find(query_filter).to_list(length=None)
        
        # 转换ObjectId为字符串
        for annotation in annotations:
            if '_id' in annotation:
                annotation['_id'] = str(annotation['_id'])
            if 'task_id' in annotation:
                annotation['task_id'] = str(annotation['task_id'])
            if 'coder_user_id' in annotation:
                annotation['coder_user_id'] = str(annotation['coder_user_id'])
        
        return {"annotations": annotations, "total": len(annotations)}
    except Exception as e:
        return {"error": str(e), "annotations": [], "total": 0}

@router.post("/projects/{project_id}/annotations")
async def create_annotation(project_id: str, annotation_data: dict):
    """创建标注（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        annotation = {
            "_id": ObjectId(),
            "task_id": ObjectId(annotation_data.get("task_id")),
            "coder_user_id": ObjectId(CURRENT_USER["id"]),
            "schema_version": annotation_data.get("schema_version", 1),
            "labels": annotation_data.get("labels", []),
            "notes": annotation_data.get("notes", ""),
            "completed_at": datetime.utcnow(),
            "version": 1
        }
        
        result = await project_db.annotations.insert_one(annotation)
        annotation["_id"] = str(result.inserted_id)
        annotation["task_id"] = str(annotation["task_id"])
        annotation["coder_user_id"] = str(annotation["coder_user_id"])
        
        return {"message": "标注创建成功", "annotation": annotation}
    except Exception as e:
        return {"error": str(e)}

@router.get("/projects/{project_id}/assignments")
async def get_assignments(project_id: str, task_id: Optional[str] = None, coder_user_id: Optional[str] = None):
    """获取分配列表（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        query_filter = {}
        if task_id:
            query_filter["task_id"] = ObjectId(task_id)
        if coder_user_id:
            query_filter["coder_user_id"] = ObjectId(coder_user_id)
        
        assignments = await project_db.assignments.find(query_filter).to_list(length=None)
        
        # 转换ObjectId为字符串
        for assignment in assignments:
            if '_id' in assignment:
                assignment['_id'] = str(assignment['_id'])
            if 'task_id' in assignment:
                assignment['task_id'] = str(assignment['task_id'])
            if 'coder_user_id' in assignment:
                assignment['coder_user_id'] = str(assignment['coder_user_id'])
        
        return {"assignments": assignments, "total": len(assignments)}
    except Exception as e:
        return {"error": str(e), "assignments": [], "total": 0}

@router.post("/projects/{project_id}/assignments")
async def create_assignment(project_id: str, assignment_data: dict):
    """创建分配（无需认证）"""
    try:
        project_db = await get_project_db(project_id)
        
        assignment = {
            "_id": ObjectId(),
            "task_id": ObjectId(assignment_data.get("task_id")),
            "coder_user_id": ObjectId(assignment_data.get("coder_user_id", CURRENT_USER["id"])),
            "state": assignment_data.get("state", "assigned"),
            "progress": assignment_data.get("progress", 0),
            "updated_at": datetime.utcnow()
        }
        
        result = await project_db.assignments.insert_one(assignment)
        assignment["_id"] = str(result.inserted_id)
        assignment["task_id"] = str(assignment["task_id"])
        assignment["coder_user_id"] = str(assignment["coder_user_id"])
        
        return {"message": "分配创建成功", "assignment": assignment}
    except Exception as e:
        return {"error": str(e)}
