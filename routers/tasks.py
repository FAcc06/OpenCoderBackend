from fastapi import APIRouter, HTTPException, Depends, status, UploadFile, File, Form, Query
from typing import List, Optional
from bson import ObjectId
import json

from database import get_core_db, get_project_db
from models import Task, TaskCreate, TaskBulkCreate, TaskUpdate, TaskStatus, TaskType, PaginatedResponse, User
from routers.auth import verify_token
from services.google_drive import GoogleDriveService
from utils import paginate_query, calculate_pages
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
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
    
    # 如果查询 open 或 pending 任务，排除已分配的任务
    if status in ["open", "pending"]:
        # 获取所有已分配的任务 ID
        assigned_tasks = await project_db.assignments.find({}, {"task_id": 1}).to_list(length=None)
        assigned_task_ids = [a["task_id"] for a in assigned_tasks]
        
        # 排除已分配的任务
        if assigned_task_ids:
            query["_id"] = {"$nin": assigned_task_ids}
    
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