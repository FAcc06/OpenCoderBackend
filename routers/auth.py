"""
Google OAuth 2.0 认证路由
提供登录、回调、用户信息获取和登出功能
"""

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse, JSONResponse
from authlib.integrations.starlette_client import OAuth
from starlette.config import Config
from pydantic import BaseModel
from bson import ObjectId
import os
from datetime import datetime, timedelta
from jose import JWTError, jwt
from typing import Optional
import logging

from database import get_core_db
from models import User

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter()

# ==================== 配置 ====================

# OAuth 配置
config = Config(environ=os.environ)
oauth = OAuth(config)

oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile https://www.googleapis.com/auth/drive.file',
        'access_type': 'offline',
        'prompt': 'consent',
        'include_granted_scopes': 'true'  # 包含之前授予的 scopes
    },
    authorize_params={
        'access_type': 'offline',
        'prompt': 'consent'
    }
)

# JWT 配置
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))
# 前端URL - 如果环境变量未设置，则必须由前端传入redirect_uri
FRONTEND_URL = os.getenv('FRONTEND_URL')
REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI', 'http://localhost:8000/auth/google/callback')

# ==================== 响应模型 ====================

class UserResponse(BaseModel):
    """用户信息响应模型"""
    id: str
    email: str
    name: str
    avatar_url: Optional[str] = None
    role: Optional[str] = None
    project_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class LogoutResponse(BaseModel):
    """登出响应模型"""
    message: str
    success: bool = True

class ErrorResponse(BaseModel):
    """错误响应模型"""
    detail: str
    error_code: Optional[str] = None

# ==================== 辅助函数 ====================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    创建 JWT 访问令牌
    
    Args:
        data: 要编码的数据（通常包含用户ID和邮箱）
        expires_delta: 自定义过期时间
        
    Returns:
        编码后的 JWT 字符串
    """
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "iat": datetime.utcnow()})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    
    return encoded_jwt


async def get_or_create_user(email: str, name: str, avatar_url: Optional[str]) -> str:
    """
    获取或创建用户
    
    Args:
        email: 用户邮箱
        name: 用户姓名
        avatar_url: 用户头像 URL
        
    Returns:
        用户 ID
    """
    core_db = get_core_db()
    
    if core_db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection not available"
        )
    
    # 查找现有用户
    existing_user = await core_db.users.find_one({"email": email})
    
    if existing_user:
        # 更新现有用户信息
        user_id = str(existing_user["_id"])
        await core_db.users.update_one(
            {"_id": existing_user["_id"]},
            {
                "$set": {
                    "name": name,
                    "avatar_url": avatar_url,
                    "updated_at": datetime.utcnow()
                }
            }
        )
        logger.info(f"Updated user: {email}")
    else:
        # 创建新用户
        user = User(
            email=email,
            name=name,
            avatar_url=avatar_url,
            role=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        result = await core_db.users.insert_one(user.model_dump(by_alias=True))
        user_id = str(result.inserted_id)
        logger.info(f"Created new user: {email}")
    
    return user_id


def verify_token(token: str) -> dict:
    """
    验证并解码 JWT 令牌
    
    Args:
        token: JWT 令牌字符串
        
    Returns:
        解码后的 payload
        
    Raises:
        HTTPException: 令牌无效或过期
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        logger.error(f"JWT verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token"
        )

# ==================== API 端点 ====================

@router.get('/login', tags=["authentication"])
async def google_login(request: Request, redirect_uri: Optional[str] = None):
    """
    发起 Google OAuth 登录
    
    重定向用户到 Google 登录页面进行授权
    
    **参数：**
    - redirect_uri: 前端回调地址（可选），例如 https://opencoderfrontend.onrender.com/auth/callback
    
    **流程：**
    1. 用户访问此端点
    2. 重定向到 Google OAuth 页面
    3. 用户授权后返回到 /auth/google/callback
    """
    logger.info("Initiating Google OAuth login")
    
    # 将前端传入的 redirect_uri 存储到 session 中
    if redirect_uri:
        request.session['frontend_redirect_uri'] = redirect_uri
        logger.info(f"Stored frontend redirect URI in session: {redirect_uri}")
    
    # 显式指定授权参数，确保获取 Drive 权限和 refresh_token
    return await oauth.google.authorize_redirect(
        request, 
        REDIRECT_URI,
        access_type='offline',
        prompt='consent',
        include_granted_scopes='true'
    )


@router.get('/google/callback', tags=["authentication"])
async def google_callback(request: Request):
    """
    Google OAuth 回调端点
    
    Google 认证成功后会重定向到此端点，处理授权并创建会话
    
    **流程：**
    1. 接收 Google 的授权码
    2. 获取用户信息
    3. 创建或更新数据库中的用户
    4. 生成 JWT Token
    5. 重定向回前端（带 token）
    
    **返回：**
    - 成功：重定向到前端页面，URL 参数带 token
    - 失败：重定向到前端页面，URL 参数带 error
    """
    try:
        # 获取访问令牌
        token = await oauth.google.authorize_access_token(request)
        logger.info("Successfully obtained access token from Google")
        
        # 🔍 调试：打印 Google 返回的完整 token 信息
        logger.info(f"📊 Google token keys: {token.keys()}")
        logger.info(f"🔑 Has refresh_token: {bool(token.get('refresh_token'))}")
        logger.info(f"📋 Scope returned: {token.get('scope')}")
        logger.info(f"⏰ Expires in: {token.get('expires_in')}")
        
        # 获取用户信息
        user_info = token.get('userinfo')
        if not user_info:
            logger.error("Failed to get userinfo from token")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to get user info from Google"
            )
        
        # 提取必要的用户信息
        email = user_info.get('email')
        name = user_info.get('name') or email
        avatar_url = user_info.get('picture')
        
        if not email:
            logger.error("Email not provided by Google")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email not provided by Google"
            )
        
        logger.info(f"Processing login for user: {email}")
        
        # 获取或创建用户
        user_id = await get_or_create_user(email, name, avatar_url)
        
        # 保存 Google credentials（用于 Drive API）
        core_db = get_core_db()
        
        # 🔍 调试：检查 Google 返回的 scopes
        scope_string = token.get('scope', '')
        scope_list = scope_string.split() if scope_string else []
        has_drive_scope = 'https://www.googleapis.com/auth/drive.file' in scope_list
        
        logger.info(f"📋 Scopes returned by Google: {scope_list}")
        logger.info(f"🔑 Has drive.file scope: {has_drive_scope}")
        logger.info(f"🔄 Has refresh_token: {bool(token.get('refresh_token'))}")
        
        if not has_drive_scope:
            logger.warning("⚠️ Google did not grant Drive scope! Check OAuth consent screen configuration.")
        
        if not token.get('refresh_token'):
            logger.warning("⚠️ Google did not provide refresh_token! User may need to revoke access first.")
        
        google_credentials = {
            "access_token": token.get('access_token'),
            "refresh_token": token.get('refresh_token'),
            "token_expiry": datetime.utcnow() + timedelta(seconds=token.get('expires_in', 3600)),
            "scopes": scope_list
        }
        
        await core_db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"google_credentials": google_credentials}}
        )
        logger.info(f"✅ Saved Google credentials for user {user_id}")
        
        # 创建 JWT 令牌
        access_token = create_access_token(
            data={
                "sub": user_id,
                "email": email,
                "name": name
            }
        )
        
        # 从 session 中获取前端传入的 redirect_uri，如果没有则使用环境变量
        frontend_redirect_uri = request.session.get('frontend_redirect_uri')
        if frontend_redirect_uri:
            logger.info(f"Using frontend redirect URI from session: {frontend_redirect_uri}")
            final_callback_url = f"{frontend_redirect_uri}?token={access_token}"
            # 清除 session 中的 redirect_uri
            request.session.pop('frontend_redirect_uri', None)
        elif FRONTEND_URL:
            logger.info(f"Using FRONTEND_URL from env: {FRONTEND_URL}")
            final_callback_url = f"{FRONTEND_URL}/auth/callback?token={access_token}"
        else:
            logger.error("No redirect_uri provided and FRONTEND_URL not set")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No redirect_uri configured. Please set FRONTEND_URL environment variable or pass redirect_uri parameter."
            )
        
        logger.info(f"Login successful for user: {email}")
        print(f"🔗 Final redirect URL: {final_callback_url}")
        
        return RedirectResponse(url=final_callback_url)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OAuth callback error: {e}", exc_info=True)
        # 从 session 中获取前端传入的 redirect_uri
        frontend_redirect_uri = request.session.get('frontend_redirect_uri')
        if frontend_redirect_uri:
            error_url = f"{frontend_redirect_uri}?error=auth_failed&message={str(e)}"
            # 清除 session 中的 redirect_uri
            request.session.pop('frontend_redirect_uri', None)
        elif FRONTEND_URL:
            error_url = f"{FRONTEND_URL}/auth/callback?error=auth_failed&message={str(e)}"
        else:
            # 如果没有任何重定向URL配置，返回JSON错误
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"OAuth callback error: {str(e)}"
            )
        # 重定向到错误页面
        print(f"❌ Error redirect URL: {error_url}")
        return RedirectResponse(url=error_url)


@router.get('/user', response_model=UserResponse, tags=["authentication"])
async def get_current_user(token: str):
    """
    根据 JWT 令牌获取当前用户信息
    
    前端可以使用此端点验证 token 并获取用户详细信息
    
    **参数：**
    - token: JWT 访问令牌（查询参数）
    
    **返回：**
    - 用户完整信息
    
    **错误：**
    - 401: Token 无效或过期
    - 404: 用户不存在
    - 500: 服务器错误
    """
    # 验证 token
    payload = verify_token(token)
    
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )
    
    # 从数据库获取用户
    core_db = get_core_db()
    if core_db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection not available"
        )
    
    try:
        user = await core_db.users.find_one({"_id": ObjectId(user_id)})
    except Exception as e:
        logger.error(f"Error fetching user from database: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error retrieving user information"
        )
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # 返回用户信息
    return UserResponse(
        id=str(user["_id"]),
        email=user.get("email"),
        name=user.get("name"),
        avatar_url=user.get("avatar_url"),
        role=user.get("role"),
        project_id=str(user["project_id"]) if user.get("project_id") else None,
        created_at=user.get("created_at"),
        updated_at=user.get("updated_at")
    )


@router.post('/logout', response_model=LogoutResponse, tags=["authentication"])
async def logout():
    """
    登出端点
    
    前端调用此端点后应该：
    1. 删除本地存储的 token（localStorage）
    2. 清除用户状态
    3. 重定向到登录页面（可选）
    
    **注意：** 由于使用 JWT，服务器端无状态，实际登出由前端处理
    
    **返回：**
    - 成功消息
    """
    logger.info("Logout endpoint called")
    return LogoutResponse(
        message="Logged out successfully. Please remove token from client.",
        success=True
    )


@router.get('/verify', tags=["authentication"])
async def verify_auth(token: str):
    """
    快速验证 Token 是否有效
    
    **参数：**
    - token: JWT 访问令牌
    
    **返回：**
    - valid: Token 是否有效
    - user_id: 用户 ID（如果有效）
    - expires_at: 过期时间（如果有效）
    """
    try:
        payload = verify_token(token)
        return {
            "valid": True,
            "user_id": payload.get("sub"),
            "email": payload.get("email"),
            "expires_at": datetime.fromtimestamp(payload.get("exp"))
        }
    except HTTPException:
        return {
            "valid": False,
            "error": "Token is invalid or expired"
        }
