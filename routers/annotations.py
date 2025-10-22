from fastapi import APIRouter, HTTPException, Depends, status
from typing import List, Optional
from bson import ObjectId

from database import get_core_db, get_project_db
from models import Annotation, AnnotationCreate, PaginatedResponse, User
from utils import validate_tag_group_constraints
from datetime import datetime

router = APIRouter()

@router.post("/{project_id}/annotations", response_model=Annotation)
async def create_annotation(
    project_id: str,
    annotation_data: AnnotationCreate
):
    """提交标注 - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
        task_oid = ObjectId(annotation_data.task_id)
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
    
    # 获取标签组并验证约束
    tag_groups_data = await project_db.tag_groups.find().to_list(length=None)
    
    # 将 Pydantic 模型转换为字典进行验证
    labels_dict = [label.dict() for label in annotation_data.labels]
    
    # 验证标签组约束（同步函数，不需要 await）
    try:
        validate_tag_group_constraints(labels_dict, tag_groups_data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    
    # 创建标注
    annotation = Annotation(
        task_id=task_oid,
        coder_user_id=ObjectId(),  # 使用随机ObjectId作为默认标注者
        labels=annotation_data.labels,
        notes=annotation_data.notes,
        completed_at=datetime.utcnow()
    )
    
    result = await project_db.annotations.insert_one(annotation.dict(by_alias=True))
    annotation.id = result.inserted_id
    
    # 更新任务状态为 DONE
    from models import TaskStatus
    await project_db.tasks.update_one(
        {"_id": task_oid},
        {
            "$set": {
                "status": TaskStatus.DONE,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    return annotation

@router.get("/{project_id}/annotations", response_model=PaginatedResponse)
async def get_annotations(
    project_id: str,
    page: int = 1,
    limit: int = 10,
    task_id: Optional[str] = None
):
    """获取标注列表 - 无需认证"""
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
    if task_id:
        try:
            task_oid = ObjectId(task_id)
            query["task_id"] = task_oid
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid task ID")
    
    # 获取总数
    total = await project_db.annotations.count_documents(query)
    
    # 分页查询
    skip = (page - 1) * limit
    annotations = await project_db.annotations.find(query).skip(skip).limit(limit).to_list(length=None)
    
    return PaginatedResponse(
        items=[Annotation(**annotation) for annotation in annotations],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit
    )

@router.get("/{project_id}/annotations/{annotation_id}", response_model=Annotation)
async def get_annotation(
    project_id: str,
    annotation_id: str
):
    """获取单个标注 - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
        annotation_oid = ObjectId(annotation_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    # 检查项目是否存在
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 获取项目数据库
    project_db = await get_project_db(project_id)
    
    # 查找标注
    annotation = await project_db.annotations.find_one({"_id": annotation_oid})
    if not annotation:
        raise HTTPException(status_code=404, detail="Annotation not found")
    
    return Annotation(**annotation)

@router.post("/{project_id}/submit-and-next")
async def submit_annotation_and_get_next(
    project_id: str,
    token: str,
    annotation_data: AnnotationCreate
):
    """提交标注并获取下一个任务 - 需要 Token 认证
    
    工作流：
    1. 提交当前任务的标注
    2. 更新分配状态为 DONE
    3. 自动获取下一个未完成任务
    """
    from jose import jwt, JWTError
    import os
    from models import Task, TagGroup, AssignmentState
    
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
            task_oid = ObjectId(annotation_data.task_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid ID format")
            
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
    
    # 4. 验证任务存在
    task = await project_db.tasks.find_one({"_id": task_oid})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 5. 获取标签组并验证约束
    tag_groups_data = await project_db.tag_groups.find().to_list(length=None)
    
    # 将 Pydantic 模型转换为字典进行验证
    labels_dict = [label.dict() for label in annotation_data.labels]
    
    # 验证标签组约束（同步函数，不需要 await）
    try:
        validate_tag_group_constraints(labels_dict, tag_groups_data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    
    # 6. 创建标注
    annotation = Annotation(
        task_id=task_oid,
        coder_user_id=coder_user_id,  # 使用真实的 coder_user_id
        labels=annotation_data.labels,
        notes=annotation_data.notes,
        completed_at=datetime.utcnow()
    )
    
    result = await project_db.annotations.insert_one(annotation.dict(by_alias=True))
    annotation.id = result.inserted_id
    
    # 7. 更新任务状态为 DONE
    from models import TaskStatus
    await project_db.tasks.update_one(
        {"_id": task_oid},
        {
            "$set": {
                "status": TaskStatus.DONE,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    # 8. 更新分配状态为 DONE
    await project_db.assignments.update_one(
        {
            "task_id": task_oid,
            "coder_user_id": coder_user_id
        },
        {
            "$set": {
                "state": AssignmentState.DONE,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    # 9. 查找下一个未完成的任务
    next_assignment = await project_db.assignments.find_one({
        "coder_user_id": coder_user_id,
        "state": {"$in": [AssignmentState.ASSIGNED, AssignmentState.IN_PROGRESS]}
    }, sort=[("created_at", 1)])
    
    # 10. 如果有下一个任务，获取任务详情和 Tag Groups
    next_task = None
    tag_groups = None
    
    if next_assignment:
        next_task = await project_db.tasks.find_one({"_id": next_assignment["task_id"]})
        tag_groups = await project_db.tag_groups.find().sort("order", 1).to_list(length=None)
        
        # 自动更新为 IN_PROGRESS
        if next_assignment["state"] == AssignmentState.ASSIGNED:
            await project_db.assignments.update_one(
                {"_id": next_assignment["_id"]},
                {
                    "$set": {
                        "state": AssignmentState.IN_PROGRESS,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            next_assignment["state"] = AssignmentState.IN_PROGRESS
    
    # 11. 返回结果
    from models import Assignment
    return {
        "submitted": {
            "annotation_id": str(annotation.id),
            "task_id": str(task_oid),
            "status": "success"
        },
        "next_task": {
            "task": Task(**next_task) if next_task else None,
            "assignment": Assignment(**next_assignment) if next_assignment else None,
            "tag_groups": [TagGroup(**tg) for tg in tag_groups] if tag_groups else []
        },
        "has_more": next_task is not None,
        "message": "Task completed! 🎉" if not next_task else "Task completed! Moving to next..."
    }