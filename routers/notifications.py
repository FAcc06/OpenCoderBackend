"""
Notifications Router
通知系统 - 支持手动留言和自动触发
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime
from bson import ObjectId

from database import get_core_db, get_project_db
from routers.auth import verify_token

router = APIRouter()


# ============== Request/Response Models ==============

class CreateNotificationRequest(BaseModel):
    """创建通知请求"""
    type: str  # "message" 或 "task_completed"
    message: str
    priority: Optional[str] = "normal"  # "low", "normal", "high"


class NotificationResponse(BaseModel):
    """通知响应"""
    id: str
    type: str
    message: str
    priority: str
    from_user_id: str
    from_user_name: str
    to_user_id: str
    project_id: str
    is_read: bool
    created_at: str


# ============== Helper Functions ==============

async def create_notification(
    project_id: str,
    from_user_id: str,
    to_user_id: str,
    notification_type: str,
    message: str,
    priority: str = "normal"
):
    """创建通知记录"""
    core_db = get_core_db()
    
    notification = {
        "project_id": project_id,
        "from_user_id": ObjectId(from_user_id),
        "to_user_id": ObjectId(to_user_id),
        "type": notification_type,
        "message": message,
        "priority": priority,
        "is_read": False,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    result = await core_db.notifications.insert_one(notification)
    notification["_id"] = result.inserted_id
    
    return notification


async def check_and_notify_all_tasks_completed(project_id: str, coder_user_id: str):
    """
    检查coder是否完成了所有分配的任务
    如果是，自动发送通知给manager
    """
    project_db = await get_project_db(project_id)
    if project_db is None:
        return False
    
    # 获取该coder的所有分配任务
    coder_oid = ObjectId(coder_user_id)
    total_assignments = await project_db.assignments.count_documents({
        "coder_user_id": coder_oid
    })
    
    if total_assignments == 0:
        return False
    
    # 获取已完成的任务数
    completed_assignments = await project_db.assignments.count_documents({
        "coder_user_id": coder_oid,
        "state": "done"
    })
    
    # 如果全部完成，发送通知
    if completed_assignments == total_assignments:
        core_db = get_core_db()
        
        # 获取coder信息
        coder = await core_db.users.find_one({"_id": coder_oid})
        if not coder:
            return False
        
        coder_name = coder.get("name") or coder.get("username") or coder.get("email", "Coder")
        
        # 获取project的manager
        project = await core_db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            return False
        
        manager_id = project.get("owner_id")
        if not manager_id:
            print(f"⚠️  Project {project_id} has no owner_id")
            return False
        
        manager_id = str(manager_id)
        
        # 检查是否已经发送过此通知（避免重复）
        existing_notification = await core_db.notifications.find_one({
            "project_id": project_id,
            "from_user_id": coder_oid,
            "type": "all_tasks_completed",
            "created_at": {"$gte": datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)}
        })
        
        if existing_notification:
            return False  # 今天已经发送过了
        
        # 发送通知
        await create_notification(
            project_id=project_id,
            from_user_id=coder_user_id,
            to_user_id=manager_id,
            notification_type="all_tasks_completed",
            message=f"{coder_name} has completed all assigned tasks ({total_assignments} tasks). Please review and assign new tasks.",
            priority="high"
        )
        
        return True
    
    return False


# ============== API Endpoints ==============

@router.post("/api/notifications/send")
async def send_notification(
    request: CreateNotificationRequest,
    token: str = Query(...)
):
    """
    发送通知（手动留言）
    
    Coder可以给Manager发送消息
    """
    # 验证用户
    try:
        user_data = verify_token(token)
        from_user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    
    # 获取发送者信息
    from_user = await core_db.users.find_one({"_id": ObjectId(from_user_id)})
    if not from_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    project_id = from_user.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="User not assigned to any project")
    
    # 获取项目的manager
    project = await core_db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    manager_id = project.get("owner_id")
    if not manager_id:
        raise HTTPException(status_code=400, detail="Project has no manager assigned")
    
    # 创建通知
    notification = await create_notification(
        project_id=str(project_id),
        from_user_id=from_user_id,
        to_user_id=str(manager_id),
        notification_type=request.type,
        message=request.message,
        priority=request.priority
    )
    
    return {
        "success": True,
        "notification_id": str(notification["_id"]),
        "message": "Notification sent successfully"
    }


@router.get("/api/notifications/list")
async def list_notifications(
    token: str = Query(...),
    unread_only: bool = Query(False)
):
    """
    获取当前用户的通知列表
    
    Manager查看所有收到的通知
    """
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    
    # 构建查询条件
    query = {"to_user_id": ObjectId(user_id)}
    if unread_only:
        query["is_read"] = False
    
    # 获取通知列表
    notifications = await core_db.notifications.find(query).sort("created_at", -1).to_list(100)
    
    # 格式化返回数据
    result = []
    for notif in notifications:
        # 获取发送者信息
        from_user = await core_db.users.find_one({"_id": notif["from_user_id"]})
        from_user_name = "Unknown"
        if from_user:
            from_user_name = from_user.get("name") or from_user.get("username") or from_user.get("email", "User")
        
        result.append({
            "id": str(notif["_id"]),
            "type": notif["type"],
            "message": notif["message"],
            "priority": notif["priority"],
            "from_user_id": str(notif["from_user_id"]),
            "from_user_name": from_user_name,
            "to_user_id": str(notif["to_user_id"]),
            "project_id": str(notif["project_id"]),
            "is_read": notif["is_read"],
            "created_at": notif["created_at"].isoformat()
        })
    
    # 统计未读数量
    unread_count = await core_db.notifications.count_documents({
        "to_user_id": ObjectId(user_id),
        "is_read": False
    })
    
    return {
        "success": True,
        "notifications": result,
        "unread_count": unread_count,
        "total_count": len(result)
    }


@router.post("/api/notifications/{notification_id}/mark-read")
async def mark_notification_read(
    notification_id: str,
    token: str = Query(...)
):
    """标记通知为已读"""
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    
    # 更新通知状态
    result = await core_db.notifications.update_one(
        {
            "_id": ObjectId(notification_id),
            "to_user_id": ObjectId(user_id)
        },
        {
            "$set": {
                "is_read": True,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {
        "success": True,
        "message": "Notification marked as read"
    }


@router.post("/api/notifications/mark-all-read")
async def mark_all_notifications_read(token: str = Query(...)):
    """标记所有通知为已读"""
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    
    # 更新所有未读通知
    result = await core_db.notifications.update_many(
        {
            "to_user_id": ObjectId(user_id),
            "is_read": False
        },
        {
            "$set": {
                "is_read": True,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    return {
        "success": True,
        "marked_count": result.modified_count,
        "message": f"Marked {result.modified_count} notifications as read"
    }


@router.get("/api/notifications/unread-count")
async def get_unread_count(token: str = Query(...)):
    """获取未读通知数量"""
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    
    count = await core_db.notifications.count_documents({
        "to_user_id": ObjectId(user_id),
        "is_read": False
    })
    
    return {
        "success": True,
        "unread_count": count
    }
