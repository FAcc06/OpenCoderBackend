from fastapi import APIRouter, HTTPException, Depends, status, UploadFile, File, Form, Query, Request
from typing import List, Optional, Tuple, Any, Dict
from bson import ObjectId
import json
import os

from database import get_core_db, get_project_db
from models import (
    Task, TaskCreate, TaskBulkCreate, TaskUpdate, TaskStatus, TaskType,
    PaginatedResponse, User, InitMediaUploadRequest, FinalizeMediaUploadRequest,
    FinalizeMultimodalUploadRequest,
)
from routers.auth import verify_token
from services.google_drive import GoogleDriveService
from utils import paginate_query, calculate_pages
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

MEDIA_SIZE_LIMITS = {
    "image": 10 * 1024 * 1024,
    "audio": 50 * 1024 * 1024,
    "video": 100 * 1024 * 1024,
}

MEDIA_MIME_PREFIX = {
    "image": "image/",
    "audio": "audio/",
    "video": "video/",
}


async def _resolve_manager_drive_context(
    project_id: str,
    token: str,
) -> Tuple[str, Dict[str, Any], Dict[str, Any], GoogleDriveService, Any]:
    """
    Shared auth + Manager Drive setup for media uploads.
    Returns: (user_id, project, manager, drive_service, project_oid)
    """
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    core_db = get_core_db()
    if core_db is None:
        raise HTTPException(status_code=503, detail="Database connection not available")

    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")

    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if str(user.get("project_id")) != project_id:
        raise HTTPException(status_code=403, detail="User not in this project")

    manager_id = project.get("owner_user_id")
    if not manager_id:
        raise HTTPException(status_code=500, detail="Project manager not found")

    manager = await core_db.users.find_one({"_id": manager_id})
    if not manager:
        raise HTTPException(status_code=404, detail="Manager not found")

    google_creds = manager.get("google_credentials")
    if not google_creds or not google_creds.get("access_token"):
        raise HTTPException(
            status_code=400,
            detail="Manager's Google Drive not authorized. Manager needs to login again to grant Drive access.",
        )
    if not google_creds.get("refresh_token"):
        raise HTTPException(
            status_code=400,
            detail="Manager's Google credentials are incomplete. Please logout and login again with Google to grant full Drive access.",
        )

    try:
        drive_service = GoogleDriveService(
            access_token=google_creds["access_token"],
            refresh_token=google_creds.get("refresh_token"),
        )
    except Exception as e:
        logger.error(f"Failed to initialize Drive service: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initialize Google Drive: {str(e)}")

    # Persist refreshed access token if needed
    if drive_service.access_token != google_creds["access_token"]:
        await core_db.users.update_one(
            {"_id": manager_id if isinstance(manager_id, ObjectId) else ObjectId(manager_id)},
            {
                "$set": {
                    "google_credentials.access_token": drive_service.access_token,
                    "google_credentials.token_expiry": datetime.utcnow() + timedelta(seconds=3600),
                }
            },
        )

    return user_id, project, manager, drive_service, project_oid


async def _ensure_project_folder(
    project: Dict[str, Any],
    project_oid: ObjectId,
    drive_service: GoogleDriveService,
) -> str:
    core_db = get_core_db()
    project_folder_id = project.get("drive_folder_id")
    if project_folder_id:
        return project_folder_id

    project_name = project.get("name", f"Project_{project_oid}")
    try:
        project_folder_id = drive_service.create_project_folder(f"OpenCoder_{project_name}")
        await core_db.projects.update_one(
            {"_id": project_oid},
            {"$set": {"drive_folder_id": project_folder_id}},
        )
        project["drive_folder_id"] = project_folder_id
        logger.info(f"Project folder created: {project_folder_id}")
        return project_folder_id
    except Exception as e:
        logger.error(f"Failed to create project folder: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create project folder: {str(e)}")


def _validate_media_meta(media_type: str, mime_type: str, file_size: int) -> None:
    if media_type not in MEDIA_SIZE_LIMITS:
        raise HTTPException(status_code=400, detail="media_type must be image, video, or audio")

    prefix = MEDIA_MIME_PREFIX[media_type]
    if not mime_type or not mime_type.startswith(prefix):
        raise HTTPException(
            status_code=400,
            detail=f"Only {media_type} files are allowed",
        )

    limit = MEDIA_SIZE_LIMITS[media_type]
    if file_size <= 0:
        raise HTTPException(status_code=400, detail="Invalid file size")
    if file_size > limit:
        raise HTTPException(
            status_code=400,
            detail=f"{media_type.capitalize()} size exceeds {limit // (1024 * 1024)}MB limit",
        )

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
            task_type=task_data.task_type if task_data.task_type else TaskType.TEXT,
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

@router.get("/{project_id}/tasks/stats/by-type")
async def get_task_type_stats(project_id: str, token: str = Query(...)):
    """获取按任务类型分组的统计信息 - 统计当前 Coder 的已分配任务"""
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
    
    # 获取该 Coder 所有未完成的分配
    from models import AssignmentState
    assignments = await project_db.assignments.find({
        "coder_user_id": coder_user_id,
        "state": {"$in": [AssignmentState.ASSIGNED, AssignmentState.IN_PROGRESS]}
    }).to_list(length=None)
    
    # 获取对应的任务 ID
    task_ids = [a["task_id"] for a in assignments]
    
    if not task_ids:
        return {"stats": {}, "total": 0}
    
    # 聚合统计：按 task_type 分组
    pipeline = [
        {"$match": {"_id": {"$in": task_ids}}},
        {"$group": {
            "_id": "$task_type",
            "count": {"$sum": 1}
        }}
    ]
    
    results = await project_db.tasks.aggregate(pipeline).to_list(None)
    
    # 转换为字典格式
    stats = {}
    for item in results:
        task_type = item["_id"] if item["_id"] else "text"  # 默认为 text
        stats[task_type] = item["count"]
    
    return {
        "stats": stats,
        "total": sum(stats.values())
    }

@router.get("/{project_id}/tasks", response_model=PaginatedResponse)
async def get_tasks(
    project_id: str,
    page: int = 1,
    limit: int = 10,
    status: Optional[TaskStatus] = None,
    tags: Optional[str] = None,
    task_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
):
    """获取任务列表 - 无需认证"""
    from datetime import datetime as dt
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
    query: dict = {}
    if status:
        query["status"] = status
    if tags:
        query["tags"] = {"$in": tags.split(",")}
    if task_type:
        query["task_type"] = task_type
    if date_from or date_to:
        date_filter: dict = {}
        if date_from:
            try:
                date_filter["$gte"] = dt.fromisoformat(date_from)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date_from format (use YYYY-MM-DD)")
        if date_to:
            try:
                # inclusive end of day
                date_filter["$lte"] = dt.fromisoformat(date_to).replace(hour=23, minute=59, second=59)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date_to format (use YYYY-MM-DD)")
        query["created_at"] = date_filter
    if search:
        query["title"] = {"$regex": search, "$options": "i"}

    # 如果查询 open 或 pending 任务，排除已被分配满（2次）的任务
    if status in ["open", "pending"]:
        # 使用聚合管道统计每个任务的分配次数
        pipeline = [
            {"$group": {
                "_id": "$task_id",
                "assignment_count": {"$sum": 1}
            }},
            {"$match": {"assignment_count": {"$gte": 2}}}  # 已分配2次或以上
        ]
        
        fully_assigned = await project_db.assignments.aggregate(pipeline).to_list(None)
        fully_assigned_task_ids = [doc["_id"] for doc in fully_assigned]
        
        # 排除已分配满的任务
        if fully_assigned_task_ids:
            query["_id"] = {"$nin": fully_assigned_task_ids}
    
    # 获取总数
    total = await project_db.tasks.count_documents(query)
    
    # 分页查询（limit=-1 表示返回全部）
    skip = (page - 1) * limit
    cursor = project_db.tasks.find(query).sort("created_at", -1).skip(skip)
    if limit > 0:
        cursor = cursor.limit(limit)
    tasks = await cursor.to_list(length=None)
    
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


@router.post("/{project_id}/tasks/upload-image")
async def upload_image_task(
    project_id: str,
    title: str = Form(...),
    tags: str = Form("[]"),
    image: UploadFile = File(...),
    token: str = Query(...)
):
    """
    上传图片任务（使用 Manager 的 Drive 统一存储）
    
    流程：
    1. 验证用户权限
    2. 获取项目 Manager 的 Google Drive 授权
    3. 上传图片到 Manager 的 Drive
    4. 分享给所有项目成员
    5. 创建图片类型的任务
    """
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    
    # 检查项目是否存在
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 验证用户是否属于该项目
    user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if str(user.get("project_id")) != project_id:
        raise HTTPException(status_code=403, detail="User not in this project")
    
    # 获取项目 Manager 的 Google credentials
    manager_id = project.get("owner_user_id")
    if not manager_id:
        raise HTTPException(status_code=500, detail="Project manager not found")
    
    manager = await core_db.users.find_one({"_id": manager_id})
    if not manager:
        raise HTTPException(status_code=404, detail="Manager not found")
    
    google_creds = manager.get("google_credentials")
    if not google_creds or not google_creds.get("access_token"):
        raise HTTPException(
            status_code=400,
            detail="Manager's Google Drive not authorized. Manager needs to login again to grant Drive access."
        )
    
    # 检查是否有 refresh_token
    if not google_creds.get("refresh_token"):
        raise HTTPException(
            status_code=400,
            detail="Manager's Google credentials are incomplete. Please logout and login again with Google to grant full Drive access."
        )
    
    # 验证文件类型
    if not image.content_type or not image.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="Only image files are allowed")
    
    # 读取文件内容
    try:
        file_content = await image.read()
        file_size = len(file_content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")
    
    # 限制文件大小 (10MB)
    if file_size > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image size exceeds 10MB limit")
    
    logger.info(f"User {user_id} uploading image task: {image.filename} ({file_size} bytes) to Manager's Drive")
    
    # 初始化 Google Drive 服务（使用 Manager 的凭证）
    try:
        drive_service = GoogleDriveService(
            access_token=google_creds["access_token"],
            refresh_token=google_creds.get("refresh_token")
        )
    except Exception as e:
        logger.error(f"Failed to initialize Drive service: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initialize Google Drive: {str(e)}")
    
    # 获取或创建项目文件夹
    project_folder_id = project.get("drive_folder_id")
    if not project_folder_id:
        logger.info("Creating project folder in Manager's Drive")
        try:
            project_name = project.get("name", f"Project_{project_id}")
            project_folder_id = drive_service.create_project_folder(f"OpenCoder_{project_name}")
            
            # 保存到项目文档
            await core_db.projects.update_one(
                {"_id": project_oid},
                {"$set": {"drive_folder_id": project_folder_id}}
            )
            logger.info(f"Project folder created: {project_folder_id}")
        except Exception as e:
            logger.error(f"Failed to create project folder: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create project folder: {str(e)}")
    
    # 上传图片到 Manager 的 Google Drive
    try:
        drive_result = drive_service.upload_image(
            file_content=file_content,
            filename=image.filename,
            mime_type=image.content_type,
            folder_id=project_folder_id
        )
        
        logger.info(f"Image uploaded to Manager's Drive: {drive_result['drive_file_id']}")
        
    except Exception as e:
        logger.error(f"Failed to upload to Drive: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload to Google Drive: {str(e)}")
    
    # 注意：图片已在 upload_image 中设置为"任何人有链接可查看"
    # 这样可以直接在 <img> 标签中加载，无需额外认证
    logger.info(f"Image is public via link (anyone with link can view)")
    
    # 如果 token 被刷新，更新 Manager 的数据库
    if drive_service.access_token != google_creds["access_token"]:
        logger.info("Manager's access token was refreshed, updating database")
        await core_db.users.update_one(
            {"_id": ObjectId(manager_id)},
            {
                "$set": {
                    "google_credentials.access_token": drive_service.access_token,
                    "google_credentials.token_expiry": datetime.utcnow() + timedelta(seconds=3600)
                }
            }
        )
    
    # 解析标签
    try:
        tags_list = json.loads(tags) if tags else []
    except Exception:
        tags_list = []
    
    # 创建图片任务
    project_db = await get_project_db(project_id)
    
    task_doc = {
        "title": title,
        "task_type": "image",
        "payload": {
            "text": None,
            "url": None,
            "image": {
                **drive_result,
                "uploaded_at": datetime.utcnow()
            },
            "meta": {}
        },
        "status": "open",
        "tags": tags_list,
        "created_by": ObjectId(user_id),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    try:
        result = await project_db.tasks.insert_one(task_doc)
        task_doc["_id"] = result.inserted_id
        
        logger.info(f"Created image task: {result.inserted_id}")
        
        return {
            "success": True,
            "task_id": str(result.inserted_id),
            "image_url": drive_result["drive_file_url"],
            "message": "Image task created successfully"
        }
        
    except Exception as e:
        logger.error(f"Failed to create task: {e}")
        # 如果任务创建失败，尝试删除已上传的图片
        try:
            drive_service.delete_file(drive_result["drive_file_id"])
            logger.info("Rolled back: deleted uploaded image")
        except Exception:
            pass
        
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")


@router.post("/{project_id}/tasks/upload-video")
async def upload_video_task(
    project_id: str,
    title: str = Form(...),
    tags: str = Form("[]"),
    video: UploadFile = File(...),
    token: str = Query(...)
):
    """
    上传视频任务（使用 Manager 的 Drive 统一存储）
    
    支持格式: MP4, MOV, AVI, MKV, WebM
    
    流程：
    1. 验证用户权限
    2. 获取项目 Manager 的 Google Drive 授权
    3. 上传视频到 Manager 的 Drive
    4. 创建视频类型的任务
    """
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    
    # 检查项目是否存在
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 验证用户是否属于该项目
    user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if str(user.get("project_id")) != project_id:
        raise HTTPException(status_code=403, detail="User not in this project")
    
    # 获取项目 Manager 的 Google credentials
    manager_id = project.get("owner_user_id")
    if not manager_id:
        raise HTTPException(status_code=500, detail="Project manager not found")
    
    manager = await core_db.users.find_one({"_id": manager_id})
    if not manager:
        raise HTTPException(status_code=404, detail="Manager not found")
    
    google_creds = manager.get("google_credentials")
    if not google_creds or not google_creds.get("access_token"):
        raise HTTPException(
            status_code=400,
            detail="Manager's Google Drive not authorized. Manager needs to login again to grant Drive access."
        )
    
    if not google_creds.get("refresh_token"):
        raise HTTPException(
            status_code=400,
            detail="Manager's Google credentials are incomplete. Please logout and login again with Google to grant full Drive access."
        )
    
    # 验证文件类型
    if not video.content_type or not video.content_type.startswith('video/'):
        raise HTTPException(status_code=400, detail="Only video files are allowed (MP4, MOV, AVI, MKV, WebM)")
    
    # 读取文件内容
    try:
        file_content = await video.read()
        file_size = len(file_content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")
    
    # 限制文件大小 (100MB)
    if file_size > 100 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Video size exceeds 100MB limit")
    
    logger.info(f"User {user_id} uploading video task: {video.filename} ({file_size} bytes) to Manager's Drive")
    
    # 初始化 Google Drive 服务（使用 Manager 的凭证）
    try:
        drive_service = GoogleDriveService(
            access_token=google_creds["access_token"],
            refresh_token=google_creds.get("refresh_token")
        )
    except Exception as e:
        logger.error(f"Failed to initialize Drive service: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initialize Google Drive: {str(e)}")
    
    # 获取或创建项目文件夹
    project_folder_id = project.get("drive_folder_id")
    if not project_folder_id:
        logger.info("Creating project folder in Manager's Drive")
        try:
            project_name = project.get("name", f"Project_{project_id}")
            project_folder_id = drive_service.create_project_folder(f"OpenCoder_{project_name}")
            
            await core_db.projects.update_one(
                {"_id": project_oid},
                {"$set": {"drive_folder_id": project_folder_id}}
            )
            logger.info(f"Project folder created: {project_folder_id}")
        except Exception as e:
            logger.error(f"Failed to create project folder: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create project folder: {str(e)}")
    
    # 上传视频到 Manager 的 Google Drive
    try:
        drive_result = drive_service.upload_file(
            file_content=file_content,
            filename=video.filename,
            mime_type=video.content_type,
            folder_id=project_folder_id
        )
        
        logger.info(f"Video uploaded to Manager's Drive: {drive_result['drive_file_id']}")
        
    except Exception as e:
        logger.error(f"Failed to upload to Drive: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload to Google Drive: {str(e)}")
    
    logger.info(f"Video is public via link (anyone with link can view)")
    
    # 如果 token 被刷新，更新 Manager 的数据库
    if drive_service.access_token != google_creds["access_token"]:
        logger.info("Manager's access token was refreshed, updating database")
        await core_db.users.update_one(
            {"_id": ObjectId(manager_id)},
            {
                "$set": {
                    "google_credentials.access_token": drive_service.access_token,
                    "google_credentials.token_expiry": datetime.utcnow() + timedelta(seconds=3600)
                }
            }
        )
    
    # 解析标签
    try:
        tags_list = json.loads(tags) if tags else []
    except Exception:
        tags_list = []
    
    # 创建视频任务
    project_db = await get_project_db(project_id)
    
    task_doc = {
        "title": title,
        "task_type": "video",
        "payload": {
            "text": None,
            "url": None,
            "video": {
                **drive_result,
                "uploaded_at": datetime.utcnow()
            },
            "meta": {}
        },
        "status": "open",
        "tags": tags_list,
        "created_by": ObjectId(user_id),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    try:
        result = await project_db.tasks.insert_one(task_doc)
        task_doc["_id"] = result.inserted_id
        
        logger.info(f"Created video task: {result.inserted_id}")
        
        return {
            "success": True,
            "task_id": str(result.inserted_id),
            "video_url": drive_result["drive_file_url"],
            "message": "Video task created successfully"
        }
        
    except Exception as e:
        logger.error(f"Failed to create task: {e}")
        # 如果任务创建失败，尝试删除已上传的视频
        try:
            drive_service.delete_file(drive_result["drive_file_id"])
            logger.info("Rolled back: deleted uploaded video")
        except Exception:
            pass
        
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")


@router.post("/{project_id}/tasks/upload-audio")
async def upload_audio_task(
    project_id: str,
    title: str = Form(...),
    tags: str = Form("[]"),
    audio: UploadFile = File(...),
    token: str = Query(...)
):
    """
    上传音频任务（使用 Manager 的 Drive 统一存储）
    
    支持格式: MP3, WAV, AAC, OGG, M4A
    
    流程：
    1. 验证用户权限
    2. 获取项目 Manager 的 Google Drive 授权
    3. 上传音频到 Manager 的 Drive
    4. 创建音频类型的任务
    """
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    
    # 检查项目是否存在
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 验证用户是否属于该项目
    user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if str(user.get("project_id")) != project_id:
        raise HTTPException(status_code=403, detail="User not in this project")
    
    # 获取项目 Manager 的 Google credentials
    manager_id = project.get("owner_user_id")
    if not manager_id:
        raise HTTPException(status_code=500, detail="Project manager not found")
    
    manager = await core_db.users.find_one({"_id": manager_id})
    if not manager:
        raise HTTPException(status_code=404, detail="Manager not found")
    
    google_creds = manager.get("google_credentials")
    if not google_creds or not google_creds.get("access_token"):
        raise HTTPException(
            status_code=400,
            detail="Manager's Google Drive not authorized. Manager needs to login again to grant Drive access."
        )
    
    if not google_creds.get("refresh_token"):
        raise HTTPException(
            status_code=400,
            detail="Manager's Google credentials are incomplete. Please logout and login again with Google to grant full Drive access."
        )
    
    # 验证文件类型
    if not audio.content_type or not audio.content_type.startswith('audio/'):
        raise HTTPException(status_code=400, detail="Only audio files are allowed (MP3, WAV, AAC, OGG, M4A)")
    
    # 读取文件内容
    try:
        file_content = await audio.read()
        file_size = len(file_content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to read file: {str(e)}")
    
    # 限制文件大小 (50MB)
    if file_size > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Audio size exceeds 50MB limit")
    
    logger.info(f"User {user_id} uploading audio task: {audio.filename} ({file_size} bytes) to Manager's Drive")
    
    # 初始化 Google Drive 服务（使用 Manager 的凭证）
    try:
        drive_service = GoogleDriveService(
            access_token=google_creds["access_token"],
            refresh_token=google_creds.get("refresh_token")
        )
    except Exception as e:
        logger.error(f"Failed to initialize Drive service: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initialize Google Drive: {str(e)}")
    
    # 获取或创建项目文件夹
    project_folder_id = project.get("drive_folder_id")
    if not project_folder_id:
        logger.info("Creating project folder in Manager's Drive")
        try:
            project_name = project.get("name", f"Project_{project_id}")
            project_folder_id = drive_service.create_project_folder(f"OpenCoder_{project_name}")
            
            await core_db.projects.update_one(
                {"_id": project_oid},
                {"$set": {"drive_folder_id": project_folder_id}}
            )
            logger.info(f"Project folder created: {project_folder_id}")
        except Exception as e:
            logger.error(f"Failed to create project folder: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create project folder: {str(e)}")
    
    # 上传音频到 Manager 的 Google Drive
    try:
        drive_result = drive_service.upload_file(
            file_content=file_content,
            filename=audio.filename,
            mime_type=audio.content_type,
            folder_id=project_folder_id
        )
        
        logger.info(f"Audio uploaded to Manager's Drive: {drive_result['drive_file_id']}")
        
    except Exception as e:
        logger.error(f"Failed to upload to Drive: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload to Google Drive: {str(e)}")
    
    logger.info(f"Audio is public via link (anyone with link can view)")
    
    # 如果 token 被刷新，更新 Manager 的数据库
    if drive_service.access_token != google_creds["access_token"]:
        logger.info("Manager's access token was refreshed, updating database")
        await core_db.users.update_one(
            {"_id": ObjectId(manager_id)},
            {
                "$set": {
                    "google_credentials.access_token": drive_service.access_token,
                    "google_credentials.token_expiry": datetime.utcnow() + timedelta(seconds=3600)
                }
            }
        )
    
    # 解析标签
    try:
        tags_list = json.loads(tags) if tags else []
    except Exception:
        tags_list = []
    
    # 创建音频任务
    project_db = await get_project_db(project_id)
    
    task_doc = {
        "title": title,
        "task_type": "audio",
        "payload": {
            "text": None,
            "url": None,
            "audio": {
                **drive_result,
                "uploaded_at": datetime.utcnow()
            },
            "meta": {}
        },
        "status": "open",
        "tags": tags_list,
        "created_by": ObjectId(user_id),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    try:
        result = await project_db.tasks.insert_one(task_doc)
        task_doc["_id"] = result.inserted_id
        
        logger.info(f"Created audio task: {result.inserted_id}")
        
        return {
            "success": True,
            "task_id": str(result.inserted_id),
            "audio_url": drive_result["drive_file_url"],
            "message": "Audio task created successfully"
        }
        
    except Exception as e:
        logger.error(f"Failed to create task: {e}")
        # 如果任务创建失败，尝试删除已上传的音频
        try:
            drive_service.delete_file(drive_result["drive_file_id"])
            logger.info("Rolled back: deleted uploaded audio")
        except Exception:
            pass
        
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")


@router.post("/{project_id}/tasks/init-media-upload")
async def init_media_upload(
    project_id: str,
    body: InitMediaUploadRequest,
    request: Request,
    token: str = Query(...),
):
    """
    Create a Google Drive resumable upload session.
    Browser uploads file bytes directly to the returned upload_url.
    """
    user_id, project, _manager, drive_service, project_oid = await _resolve_manager_drive_context(
        project_id, token
    )
    _validate_media_meta(body.media_type, body.mime_type, body.file_size)

    folder_id = await _ensure_project_folder(project, project_oid, drive_service)
    origin = body.origin or request.headers.get("origin") or os.getenv("FRONTEND_URL")

    try:
        upload_url = drive_service.create_resumable_upload_session(
            filename=body.filename,
            mime_type=body.mime_type,
            file_size=body.file_size,
            folder_id=folder_id,
            origin=origin,
        )
    except Exception as e:
        logger.error(f"init-media-upload failed for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "upload_url": upload_url,
        "folder_id": folder_id,
        "media_type": body.media_type,
    }


@router.post("/{project_id}/tasks/finalize-media-upload")
async def finalize_media_upload(
    project_id: str,
    body: FinalizeMediaUploadRequest,
    token: str = Query(...),
):
    """
    After browser finished Drive upload: verify folder, set public permission, create task.
    """
    user_id, project, _manager, drive_service, project_oid = await _resolve_manager_drive_context(
        project_id, token
    )

    if body.media_type not in MEDIA_SIZE_LIMITS:
        raise HTTPException(status_code=400, detail="media_type must be image, video, or audio")

    if not body.drive_file_id or not body.drive_file_id.strip():
        raise HTTPException(status_code=400, detail="drive_file_id is required")

    folder_id = project.get("drive_folder_id")
    if not folder_id:
        raise HTTPException(
            status_code=400,
            detail="Project Drive folder not found. Please init upload first.",
        )

    if not drive_service.file_in_folder(body.drive_file_id, folder_id):
        raise HTTPException(
            status_code=403,
            detail="Uploaded file is not in the project Drive folder",
        )

    try:
        drive_result = drive_service.finalize_uploaded_file(
            file_id=body.drive_file_id,
            filename=body.original_filename,
            mime_type=body.mime_type,
            file_size=body.file_size,
        )
    except Exception as e:
        logger.error(f"finalize-media-upload Drive finalize failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # Prefer client-provided metadata when Drive returns empty size/name briefly
    if body.original_filename:
        drive_result["original_filename"] = body.original_filename
    if body.mime_type:
        drive_result["mime_type"] = body.mime_type
    if body.file_size:
        drive_result["file_size"] = body.file_size

    project_db = await get_project_db(project_id)
    media_key = body.media_type  # image | video | audio
    task_doc = {
        "title": body.title.strip() or drive_result.get("original_filename") or "Untitled",
        "task_type": media_key,
        "payload": {
            "text": None,
            "url": None,
            "image": None,
            "video": None,
            "audio": None,
            "meta": {},
        },
        "status": "open",
        "tags": body.tags or [],
        "created_by": ObjectId(user_id),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }
    task_doc["payload"][media_key] = {
        **drive_result,
        "uploaded_at": datetime.utcnow(),
    }

    try:
        result = await project_db.tasks.insert_one(task_doc)
        task_id = result.inserted_id
        logger.info(f"Created {media_key} task via resumable upload: {task_id}")

        # Auto-trigger transcription for audio/video tasks
        if media_key in ("audio", "video"):
            try:
                now = datetime.utcnow()
                job_doc = {
                    "task_id":       task_id,
                    "project_id":    project_oid,
                    "status":        "queued",
                    "attempt_count": 0,
                    "max_attempts":  3,
                    "locked_at":     None,
                    "locked_by":     None,
                    "error_code":    None,
                    "error_message": None,
                    "transcript_id": None,
                    "created_by":    ObjectId(user_id),
                    "created_at":    now,
                    "started_at":    None,
                    "completed_at":  None,
                    "updated_at":    now,
                }
                core_db = get_core_db()
                await core_db.transcription_jobs.insert_one(job_doc)
                await project_db.tasks.update_one(
                    {"_id": task_id},
                    {"$set": {"transcription_status": "queued", "updated_at": now}},
                )
                logger.info(f"Auto-queued transcription job for {media_key} task {task_id}")
            except Exception as tx_err:
                # Non-fatal: task was created, transcription can be triggered manually
                logger.warning(f"Auto-transcription queue failed (non-fatal): {tx_err}")

        return {
            "success": True,
            "task_id": str(task_id),
            f"{media_key}_url": drive_result["drive_file_url"],
            "message": f"{media_key.capitalize()} task created successfully",
            "transcription_queued": media_key in ("audio", "video"),
        }
    except Exception as e:
        logger.error(f"Failed to create task after resumable upload: {e}")
        try:
            drive_service.delete_file(body.drive_file_id)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")


@router.post("/{project_id}/tasks/finalize-multimodal-upload")
async def finalize_multimodal_upload(
    project_id: str,
    body: FinalizeMultimodalUploadRequest,
    token: str = Query(...),
):
    """
    After browser uploaded multiple Drive files: create ONE multimodal task
    with image/video/audio payload slots (at most one of each).
    """
    user_id, project, _manager, drive_service, project_oid = await _resolve_manager_drive_context(
        project_id, token
    )

    if not body.items:
        raise HTTPException(status_code=400, detail="At least one media item is required")

    seen_types = set()
    for item in body.items:
        if item.media_type not in MEDIA_SIZE_LIMITS:
            raise HTTPException(status_code=400, detail=f"Invalid media_type: {item.media_type}")
        if item.media_type in seen_types:
            raise HTTPException(
                status_code=400,
                detail=f"Duplicate media_type '{item.media_type}'. Multimodal allows one of each type.",
            )
        seen_types.add(item.media_type)
        if not item.drive_file_id or not item.drive_file_id.strip():
            raise HTTPException(status_code=400, detail="drive_file_id is required for each item")

    folder_id = project.get("drive_folder_id")
    if not folder_id:
        raise HTTPException(
            status_code=400,
            detail="Project Drive folder not found. Please init upload first.",
        )

    payload = {
        "text": (body.text.strip() if body.text and body.text.strip() else None),
        "url": None,
        "image": None,
        "video": None,
        "audio": None,
        "meta": {"modalities": sorted(seen_types)},
    }

    for item in body.items:
        if not drive_service.file_in_folder(item.drive_file_id, folder_id):
            raise HTTPException(
                status_code=403,
                detail=f"Uploaded file {item.drive_file_id} is not in the project Drive folder",
            )
        try:
            drive_result = drive_service.finalize_uploaded_file(
                file_id=item.drive_file_id,
                filename=item.original_filename,
                mime_type=item.mime_type,
                file_size=item.file_size,
            )
        except Exception as e:
            logger.error(f"finalize-multimodal Drive finalize failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

        if item.original_filename:
            drive_result["original_filename"] = item.original_filename
        if item.mime_type:
            drive_result["mime_type"] = item.mime_type
        if item.file_size:
            drive_result["file_size"] = item.file_size

        payload[item.media_type] = {
            **drive_result,
            "uploaded_at": datetime.utcnow(),
        }

    project_db = await get_project_db(project_id)
    title = body.title.strip()
    if not title:
        title = next(
            (i.original_filename for i in body.items if i.original_filename),
            "Multimodal Task",
        )

    task_doc = {
        "title": title,
        "task_type": TaskType.MULTIMODAL.value,
        "payload": payload,
        "status": "open",
        "tags": body.tags or [],
        "created_by": ObjectId(user_id),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    try:
        result = await project_db.tasks.insert_one(task_doc)
        task_id = result.inserted_id
        logger.info(f"Created multimodal task {task_id} with modalities={sorted(seen_types)}")

        transcription_queued = False
        if "audio" in seen_types or "video" in seen_types:
            try:
                now = datetime.utcnow()
                job_doc = {
                    "task_id":       task_id,
                    "project_id":    project_oid,
                    "status":        "queued",
                    "attempt_count": 0,
                    "max_attempts":  3,
                    "locked_at":     None,
                    "locked_by":     None,
                    "error_code":    None,
                    "error_message": None,
                    "transcript_id": None,
                    "created_by":    ObjectId(user_id),
                    "created_at":    now,
                    "started_at":    None,
                    "completed_at":  None,
                    "updated_at":    now,
                }
                core_db = get_core_db()
                await core_db.transcription_jobs.insert_one(job_doc)
                await project_db.tasks.update_one(
                    {"_id": task_id},
                    {"$set": {"transcription_status": "queued", "updated_at": now}},
                )
                transcription_queued = True
                logger.info(f"Auto-queued transcription for multimodal task {task_id}")
            except Exception as tx_err:
                logger.warning(f"Auto-transcription queue failed (non-fatal): {tx_err}")

        return {
            "success": True,
            "task_id": str(task_id),
            "modalities": sorted(seen_types),
            "message": "Multimodal task created successfully",
            "transcription_queued": transcription_queued,
        }
    except Exception as e:
        logger.error(f"Failed to create multimodal task: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")

