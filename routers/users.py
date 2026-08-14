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

class ActiveProjectRequest(BaseModel):
    project_id: Optional[str] = None  # null clears active project
    as_role: Optional[str] = None     # "manager" | "coder" — which shell to enter

@router.get("/")
async def get_all_users(
    role: Optional[str] = None,
    project_id: Optional[str] = None,
    page: int = 1,
    limit: int = 100
):
    """
    List users. When project_id is set, filter via project_memberships.roles
    (not users.role / active shell) so dual-role managers appear as coders.
    """
    core_db = get_core_db()

    if role and role not in ["manager", "coder"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid role. Must be 'manager' or 'coder'"
        )

    # Membership-aware path (Assign Task, team lists)
    if project_id:
        from services.membership_service import list_project_member_users
        try:
            project_oid = ObjectId(project_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid project ID")

        users_list = await list_project_member_users(
            core_db,
            project_oid,
            require_role=role,
        )
        total = len(users_list)
        skip = (page - 1) * limit
        page_items = users_list[skip: skip + limit]
        return {
            "users": page_items,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": (total + limit - 1) // limit if limit else 0,
        }

    # Legacy: no project_id — filter active shell on users collection
    query = {}
    if role:
        query["role"] = role

    total = await core_db.users.count_documents(query)
    skip = (page - 1) * limit
    users = await core_db.users.find(query).skip(skip).limit(limit).to_list(length=None)

    users_list = []
    for user in users:
        users_list.append({
            "id": str(user["_id"]),
            "email": user.get("email"),
            "name": user.get("name"),
            "role": user.get("role"),
            "roles": [user["role"]] if user.get("role") in ("manager", "coder") else [],
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
        "pages": (total + limit - 1) // limit if limit else 0
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
    """Deprecated: use Hub + POST /api/users/me/active-project with as_role."""
    raise HTTPException(
        status_code=410,
        detail=(
            "Direct role updates are disabled. "
            "Open /hub and use Enter as Manager or Enter as Coder "
            "(POST /api/users/me/active-project)."
        ),
    )

@router.get("/me/projects")
async def list_my_projects(token: str = Query(...)):
    """List all projects the current user owns or has joined (max 10 active)."""
    from jose import jwt, JWTError
    from services.membership_service import list_user_projects, count_active_memberships, MAX_ACTIVE_MEMBERSHIPS

    core_db = get_core_db()
    if core_db is None:
        raise HTTPException(status_code=503, detail="Database connection not available")

    try:
        SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        ALGORITHM = os.getenv("ALGORITHM", "HS256")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        user_oid = ObjectId(user_id)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    projects = await list_user_projects(core_db, user_oid)
    total = await count_active_memberships(core_db, user_oid)

    user = await core_db.users.find_one({"_id": user_oid})
    active_project_id = str(user["project_id"]) if user and user.get("project_id") else None
    active_role = user.get("role") if user else None

    return {
        "projects": projects,
        "total": total,
        "limit": MAX_ACTIVE_MEMBERSHIPS,
        "active_project_id": active_project_id,
        "active_role": active_role,
    }


@router.post("/me/active-project")
async def set_active_project(
    body: ActiveProjectRequest,
    token: str = Query(...),
):
    """
    Switch the user's active project (sets user.project_id + user.role
    so existing manager/coder pages keep working).
    Pass project_id=null to clear and return to Hub.
    """
    from jose import jwt, JWTError
    from services.membership_service import (
        get_membership,
        bootstrap_legacy_memberships,
        normalize_roles,
        primary_role,
    )

    core_db = get_core_db()
    if core_db is None:
        raise HTTPException(status_code=503, detail="Database connection not available")

    try:
        SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        ALGORITHM = os.getenv("ALGORITHM", "HS256")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        user_email = payload.get("email")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        user_oid = ObjectId(user_id)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Clear active project → back to Hub
    if not body.project_id:
        await core_db.users.update_one(
            {"_id": user_oid},
            {"$set": {"project_id": None, "role": None, "updated_at": datetime.utcnow()}},
        )
        return {
            "success": True,
            "project_id": None,
            "role": None,
        }

    try:
        project_oid = ObjectId(body.project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project_id")

    await bootstrap_legacy_memberships(core_db, user_oid)
    membership = await get_membership(core_db, user_oid, project_oid)
    if not membership:
        raise HTTPException(status_code=403, detail="You are not a member of this project")

    roles = normalize_roles(membership)

    # Manager on a project can always enter as coder (grant if missing)
    if (
        body.as_role == "coder"
        and "coder" not in roles
        and "manager" in roles
    ):
        from services.membership_service import upsert_membership
        membership = await upsert_membership(
            core_db,
            user_id=user_oid,
            project_id=project_oid,
            role="coder",
            roles=["manager", "coder"],
        )
        roles = normalize_roles(membership)

    if body.as_role:
        if body.as_role not in ("manager", "coder"):
            raise HTTPException(status_code=400, detail="as_role must be manager or coder")
        if body.as_role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"You do not have the '{body.as_role}' role on this project",
            )
        role = body.as_role
    else:
        role = primary_role(roles)

    await core_db.users.update_one(
        {"_id": user_oid},
        {
            "$set": {
                "project_id": project_oid,
                "role": role,
                "updated_at": datetime.utcnow(),
            }
        },
    )

    try:
        from services.activity_log_service import log_user_activity
        await log_user_activity(
            core_db,
            user_oid,
            "project.enter",
            f"Entered project as {role}",
            project_id=project_oid,
            resource_type="project",
            resource_id=str(project_oid),
            meta={"as_role": role, "roles": roles},
        )
    except Exception:
        pass

    return {
        "success": True,
        "project_id": str(project_oid),
        "role": role,
        "roles": roles,
        "email": user_email,
    }


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