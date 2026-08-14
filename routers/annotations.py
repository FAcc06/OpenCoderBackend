from fastapi import APIRouter, HTTPException, Depends, status, Query
from pydantic import BaseModel
from typing import List, Optional
from bson import ObjectId

from database import get_core_db, get_project_db
from models import Annotation, AnnotationCreate, PaginatedResponse, User
from utils import validate_tag_group_constraints
from datetime import datetime

router = APIRouter()


# ── Task Notes (reflexive memos) ──────────────────────────────────────────────

class NoteUpsert(BaseModel):
    content: str = ""


@router.get("/{project_id}/tasks/{task_id}/note")
async def get_task_note(project_id: str, task_id: str, token: str = Query(...)):
    """Return the coder's reflexive note for this task (empty string if none yet)."""
    from jose import jwt, JWTError
    import os
    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    ALGORITHM  = os.getenv("ALGORITHM", "HS256")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        coder_id = ObjectId(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        task_oid    = ObjectId(task_id)
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID")

    project_db = await get_project_db(project_id)
    doc = await project_db.task_notes.find_one(
        {"task_id": task_oid, "coder_user_id": coder_id}
    )
    return {
        "task_id":   task_id,
        "content":   doc["content"] if doc else "",
        "updated_at": doc["updated_at"].isoformat() if doc else None,
    }


@router.put("/{project_id}/tasks/{task_id}/note")
async def upsert_task_note(
    project_id: str,
    task_id:    str,
    body:       NoteUpsert,
    token:      str = Query(...),
):
    """Create or update the coder's reflexive note for this task."""
    from jose import jwt, JWTError
    import os
    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    ALGORITHM  = os.getenv("ALGORITHM", "HS256")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        coder_id = ObjectId(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        task_oid    = ObjectId(task_id)
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID")

    now        = datetime.utcnow()
    project_db = await get_project_db(project_id)

    await project_db.task_notes.update_one(
        {"task_id": task_oid, "coder_user_id": coder_id},
        {"$set": {
            "content":    body.content,
            "project_id": project_oid,
            "updated_at": now,
        }, "$setOnInsert": {
            "created_at": now,
        }},
        upsert=True,
    )
    return {"success": True, "updated_at": now.isoformat()}


@router.get("/{project_id}/notes")
async def list_notes(
    project_id: str,
    token: str = Query(...),
    task_type: Optional[str] = None,
    coder_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sort: str = "newest",
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
):
    """
    List reflexive task memos.
    - Coders: only their own notes.
    - Project managers: all notes in the project (optional coder_id filter).
    task_type supports a single value or comma-separated list.
    """
    from jose import jwt
    from datetime import datetime as dt
    import os
    from services.membership_service import is_project_manager

    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    ALGORITHM  = os.getenv("ALGORITHM", "HS256")
    try:
        payload  = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_oid = ObjectId(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project_id")

    core_db = get_core_db()
    is_manager = await is_project_manager(core_db, user_oid, project_oid)

    project_db = await get_project_db(project_id)

    # ── Build notes query ────────────────────────────────────────────────────
    notes_query: dict = {
        "content": {"$nin": ["", None]},
    }
    if is_manager:
        if coder_id:
            try:
                notes_query["coder_user_id"] = ObjectId(coder_id)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid coder_id")
    else:
        notes_query["coder_user_id"] = user_oid

    if date_from or date_to:
        date_filter: dict = {}
        if date_from:
            try:
                date_filter["$gte"] = dt.fromisoformat(date_from)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date_from (use YYYY-MM-DD)")
        if date_to:
            try:
                date_filter["$lte"] = dt.fromisoformat(date_to).replace(hour=23, minute=59, second=59)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date_to (use YYYY-MM-DD)")
        notes_query["updated_at"] = date_filter

    sort_dir = -1 if sort == "newest" else 1
    all_notes = await (
        project_db.task_notes.find(notes_query)
        .sort("updated_at", sort_dir)
        .to_list(length=5000)
    )

    # ── Batch-fetch task info ─────────────────────────────────────────────────
    task_ids = list({n["task_id"] for n in all_notes if n.get("task_id")})
    task_query: dict = {"_id": {"$in": task_ids}}
    type_list = []
    if task_type and task_type != "all":
        type_list = [t.strip() for t in task_type.split(",") if t.strip() and t.strip() != "all"]
        if len(type_list) == 1:
            task_query["task_type"] = type_list[0]
        elif len(type_list) > 1:
            task_query["task_type"] = {"$in": type_list}
    tasks_map = {
        t["_id"]: t
        async for t in project_db.tasks.find(task_query, {"title": 1, "task_type": 1})
    }

    # ── Batch-fetch coder profiles (managers need names) ──────────────────────
    coder_ids = list({n["coder_user_id"] for n in all_notes if n.get("coder_user_id")})
    users_map: dict = {}
    if coder_ids:
        async for u in core_db.users.find(
            {"_id": {"$in": coder_ids}},
            {"name": 1, "email": 1},
        ):
            users_map[u["_id"]] = u

    # ── Build result, applying task_type + text search filters ───────────────
    result = []
    for n in all_notes:
        t = tasks_map.get(n["task_id"])
        if t is None:
            # task_type filter excluded this task
            if type_list:
                continue
            t = {}
        content    = n.get("content", "")
        task_title = t.get("title", "Untitled")
        coder = users_map.get(n.get("coder_user_id")) or {}
        coder_name = coder.get("name") or "Unknown"
        if search:
            q = search.lower()
            if (
                q not in task_title.lower()
                and q not in content.lower()
                and q not in coder_name.lower()
            ):
                continue
        result.append({
            "note_id":    str(n["_id"]),
            "task_id":    str(n["task_id"]),
            "task_title": task_title,
            "task_type":  t.get("task_type", ""),
            "content":    content,
            "updated_at": n["updated_at"].isoformat() if n.get("updated_at") else None,
            "coder_user_id": str(n["coder_user_id"]) if n.get("coder_user_id") else None,
            "coder_name": coder_name,
            "coder_email": coder.get("email"),
        })

    total = len(result)
    # Paginate
    start = (page - 1) * limit
    paginated = result[start: start + limit] if limit > 0 else result

    return {
        "notes":  paginated,
        "total":  total,
        "page":   page,
        "limit":  limit,
        "pages":  max(1, -(-total // limit)) if limit > 0 else 1,
        "scope":  "project" if is_manager else "mine",
    }

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
    
    # 11. 检查是否完成所有任务，如果是则自动通知Manager
    if not next_assignment:
        # 没有下一个任务了，可能完成所有任务
        from routers.notifications import check_and_notify_all_tasks_completed
        try:
            notification_sent = await check_and_notify_all_tasks_completed(
                project_id=project_id,
                coder_user_id=coder_user_id_str
            )
            if notification_sent:
                print(f"✅ Auto-notification sent: Coder {coder_user_id_str} completed all tasks")
        except Exception as e:
            print(f"⚠️  Failed to send auto-notification: {e}")
            # 不影响主流程，继续
    
    try:
        from services.activity_log_service import log_user_activity
        await log_user_activity(
            core_db,
            coder_user_id,
            "annotation.submit",
            f"Submitted annotation for task: {task.get('title') or task_oid}",
            project_id=project_oid,
            event_type="coding.activity",
            resource_type="annotation",
            resource_id=str(annotation.id),
            role="coder",
            payload={
                "task_id": str(task_oid),
                "task_title": task.get("title"),
                "task_type": task.get("task_type"),
                "label_count": len(annotation_data.labels or []),
            },
        )
    except Exception:
        pass

    # 12. 返回结果
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