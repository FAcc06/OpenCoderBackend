from fastapi import APIRouter, HTTPException, Depends, status, Query
from typing import List, Optional
from bson import ObjectId
from pydantic import BaseModel

from database import get_core_db, get_project_db
from models import TagGroup, TagGroupCreate, TagGroupUpdate, User
from datetime import datetime

router = APIRouter()

# Batch overwrite model
class TagGroupsBatchOverwrite(BaseModel):
    tag_groups: List[TagGroupCreate]
    # Optional note explaining this save; shown on Timeline for the focused group
    change_note: Optional[str] = None
    change_group_id: Optional[str] = None

@router.get("/{project_id}/tag-groups", response_model=List[TagGroup])
async def get_tag_groups(project_id: str):
    """获取标签组列表 - 无需认证"""
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
    
    # 获取标签组列表
    tag_groups = await project_db.tag_groups.find().sort("order", 1).to_list(length=None)
    
    return [TagGroup(**group) for group in tag_groups]

@router.post("/{project_id}/tag-groups", response_model=TagGroup)
async def create_tag_group(
    project_id: str,
    group_data: TagGroupCreate,
    token: Optional[str] = Query(None),
):
    """创建标签组"""
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
    
    # 检查group_id是否已存在
    existing_group = await project_db.tag_groups.find_one({"group_id": group_data.group_id})
    if existing_group:
        raise HTTPException(
            status_code=400,
            detail="Tag group ID already exists"
        )
    
    # 创建标签组
    tag_group = TagGroup(**group_data.dict())
    
    result = await project_db.tag_groups.insert_one(tag_group.dict(by_alias=True))
    tag_group.id = result.inserted_id

    try:
        from services.activity_log_service import log_user_activity, try_user_id_from_token
        from services.tag_activity import diff_group, snapshot_group
        actor_id = try_user_id_from_token(token) or project.get("owner_user_id")
        if actor_id:
            _etype, change = diff_group(None, snapshot_group(group_data))
            await log_user_activity(
                core_db,
                actor_id if isinstance(actor_id, ObjectId) else ObjectId(str(actor_id)),
                "tag.group_created",
                f"Created tag group {change['groupName']}",
                project_id=project_oid,
                event_type="tag.group_created",
                resource_type="tag_group",
                resource_id=group_data.group_id,
                role="project-manager",
                payload={"change": change},
            )
    except Exception:
        pass
    
    return tag_group

@router.put("/{project_id}/tag-groups-overwrite", response_model=List[TagGroup])
async def overwrite_tag_groups(
    project_id: str,
    batch_data: TagGroupsBatchOverwrite,
    token: Optional[str] = Query(None),
):
    """
    批量覆盖标签组
    删除所有现有标签组，然后创建新的标签组；并写入 tag timeline diffs。
    """
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

    # Snapshot before overwrite (for activity timeline)
    before_docs = await project_db.tag_groups.find().to_list(length=None)
    
    # Step 1: 删除所有现有标签组
    await project_db.tag_groups.delete_many({})
    
    # Step 2: 验证新标签组的 group_id 唯一性
    group_ids = [tg.group_id for tg in batch_data.tag_groups]
    if len(group_ids) != len(set(group_ids)):
        raise HTTPException(
            status_code=400,
            detail="Duplicate group_id found in the new tag groups"
        )
    
    # Step 3: 批量创建新标签组
    created_groups = []
    for group_data in batch_data.tag_groups:
        tag_group = TagGroup(**group_data.dict())
        created_groups.append(tag_group)
    
    # 批量插入
    if created_groups:
        tag_group_dicts = [tg.dict(by_alias=True) for tg in created_groups]
        result = await project_db.tag_groups.insert_many(tag_group_dicts)
        
        # 更新ID
        for i, tag_group in enumerate(created_groups):
            tag_group.id = result.inserted_ids[i]

    try:
        from services.activity_log_service import try_user_id_from_token
        from services.tag_activity import log_tag_overwrite_diff
        actor_id = try_user_id_from_token(token) or project.get("owner_user_id")
        if actor_id:
            await log_tag_overwrite_diff(
                core_db,
                actor_id=actor_id if isinstance(actor_id, ObjectId) else ObjectId(str(actor_id)),
                project_id=project_oid,
                before_docs=before_docs,
                after_groups=batch_data.tag_groups,
                change_note=(batch_data.change_note or "").strip() or None,
                change_group_id=(batch_data.change_group_id or "").strip() or None,
            )
    except Exception:
        pass
    
    return created_groups

@router.get("/{project_id}/tag-groups/{group_id}", response_model=TagGroup)
async def get_tag_group(
    project_id: str,
    group_id: str
):
    """获取单个标签组 - 无需认证"""
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
    
    # 查找标签组
    tag_group = await project_db.tag_groups.find_one({"group_id": group_id})
    if not tag_group:
        raise HTTPException(status_code=404, detail="Tag group not found")
    
    return TagGroup(**tag_group)

@router.put("/{project_id}/tag-groups/{group_id}", response_model=TagGroup)
async def update_tag_group(
    project_id: str,
    group_id: str,
    group_update: TagGroupUpdate,
    token: Optional[str] = Query(None),
):
    """更新标签组"""
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
    
    # 检查标签组是否存在
    existing_group = await project_db.tag_groups.find_one({"group_id": group_id})
    if not existing_group:
        raise HTTPException(status_code=404, detail="Tag group not found")
    
    # 构建更新数据
    update_data = {"updated_at": datetime.utcnow()}
    if group_update.name is not None:
        update_data["name"] = group_update.name
    if group_update.description is not None:
        update_data["description"] = group_update.description
    if group_update.type is not None:
        update_data["type"] = group_update.type
    if group_update.required is not None:
        update_data["required"] = group_update.required
    if group_update.order is not None:
        update_data["order"] = group_update.order
    if group_update.active is not None:
        update_data["active"] = group_update.active
    if group_update.options is not None:
        update_data["options"] = [option.dict() for option in group_update.options]
    if group_update.constraints is not None:
        update_data["constraints"] = group_update.constraints.dict()
    
    # 更新标签组
    await project_db.tag_groups.update_one(
        {"group_id": group_id},
        {"$set": update_data}
    )
    
    # 返回更新后的标签组
    updated_group = await project_db.tag_groups.find_one({"group_id": group_id})

    try:
        from services.activity_log_service import log_user_activity, try_user_id_from_token
        from services.tag_activity import diff_group, snapshot_group, group_changed
        actor_id = try_user_id_from_token(token) or project.get("owner_user_id")
        before = snapshot_group(existing_group)
        after = snapshot_group(updated_group)
        if actor_id and group_changed(before, after):
            _etype, change = diff_group(before, after)
            await log_user_activity(
                core_db,
                actor_id if isinstance(actor_id, ObjectId) else ObjectId(str(actor_id)),
                "tag.group_updated",
                f"Updated tag group {change['groupName']}",
                project_id=project_oid,
                event_type="tag.group_updated",
                resource_type="tag_group",
                resource_id=group_id,
                role="project-manager",
                payload={"change": change},
            )
    except Exception:
        pass

    return TagGroup(**updated_group)

@router.delete("/{project_id}/tag-groups/{group_id}")
async def delete_tag_group(
    project_id: str,
    group_id: str,
    token: Optional[str] = Query(None),
):
    """删除标签组"""
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
    
    # 检查标签组是否存在
    existing_group = await project_db.tag_groups.find_one({"group_id": group_id})
    if not existing_group:
        raise HTTPException(status_code=404, detail="Tag group not found")
    
    # 删除标签组
    await project_db.tag_groups.delete_one({"group_id": group_id})

    try:
        from services.activity_log_service import log_user_activity, try_user_id_from_token
        from services.tag_activity import diff_group, snapshot_group
        actor_id = try_user_id_from_token(token) or project.get("owner_user_id")
        if actor_id:
            _etype, change = diff_group(snapshot_group(existing_group), None)
            await log_user_activity(
                core_db,
                actor_id if isinstance(actor_id, ObjectId) else ObjectId(str(actor_id)),
                "tag.group_deleted",
                f"Deleted tag group {change['groupName']}",
                project_id=project_oid,
                event_type="tag.group_deleted",
                resource_type="tag_group",
                resource_id=group_id,
                role="project-manager",
                payload={"change": change},
            )
    except Exception:
        pass
    
    return {"message": "Tag group deleted successfully"}