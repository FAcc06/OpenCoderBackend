#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google OAuth 配置助手脚本
自动生成 .env 文件并配置所需的环境变量
"""

import secrets
import os
import sys

# Windows 控制台编码修复
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

def generate_secret_key():
    """生成安全的 SECRET_KEY"""
    return secrets.token_urlsafe(32)

def create_env_file():
    """创建 .env 文件"""
    
    print("=== Google OAuth 配置助手 ===")
    print("=" * 50)
    
    # 检查是否已存在 .env 文件
    if os.path.exists('.env'):
        response = input("\n[!] .env 文件已存在，是否覆盖？(y/n): ")
        if response.lower() != 'y':
            print("[X] 取消操作")
            return
    
    # 生成 SECRET_KEY
    secret_key = generate_secret_key()
    print(f"\n[OK] 已生成安全的 SECRET_KEY: {secret_key[:20]}...")
    
    # 获取 MongoDB URI
    print("\n=== MongoDB 配置 ===")
    mongodb_uri = input("请输入您的 MongoDB URI（回车使用本地默认）: ").strip()
    if not mongodb_uri:
        mongodb_uri = "mongodb://localhost:27017"
    
    # Google OAuth 配置（使用提供的凭证）
    google_client_id = "563398094094-k0ehp6asurcoa4p1n5ig75sis6k1st3c.apps.googleusercontent.com"
    google_client_secret = "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"
    
    print("\n=== Google OAuth 配置 ===")
    print(f"Client ID: {google_client_id}")
    print(f"Client Secret: {google_client_secret[:20]}...")
    
    # 环境选择
    print("\n=== 部署环境 ===")
    print("1. 本地开发 (localhost:8000)")
    print("2. 生产环境（自定义域名）")
    env_choice = input("请选择环境 (1/2，默认1): ").strip() or "1"
    
    if env_choice == "2":
        domain = input("请输入您的域名（如 https://example.com）: ").strip()
        redirect_uri = f"{domain}/auth/google/callback"
        frontend_url = domain
        print(f"\n[!] 请确保在 Google Cloud Console 中添加重定向 URI: {redirect_uri}")
    else:
        redirect_uri = "http://localhost:8000/auth/google/callback"
        frontend_url = "http://localhost:8000"
    
    # Token 有效期
    print("\n=== Token 配置 ===")
    expire_days = input("JWT Token 有效期（天，默认7天）: ").strip() or "7"
    expire_minutes = int(expire_days) * 24 * 60
    
    # 生成 .env 内容
    env_content = f"""# MongoDB配置
MONGODB_URI={mongodb_uri}

# Google OAuth配置
GOOGLE_CLIENT_ID={google_client_id}
GOOGLE_CLIENT_SECRET={google_client_secret}
GOOGLE_REDIRECT_URI={redirect_uri}

# JWT配置
SECRET_KEY={secret_key}
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES={expire_minutes}

# 前端URL（用于OAuth回调后的重定向）
FRONTEND_URL={frontend_url}
"""
    
    # 写入文件
    try:
        with open('.env', 'w', encoding='utf-8') as f:
            f.write(env_content)
        
        print("\n" + "=" * 50)
        print("[OK] .env 文件创建成功！")
        print("=" * 50)
        
        print("\n=== 配置摘要 ===")
        print(f"   MongoDB URI: {mongodb_uri}")
        print(f"   Redirect URI: {redirect_uri}")
        print(f"   Frontend URL: {frontend_url}")
        print(f"   Token 有效期: {expire_days} 天")
        
        print("\n=== 下一步 ===")
        print("   1. 确保在 Google Cloud Console 中配置了正确的重定向 URI")
        print("   2. 安装依赖: pip install -r requirements.txt")
        print("   3. 启动服务器: python main.py")
        print("   4. 访问: http://localhost:8000/static/index.html")
        
        print("\n[INFO] 详细文档请查看: GOOGLE_OAUTH_SETUP.md")
        
    except Exception as e:
        print(f"\n[X] 创建 .env 文件失败: {e}")

def check_dependencies():
    """检查依赖包是否安装"""
    print("\n=== 检查依赖包 ===")
    
    required_packages = [
        'fastapi',
        'uvicorn',
        'motor',
        'authlib',
        'python-jose',
        'python-dotenv'
    ]
    
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
            print(f"   [OK] {package}")
        except ImportError:
            print(f"   [X] {package} - 未安装")
            missing_packages.append(package)
    
    if missing_packages:
        print(f"\n[!] 缺少以下依赖包: {', '.join(missing_packages)}")
        print("   请运行: pip install -r requirements.txt")
        return False
    else:
        print("\n[OK] 所有依赖包已安装")
        return True

if __name__ == "__main__":
    create_env_file()
    
    # 检查依赖
    print("\n" + "=" * 50)
    check_dependencies()
    
    print("\n" + "=" * 50)
    print("[SUCCESS] 配置完成！祝您使用愉快！")
    print("=" * 50)

