# Google OAuth API 文档

## 📋 概述

本文档描述了重构后的 Google OAuth 2.0 认证 API。所有端点都在 `/auth` 路径下。

---

## 🔄 改进点

### ✅ 代码质量提升

1. **更好的代码组织**
   - 清晰的区块划分（配置、模型、辅助函数、API 端点）
   - 函数职责单一化

2. **完善的类型注解**
   - 所有函数都有类型提示
   - 使用 Pydantic 模型定义响应

3. **改进的错误处理**
   - 统一的异常处理
   - 明确的错误状态码
   - 详细的错误日志

4. **日志记录**
   - 添加了完整的日志系统
   - 记录关键操作和错误

5. **文档完善**
   - 详细的函数和 API 文档字符串
   - 清晰的流程说明

### 🆕 新增功能

- **Token 验证端点** (`/auth/verify`) - 快速验证 token 是否有效

---

## 🔌 API 端点

### 1. 发起 Google 登录

```
GET /auth/login
```

**描述：** 重定向用户到 Google OAuth 登录页面

**使用方式：**
```javascript
// 前端直接跳转
window.location.href = 'http://localhost:8000/auth/login';
```

**流程：**
```
用户 → /auth/login → Google OAuth 页面 → 用户授权 → /auth/google/callback
```

---

### 2. Google OAuth 回调（自动）

```
GET /auth/google/callback
```

**描述：** Google 授权后的回调处理

**流程：**
1. 接收 Google 授权码
2. 获取用户信息（email, name, avatar）
3. 创建/更新数据库用户
4. 生成 JWT Token
5. 重定向到前端

**成功返回：**
```
重定向到: {FRONTEND_URL}/static/index.html?token={JWT_TOKEN}
```

**失败返回：**
```
重定向到: {FRONTEND_URL}/static/index.html?error=auth_failed&message={错误信息}
```

---

### 3. 获取用户信息 ⭐

```
GET /auth/user?token={JWT_TOKEN}
```

**描述：** 根据 JWT Token 获取当前用户信息

**参数：**
| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| token | string | 是 | JWT 访问令牌 |

**请求示例：**
```bash
curl "http://localhost:8000/auth/user?token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
```

**成功响应：** `200 OK`
```json
{
  "id": "507f1f77bcf86cd799439011",
  "email": "user@example.com",
  "name": "张三",
  "avatar_url": "https://lh3.googleusercontent.com/...",
  "role": "manager",
  "project_id": "507f1f77bcf86cd799439012",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

**错误响应：**

| 状态码 | 说明 |
|--------|------|
| 401 | Token 无效或过期 |
| 404 | 用户不存在 |
| 503 | 数据库连接不可用 |
| 500 | 服务器内部错误 |

**前端使用示例：**
```javascript
async function getUserInfo() {
    const token = localStorage.getItem('authToken');
    const response = await fetch(`http://localhost:8000/auth/user?token=${token}`);
    
    if (response.ok) {
        const user = await response.json();
        console.log('用户信息:', user);
    } else {
        console.error('获取用户信息失败');
    }
}
```

---

### 4. 验证 Token 🆕

```
GET /auth/verify?token={JWT_TOKEN}
```

**描述：** 快速验证 Token 是否有效（不查询数据库）

**参数：**
| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| token | string | 是 | JWT 访问令牌 |

**成功响应：** `200 OK`
```json
{
  "valid": true,
  "user_id": "507f1f77bcf86cd799439011",
  "email": "user@example.com",
  "expires_at": "2024-01-22T10:30:00Z"
}
```

**Token 无效响应：** `200 OK`
```json
{
  "valid": false,
  "error": "Token is invalid or expired"
}
```

**前端使用示例：**
```javascript
async function checkAuth() {
    const token = localStorage.getItem('authToken');
    if (!token) return false;
    
    const response = await fetch(`http://localhost:8000/auth/verify?token=${token}`);
    const result = await response.json();
    
    return result.valid;
}
```

---

### 5. 登出

```
POST /auth/logout
```

**描述：** 登出端点（前端需要删除本地 Token）

**响应：** `200 OK`
```json
{
  "message": "Logged out successfully. Please remove token from client.",
  "success": true
}
```

**前端使用示例：**
```javascript
async function logout() {
    await fetch('http://localhost:8000/auth/logout', {
        method: 'POST'
    });
    
    // 删除本地 Token
    localStorage.removeItem('authToken');
    
    // 清除用户状态
    currentUser = null;
    
    // 刷新页面或跳转到登录页
    window.location.reload();
}
```

---

## 🔐 JWT Token 结构

### Payload 内容

```json
{
  "sub": "507f1f77bcf86cd799439011",  // 用户 ID
  "email": "user@example.com",        // 用户邮箱
  "name": "张三",                      // 用户姓名
  "exp": 1705920600,                  // 过期时间戳
  "iat": 1705316600                   // 签发时间戳
}
```

### 有效期

- **默认：** 7 天（10080 分钟）
- **配置：** 可通过环境变量 `ACCESS_TOKEN_EXPIRE_MINUTES` 修改

---

## 📱 前端集成指南

### 完整的认证流程

```javascript
// ========== 1. 初始化 ==========
let currentUser = null;
let authToken = null;

// ========== 2. 页面加载时检查登录状态 ==========
async function checkLoginStatus() {
    // 检查 URL 参数中的 token（OAuth 回调）
    const urlParams = new URLSearchParams(window.location.search);
    const tokenFromUrl = urlParams.get('token');
    const errorFromUrl = urlParams.get('error');
    
    if (errorFromUrl) {
        alert('登录失败: ' + errorFromUrl);
        return;
    }
    
    if (tokenFromUrl) {
        // 保存 token
        localStorage.setItem('authToken', tokenFromUrl);
        authToken = tokenFromUrl;
        // 清除 URL 参数
        window.history.replaceState({}, document.title, window.location.pathname);
    } else {
        // 从 localStorage 读取
        authToken = localStorage.getItem('authToken');
    }
    
    if (authToken) {
        // 验证 token 并获取用户信息
        try {
            const response = await fetch(`http://localhost:8000/auth/user?token=${authToken}`);
            if (response.ok) {
                currentUser = await response.json();
                updateUI();
            } else {
                // token 无效，清除
                localStorage.removeItem('authToken');
                authToken = null;
            }
        } catch (error) {
            console.error('验证失败:', error);
        }
    }
}

// ========== 3. 更新 UI ==========
function updateUI() {
    if (currentUser) {
        // 显示用户信息
        document.getElementById('user-avatar').src = currentUser.avatar_url;
        document.getElementById('user-name').textContent = currentUser.name;
        document.getElementById('login-btn').style.display = 'none';
        document.getElementById('user-info').style.display = 'block';
    } else {
        // 显示登录按钮
        document.getElementById('login-btn').style.display = 'block';
        document.getElementById('user-info').style.display = 'none';
    }
}

// ========== 4. 登录 ==========
function googleLogin() {
    window.location.href = 'http://localhost:8000/auth/login';
}

// ========== 5. 登出 ==========
async function logout() {
    await fetch('http://localhost:8000/auth/logout', { method: 'POST' });
    localStorage.removeItem('authToken');
    currentUser = null;
    authToken = null;
    updateUI();
}

// ========== 6. 页面加载 ==========
window.addEventListener('DOMContentLoaded', checkLoginStatus);
```

---

## 🔧 环境变量配置

```env
# Google OAuth
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback

# JWT
SECRET_KEY=your-secret-key-change-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080

# 前端 URL
FRONTEND_URL=http://localhost:8000
```

---

## 📊 数据流图

### 登录流程

```
┌─────────┐          ┌──────────┐          ┌────────┐          ┌──────────┐
│  用户   │          │  前端    │          │ 后端   │          │  Google  │
└────┬────┘          └────┬─────┘          └───┬────┘          └────┬─────┘
     │                    │                    │                     │
     │ 1. 点击登录按钮    │                    │                     │
     ├───────────────────>│                    │                     │
     │                    │ 2. GET /auth/login │                     │
     │                    ├───────────────────>│                     │
     │                    │                    │ 3. 重定向到 Google  │
     │                    │                    ├────────────────────>│
     │                    │                    │                     │
     │ 4. Google 授权页面 │<───────────────────┴─────────────────────┤
     ├───────────────────>│                                          │
     │                    │ 5. 授权成功，返回授权码                  │
     │                    │<─────────────────────────────────────────┤
     │                    │ 6. GET /auth/google/callback?code=xxx    │
     │                    ├──────────────────>│                      │
     │                    │                   │ 7. 获取用户信息       │
     │                    │                   │ 8. 创建/更新用户     │
     │                    │                   │ 9. 生成 JWT          │
     │                    │ 10. 重定向+Token  │                      │
     │                    │<──────────────────┤                      │
     │ 11. 显示用户信息   │                   │                      │
     │<───────────────────┤                   │                      │
     │                    │                   │                      │
```

---

## 🧪 测试示例

### 使用 cURL 测试

```bash
# 1. 获取用户信息
curl "http://localhost:8000/auth/user?token=YOUR_JWT_TOKEN"

# 2. 验证 Token
curl "http://localhost:8000/auth/verify?token=YOUR_JWT_TOKEN"

# 3. 登出
curl -X POST "http://localhost:8000/auth/logout"
```

### 使用 Python 测试

```python
import requests

# 获取用户信息
token = "YOUR_JWT_TOKEN"
response = requests.get(f"http://localhost:8000/auth/user?token={token}")
print(response.json())

# 验证 Token
response = requests.get(f"http://localhost:8000/auth/verify?token={token}")
print(response.json())
```

---

## 📝 常见问题

### Q: Token 有效期是多久？
A: 默认 7 天（10080 分钟），可通过环境变量配置。

### Q: Token 过期后怎么办？
A: 需要重新登录。建议在前端定期检查 token 是否即将过期。

### Q: 如何在 API 请求中使用 Token？
A: 目前作为查询参数传递。未来可以改为 Bearer Token（Header）。

### Q: 是否支持刷新 Token？
A: 当前版本不支持。Token 过期后需要重新登录。

---

## 🎯 后续改进建议

1. **添加 Refresh Token 机制**
   - 短期 Access Token + 长期 Refresh Token
   - 自动刷新无需重新登录

2. **改用 Bearer Token**
   ```
   Authorization: Bearer {token}
   ```

3. **添加速率限制**
   - 防止暴力破解
   - 保护 API 端点

4. **Token 黑名单**
   - 支持主动撤销 Token
   - 使用 Redis 存储已撤销的 Token

5. **多因素认证（MFA）**
   - 增加安全性
   - 可选的第二因素验证

---

## 📞 技术支持

如有问题，请查看：
- 代码文件：`routers/auth.py`
- 快速开始：`QUICKSTART.md`
- 服务器日志：查看终端输出

---

**版本：** 2.0  
**最后更新：** 2024  
**作者：** MongoDB 标注平台团队

