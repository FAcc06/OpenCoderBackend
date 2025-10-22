from fastapi import APIRouter, HTTPException, Depends, status
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
    
    # 检查用户是否已经有项目
    user = await core_db.users.find_one({"email": user_email})
    if user and user.get("project_id"):
        raise HTTPException(
            status_code=400,
            detail="User already has a project. Each user can only own one project."
        )
    
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
    project = Project(
        name=project_data.name,
        slug=slug,
        owner_user_id=ObjectId(user_id),  # ✅ 使用真实用户ID
        db_name=db_name,
        tags=project_data.tags,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    result = await core_db.projects.insert_one(project.dict(by_alias=True))
    project.id = result.inserted_id
    
    # 更新用户的 project_id
    await core_db.users.update_one(
        {"email": user_email},
        {
            "$set": {
                "project_id": project.id,
                "updated_at": datetime.utcnow()
            }
        }
    )
    
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