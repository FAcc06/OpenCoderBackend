from fastapi import APIRouter, HTTPException, Depends, status
from typing import List, Optional
from bson import ObjectId

from database import get_core_db, get_project_db
from models import Assignment, AssignmentCreate, AssignmentUpdate, AssignmentState, User
from models import PaginatedResponse
from datetime import datetime

router = APIRouter()

@router.post("/{project_id}/assignments", response_model=List[Assignment])
async def create_assignments(
    project_id: str,
    assignment_data: AssignmentCreate
):
    """批量创建分配 - 无需认证"""
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
    
    # 创建分配
    assignments = []
    for task_id in assignment_data.task_ids:
        assignment = Assignment(
            task_id=task_id,
            coder_user_id=assignment_data.coder_user_id,
            state=assignment_data.state or AssignmentState.ASSIGNED
        )
        assignments.append(assignment)
    
    # 批量插入
    assignment_dicts = [assignment.dict(by_alias=True) for assignment in assignments]
    
    try:
        result = await project_db.assignments.insert_many(assignment_dicts)
        
        # 更新分配ID
        for i, assignment in enumerate(assignments):
            assignment.id = result.inserted_ids[i]
        
        return assignments
    except Exception as e:
        # 处理重复分配错误
        if "duplicate key error" in str(e) or "E11000" in str(e):
            raise HTTPException(
                status_code=400,
                detail="One or more tasks are already assigned to this coder. Please check existing assignments."
            )
        raise HTTPException(status_code=500, detail=f"Failed to create assignments: {str(e)}")

@router.get("/{project_id}/assignments", response_model=PaginatedResponse)
async def get_assignments(
    project_id: str,
    page: int = 1,
    limit: int = 10,
    state: Optional[AssignmentState] = None
):
    """获取分配列表 - 无需认证"""
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
    if state:
        query["state"] = state
    
    # 获取总数
    total = await project_db.assignments.count_documents(query)
    
    # 分页查询
    skip = (page - 1) * limit
    assignments = await project_db.assignments.find(query).skip(skip).limit(limit).to_list(length=None)
    
    return PaginatedResponse(
        items=[Assignment(**assignment) for assignment in assignments],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit
    )

@router.put("/{project_id}/assignments/{assignment_id}", response_model=Assignment)
async def update_assignment(
    project_id: str,
    assignment_id: str,
    assignment_update: AssignmentUpdate
):
    """更新分配 - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
        assignment_oid = ObjectId(assignment_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    # 检查项目是否存在
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 获取项目数据库
    project_db = await get_project_db(project_id)
    
    # 检查分配是否存在
    assignment = await project_db.assignments.find_one({"_id": assignment_oid})
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    
    # 构建更新数据
    update_data = {"updated_at": datetime.utcnow()}
    if assignment_update.state is not None:
        update_data["state"] = assignment_update.state
    if assignment_update.progress is not None:
        update_data["progress"] = assignment_update.progress
    
    # 更新分配
    await project_db.assignments.update_one(
        {"_id": assignment_oid},
        {"$set": update_data}
    )
    
    # 返回更新后的分配
    updated_assignment = await project_db.assignments.find_one({"_id": assignment_oid})
    return Assignment(**updated_assignment)

@router.get("/{project_id}/my-assignments", response_model=PaginatedResponse)
async def get_my_assignments(
    project_id: str,
    token: str,
    state: Optional[AssignmentState] = None,
    page: int = 1,
    limit: int = 10
):
    """获取当前 Coder 的分配任务 - 需要 Token 认证"""
    from jose import jwt, JWTError
    import os
    
    core_db = get_core_db()
    
    # 1. 验证并解析 Token
    try:
        SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        ALGORITHM = os.getenv("ALGORITHM", "HS256")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        coder_user_id_str = payload.get("sub")
        
        if not coder_user_id_str:
            raise HTTPException(status_code=401, detail="Invalid token: missing user ID")
        
        try:
            coder_user_id = ObjectId(coder_user_id_str)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid user ID format")
            
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    
    # 2. 验证项目
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 3. 获取项目数据库
    project_db = await get_project_db(project_id)
    
    # 4. 构建查询条件
    query = {"coder_user_id": coder_user_id}
    if state:
        query["state"] = state
    
    # 5. 获取总数
    total = await project_db.assignments.count_documents(query)
    
    # 6. 分页查询
    skip = (page - 1) * limit
    assignments = await project_db.assignments.find(query).sort("created_at", 1).skip(skip).limit(limit).to_list(length=None)
    
    return PaginatedResponse(
        items=[Assignment(**assignment) for assignment in assignments],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit
    )

@router.get("/{project_id}/my-next-task")
async def get_my_next_task(
    project_id: str,
    token: str,
    task_type: Optional[str] = None
):
    """获取当前 Coder 的下一个未完成任务 - 需要 Token 认证
    
    参数:
    - task_type: 可选，过滤特定类型的任务 (text, image, video, audio, url)
    
    返回：任务详情 + 分配信息 + Tag Groups
    """
    from jose import jwt, JWTError
    import os
    
    core_db = get_core_db()
    
    # 1. 验证并解析 Token
    try:
        SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        ALGORITHM = os.getenv("ALGORITHM", "HS256")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        coder_user_id_str = payload.get("sub")
        
        if not coder_user_id_str:
            raise HTTPException(status_code=401, detail="Invalid token: missing user ID")
        
        try:
            coder_user_id = ObjectId(coder_user_id_str)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid user ID format")
            
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    
    # 2. 验证项目
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 3. 获取项目数据库
    project_db = await get_project_db(project_id)
    
    # 4. 查找未完成的分配（按创建时间排序）
    # 需要跳过指向不存在任务的分配
    assignment_query = {
        "coder_user_id": coder_user_id,
        "state": {"$in": [AssignmentState.ASSIGNED, AssignmentState.IN_PROGRESS]}
    }
    
    all_assignments = await project_db.assignments.find(
        assignment_query
    ).sort("created_at", 1).to_list(length=None)
    
    if not all_assignments:
        raise HTTPException(
            status_code=404,
            detail="No pending tasks found. All tasks completed! 🎉"
        )
    
    # 5. 查找第一个有效的分配（任务存在且符合类型要求）
    from models import Task, TagGroup
    task = None
    assignment = None
    
    for assgn in all_assignments:
        t = await project_db.tasks.find_one({"_id": assgn["task_id"]})
        if not t:
            # 跳过指向不存在任务的分配
            continue
        
        # 如果指定了 task_type，检查是否匹配
        if task_type:
            actual_type = t.get("task_type", "text")
            if actual_type != task_type:
                continue
        
        # 找到有效的任务和分配
        task = t
        assignment = assgn
        break
    
    if not task or not assignment:
        if task_type:
            raise HTTPException(
                status_code=404,
                detail=f"No pending {task_type} tasks found."
            )
        else:
            raise HTTPException(
                status_code=404,
                detail="No pending tasks found. All tasks completed! 🎉"
            )
    
    # 6. 获取 Tag Groups
    tag_groups = await project_db.tag_groups.find().sort("order", 1).to_list(length=None)
    
    # 7. 自动更新分配状态为 IN_PROGRESS（如果还是 ASSIGNED）
    if assignment["state"] == AssignmentState.ASSIGNED:
        await project_db.assignments.update_one(
            {"_id": assignment["_id"]},
            {
                "$set": {
                    "state": AssignmentState.IN_PROGRESS,
                    "updated_at": datetime.utcnow()
                }
            }
        )
        assignment["state"] = AssignmentState.IN_PROGRESS
    
    # 8. 返回完整信息
    return {
        "task": Task(**task),
        "assignment": Assignment(**assignment),
        "tag_groups": [TagGroup(**tg) for tg in tag_groups],
        "project_name": project.get("name"),
        "project_slug": project.get("slug")
    }