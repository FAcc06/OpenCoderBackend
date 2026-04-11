"""
WebSocket Connection Manager
单实例部署 - 使用内存存储连接
"""
from typing import Dict, Set, List
from fastapi import WebSocket
import json
from datetime import datetime

class ConnectionManager:
    """管理 WebSocket 连接的类"""
    
    def __init__(self):
        # 存储活跃连接: {user_id: WebSocket}
        self.active_connections: Dict[str, WebSocket] = {}
        
        # 存储房间成员: {room_id: Set[user_id]}
        self.rooms: Dict[str, Set[str]] = {}
    
    async def connect(self, user_id: str, websocket: WebSocket):
        """用户连接"""
        await websocket.accept()
        self.active_connections[user_id] = websocket
        print(f"✅ User {user_id} connected. Total connections: {len(self.active_connections)}")
    
    def disconnect(self, user_id: str):
        """用户断开"""
        if user_id in self.active_connections:
            del self.active_connections[user_id]
            print(f"❌ User {user_id} disconnected. Total connections: {len(self.active_connections)}")
        
        # 从所有房间移除
        for room_id, members in self.rooms.items():
            if user_id in members:
                members.remove(user_id)
    
    async def join_room(self, user_id: str, room_id: str):
        """加入房间"""
        if room_id not in self.rooms:
            self.rooms[room_id] = set()
        self.rooms[room_id].add(user_id)
        print(f"🚪 User {user_id} joined room {room_id}")
    
    async def leave_room(self, user_id: str, room_id: str):
        """离开房间"""
        if room_id in self.rooms and user_id in self.rooms[room_id]:
            self.rooms[room_id].remove(user_id)
            print(f"🚪 User {user_id} left room {room_id}")
    
    async def send_personal_message(self, user_id: str, message: dict):
        """发送个人消息"""
        if user_id in self.active_connections:
            try:
                await self.active_connections[user_id].send_text(json.dumps(message))
                return True
            except Exception as e:
                print(f"❌ Failed to send to {user_id}: {e}")
                self.disconnect(user_id)
                return False
        return False
    
    async def broadcast_to_room(self, room_id: str, message: dict, exclude_user: str = None):
        """向房间内所有成员广播消息"""
        if room_id not in self.rooms:
            return
        
        disconnected_users = []
        for user_id in self.rooms[room_id]:
            if user_id == exclude_user:
                continue
            
            success = await self.send_personal_message(user_id, message)
            if not success:
                disconnected_users.append(user_id)
        
        # 清理断开的连接
        for user_id in disconnected_users:
            self.disconnect(user_id)
    
    async def broadcast_to_all(self, message: dict, exclude_user: str = None):
        """全局广播"""
        disconnected_users = []
        for user_id in list(self.active_connections.keys()):
            if user_id == exclude_user:
                continue
            
            success = await self.send_personal_message(user_id, message)
            if not success:
                disconnected_users.append(user_id)
        
        # 清理断开的连接
        for user_id in disconnected_users:
            self.disconnect(user_id)
    
    def get_online_users(self) -> List[str]:
        """获取在线用户列表"""
        return list(self.active_connections.keys())
    
    def get_room_members(self, room_id: str) -> List[str]:
        """获取房间成员列表"""
        return list(self.rooms.get(room_id, set()))
    
    def is_online(self, user_id: str) -> bool:
        """检查用户是否在线"""
        return user_id in self.active_connections


# 全局单例
connection_manager = ConnectionManager()
