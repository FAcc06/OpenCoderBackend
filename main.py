from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from contextlib import asynccontextmanager
import os
from datetime import datetime
from dotenv import load_dotenv

from database import connect_to_mongo, close_mongo_connection
from routers import users, projects, applications, tasks, assignments, annotations, tag_groups, board, public, dashboard, auth

# 加载环境变量
load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时连接数据库
    await connect_to_mongo()
    yield
    # 关闭时断开数据库连接
    await close_mongo_connection()

app = FastAPI(
    title="MongoDB Annotation Platform API",
    description="A comprehensive annotation platform backend built with FastAPI and MongoDB",
    version="1.0.0",
    lifespan=lifespan
)

# Session中间件（OAuth需要）
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "your-secret-key-change-in-production"),
    max_age=604800  # 7天
)

# CORS中间件
# 获取前端 URL（支持多个，用逗号分隔）
frontend_url = os.getenv("FRONTEND_URL")
allowed_origins = [
    # "http://localhost:5173",  # 前端开发服务器
    # "http://127.0.0.1:5173",  # 前端开发服务器（备用）
    # "http://localhost:3000",  # React默认端口（备用）
    # "http://localhost:8000",  # 本地后端
    "https://opencoderfrontend.onrender.com",  # ⭐ 生产环境前端
]

# 添加生产环境前端 URL（如果通过环境变量提供了额外的URL）
if frontend_url not in allowed_origins:
    allowed_origins.append(frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有HTTP方法
    allow_headers=["*"],
)

# 静态文件服务
app.mount("/static", StaticFiles(directory="static"), name="static")

# 注册路由
# 认证路由
app.include_router(auth.router, prefix="/auth", tags=["authentication"])
# 所有API都无需认证
app.include_router(public.router, prefix="/api/public", tags=["public"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(applications.router, prefix="/api/projects", tags=["applications"])
app.include_router(tasks.router, prefix="/api/projects", tags=["tasks"])
app.include_router(assignments.router, prefix="/api/projects", tags=["assignments"])
app.include_router(annotations.router, prefix="/api/projects", tags=["annotations"])
app.include_router(tag_groups.router, prefix="/api/projects", tags=["tag-groups"])
app.include_router(board.router, prefix="/api/projects", tags=["board"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])

@app.get("/")
async def root():
    return {"message": "MongoDB Annotation Platform API", "version": "1.0.0"}

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

@app.get("/test")
async def test_endpoint():
    """测试端点，不需要认证"""
    from database import get_core_db
    core_db = get_core_db()
    return {
        "message": "测试端点正常工作",
        "timestamp": datetime.utcnow().isoformat(),
        "database_connected": core_db is not None
    }

@app.get("/api/info")
async def api_info():
    """API信息端点，不需要认证"""
    return {
        "name": "MongoDB Annotation Platform API",
        "version": "1.0.0",
        "description": "一个基于FastAPI和MongoDB的标注平台",
        "endpoints": {
            "total": 30,
            "categories": [
                "用户管理 (3个)",
                "项目管理 (4个)", 
                "申请管理 (4个)",
                "任务管理 (5个)",
                "分配管理 (3个)",
                "标注管理 (3个)",
                "标签组管理 (5个)",
                "看板视图 (2个)",
                "系统API (1个)"
            ]
        },
        "features": [
            "异步MongoDB连接",
            "无需认证访问",
            "项目独立数据库",
            "标签组约束验证",
            "看板视图",
            "批量操作支持"
        ]
    }

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.getenv("PORT", 8000))  # Render 会提供 PORT 环境变量
    uvicorn.run(app, host="0.0.0.0", port=port)
