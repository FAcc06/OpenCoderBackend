import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

# 全局数据库连接
client = None
core_db = None
project_dbs = {}  # 缓存项目数据库连接

async def connect_to_mongo():
    """连接到远程MongoDB Atlas"""
    global client, core_db
    
    mongodb_uri = os.getenv("MONGODB_URI")
    if not mongodb_uri:
        print("⚠️  MONGODB_URI not found, using default local MongoDB")
        mongodb_uri = "mongodb://localhost:27017"
    
    try:
        client = AsyncIOMotorClient(mongodb_uri)
        core_db = client.app_core
        
        # 测试连接
        await client.admin.command('ping')
        
        # 创建索引
        await create_indexes()
        print("[OK] Connected to MongoDB Atlas")
    except Exception as e:
        print(f"[ERROR] MongoDB connection failed: {e}")
        print("[WARNING] Application will start but database features will be limited")
        # 即使连接失败也继续启动应用
        client = None
        core_db = None

async def close_mongo_connection():
    """关闭MongoDB连接"""
    global client
    if client:
        client.close()
        print("Disconnected from MongoDB")

async def create_indexes():
    """创建必要的索引"""
    if core_db is None:
        return
    
    # Users集合索引
    await core_db.users.create_index("email", unique=True)
    await core_db.users.create_index("project_id")
    
    # Projects集合索引
    await core_db.projects.create_index("slug", unique=True)
    
    # Applications集合索引
    await core_db.applications.create_index([("project_id", 1), ("applicant_user_id", 1)], unique=True)

async def get_project_db(project_id: str):
    """获取项目数据库连接"""
    global project_dbs
    
    if project_id not in project_dbs:
        # 从core_db获取项目信息
        from bson import ObjectId
        project = await core_db.projects.find_one({"_id": ObjectId(project_id)})
        if project is None:
            raise ValueError(f"Project {project_id} not found")
        
        db_name = project.get("db_name")
        if db_name is None:
            raise ValueError(f"Project {project_id} has no database name")
        
        # 创建项目数据库连接
        project_dbs[project_id] = client[db_name]
        
        # 为项目数据库创建索引
        await create_project_indexes(project_dbs[project_id])
    
    return project_dbs[project_id]

async def create_project_indexes(db):
    """为项目数据库创建索引"""
    # Tasks集合索引
    await db.tasks.create_index("status")
    await db.tasks.create_index("tags")
    await db.tasks.create_index("task_type")
    
    # Assignments集合索引
    await db.assignments.create_index([("task_id", 1), ("coder_user_id", 1)], unique=True)
    
    # Annotations集合索引
    await db.annotations.create_index("task_id")
    await db.annotations.create_index("coder_user_id")
    await db.annotations.create_index("schema_version")
    
    # Tag groups集合索引
    await db.tag_groups.create_index("group_id", unique=True)
    await db.tag_groups.create_index("active")
    
    # Chat conversations集合索引
    await db.conversations.create_index("participants")
    await db.conversations.create_index("type")
    await db.conversations.create_index("updated_at")
    
    # Chat messages集合索引
    await db.chat_messages.create_index([("conversation_id", 1), ("created_at", -1)])
    await db.chat_messages.create_index("sender_id")

def get_core_db():
    """获取核心数据库连接"""
    return core_db
