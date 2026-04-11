"""
Chat Router
实时聊天系统 - REST API + WebSocket
"""
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query
from typing import Optional, List
from datetime import datetime, timedelta
from bson import ObjectId
import json

from database import get_core_db, get_project_db
from routers.auth import verify_token
from models import (
    Conversation, ChatMessage, ChatMessageCreate,
    ConversationType, OnlineState, UserChatStatus
)
from chat_manager import connection_manager

router = APIRouter()


# ============== Helper Functions ==============

async def check_chat_permission(user_id: str, conversation_id: str, core_db, project_db) -> bool:
    """检查用户是否有权限访问某个对话"""
    conversation = await project_db.conversations.find_one({"_id": ObjectId(conversation_id)})
    if not conversation:
        return False
    
    # 检查用户是否在参与者列表中
    user_oid = ObjectId(user_id)
    if user_oid in conversation.get("participants", []):
        return True
    
    # Manager 可以访问项目内所有对话
    user = await core_db.users.find_one({"_id": user_oid})
    if user and user.get("role") == "manager":
        project_id = conversation.get("project_id")
        if project_id and str(user.get("project_id")) == str(project_id):
            return True
    
    return False


async def can_start_p2p_chat(user1_id: str, user2_id: str, core_db) -> bool:
    """检查两个用户是否可以开始1v1聊天"""
    user1 = await core_db.users.find_one({"_id": ObjectId(user1_id)})
    user2 = await core_db.users.find_one({"_id": ObjectId(user2_id)})
    
    if not user1 or not user2:
        return False
    
    # 同项目才能1v1
    if str(user1.get("project_id")) == str(user2.get("project_id")):
        return True
    
    # Manager可以主动联系项目内任何人
    if user1.get("role") == "manager":
        if str(user1.get("project_id")) == str(user2.get("project_id")):
            return True
    
    return False


# ============== REST API Endpoints ==============

@router.get("/api/chat/conversations")
async def get_conversations(
    token: str = Query(...),
    project_id: Optional[str] = Query(None)
):
    """
    获取当前用户的会话列表
    包含最后一条消息预览
    """
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # 如果没有指定 project_id，使用用户的项目
    if not project_id:
        project_id = str(user.get("project_id"))
        if not project_id:
            return {"success": True, "conversations": []}
    
    project_db = await get_project_db(project_id)
    if project_db is None:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 查询用户参与的对话
    user_oid = ObjectId(user_id)
    conversations = await project_db.conversations.find({
        "participants": user_oid
    }).sort("updated_at", -1).to_list(100)
    
    # 格式化返回
    result = []
    for conv in conversations:
        # 获取参与者信息
        participants_info = []
        for participant_id in conv.get("participants", []):
            participant = await core_db.users.find_one({"_id": participant_id})
            if participant:
                participants_info.append({
                    "id": str(participant["_id"]),
                    "name": participant.get("name"),
                    "avatar_url": participant.get("avatar_url"),
                    "online_state": connection_manager.is_online(str(participant["_id"]))
                })
        
        result.append({
            "id": str(conv["_id"]),
            "type": conv.get("type"),
            "name": conv.get("name"),
            "participants": participants_info,
            "last_message": conv.get("last_message"),
            "updated_at": conv.get("updated_at").isoformat() if conv.get("updated_at") else None
        })
    
    return {
        "success": True,
        "conversations": result
    }


@router.get("/api/chat/messages")
async def get_messages(
    conversation_id: str = Query(...),
    token: str = Query(...),
    limit: int = Query(50, le=100),
    before_timestamp: Optional[str] = Query(None)
):
    """
    获取对话历史消息
    支持游标翻页
    """
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    project_id = str(user.get("project_id"))
    if not project_id:
        raise HTTPException(status_code=400, detail="User not in any project")
    
    project_db = await get_project_db(project_id)
    
    # 检查权限
    has_permission = await check_chat_permission(user_id, conversation_id, core_db, project_db)
    if not has_permission:
        raise HTTPException(status_code=403, detail="No permission to access this conversation")
    
    # 构建查询条件
    query = {"conversation_id": ObjectId(conversation_id)}
    if before_timestamp:
        before_dt = datetime.fromisoformat(before_timestamp)
        query["created_at"] = {"$lt": before_dt}
    
    # 查询消息
    messages = await project_db.chat_messages.find(query).sort("created_at", -1).limit(limit).to_list(limit)
    
    # 反转顺序（最新的在最后）
    messages.reverse()
    
    # 格式化返回
    result = []
    for msg in messages:
        sender = await core_db.users.find_one({"_id": msg["sender_id"]})
        result.append({
            "id": str(msg["_id"]),
            "conversation_id": str(msg["conversation_id"]),
            "sender": {
                "id": str(msg["sender_id"]),
                "name": sender.get("name") if sender else "Unknown",
                "avatar_url": sender.get("avatar_url") if sender else None
            },
            "content": msg["content"],
            "message_type": msg.get("message_type", "text"),
            "read_by": [str(uid) for uid in msg.get("read_by", [])],
            "created_at": msg["created_at"].isoformat()
        })
    
    return {
        "success": True,
        "messages": result,
        "has_more": len(messages) == limit
    }


@router.get("/api/chat/members")
async def get_chat_members(
    token: str = Query(...),
    project_id: Optional[str] = Query(None)
):
    """
    获取项目内成员列表及在线状态
    隐身用户显示为离线
    """
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # 如果没有指定 project_id，使用用户的项目
    if not project_id:
        project_id = str(user.get("project_id"))
        if not project_id:
            return {"success": True, "members": []}
    
    # 查询项目成员
    members = await core_db.users.find({
        "project_id": ObjectId(project_id)
    }).to_list(100)
    
    result = []
    for member in members:
        member_id = str(member["_id"])
        
        # 检查实时在线状态
        is_online = connection_manager.is_online(member_id)
        
        # 脱敏逻辑：如果设置为隐身，显示为离线
        online_state = member.get("online_state", "offline")
        if online_state == "invisible":
            display_state = "offline"
        elif is_online:
            display_state = "online"
        else:
            display_state = "offline"
        
        result.append({
            "id": member_id,
            "name": member.get("name"),
            "email": member.get("email"),
            "role": member.get("role"),
            "avatar_url": member.get("avatar_url"),
            "online_state": display_state,
            "last_seen": member.get("last_seen").isoformat() if member.get("last_seen") else None
        })
    
    return {
        "success": True,
        "members": result
    }


@router.post("/api/chat/conversations/create")
async def create_conversation(
    token: str = Query(...),
    type: str = Query(...),
    participant_ids: List[str] = Query(...),
    name: Optional[str] = Query(None)
):
    """
    创建新对话
    type: "p2p" | "project_group"
    """
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    project_id = user.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="User not in any project")
    
    project_db = await get_project_db(str(project_id))
    
    # 验证权限
    if type == "p2p":
        if len(participant_ids) != 2:
            raise HTTPException(status_code=400, detail="P2P chat requires exactly 2 participants")
        
        other_user_id = participant_ids[0] if participant_ids[1] == user_id else participant_ids[1]
        can_chat = await can_start_p2p_chat(user_id, other_user_id, core_db)
        if not can_chat:
            raise HTTPException(status_code=403, detail="Cannot start chat with this user")
    
    # 检查是否已存在相同的对话
    participants_oids = [ObjectId(uid) for uid in participant_ids]
    existing = await project_db.conversations.find_one({
        "type": type,
        "participants": {"$all": participants_oids, "$size": len(participants_oids)}
    })
    
    if existing:
        return {
            "success": True,
            "conversation_id": str(existing["_id"]),
            "already_exists": True
        }
    
    # 创建新对话
    conversation = {
        "type": type,
        "participants": participants_oids,
        "project_id": project_id,
        "name": name,
        "last_message": None,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    result = await project_db.conversations.insert_one(conversation)
    
    return {
        "success": True,
        "conversation_id": str(result.inserted_id),
        "already_exists": False
    }


@router.post("/api/chat/messages/{message_id}/mark-read")
async def mark_message_read(
    message_id: str,
    token: str = Query(...)
):
    """标记消息为已读"""
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    core_db = get_core_db()
    user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    project_id = str(user.get("project_id"))
    if not project_id:
        raise HTTPException(status_code=400, detail="User not in any project")
    
    project_db = await get_project_db(project_id)
    
    # 更新消息
    user_oid = ObjectId(user_id)
    result = await project_db.chat_messages.update_one(
        {"_id": ObjectId(message_id)},
        {"$addToSet": {"read_by": user_oid}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Message not found")
    
    return {"success": True}


@router.post("/api/chat/update-status")
async def update_online_status(
    token: str = Query(...),
    status: str = Query(...)
):
    """更新用户在线状态"""
    # 验证用户
    try:
        user_data = verify_token(token)
        user_id = user_data.get("sub")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")
    
    if status not in ["online", "offline", "invisible"]:
        raise HTTPException(status_code=400, detail="Invalid status")
    
    core_db = get_core_db()
    
    # 更新数据库
    result = await core_db.users.update_one(
        {"_id": ObjectId(user_id)},
        {
            "$set": {
                "online_state": status,
                "last_seen": datetime.utcnow()
            }
        }
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"success": True, "status": status}


# ============== WebSocket Endpoint ==============

@router.websocket("/ws/chat/{user_id}")
async def websocket_chat_endpoint(websocket: WebSocket, user_id: str, token: str):
    """
    WebSocket 连接端点
    处理实时消息收发
    """
    # 验证 token
    try:
        user_data = verify_token(token)
        verified_user_id = user_data.get("sub")
        
        if verified_user_id != user_id:
            await websocket.close(code=1008, reason="User ID mismatch")
            return
    except Exception as e:
        await websocket.close(code=1008, reason="Invalid token")
        return
    
    # 连接用户
    await connection_manager.connect(user_id, websocket)
    
    # 更新数据库中的在线状态
    core_db = get_core_db()
    await core_db.users.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"online_state": "online", "last_seen": datetime.utcnow()}}
    )
    
    # 通知其他用户该用户上线
    user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    if user and user.get("project_id"):
        project_id = str(user.get("project_id"))
        project_db = await get_project_db(project_id)
        
        # 加入项目群聊房间
        await connection_manager.join_room(user_id, f"project_{project_id}")
        
        # 广播用户上线状态
        await connection_manager.broadcast_to_room(
            f"project_{project_id}",
            {
                "type": "user_status",
                "user_id": user_id,
                "status": "online"
            },
            exclude_user=user_id
        )
    
    try:
        while True:
            # 接收客户端消息
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            message_type = message_data.get("type")
            
            if message_type == "send_message":
                # 发送聊天消息
                await handle_send_message(user_id, message_data, core_db)
            
            elif message_type == "typing":
                # 输入状态提示
                await handle_typing_indicator(user_id, message_data)
            
            elif message_type == "ping":
                # 心跳检测
                await websocket.send_text(json.dumps({"type": "pong"}))
    
    except WebSocketDisconnect:
        # 用户断开连接
        connection_manager.disconnect(user_id)
        
        # 更新数据库
        await core_db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"online_state": "offline", "last_seen": datetime.utcnow()}}
        )
        
        # 通知其他用户
        if user and user.get("project_id"):
            project_id = str(user.get("project_id"))
            await connection_manager.broadcast_to_room(
                f"project_{project_id}",
                {
                    "type": "user_status",
                    "user_id": user_id,
                    "status": "offline"
                }
            )


async def handle_send_message(user_id: str, message_data: dict, core_db):
    """处理发送消息"""
    conversation_id = message_data.get("conversation_id")
    content = message_data.get("content")
    
    if not conversation_id or not content:
        return
    
    user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        return
    
    project_id = str(user.get("project_id"))
    project_db = await get_project_db(project_id)
    
    # 验证权限
    has_permission = await check_chat_permission(user_id, conversation_id, core_db, project_db)
    if not has_permission:
        return
    
    # 保存消息到数据库
    message = {
        "conversation_id": ObjectId(conversation_id),
        "sender_id": ObjectId(user_id),
        "content": content,
        "message_type": "text",
        "read_by": [ObjectId(user_id)],
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    result = await project_db.chat_messages.insert_one(message)
    message["_id"] = result.inserted_id
    
    # 更新对话的最后消息
    await project_db.conversations.update_one(
        {"_id": ObjectId(conversation_id)},
        {
            "$set": {
                "last_message": {
                    "content": content,
                    "sender_id": user_id,
                    "created_at": datetime.utcnow().isoformat() + "Z"
                },
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    # 获取对话信息
    conversation = await project_db.conversations.find_one({"_id": ObjectId(conversation_id)})
    if not conversation:
        return
    
    # 实时推送给参与者
    sender_info = {
        "id": str(user["_id"]),
        "name": user.get("name"),
        "avatar_url": user.get("avatar_url")
    }
    
    broadcast_message = {
        "type": "new_message",
        "message": {
            "id": str(message["_id"]),
            "conversation_id": conversation_id,
            "sender": sender_info,
            "content": content,
            "created_at": message["created_at"].isoformat() + "Z"
        }
    }
    
    # 发送给所有参与者（包括发送者自己，用于确认）
    for participant_id in conversation.get("participants", []):
        await connection_manager.send_personal_message(str(participant_id), broadcast_message)


async def handle_typing_indicator(user_id: str, message_data: dict):
    """处理输入状态提示"""
    conversation_id = message_data.get("conversation_id")
    is_typing = message_data.get("is_typing", False)
    
    if not conversation_id:
        return
    
    # 广播输入状态
    broadcast_message = {
        "type": "typing",
        "conversation_id": conversation_id,
        "user_id": user_id,
        "is_typing": is_typing
    }
    
    # 这里简化处理，实际应该只发给对话参与者
    # 可以根据 conversation 查询 participants 再发送
