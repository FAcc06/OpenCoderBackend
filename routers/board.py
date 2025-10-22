from fastapi import APIRouter, HTTPException, Depends, status
from typing import List
from bson import ObjectId

from database import get_core_db, get_project_db
from models import BoardResponse, BoardItem, Task, Assignment, Annotation, PaginatedResponse, User
from datetime import datetime

router = APIRouter()

@router.get("/{project_id}/board", response_model=BoardResponse)
async def get_project_board(
    project_id: str,
    status: str = None,
    coder_user_id: str = None,
    page: int = 1,
    limit: int = 20
):
    """获取项目看板视图 - 无需认证"""
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
    
    # 构建任务查询条件
    task_query = {}
    if status:
        task_query["status"] = status
    
    # 获取任务列表
    skip = (page - 1) * limit
    tasks = await project_db.tasks.find(task_query).skip(skip).limit(limit).to_list(length=None)
    
    # 获取所有任务ID
    task_ids = [task["_id"] for task in tasks]
    
    # 获取分配信息
    assignment_query = {"task_id": {"$in": task_ids}}
    if coder_user_id:
        try:
            coder_oid = ObjectId(coder_user_id)
            assignment_query["coder_user_id"] = coder_oid
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid coder user ID")
    
    assignments = await project_db.assignments.find(assignment_query).to_list(length=None)
    
    # 获取标注信息
    annotations = await project_db.annotations.find({"task_id": {"$in": task_ids}}).to_list(length=None)
    
    # 构建看板项目
    board_items = []
    for task in tasks:
        task_assignments = [Assignment(**assignment) for assignment in assignments 
                          if assignment["task_id"] == task["_id"]]
        task_annotations = [Annotation(**annotation) for annotation in annotations 
                          if annotation["task_id"] == task["_id"]]
        
        board_item = BoardItem(
            task=Task(**task),
            assignments=task_assignments,
            annotations=task_annotations
        )
        board_items.append(board_item)
    
    # 获取总数
    total = await project_db.tasks.count_documents(task_query)
    
    return BoardResponse(
        items=board_items,
        total=total
    )

@router.get("/{project_id}/board/stats")
async def get_board_stats(project_id: str):
    """获取看板统计信息 - 无需认证"""
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
    
    # 统计任务状态
    task_stats = {}
    task_statuses = ["open", "assigned", "in_progress", "done"]
    for status in task_statuses:
        count = await project_db.tasks.count_documents({"status": status})
        task_stats[status] = count
    
    # 统计分配状态
    assignment_stats = {}
    assignment_states = ["assigned", "in_progress", "done"]
    for state in assignment_states:
        count = await project_db.assignments.count_documents({"state": state})
        assignment_stats[state] = count
    
    # 统计标注数量
    annotation_count = await project_db.annotations.count_documents({})
    
    return {
        "task_stats": task_stats,
        "assignment_stats": assignment_stats,
        "annotation_count": annotation_count,
        "total_tasks": sum(task_stats.values()),
        "total_assignments": sum(assignment_stats.values())
    }