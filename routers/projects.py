from fastapi import APIRouter, HTTPException, Depends, status, Query
from typing import List, Optional
from bson import ObjectId
from pydantic import BaseModel
import re

from database import get_core_db, get_project_db
from models import Project, ProjectCreate, ProjectStatus, User
from utils import generate_db_name, sanitize_slug
from datetime import datetime

router = APIRouter()

# 请求模型
class ClusterUriUpdate(BaseModel):
    cluster_uri: Optional[str] = None


class ProjectSettingsUpdate(BaseModel):
    """Manager Settings — database + LLM overrides for this project."""
    cluster_uri: Optional[str] = None
    llm_enabled: Optional[bool] = None
    openrouter_api_key: Optional[str] = None  # empty string clears override
    llm_model: Optional[str] = None
    annotation_model: Optional[str] = None


class OpenRouterKeyCheck(BaseModel):
    """Optional key to test; if omitted/blank, uses the project's saved key."""
    openrouter_api_key: Optional[str] = None


def _mask_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    if len(value) <= 12:
        return "***"
    return f"{value[:8]}…{value[-4:]}"

def validate_mongodb_uri(uri: str) -> bool:
    """验证MongoDB URI格式"""
    if not uri:
        return True  # 允许空值
    
    # MongoDB URI 格式验证
    # 支持 mongodb:// 和 mongodb+srv:// 格式
    mongodb_pattern = r'^mongodb(\+srv)?:\/\/.+'
    
    if not re.match(mongodb_pattern, uri, re.IGNORECASE):
        return False
    
    return True

@router.post("/", response_model=Project)
async def create_project(token: str, project_data: ProjectCreate):
    """创建新项目 - 需要 Token 认证"""
    from jose import jwt, JWTError
    import os
    
    core_db = get_core_db()
    if core_db is None:
        raise HTTPException(status_code=503, detail="Database connection not available")
    
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
    
    from services.membership_service import (
        ensure_under_limit,
        upsert_membership,
        bootstrap_legacy_memberships,
    )

    user_oid = ObjectId(user_id)
    # Backfill legacy memberships so the 10-limit count is accurate
    await bootstrap_legacy_memberships(core_db, user_oid)
    await ensure_under_limit(core_db, user_oid)

    # 清理slug
    slug = sanitize_slug(project_data.slug)
    
    # 检查slug是否已存在
    existing_project = await core_db.projects.find_one({"slug": slug})
    if existing_project:
        raise HTTPException(
            status_code=400,
            detail="Project slug already exists"
        )
    
    # 生成数据库名称
    db_name = generate_db_name(slug)
    
    # 创建项目（使用真实的用户ID作为所有者）
    memo = (project_data.memo or "").strip() or None
    project = Project(
        name=project_data.name,
        slug=slug,
        owner_user_id=user_oid,
        db_name=db_name,
        tags=project_data.tags,
        memo=memo,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    result = await core_db.projects.insert_one(project.dict(by_alias=True))
    project.id = result.inserted_id

    # Owner gets both roles so Hub shows Manager + Coder enter buttons
    await upsert_membership(
        core_db,
        user_id=user_oid,
        project_id=project.id,
        role="manager",
        roles=["manager", "coder"],
    )

    # Set as active project so existing UI keeps working
    await core_db.users.update_one(
        {"email": user_email},
        {
            "$set": {
                "project_id": project.id,
                "role": "manager",
                "updated_at": datetime.utcnow()
            }
        }
    )

    try:
        from services.activity_log_service import log_user_activity
        payload = {
            "projectName": project_data.name,
            "project_name": project_data.name,
            "slug": slug,
        }
        if memo:
            payload["memo"] = memo
        await log_user_activity(
            core_db,
            user_oid,
            "project.created",
            f"Created project {project_data.name}",
            project_id=project.id,
            event_type="project.created",
            resource_type="project",
            resource_id=str(project.id),
            role="project-manager",
            payload=payload,
        )
    except Exception:
        pass
    
    return project

@router.get("/{project_id}", response_model=Project)
async def get_project(project_id: str):
    """获取项目信息 - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    return Project(**project)

@router.post("/batch", response_model=List[Project])
async def get_projects_batch(project_ids: List[str]):
    """批量获取项目信息 - 无需认证（建议B）
    
    用于前端一次性获取多个项目的详细信息
    
    示例请求体:
    {
        "project_ids": ["id1", "id2", "id3"]
    }
    
    或直接传递数组:
    ["id1", "id2", "id3"]
    """
    core_db = get_core_db()
    
    if not project_ids:
        return []
    
    # 转换为 ObjectId
    valid_oids = []
    for pid in project_ids:
        try:
            valid_oids.append(ObjectId(pid))
        except Exception:
            # 忽略无效的ID，继续处理其他ID
            pass
    
    if not valid_oids:
        return []
    
    # 批量查询
    projects = await core_db.projects.find({"_id": {"$in": valid_oids}}).to_list(length=None)
    
    return [Project(**project) for project in projects]

@router.get("/", response_model=List[Project])
async def get_user_projects():
    """获取项目列表 - 无需认证"""
    core_db = get_core_db()
    
    # 获取所有项目（因为认证已禁用）
    projects = await core_db.projects.find().to_list(length=None)
    
    return [Project(**project) for project in projects]

@router.post("/{project_id}/archive")
async def archive_project(project_id: str):
    """归档项目 - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    # 检查项目是否存在
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 更新项目状态
    await core_db.projects.update_one(
        {"_id": project_oid},
        {
            "$set": {
                "status": ProjectStatus.ARCHIVED,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    return {"message": "Project archived successfully"}

@router.patch("/{project_id}/cluster-uri")
async def update_cluster_uri(project_id: str, data: ClusterUriUpdate):
    """更新项目的 cluster_uri - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    # 检查项目是否存在
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # 验证 MongoDB URI 格式
    if data.cluster_uri and not validate_mongodb_uri(data.cluster_uri):
        raise HTTPException(
            status_code=400, 
            detail="Invalid MongoDB URI format. URI must start with 'mongodb://' or 'mongodb+srv://'"
        )
    
    # 更新 cluster_uri
    await core_db.projects.update_one(
        {"_id": project_oid},
        {
            "$set": {
                "cluster_uri": data.cluster_uri,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
    return {
        "message": "Cluster URI updated successfully",
        "cluster_uri": data.cluster_uri
    }

@router.get("/{project_id}/cluster-uri-status")
async def get_cluster_uri_status(project_id: str):
    """检查项目的 cluster_uri 状态 - 无需认证"""
    core_db = get_core_db()
    
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    # 查找项目
    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    cluster_uri = project.get("cluster_uri")
    
    return {
        "project_id": project_id,
        "has_cluster_uri": cluster_uri is not None and cluster_uri != "",
        "cluster_uri": cluster_uri,
        "cluster_uri_masked": f"{cluster_uri[:20]}...{cluster_uri[-10:]}" if cluster_uri and len(cluster_uri) > 30 else cluster_uri
    }


@router.get("/{project_id}/settings")
async def get_project_settings(project_id: str, token: str = Query(...)):
    """
    Manager Settings (masked secrets).
    Includes MongoDB Atlas URI status + LLM overrides for this project.
    """
    from services.membership_service import is_project_manager
    from jose import jwt, JWTError
    import os

    core_db = get_core_db()
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")

    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    ALGORITHM = os.getenv("ALGORITHM", "HS256")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_oid = ObjectId(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not await is_project_manager(core_db, user_oid, project_oid):
        raise HTTPException(status_code=403, detail="Only managers can view project settings")

    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    cluster_uri = project.get("cluster_uri") or ""
    llm = project.get("llm_settings") or {}
    key = llm.get("openrouter_api_key") or ""
    # Default on so existing projects keep basic AI unless managers turn it off
    llm_enabled = llm.get("llm_enabled", True)
    if not isinstance(llm_enabled, bool):
        llm_enabled = True

    return {
        "project_id": project_id,
        "cluster_uri": cluster_uri,
        "cluster_uri_masked": _mask_secret(cluster_uri) if cluster_uri else None,
        "has_cluster_uri": bool(cluster_uri),
        "llm_enabled": llm_enabled,
        "openrouter_api_key_masked": _mask_secret(key) if key else None,
        "has_openrouter_api_key": bool(key),
        "llm_model": llm.get("llm_model") or "",
        "annotation_model": llm.get("annotation_model") or "",
        "defaults": {
            "llm_model": os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
            "annotation_model": os.getenv("ANNOTATION_MODEL", "openai/gpt-4o-mini"),
            "server_has_openrouter_key": bool(os.getenv("OPENROUTER_API_KEY")),
            "basic_model": os.getenv("ANNOTATION_MODEL", "openai/gpt-4o-mini"),
        },
    }


@router.patch("/{project_id}/settings")
async def update_project_settings(
    project_id: str,
    data: ProjectSettingsUpdate,
    token: str = Query(...),
):
    """Update Manager Settings: Atlas URI and/or LLM overrides."""
    from services.membership_service import is_project_manager
    from jose import jwt
    import os

    core_db = get_core_db()
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")

    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    ALGORITHM = os.getenv("ALGORITHM", "HS256")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_oid = ObjectId(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not await is_project_manager(core_db, user_oid, project_oid):
        raise HTTPException(status_code=403, detail="Only managers can update project settings")

    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    updates: dict = {"updated_at": datetime.utcnow()}
    llm = dict(project.get("llm_settings") or {})

    if data.cluster_uri is not None:
        uri = data.cluster_uri.strip()
        if uri and not validate_mongodb_uri(uri):
            raise HTTPException(
                status_code=400,
                detail="Invalid MongoDB URI. Must start with mongodb:// or mongodb+srv://",
            )
        # Prefer Atlas for this Settings UI
        if uri and not uri.startswith("mongodb+srv://"):
            raise HTTPException(
                status_code=400,
                detail="Only MongoDB Atlas URIs are accepted (must start with mongodb+srv://)",
            )
        updates["cluster_uri"] = uri or None

    if data.llm_enabled is not None:
        llm["llm_enabled"] = bool(data.llm_enabled)

    if data.openrouter_api_key is not None:
        key = data.openrouter_api_key.strip()
        if key:
            llm["openrouter_api_key"] = key
        else:
            llm.pop("openrouter_api_key", None)

    if data.llm_model is not None:
        model = data.llm_model.strip()
        if model:
            llm["llm_model"] = model
        else:
            llm.pop("llm_model", None)

    if data.annotation_model is not None:
        model = data.annotation_model.strip()
        if model:
            llm["annotation_model"] = model
        else:
            llm.pop("annotation_model", None)

    updates["llm_settings"] = llm

    await core_db.projects.update_one({"_id": project_oid}, {"$set": updates})

    return {"success": True, "message": "Settings saved"}


@router.post("/{project_id}/settings/verify-openrouter-key")
async def verify_openrouter_key(
    project_id: str,
    data: OpenRouterKeyCheck,
    token: str = Query(...),
):
    """
    Check OpenRouter key eligibility via GET https://openrouter.ai/api/v1/key
    Uses the pasted key, or the project's saved key if the field is blank.
    """
    from services.membership_service import is_project_manager
    from jose import jwt
    import os
    import httpx

    core_db = get_core_db()
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")

    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    ALGORITHM = os.getenv("ALGORITHM", "HS256")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_oid = ObjectId(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not await is_project_manager(core_db, user_oid, project_oid):
        raise HTTPException(status_code=403, detail="Only managers can verify API keys")

    project = await core_db.projects.find_one({"_id": project_oid})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    key = (data.openrouter_api_key or "").strip()
    used_saved = False
    if not key:
        llm = project.get("llm_settings") or {}
        key = (llm.get("openrouter_api_key") or "").strip()
        used_saved = bool(key)
    if not key:
        raise HTTPException(
            status_code=400,
            detail="Paste an OpenRouter API key to check, or save one first.",
        )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.get(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": f"Bearer {key}"},
            )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach OpenRouter: {e}")

    if res.status_code == 401:
        return {
            "valid": False,
            "eligible": False,
            "message": "Invalid API key (OpenRouter returned 401).",
            "checked_saved_key": used_saved,
        }
    if res.status_code != 200:
        detail = (res.text or "")[:300]
        return {
            "valid": False,
            "eligible": False,
            "message": f"OpenRouter error HTTP {res.status_code}: {detail}",
            "checked_saved_key": used_saved,
        }

    body = res.json() if res.content else {}
    info = body.get("data") if isinstance(body, dict) else None
    if not isinstance(info, dict):
        info = body if isinstance(body, dict) else {}

    limit_remaining = info.get("limit_remaining")
    usage = info.get("usage")
    limit = info.get("limit")
    is_free_tier = info.get("is_free_tier")
    label = info.get("label")

    # Eligible if authenticated; warn when per-key remaining credits are exhausted
    eligible = True
    notes = []
    if limit_remaining is not None:
        try:
            if float(limit_remaining) <= 0:
                eligible = False
                notes.append("Key credit limit remaining is 0.")
        except (TypeError, ValueError):
            pass

    msg_parts = ["API key is valid."]
    if label:
        msg_parts.append(f"Label: {label}.")
    if limit is not None:
        msg_parts.append(f"Limit: {limit}.")
    if limit_remaining is not None:
        msg_parts.append(f"Remaining: {limit_remaining}.")
    if usage is not None:
        msg_parts.append(f"Usage: {usage}.")
    if is_free_tier is not None:
        msg_parts.append(f"Free tier: {is_free_tier}.")
    if notes:
        msg_parts.extend(notes)

    return {
        "valid": True,
        "eligible": eligible,
        "message": " ".join(msg_parts),
        "checked_saved_key": used_saved,
        "data": {
            "label": label,
            "limit": limit,
            "limit_remaining": limit_remaining,
            "limit_reset": info.get("limit_reset"),
            "usage": usage,
            "is_free_tier": is_free_tier,
        },
    }


@router.get("/{project_id}/ai-status")
async def get_project_ai_status(project_id: str, token: str = Query(...)):
    """
    Lightweight flag for Coder UI: whether Get AI Suggestion is enabled.
    Any project member (manager or coder) may read this — no secrets returned.
    """
    from services.membership_service import get_membership, is_project_manager
    from jose import jwt
    import os

    core_db = get_core_db()
    try:
        project_oid = ObjectId(project_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid project ID")

    SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
    ALGORITHM = os.getenv("ALGORITHM", "HS256")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_oid = ObjectId(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = await core_db.users.find_one({"_id": user_oid})
    membership = await get_membership(core_db, user_oid, project_oid)
    is_mgr = await is_project_manager(core_db, user_oid, project_oid)
    legacy_ok = user and str(user.get("project_id") or "") == str(project_oid)
    if not membership and not is_mgr and not legacy_ok:
        raise HTTPException(status_code=403, detail="Not a member of this project")

    project = await core_db.projects.find_one({"_id": project_oid}, {"llm_settings": 1})
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    llm = project.get("llm_settings") or {}
    enabled = llm.get("llm_enabled", True)
    if not isinstance(enabled, bool):
        enabled = True

    return {"llm_enabled": enabled}


@router.get("/{project_id}/intercoder-reliability")
async def get_intercoder_reliability_via_projects(project_id: str, token: str = Query(...)):
    """
    Same JSON as GET /api/dashboard/{project_id}/intercoder-reliability.
    Mounted under /api/projects for environments where dashboard sub-routes return 404.
    """
    del token
    from routers.dashboard import compute_intercoder_reliability_response

    return await compute_intercoder_reliability_response(project_id)