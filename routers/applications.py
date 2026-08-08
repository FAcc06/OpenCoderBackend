from fastapi import APIRouter, HTTPException, Depends, status
from typing import List, Optional
from bson import ObjectId

from database import get_core_db
from models import Application, ApplicationCreate, ApplicationUpdate, ApplicationStatus, User
from models import PaginatedResponse
from datetime import datetime

router = APIRouter()

@router.post("/{project_id}/apply", response_model=Application)
async def apply_to_project(
    project_id: str,
    token: str,
    application_data: ApplicationCreate
):
    """申请加入项目 - 需要 Token 认证"""
    from jose import jwt, JWTError
    import os
    
    core_db = get_core_db()
    
    # 1. 验证并解析 Token，获取用户ID
    try:
        SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        ALGORITHM = os.getenv("ALGORITHM", "HS256")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        # ⭐ 关键修复：使用 token 的 sub 作为用户ID
        applicant_user_id_str = payload.get("sub")
        user_email = payload.get("email")
        
        if not applicant_user_id_str:
            raise HTTPException(status_code=401, detail="Invalid token: missing user ID")
        
        # 转换为 ObjectId
        try:
            applicant_user_id = ObjectId(applicant_user_id_str)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid user ID format")
            
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    
    # 2. 验证用户存在并获取用户信息
    user = await core_db.users.find_one({"_id": applicant_user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # 获取申请人姓名和邮箱
    applicant_name = user.get("name")
    applicant_email = user.get("email")
    
    # 3. 验证项目ID格式
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    # 4. 检查项目是否存在
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 5. 查询项目 Manager 信息（用于联系）
    manager_email = None
    manager_name = None
    manager_user_id = project.get("owner_user_id")
    
    if manager_user_id:
        try:
            manager = await core_db.users.find_one({"_id": ObjectId(manager_user_id)})
            if manager:
                manager_email = manager.get("email")
                manager_name = manager.get("name")
        except Exception as e:
            # Manager 查询失败不影响申请创建
            print(f"Warning: Failed to fetch manager info: {e}")
    
    # 6. 检查是否已经申请过（防止重复申请）
    existing_app = await core_db.applications.find_one({
        "project_id": project_oid,
        "applicant_user_id": applicant_user_id,
        "status": {"$in": [ApplicationStatus.PENDING, ApplicationStatus.APPROVED]}
    })
    if existing_app:
        raise HTTPException(
            status_code=409,
            detail="You have already applied to this project"
        )

    # 6b. Membership checks
    from services.membership_service import (
        ensure_under_limit,
        get_membership,
        bootstrap_legacy_memberships,
        normalize_roles,
    )
    await bootstrap_legacy_memberships(core_db, applicant_user_id)

    existing_membership = await get_membership(core_db, applicant_user_id, project_oid)
    existing_roles = normalize_roles(existing_membership)

    # Already a coder (possibly also manager) — nothing to apply for
    if "coder" in existing_roles:
        raise HTTPException(
            status_code=409,
            detail="You already have coder access on this project. Open it from Hub.",
        )

    # Owner already gets manager+coder on create; tell them to use Hub
    if str(project.get("owner_user_id")) == str(applicant_user_id):
        raise HTTPException(
            status_code=400,
            detail="You own this project. Use Hub → Enter as Coder (owners have both roles).",
        )

    if not existing_membership:
        await ensure_under_limit(core_db, applicant_user_id)
    
    # 7. 创建申请（使用 token 中的真实用户ID，存储项目、Manager 和申请人信息）
    application = Application(
        project_id=project_oid,
        applicant_user_id=applicant_user_id,  # ⭐ 使用 token 的用户ID
        applicant_name=applicant_name,  # ⭐ 冗余存储申请人姓名
        applicant_email=applicant_email,  # ⭐ 冗余存储申请人邮箱
        message=application_data.message,
        project_name=project.get("name"),  # ⭐ 冗余存储项目名称
        project_slug=project.get("slug"),  # ⭐ 冗余存储项目slug
        manager_email=manager_email,  # ⭐ 冗余存储 Manager 邮箱
        manager_name=manager_name,    # ⭐ 冗余存储 Manager 名字
        manager_user_id=manager_user_id if manager_user_id else None  # ⭐ Manager ID
    )
    
    result = await core_db.applications.insert_one(application.dict(by_alias=True))
    application.id = result.inserted_id
    
    return application

@router.get("/user/me/applications", response_model=List[Application])
async def get_my_applications(
    token: str,
    status: Optional[ApplicationStatus] = None
):
    """获取当前用户的所有申请 - 需要 Token 认证"""
    from jose import jwt, JWTError
    import os
    
    core_db = get_core_db()
    
    # 1. 验证并解析 Token
    try:
        SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        ALGORITHM = os.getenv("ALGORITHM", "HS256")
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        
        applicant_user_id_str = payload.get("sub")
        
        if not applicant_user_id_str:
            raise HTTPException(status_code=401, detail="Invalid token: missing user ID")
        
        try:
            applicant_user_id = ObjectId(applicant_user_id_str)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid user ID format")
            
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    
    # 2. 构建查询条件
    query = {"applicant_user_id": applicant_user_id}
    if status:
        query["status"] = status
    
    # 3. 获取用户的所有申请
    applications = await core_db.applications.find(query).sort("created_at", -1).to_list(length=None)
    
    return [Application(**app) for app in applications]

@router.get("/{project_id}/applications", response_model=PaginatedResponse)
async def get_project_applications(
    project_id: str,
    page: int = 1,
    limit: int = 50,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
):
    """获取项目申请列表 - 无需认证"""
    from datetime import datetime as dt
    core_db = get_core_db()

    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")

    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    query: dict = {"project_id": project_oid}

    if status and status != "all":
        query["status"] = status
    if date_from or date_to:
        date_filter: dict = {}
        if date_from:
            try:
                date_filter["$gte"] = dt.fromisoformat(date_from)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date_from format (use YYYY-MM-DD)")
        if date_to:
            try:
                date_filter["$lte"] = dt.fromisoformat(date_to).replace(hour=23, minute=59, second=59)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date_to format (use YYYY-MM-DD)")
        query["created_at"] = date_filter
    if search:
        # applicant_name and applicant_email are stored directly on the document
        query["$or"] = [
            {"applicant_name":  {"$regex": search, "$options": "i"}},
            {"applicant_email": {"$regex": search, "$options": "i"}},
        ]

    total = await core_db.applications.count_documents(query)
    skip = (page - 1) * limit
    applications = await (
        core_db.applications.find(query)
        .sort("created_at", -1)
        .skip(skip)
        .limit(limit)
        .to_list(length=None)
    )

    return PaginatedResponse(
        items=[Application(**app) for app in applications],
        total=total,
        page=page,
        limit=limit,
        pages=(total + limit - 1) // limit if limit > 0 else 1,
    )

@router.post("/{project_id}/applications/{app_id}/approve")
async def approve_application(
    project_id: str,
    app_id: str
):
    """批准申请 - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
        app_oid = ObjectId(app_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    # 检查项目是否存在
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 检查申请是否存在
    application = await core_db.applications.find_one({
        "_id": app_oid,
        "project_id": project_oid
    })
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    
    if application["status"] != ApplicationStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail="Application is not pending"
        )
    
    # ⭐ 获取申请人的 user_id
    applicant_user_id = application.get("applicant_user_id")
    if not applicant_user_id:
        raise HTTPException(
            status_code=400,
            detail="Application missing applicant_user_id"
        )
    
    from services.membership_service import (
        ensure_under_limit,
        upsert_membership,
        get_membership,
        bootstrap_legacy_memberships,
        normalize_roles,
    )

    applicant_oid = ObjectId(applicant_user_id)
    await bootstrap_legacy_memberships(core_db, applicant_oid)

    existing = await get_membership(core_db, applicant_oid, project_oid)
    if not existing:
        await ensure_under_limit(core_db, applicant_oid)

    # ⭐ 1. 更新申请状态
    await core_db.applications.update_one(
        {"_id": app_oid},
        {
            "$set": {
                "status": ApplicationStatus.APPROVED,
                "updated_at": datetime.utcnow()
            }
        }
    )

    # ⭐ 2. ADD coder into roles[] (keeps manager if already present)
    membership = await upsert_membership(
        core_db,
        user_id=applicant_oid,
        project_id=project_oid,
        role="coder",
    )
    roles = normalize_roles(membership)

    # ⭐ 3. Only set active project if user has none yet
    user_doc = await core_db.users.find_one({"_id": applicant_oid})
    user_updated = False
    if user_doc:
        if not user_doc.get("project_id"):
            await core_db.users.update_one(
                {"_id": applicant_oid},
                {"$set": {
                    "project_id": project_oid,
                    "role": "coder",
                    "updated_at": datetime.utcnow(),
                }},
            )
        else:
            await core_db.users.update_one(
                {"_id": applicant_oid},
                {"$set": {"updated_at": datetime.utcnow()}},
            )
        user_updated = True
    else:
        print(f"Warning: User {applicant_user_id} not found when approving application")

    return {
        "message": "Application approved successfully",
        "user_updated": user_updated,
        "membership_roles": roles,
        "applicant_user_id": str(applicant_user_id),
        "project_id": str(project_oid),
    }

@router.post("/{project_id}/applications/{app_id}/reject")
async def reject_application(
    project_id: str,
    app_id: str
):
    """拒绝申请 - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
        app_oid = ObjectId(app_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    # 检查项目是否存在
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 检查申请是否存在
    application = await core_db.applications.find_one({
        "_id": app_oid,
        "project_id": project_oid
    })
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    
    if application["status"] != ApplicationStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail="Application is not pending"
        )
    
    # 更新申请状态
    await core_db.applications.update_one(
        {"_id": app_oid},
        {
            "$set": {
                "status": ApplicationStatus.REJECTED,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    return {"message": "Application rejected successfully"}