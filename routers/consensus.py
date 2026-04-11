"""
Consensus Router
处理标注一致性检查和共识协商 - Coder 功能
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Dict, Any, List, Optional
from bson import ObjectId
from datetime import datetime

from database import get_core_db, get_project_db
from routers.auth import verify_token

router = APIRouter()


def compare_labels(labels1, labels2) -> Dict[str, Any]:
    """比较两个 labels，支持字符串和列表格式"""
    def parse_labels(labels) -> Dict[str, list]:
        """解析 labels 为字典，支持多种格式"""
        if not labels:
            return {}
        
        result = {}
        
        # 如果是列表格式 (新格式)
        if isinstance(labels, list):
            for item in labels:
                if isinstance(item, dict):
                    group_id = item.get('group_id', '')
                    option_ids = item.get('option_ids', [])
                    if group_id:
                        # 移除时间戳后缀，统一 group_id
                        base_group_id = '_'.join(group_id.split('_')[:-1]) if '_' in group_id and group_id.split('_')[-1].isdigit() else group_id
                        result[base_group_id] = sorted(option_ids)  # 排序以便比较
            return result
        
        # 如果是字符串格式 (旧格式)
        if isinstance(labels, str):
            parts = labels.split(';')
            for part in parts:
                part = part.strip()
                if ':' in part:
                    key, value = part.split(':', 1)
                    # 提取 group_id（去掉时间戳）
                    group_id = '_'.join(key.split('_')[:-1]) if '_' in key else key
                    result[group_id] = [value.strip()]
            return result
        
        return {}
    
    labels1_dict = parse_labels(labels1)
    labels2_dict = parse_labels(labels2)
    
    # 找出所有涉及的 group_ids
    all_groups = set(labels1_dict.keys()) | set(labels2_dict.keys())
    
    differences = []
    agreements = []
    
    for group_id in all_groups:
        val1 = labels1_dict.get(group_id, [])
        val2 = labels2_dict.get(group_id, [])
        
        # 比较选项列表（已排序）
        if val1 != val2:
            differences.append({
                "group_id": group_id,
                "value1": ', '.join(val1) if val1 else "",
                "value2": ', '.join(val2) if val2 else "",
                "conflict": True
            })
        else:
            agreements.append({
                "group_id": group_id,
                "value": ', '.join(val1) if val1 else "",
                "conflict": False
            })
    
    return {
        "has_conflict": len(differences) > 0,
        "differences": differences,
        "agreements": agreements,
        "total_groups": len(all_groups),
        "conflict_count": len(differences)
    }


@router.get("/{project_id}/my-conflicts")
async def get_my_conflicts(
    project_id: str,
    token: str = Query(..., description="JWT token")
):
    """
    获取当前 coder 参与的所有有冲突的 tasks
    只返回与当前用户相关的冲突
    """
    # 验证 token
    user = verify_token(token)
    user_id = user.get("sub") or user.get("id")
    
    if not ObjectId.is_valid(project_id):
        raise HTTPException(status_code=400, detail="Invalid project ID")
    
    core_db = get_core_db()
    project_db = await get_project_db(project_id)
    
    # 聚合查询：找出当前用户参与标注的、有多个 annotations 的 tasks
    pipeline = [
        {
            "$match": {
                # 移除 status 过滤，因为数据库中的 annotations 没有这个字段
                "coder_user_id": ObjectId(user_id)
            }
        },
        {
            "$group": {
                "_id": "$task_id",
                "my_annotation": {"$first": "$$ROOT"}
            }
        }
    ]
    
    my_annotated_tasks = await project_db.annotations.aggregate(pipeline).to_list(length=None)
    task_ids = [item["_id"] for item in my_annotated_tasks]
    
    if not task_ids:
        return {
            "success": True,
            "project_id": project_id,
            "conflict_count": 0,
            "conflicts": []
        }
    
    # 查找这些 tasks 是否有其他人的标注
    conflicts = []
    
    for task_id in task_ids:
        # 获取该 task 的所有 annotations（不包括 consensus）
        annotations = await project_db.annotations.find({
            "task_id": task_id,
            # 移除 status 过滤
            "is_consensus": {"$ne": True}
        }).to_list(length=None)
        
        # 至少需要 2 个标注
        if len(annotations) < 2:
            continue
        
        # 获取 task 信息
        task = await project_db.tasks.find_one({"_id": ObjectId(task_id)})
        if not task:
            continue
        
        # 比较标注，找出冲突
        my_annotation = None
        other_annotation = None
        
        for ann in annotations:
            ann_coder_id_str = str(ann.get("coder_user_id", ""))
            
            if ann_coder_id_str == user_id:
                my_annotation = ann
            else:
                if not other_annotation:
                    other_annotation = ann
        
        if not my_annotation or not other_annotation:
            continue
        
        # 比较 labels
        my_labels = my_annotation.get("labels", "")
        other_labels = other_annotation.get("labels", "")
        
        comparison = compare_labels(my_labels, other_labels)
        
        if comparison["has_conflict"]:
            # 检查是否已有 consensus
            consensus = await project_db.annotations.find_one({
                "task_id": task_id,
                "is_consensus": True
            })
            
            # 如果已经有 consensus，跳过这个 task，不显示在列表中
            if consensus:
                continue
            
            # 获取 coder 信息
            my_coder_oid = my_annotation.get("coder_user_id")
            other_coder_oid = other_annotation.get("coder_user_id")
            
            my_coder = await core_db.users.find_one({"_id": my_coder_oid}) if my_coder_oid else None
            other_coder = await core_db.users.find_one({"_id": other_coder_oid}) if other_coder_oid else None
            
            conflicts.append({
                "task_id": str(task_id),  # 转换 ObjectId 为字符串
                "task_title": task.get("title", "Untitled"),
                "task_type": task.get("task_type", "text"),
                "payload": task.get("payload", {}),
                "my_annotation": {
                    "id": str(my_annotation["_id"]),
                    "labels": my_annotation.get("labels", ""),
                    "note": my_annotation.get("note", ""),
                    "completed_at": my_annotation.get("completed_at")
                },
                "other_annotation": {
                    "id": str(other_annotation["_id"]),
                    "coder_id": str(other_annotation.get("coder_user_id", "")),
                    "coder_name": other_coder.get("name", "Unknown") if other_coder else "Unknown",
                    "coder_email": other_coder.get("email", "") if other_coder else "",
                    "labels": other_annotation.get("labels", ""),
                    "note": other_annotation.get("note", ""),
                    "completed_at": other_annotation.get("completed_at")
                },
                "comparison": comparison,
                "has_consensus": False,  # 因为已经过滤掉有 consensus 的了
                "consensus_id": None
            })
    
    # 按 task_id 排序，确保顺序一致
    conflicts.sort(key=lambda x: x["task_id"])
    
    return {
        "success": True,
        "project_id": project_id,
        "conflict_count": len(conflicts),
        "conflicts": conflicts
    }


@router.get("/{project_id}/task/{task_id}/detail")
async def get_task_conflict_detail(
    project_id: str,
    task_id: str,
    token: str = Query(..., description="JWT token")
):
    """
    获取某个 task 的详细冲突信息
    只允许参与该标注的 coder 查看
    """
    # 验证 token
    user = verify_token(token)
    user_id = user.get("sub") or user.get("id")
    
    if not ObjectId.is_valid(task_id):
        raise HTTPException(status_code=400, detail="Invalid task ID")
    
    core_db = get_core_db()
    project_db = await get_project_db(project_id)
    
    # 获取 task
    task = await project_db.tasks.find_one({"_id": ObjectId(task_id)})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 获取所有 annotations（不包括 consensus）
    # task_id 在 annotations 集合中是 ObjectId 类型
    annotations = await project_db.annotations.find({
        "task_id": ObjectId(task_id),
        # 移除 status 过滤
        "is_consensus": {"$ne": True}
    }).to_list(length=None)
    
    if len(annotations) < 2:
        raise HTTPException(status_code=400, detail="Task needs at least 2 annotations for consensus")
    
    # 检查当前用户是否参与了这个 task 的标注
    user_participated = any(str(ann.get("coder_user_id", "")) == user_id for ann in annotations)
    if not user_participated:
        raise HTTPException(status_code=403, detail="You did not participate in this task annotation")
    
    # 获取 tag groups
    tag_groups = await project_db.tag_groups.find({}).to_list(length=None)
    
    # 创建两个映射：
    # 1. ObjectId -> tag group 信息 (用于前端显示)
    # 2. normalized_name -> ObjectId (用于转换 labels 的 key)
    tag_group_map = {}
    name_to_id_map = {}
    
    def normalize_name(name: str) -> str:
        """标准化名称：去空格、转小写、用下划线连接"""
        return name.lower().replace(' ', '_').replace('-', '_')
    
    for tg in tag_groups:
        tg_id = str(tg["_id"])
        tg_name = tg.get("name", "Unknown")
        
        tag_group_map[tg_id] = {
            "name": tg_name,
            "options": tg.get("options", [])
        }
        
        # 使用标准化的名称作为 key
        normalized = normalize_name(tg_name)
        name_to_id_map[normalized] = tg_id
    
    # 准备 annotations 详情
    annotations_detail = []
    for ann in annotations:
        coder_oid = ann.get("coder_user_id")
        coder = await core_db.users.find_one({"_id": coder_oid}) if coder_oid else None
        
        # 解析 labels（支持列表和字符串格式）
        labels_dict = {}
        labels_raw = ann.get("labels", "")
        
        if labels_raw:
            if isinstance(labels_raw, list):
                # 新格式：列表
                for item in labels_raw:
                    if isinstance(item, dict):
                        group_id = item.get('group_id', '')
                        option_ids = item.get('option_ids', [])
                        if group_id:
                            # 移除时间戳后缀
                            base_group_id = '_'.join(group_id.split('_')[:-1]) if '_' in group_id and group_id.split('_')[-1].isdigit() else group_id
                            # 转换为 ObjectId key（base_group_id 已经是标准化格式）
                            tag_id = name_to_id_map.get(base_group_id)
                            if tag_id:
                                # 如果 option_ids 只有一个值，直接使用；否则用逗号连接
                                labels_dict[tag_id] = option_ids[0] if len(option_ids) == 1 else ', '.join(option_ids)
            elif isinstance(labels_raw, str):
                # 旧格式：字符串
                parts = labels_raw.split(';')
                for part in parts:
                    part = part.strip()
                    if ':' in part:
                        key, value = part.split(':', 1)
                        group_id = '_'.join(key.split('_')[:-1]) if '_' in key else key
                        # 转换为 ObjectId key（group_id 已经是标准化格式）
                        tag_id = name_to_id_map.get(group_id)
                        if tag_id:
                            labels_dict[tag_id] = value.strip()
        
        annotations_detail.append({
            "annotation_id": str(ann["_id"]),
            "coder_id": str(coder_oid) if coder_oid else "",
            "coder_name": coder.get("name", "Unknown") if coder else "Unknown",
            "coder_email": coder.get("email", "") if coder else "",
            "is_me": str(coder_oid) == user_id if coder_oid else False,
            "labels": labels_dict,
            "labels_raw": labels_raw,
            "note": ann.get("note", ""),
            "completed_at": ann.get("completed_at")
        })
    
    # 比较所有标注，找出差异
    comparison_result = compare_labels(
        annotations_detail[0]["labels_raw"],
        annotations_detail[1]["labels_raw"]
    )
    
    # 转换 comparison 结果中的 group_id 为 ObjectId
    for diff in comparison_result["differences"]:
        group_name = diff["group_id"]  # 已经是标准化格式（例如 "sentiment"）
        tag_id = name_to_id_map.get(group_name)
        if tag_id:
            diff["group_id"] = tag_id
    
    for agr in comparison_result["agreements"]:
        group_name = agr["group_id"]  # 已经是标准化格式
        tag_id = name_to_id_map.get(group_name)
        if tag_id:
            agr["group_id"] = tag_id
    
    return {
        "success": True,
        "task": {
            "id": str(task["_id"]),
            "title": task.get("title", ""),
            "type": task.get("task_type", "text"),
            "payload": task.get("payload", {})
        },
        "tag_groups": tag_group_map,
        "annotations": annotations_detail,
        "comparison": comparison_result
    }


@router.post("/{project_id}/task/{task_id}/resolve")
async def resolve_consensus(
    project_id: str,
    task_id: str,
    consensus_data: Dict[str, Any],
    token: str = Query(..., description="JWT token")
):
    """
    提交 consensus 结果
    只允许参与该标注的 coder 提交
    
    Request body:
    {
        "labels": "group1_timestamp:value1;group2_timestamp:value2",
        "note": "Consensus reached after discussion"
    }
    """
    # 验证 token
    user = verify_token(token)
    user_id = user.get("sub") or user.get("id")
    
    if not ObjectId.is_valid(task_id):
        raise HTTPException(status_code=400, detail="Invalid task ID")
    
    core_db = get_core_db()
    project_db = await get_project_db(project_id)
    
    task = await project_db.tasks.find_one({"_id": ObjectId(task_id)})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 获取所有参与该 task 的 coder
    annotations = await project_db.annotations.find({
        "task_id": ObjectId(task_id),
        # 移除 status 过滤
        "is_consensus": {"$ne": True}
    }).to_list(length=None)
    
    coder_ids = [str(ann.get("coder_user_id", "")) for ann in annotations if ann.get("coder_user_id")]
    
    # 检查当前用户是否参与了这个 task
    if user_id not in coder_ids:
        raise HTTPException(status_code=403, detail="You did not participate in this task annotation")
    
    # 创建 consensus annotation
    consensus_annotation = {
        "task_id": ObjectId(task_id),
        "coder_user_id": ObjectId(user_id),  # 提交者
        "labels": consensus_data.get("labels", ""),
        "note": consensus_data.get("note", ""),
        "status": "completed",
        "is_consensus": True,  # 标记为 consensus
        "resolved_by": [ObjectId(cid) for cid in coder_ids if ObjectId.is_valid(cid)],  # 所有参与的 coder IDs
        "created_at": datetime.utcnow(),
        "completed_at": datetime.utcnow()
    }
    
    result = await project_db.annotations.insert_one(consensus_annotation)
    
    return {
        "success": True,
        "message": "Consensus saved successfully",
        "consensus_id": str(result.inserted_id)
    }
