from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from bson import ObjectId
from collections import defaultdict

from database import get_core_db, get_project_db
from models import PaginatedResponse

router = APIRouter()

@router.get("/{project_id}/coders")
async def get_project_coders(project_id: str):
    """获取项目中所有标注员列表及其统计信息 - 无需认证"""
    core_db = get_core_db()
    
    # 支持直接使用数据库名称 (如 test_dashboard) 或项目ID
    if project_id == "test_dashboard":
        from motor.motor_asyncio import AsyncIOMotorClient
        import os
        mongodb_uri = os.getenv("MONGODB_URI")
        client = AsyncIOMotorClient(mongodb_uri)
        project_db = client.test_dashboard
    else:
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
    
    # 从 annotations 集合获取所有标注过的 coder
    pipeline = [
        {
            "$group": {
                "_id": "$coder_user_id",
                "total_annotations": {"$sum": 1},
                "unique_tasks": {"$addToSet": "$task_id"},
                "last_annotation": {"$max": "$completed_at"},
                "first_annotation": {"$min": "$completed_at"}
            }
        },
        {
            "$project": {
                "coder_id": {"$toString": "$_id"},
                "total_annotations": 1,
                "unique_tasks_count": {"$size": "$unique_tasks"},
                "last_annotation": 1,
                "first_annotation": 1,
                "_id": 0
            }
        },
        {
            "$sort": {"total_annotations": -1}
        }
    ]
    
    coders_stats = await project_db.annotations.aggregate(pipeline).to_list(None)
    
    # 从 assignments 获取分配信息
    for coder in coders_stats:
        coder_oid = ObjectId(coder["coder_id"])
        
        # 获取分配的任务数
        assignments_count = await project_db.assignments.count_documents({
            "coder_user_id": coder_oid
        })
        
        # 获取进行中的任务数
        in_progress_count = await project_db.assignments.count_documents({
            "coder_user_id": coder_oid,
            "state": "in_progress"
        })
        
        coder["assigned_tasks"] = assignments_count
        coder["in_progress_tasks"] = in_progress_count
        
        # 尝试从 core_db 获取用户信息
        user = await core_db.users.find_one({"_id": coder_oid})
        if user:
            coder["name"] = user.get("name", "Unknown")
            coder["email"] = user.get("email", "")
            coder["avatar_url"] = user.get("avatar_url")
        else:
            coder["name"] = f"Coder {coder['coder_id'][:8]}"
            coder["email"] = ""
            coder["avatar_url"] = None
    
    return {
        "project_id": project_id,
        "total_coders": len(coders_stats),
        "coders": coders_stats
    }

@router.get("/{project_id}/team")
async def get_team_daily_stats(
    project_id: str,
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    days: Optional[int] = Query(7, description="Number of days to look back (default 7)")
):
    """获取团队每日标注统计 - 无需认证"""
    core_db = get_core_db()
    
    # 支持直接使用数据库名称 (如 test_dashboard) 或项目ID
    if project_id == "test_dashboard":
        # 直接使用测试数据库
        from motor.motor_asyncio import AsyncIOMotorClient
        import os
        mongodb_uri = os.getenv("MONGODB_URI")
        client = AsyncIOMotorClient(mongodb_uri)
        project_db = client.test_dashboard
        project = {"name": "Test Dashboard", "slug": "test_dashboard"}
    else:
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
    
    # 构建日期范围
    date_filter = build_date_filter(start_date, end_date, days)
    
    # MongoDB 聚合管道 - 按日期统计
    pipeline = [
        {
            "$match": {
                "completed_at": date_filter,
                "completed_at": {"$exists": True, "$ne": None}
            }
        },
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$completed_at"
                    }
                },
                "annotations_count": {"$sum": 1},
                "unique_coders": {"$addToSet": "$coder_user_id"},
                "tasks_completed": {"$addToSet": "$task_id"}
            }
        },
        {
            "$project": {
                "date": "$_id",
                "annotations_count": 1,
                "active_coders_count": {"$size": "$unique_coders"},
                "tasks_completed_count": {"$size": "$tasks_completed"},
                "_id": 0
            }
        },
        {
            "$sort": {"date": 1}
        }
    ]
    
    daily_stats = await project_db.annotations.aggregate(pipeline).to_list(None)
    
    # 计算汇总统计
    summary = calculate_team_summary(daily_stats)
    
    return {
        "project_id": project_id,
        "date_range": {
            "start": daily_stats[0]["date"] if daily_stats else None,
            "end": daily_stats[-1]["date"] if daily_stats else None
        },
        "daily_stats": daily_stats,
        "summary": summary
    }

@router.get("/{project_id}/individual/{coder_id}")
async def get_individual_daily_stats(
    project_id: str,
    coder_id: str,
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    days: Optional[int] = Query(7, description="Number of days to look back (default 7)")
):
    """获取个人每日标注统计 - 无需认证"""
    core_db = get_core_db()
    
    # 支持直接使用数据库名称 (如 test_dashboard) 或项目ID
    if project_id == "test_dashboard":
        # 直接使用测试数据库
        from motor.motor_asyncio import AsyncIOMotorClient
        import os
        mongodb_uri = os.getenv("MONGODB_URI")
        client = AsyncIOMotorClient(mongodb_uri)
        project_db = client.test_dashboard
        project = {"name": "Test Dashboard", "slug": "test_dashboard"}
        try:
            coder_oid = ObjectId(coder_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid coder ID format")
    else:
        try:
            project_oid = ObjectId(project_id)
            coder_oid = ObjectId(coder_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid ID format")
        
        # 检查项目是否存在
        project = await core_db.projects.find_one({"_id": project_oid})
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        # 获取项目数据库
        project_db = await get_project_db(project_id)
    
    # 构建日期范围
    date_filter = build_date_filter(start_date, end_date, days)
    
    # MongoDB 聚合管道 - 个人按日期统计
    pipeline = [
        {
            "$match": {
                "coder_user_id": coder_oid,
                "completed_at": date_filter,
                "completed_at": {"$exists": True, "$ne": None}
            }
        },
        {
            "$group": {
                "_id": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$completed_at"
                    }
                },
                "annotations_count": {"$sum": 1},
                "tasks_completed": {"$addToSet": "$task_id"},
                "avg_completion_time": {
                    "$avg": {
                        "$subtract": ["$completed_at", "$created_at"]
                    }
                }
            }
        },
        {
            "$project": {
                "date": "$_id",
                "annotations_count": 1,
                "tasks_completed_count": {"$size": "$tasks_completed"},
                "avg_completion_minutes": {
                    "$divide": ["$avg_completion_time", 60000]  # 转换为分钟
                },
                "_id": 0
            }
        },
        {
            "$sort": {"date": 1}
        }
    ]
    
    daily_stats = await project_db.annotations.aggregate(pipeline).to_list(None)
    
    # 获取个人总体统计
    personal_summary = await calculate_personal_summary(project_db, coder_oid, date_filter)
    
    return {
        "project_id": project_id,
        "coder_id": coder_id,
        "date_range": {
            "start": daily_stats[0]["date"] if daily_stats else None,
            "end": daily_stats[-1]["date"] if daily_stats else None
        },
        "daily_stats": daily_stats,
        "summary": personal_summary
    }

@router.get("/{project_id}/overview")
async def get_project_overview(
    project_id: str,
    days: Optional[int] = Query(30, description="Number of days to look back (default 30)")
):
    """获取项目总览统计 - 无需认证"""
    core_db = get_core_db()
    
    # 支持直接使用数据库名称 (如 test_dashboard) 或项目ID
    if project_id == "test_dashboard":
        # 直接使用测试数据库
        from motor.motor_asyncio import AsyncIOMotorClient
        import os
        mongodb_uri = os.getenv("MONGODB_URI")
        client = AsyncIOMotorClient(mongodb_uri)
        project_db = client.test_dashboard
        project = {"name": "Test Dashboard", "slug": "test_dashboard"}
    else:
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
    
    # 计算日期范围
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    
    # 获取各种统计数据
    stats = await get_comprehensive_stats(project_db, start_date, end_date)
    
    return {
        "project_id": project_id,
        "project_name": project.get("name", "Unknown Project"),
        "date_range": {
            "start": start_date.strftime("%Y-%m-%d"),
            "end": end_date.strftime("%Y-%m-%d"),
            "days": days
        },
        "statistics": stats
    }

def build_date_filter(start_date: str, end_date: str, days: int) -> dict:
    """构建日期过滤条件"""
    if start_date and end_date:
        # 使用用户指定的日期范围
        start = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date) + timedelta(days=1)  # 包含结束日期
    else:
        # 使用默认的天数回溯
        end = datetime.utcnow()
        start = end - timedelta(days=days)
    
    return {
        "$gte": start,
        "$lt": end
    }

def calculate_team_summary(daily_stats: List[Dict]) -> Dict[str, Any]:
    """计算团队汇总统计"""
    if not daily_stats:
        return {
            "total_annotations": 0,
            "total_tasks_completed": 0,
            "avg_daily_annotations": 0,
            "most_productive_day": None,
            "peak_annotations": 0
        }
    
    total_annotations = sum(day["annotations_count"] for day in daily_stats)
    total_tasks = sum(day["tasks_completed_count"] for day in daily_stats)
    avg_daily = total_annotations / len(daily_stats)
    
    most_productive = max(daily_stats, key=lambda x: x["annotations_count"])
    
    return {
        "total_annotations": total_annotations,
        "total_tasks_completed": total_tasks,
        "avg_daily_annotations": round(avg_daily, 1),
        "most_productive_day": most_productive["date"],
        "peak_annotations": most_productive["annotations_count"],
        "active_days": len(daily_stats)
    }

async def calculate_personal_summary(db, coder_id: ObjectId, date_filter: dict) -> Dict[str, Any]:
    """计算个人汇总统计"""
    # 总标注数
    total_annotations = await db.annotations.count_documents({
        "coder_user_id": coder_id,
        "completed_at": date_filter
    })
    
    # 总任务数
    unique_tasks = await db.annotations.distinct("task_id", {
        "coder_user_id": coder_id,
        "completed_at": date_filter
    })
    
    # 平均每日产出
    days_active = len(await db.annotations.distinct("completed_at", {
        "coder_user_id": coder_id,
        "completed_at": date_filter
    }))
    
    avg_daily = total_annotations / max(days_active, 1)
    
    return {
        "total_annotations": total_annotations,
        "unique_tasks_completed": len(unique_tasks),
        "avg_daily_annotations": round(avg_daily, 1),
        "active_days": days_active
    }

async def get_comprehensive_stats(db, start_date: datetime, end_date: datetime) -> Dict[str, Any]:
    """获取综合统计信息"""
    date_filter = {"$gte": start_date, "$lt": end_date}
    
    # 基础计数
    total_tasks = await db.tasks.count_documents({})
    total_annotations = await db.annotations.count_documents({"completed_at": date_filter})
    total_assignments = await db.assignments.count_documents({})
    
    # 任务状态分布
    task_status_pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}}
    ]
    task_status_dist = await db.tasks.aggregate(task_status_pipeline).to_list(None)
    
    # 标签使用统计
    tag_usage_pipeline = [
        {"$unwind": "$tags"},
        {"$group": {"_id": "$tags", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ]
    popular_tags = await db.tasks.aggregate(tag_usage_pipeline).to_list(None)
    
    return {
        "totals": {
            "tasks": total_tasks,
            "annotations": total_annotations,
            "assignments": total_assignments
        },
        "task_status_distribution": {
            item["_id"]: item["count"] for item in task_status_dist
        },
        "popular_tags": [
            {"tag": item["_id"], "count": item["count"]} for item in popular_tags
        ],
        "completion_rate": calculate_completion_rate(task_status_dist)
    }

def calculate_completion_rate(status_dist: List[Dict]) -> float:
    """计算完成率"""
    status_counts = {item["_id"]: item["count"] for item in status_dist}
    total = sum(status_counts.values())
    completed = status_counts.get("done", 0)
    
    return round(completed / max(total, 1) * 100, 1)


@router.get("/{project_id}/analytics/tag-statistics")
async def get_tag_statistics(project_id: str, token: str = Query(...)):
    """
    获取项目中所有标签的统计数据，按tag group分组
    
    返回格式:
    {
        "success": true,
        "statistics": {
            "by_group": [
                {
                    "group_id": "sentiment",
                    "group_name": "Sentiment",
                    "total_annotations": 150,
                    "tags": [
                        {"tag_value": "positive", "tag_label": "Positive", "count": 80},
                        {"tag_value": "negative", "tag_label": "Negative", "count": 50},
                        {"tag_value": "neutral", "tag_label": "Neutral", "count": 20}
                    ]
                },
                ...
            ],
            "total_annotations": 300,
            "unique_tags": 15,
            "tag_groups_count": 5
        }
    }
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
    if project_db is None:
        raise HTTPException(status_code=404, detail="Project database not found")
    
    # 获取项目的tag groups定义
    tag_groups_cursor = project_db.tag_groups.find({})
    tag_groups_list = await tag_groups_cursor.to_list(None)
    
    # 构建tag group映射 {group_id: {group_name, options: {option_id: label}, label_to_id: {label: option_id}}}
    tag_groups_map = {}
    for group in tag_groups_list:
        options_map = {}
        label_to_id_map = {}  # 反向映射：从label找option_id
        
        for option in group.get("options", []):
            # 兼容不同的数据结构
            option_id = option.get("option_id") or option.get("value") or option.get("label")
            option_label = option.get("label", option_id)
            
            # option_id -> label
            options_map[option_id] = option_label
            # label -> option_id (反向映射)
            label_to_id_map[option_label.lower()] = option_id
        
        tag_groups_map[group.get("group_id")] = {
            "group_name": group.get("name", group.get("group_id")),
            "options": options_map,
            "label_to_id": label_to_id_map,
            "type": group.get("type", "single")
        }
    
    # 获取所有标注数据
    annotations_cursor = project_db.annotations.find({})
    annotations_list = await annotations_cursor.to_list(None)
    
    # 统计标签数据
    # 结构: {group_id: {option_id: count}}
    # 注意：统一使用option_id作为key，避免重复
    tag_counts = defaultdict(lambda: defaultdict(int))
    
    for annotation in annotations_list:
        labels = annotation.get("labels", {})
        
        # labels可能是不同的格式，需要兼容处理
        if isinstance(labels, list):
            # 数组格式 (实际系统使用的格式)
            # [{"group_id": "...", "option_ids": ["..."]}]
            for label_item in labels:
                if isinstance(label_item, dict):
                    group_id = label_item.get("group_id")
                    option_ids = label_item.get("option_ids", [])
                    
                    if group_id and option_ids:
                        label_map = tag_groups_map.get(group_id, {}).get("label_to_id", {})
                        for option_id in option_ids:
                            # 规范化为option_id（如果是label名，转换为option_id）
                            normalized_id = label_map.get(str(option_id).lower(), str(option_id))
                            tag_counts[group_id][normalized_id] += 1
        
        elif isinstance(labels, dict):
            # 字典格式 (旧格式或其他格式)
            for group_id, value in labels.items():
                label_map = tag_groups_map.get(group_id, {}).get("label_to_id", {})
                
                # value可能是单个值、列表或对象
                if isinstance(value, list):
                    # 列表形式
                    for tag_value in value:
                        normalized_id = label_map.get(str(tag_value).lower(), str(tag_value))
                        tag_counts[group_id][normalized_id] += 1
                elif isinstance(value, dict):
                    # 对象形式 {selected: [...]}
                    selected = value.get("selected", [])
                    if isinstance(selected, list):
                        for tag_value in selected:
                            normalized_id = label_map.get(str(tag_value).lower(), str(tag_value))
                            tag_counts[group_id][normalized_id] += 1
                    else:
                        normalized_id = label_map.get(str(selected).lower(), str(selected))
                        tag_counts[group_id][normalized_id] += 1
                else:
                    # 单个值
                    normalized_id = label_map.get(str(value).lower(), str(value))
                    tag_counts[group_id][normalized_id] += 1
    
    # 构建返回数据
    by_group = []
    total_annotations_count = len(annotations_list)
    unique_tags_count = 0
    
    for group_id, group_info in tag_groups_map.items():
        group_data = {
            "group_id": group_id,
            "group_name": group_info["group_name"],
            "type": group_info["type"],
            "total_annotations": 0,
            "tags": []
        }
        
        # 获取该组的标签统计
        group_tag_counts = tag_counts.get(group_id, {})
        
        # 统计时已经规范化，直接使用
        for option_id, count in group_tag_counts.items():
            # 获取标签显示名称
            tag_label = group_info["options"].get(option_id, option_id)
            
            group_data["tags"].append({
                "tag_value": option_id,
                "tag_label": tag_label,
                "count": count
            })
            group_data["total_annotations"] += count
            unique_tags_count += 1
        
        # 按count排序
        group_data["tags"].sort(key=lambda x: x["count"], reverse=True)
        
        if group_data["tags"]:  # 只包含有数据的组
            by_group.append(group_data)
    
    # 按total_annotations排序
    by_group.sort(key=lambda x: x["total_annotations"], reverse=True)
    
    return {
        "success": True,
        "statistics": {
            "by_group": by_group,
            "total_annotations": total_annotations_count,
            "unique_tags": unique_tags_count,
            "tag_groups_count": len(by_group)
        },
        "metadata": {
            "project_id": project_id,
            "generated_at": datetime.utcnow().isoformat()
        }
    }
