from fastapi import APIRouter, HTTPException, Depends, status
from typing import List, Optional
from bson import ObjectId

from database import get_core_db, get_project_db
from models import Task, TaskCreate, TaskBulkCreate, TaskUpdate, TaskStatus, PaginatedResponse, User
from utils import paginate_query, calculate_pages
from datetime import datetime

router = APIRouter()

@router.post("/{project_id}/tasks/bulk", response_model=List[Task])
async def create_tasks_bulk(
    project_id: str,
    tasks_data: TaskBulkCreate
):
    """批量创建任务 - 无需认证，自动跳过重复文本的任务"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    # 检查项目是否存在
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 获取项目数据库
    project_db = await get_project_db(project_id)
    
    # 创建任务
    tasks = []
    for task_data in tasks_data.tasks:
        task = Task(
            title=task_data.title,
            payload=task_data.payload,
            tags=task_data.tags,
            created_by=ObjectId()  # 使用随机ObjectId作为默认创建者
        )
        tasks.append(task)
    
    # 批量插入，使用 ordered=False 允许部分成功（跳过重复）
    task_dicts = [task.dict(by_alias=True) for task in tasks]
    
    try:
        result = await project_db.tasks.insert_many(task_dicts, ordered=False)
        inserted_ids = result.inserted_ids
    except Exception as e:
        # 处理部分插入成功的情况（有重复时）
        if hasattr(e, 'details') and e.details.get('writeErrors'):
            # 获取成功插入的ID
            inserted_ids = []
            for i, task_dict in enumerate(task_dicts):
                # 检查是否成功插入
                existing = await project_db.tasks.find_one({"payload.text": task_dict["payload"]["text"]})
                if existing:
                    inserted_ids.append(existing["_id"])
        else:
            raise HTTPException(status_code=500, detail=f"Failed to insert tasks: {str(e)}")
    
    # 获取所有成功插入或已存在的任务
    inserted_tasks = []
    for task_dict in task_dicts:
        existing = await project_db.tasks.find_one({"payload.text": task_dict["payload"]["text"]})
        if existing:
            inserted_tasks.append(Task(**existing))
    
    return inserted_tasks

@router.get("/{project_id}/tasks", response_model=PaginatedResponse)
async def get_tasks(
    project_id: str,
    page: int = 1,
    limit: int = 10,
    status: Optional[TaskStatus] = None,
    tags: Optional[str] = None
):
    """获取任务列表 - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    # 检查项目是否存在
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 获取项目数据库
    project_db = await get_project_db(project_id)
    
    # 构建查询条件
    query = {}
    if status:
        query["status"] = status
    if tags:
        query["tags"] = {"$in": tags.split(",")}
    
    # 获取总数
    total = await project_db.tasks.count_documents(query)
    
    # 分页查询
    skip = (page - 1) * limit
    tasks = await project_db.tasks.find(query).skip(skip).limit(limit).to_list(length=None)
    
    return PaginatedResponse(
        items=[Task(**task) for task in tasks],
        total=total,
        page=page,
        limit=limit,
        pages=calculate_pages(total, limit)
    )

@router.get("/{project_id}/tasks/{task_id}", response_model=Task)
async def get_task(
    project_id: str,
    task_id: str
):
    """获取单个任务 - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
        task_oid = ObjectId(task_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    # 检查项目是否存在
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 获取项目数据库
    project_db = await get_project_db(project_id)
    
    # 查找任务
    task = await project_db.tasks.find_one({"_id": task_oid})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return Task(**task)

@router.put("/{project_id}/tasks/{task_id}", response_model=Task)
async def update_task(
    project_id: str,
    task_id: str,
    task_update: TaskUpdate
):
    """更新任务 - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
        task_oid = ObjectId(task_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    # 检查项目是否存在
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 获取项目数据库
    project_db = await get_project_db(project_id)
    
    # 检查任务是否存在
    task = await project_db.tasks.find_one({"_id": task_oid})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 构建更新数据
    update_data = {"updated_at": datetime.utcnow()}
    if task_update.title is not None:
        update_data["title"] = task_update.title
    if task_update.payload is not None:
        update_data["payload"] = task_update.payload.dict()
    if task_update.status is not None:
        update_data["status"] = task_update.status
    if task_update.tags is not None:
        update_data["tags"] = task_update.tags
    
    # 更新任务
    await project_db.tasks.update_one(
        {"_id": task_oid},
        {"$set": update_data}
    )
    
    # 返回更新后的任务
    updated_task = await project_db.tasks.find_one({"_id": task_oid})
    return Task(**updated_task)

@router.delete("/{project_id}/tasks/{task_id}")
async def delete_task(
    project_id: str,
    task_id: str
):
    """删除任务 - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
        task_oid = ObjectId(task_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    # 检查项目是否存在
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 获取项目数据库
    project_db = await get_project_db(project_id)
    
    # 检查任务是否存在
    task = await project_db.tasks.find_one({"_id": task_oid})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 删除任务
    await project_db.tasks.delete_one({"_id": task_oid})
    
    return {"message": "Task deleted successfully"}