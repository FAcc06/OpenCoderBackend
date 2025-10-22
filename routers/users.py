from fastapi import APIRouter, HTTPException, Depends, status, Body, Query
from typing import Optional
from bson import ObjectId
from pydantic import BaseModel
import os

from database import get_core_db
from models import User, UserCreate, UserUpdate, UserRole
from utils import encrypt_data, decrypt_data
from datetime import datetime, timedelta

router = APIRouter()

# Request models
class RoleUpdateRequest(BaseModel):
    role: str

@router.get("/")
async def get_all_users(
    role: Optional[str] = None,
    project_id: Optional[str] = None,
    page: int = 1,
    limit: int = 100
):
    """获取所有用户列表 - 可按角色和项目筛选"""
    core_db = get_core_db()
    
    # 构建查询条件
    query = {}
    
    # 按角色筛选
    if role:
        if role not in ["manager", "coder"]:
            raise HTTPException(
                status_code=400,
                detail="Invalid role. Must be 'manager' or 'coder'"
            )
        query["role"] = role
    
    # 按项目筛选
    if project_id:
        try:
            query["project_id"] = ObjectId(project_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid project ID")
    
    # 获取总数
    total = await core_db.users.count_documents(query)
    
    # 分页查询
    skip = (page - 1) * limit
    users = await core_db.users.find(query).skip(skip).limit(limit).to_list(length=None)
    
    # 转换为返回格式
    users_list = []
    for user in users:
        users_list.append({
            "id": str(user["_id"]),
            "email": user.get("email"),
            "name": user.get("name"),
            "role": user.get("role"),
            "avatar_url": user.get("avatar_url"),
            "project_id": str(user["project_id"]) if user.get("project_id") else None,
            "created_at": user.get("created_at"),
            "updated_at": user.get("updated_at")
        })
    
    return {
        "users": users_list,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit
    }

@router.post("/", response_model=User)
async def create_user(user_data: UserCreate):
    """创建新用户 - 用于Google登录后的用户注册"""
    core_db = get_core_db()
    
    # 检查用户是否已存在
    existing_user = await core_db.users.find_one({"email": user_data.email})
    if existing_user:
        # 如果用户已存在，返回现有用户信息
        return User(**existing_user)
    
    # 创建新用户
    user = User(
        email=user_data.email,
        name=user_data.name,
        avatar_url=user_data.avatar_url,
        role=None,  # 新用户默认无角色，需要后续设置
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    # 插入数据库
    result = await core_db.users.insert_one(user.model_dump(by_alias=True))
    user.id = result.inserted_id
    
    return user

@router.get("/me")
async def get_current_user_info():
    """获取当前用户信息 - 无需认证"""
    return {"message": "Authentication is disabled. This endpoint would normally return user info."}

@router.put("/me/role")
async def update_user_role(token: str = Query(...), role_data: RoleUpdateRequest = Body(...)):
    """更新当前用户角色 - 需要 Token 认证"""
    from jose import jwt, JWTError
    
    core_db = get_core_db()
    if core_db is None:
        raise HTTPException(
            status_code=503,
            detail="Database connection not available"
        )
    
    # 验证 token 并获取用户信息
    try:
        SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        ALGORITHM = os.getenv("ALGORITHM", "HS256")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_email = payload.get("email")
        user_id = payload.get("sub")
        
        if not user_email or not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    
    # 获取角色值
    role = role_data.role
    if not role:
        raise HTTPException(
            status_code=422,
            detail="role field is required"
        )
    
    # 验证角色值
    if role not in ["manager", "coder"]:
        raise HTTPException(
            status_code=422,
            detail="role must be one of: manager, coder"
        )
    
    # 在 app_core 数据库中根据邮箱找到用户并更新
    try:
        result = await core_db.users.update_one(
            {"email": user_email},  # 使用邮箱作为检索条件
            {
                "$set": {
                    "role": role,
                    "updated_at": datetime.utcnow()
                }
            }
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        
        # 获取更新后的用户信息
        updated_user = await core_db.users.find_one({"email": user_email})
        
        return {
            "id": str(updated_user["_id"]),
            "email": updated_user.get("email"),
            "name": updated_user.get("name"),
            "avatar_url": updated_user.get("avatar_url"),
            "role": updated_user.get("role"),
            "project_id": str(updated_user["project_id"]) if updated_user.get("project_id") else None,
            "created_at": updated_user.get("created_at"),
            "updated_at": updated_user.get("updated_at")
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating user role: {str(e)}")

@router.put("/me/mongo-preference")
async def update_mongo_preference(
    use_external_mongo: bool,
    external_mongo_uri: Optional[str] = None
):
    """更新MongoDB偏好设置 - 无需认证"""
    return {"message": "Authentication is disabled. MongoDB preference update is not available."}

@router.get("/me/external-mongo-uri")
async def get_external_mongo_uri():
    """获取解密后的外部MongoDB URI - 无需认证"""
    return {"message": "Authentication is disabled. External MongoDB URI access is not available."}