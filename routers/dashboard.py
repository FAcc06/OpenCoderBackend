from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from bson import ObjectId

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
