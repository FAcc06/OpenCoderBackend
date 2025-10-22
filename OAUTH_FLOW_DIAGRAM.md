# 🔐 OAuth 流程详解

## 🎯 你的问题根源

### ❌ **当前配置（错误）**

```
.env 文件：
├─ GOOGLE_REDIRECT_URI = http://localhost:8000/auth/google/callback
│                         ↑ 本地地址，生产环境不工作！
└─ FRONTEND_URL = https://opencoderbackend.onrender.com
                  ↑ 这是后端URL，不是前端URL！
```

### ✅ **正确配置（生产环境）**

```
Render 环境变量：
├─ GOOGLE_REDIRECT_URI = https://opencoderbackend.onrender.com/auth/google/callback
│                         ↑ 后端回调地址
└─ FRONTEND_URL = https://opencoderfrontend.onrender.com
                  ↑ 前端地址
```

---

## 📊 OAuth 完整流程图

### **场景 1：正确配置（会成功）**

```
用户浏览器
    │
    ├─ 1. 访问 https://opencoderfrontend.onrender.com
    │
    ├─ 2. 点击"登录" → 发送请求
    │      GET /auth/login?redirect_uri=https://opencoderfrontend.onrender.com/auth/callback
    │
    ▼
后端 (opencoderbackend.onrender.com)
    │
    ├─ 3. 生成 state → 存入 session cookie
    │      state = "abc123"
    │
    ├─ 4. 重定向到 Google
    │      Location: https://accounts.google.com/o/oauth2/v2/auth
    │      参数:
    │      ├─ client_id = 你的Client ID
    │      ├─ redirect_uri = https://opencoderbackend.onrender.com/auth/google/callback
    │      └─ state = "abc123"
    │
    ▼
Google OAuth 服务器
    │
    ├─ 5. 用户登录并授权
    │
    ├─ 6. 验证 redirect_uri 是否在 Authorized redirect URIs 中
    │      ⚠️ 如果不在 → 拒绝！
    │
    ├─ 7. 回调后端
    │      GET https://opencoderbackend.onrender.com/auth/google/callback
    │      参数:
    │      ├─ code = "xyz789"
    │      └─ state = "abc123"
    │
    ▼
后端 (opencoderbackend.onrender.com)
    │
    ├─ 8. 读取 session cookie 中的 state
    │      ✅ 比对: cookie_state === url_state
    │      ✅ 匹配 → 继续
    │      ❌ 不匹配 → CSRF 错误！
    │
    ├─ 9. 使用 code 换取 access_token
    │
    ├─ 10. 获取用户信息
    │
    ├─ 11. 生成 JWT token
    │
    ├─ 12. 重定向到前端
    │       Location: https://opencoderfrontend.onrender.com/auth/callback?token=JWT_TOKEN
    │       ↑ 使用 FRONTEND_URL 或前端传入的 redirect_uri
    │
    ▼
前端 (opencoderfrontend.onrender.com)
    │
    └─ 13. 接收 token → 登录成功 ✅
```

---

### **场景 2：错误配置（你现在的情况）**

```
问题 1: GOOGLE_REDIRECT_URI 是 localhost
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

后端: https://opencoderbackend.onrender.com
    │
    ├─ 重定向到 Google
    │  redirect_uri = http://localhost:8000/auth/google/callback  ❌
    │
    ▼
Google: ❌ 拒绝！
    └─ redirect_uri 不在 Authorized redirect URIs 中
    └─ 或者回调到 localhost（无法访问）


问题 2: FRONTEND_URL 是后端地址
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

后端 OAuth 成功后:
    │
    ├─ 重定向到: https://opencoderbackend.onrender.com/auth/callback?token=xxx
    │             ↑ 这是后端地址，不是前端！
    │
    ▼
结果: ❌ 404 Not Found（后端没有这个前端路由）


问题 3: Session Cookie 丢失
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

如果 CORS 配置不允许前端域名:
    │
    ├─ /auth/login: 设置 session cookie
    │   但 CORS 阻止前端读取
    │
    ├─ Google 回调: /auth/google/callback
    │   无法读取 session cookie 中的 state
    │
    ▼
结果: ❌ CSRF Warning! State not equal
```

---

## 🔧 修复方案

### **1. 修改 .env 文件**

```bash
# 手动编辑 .env，改这一行：
FRONTEND_URL=https://opencoderfrontend.onrender.com
```

### **2. Google Console 配置**

在 [Google Cloud Console](https://console.cloud.google.com/) 中添加：

```
Authorized redirect URIs:
├─ http://localhost:8000/auth/google/callback           (本地测试)
└─ https://opencoderbackend.onrender.com/auth/google/callback  (生产环境)
```

**⚠️ 两个都要添加！**

### **3. Render 环境变量**

部署到 Render 后，设置：

```bash
GOOGLE_REDIRECT_URI=https://opencoderbackend.onrender.com/auth/google/callback
FRONTEND_URL=https://opencoderfrontend.onrender.com
```

---

## 🎯 关键点总结

| 配置项 | 用途 | 本地开发 | 生产环境 |
|--------|------|----------|----------|
| `GOOGLE_REDIRECT_URI` | Google回调**后端** | `http://localhost:8000/auth/google/callback` | `https://opencoderbackend.onrender.com/auth/google/callback` |
| `FRONTEND_URL` | 最终重定向**前端** | `http://localhost:5173` | `https://opencoderfrontend.onrender.com` |

**记住：**
- `GOOGLE_REDIRECT_URI` = 后端地址
- `FRONTEND_URL` = 前端地址
- **不要搞混！**

---

## ✅ 验证步骤

### **本地测试：**

1. 修改 `.env` 中的 `FRONTEND_URL`
2. 重启服务器：`python main.py`
3. 访问：`http://localhost:5173` → 登录
4. 查看日志，确认重定向地址

### **生产测试：**

1. 在 Google Console 添加生产回调地址
2. 在 Render 设置正确的环境变量
3. 部署后测试登录
4. 检查 Render 日志

---

## 🐛 如何调试

### **查看服务器日志：**

```bash
INFO:routers.auth:Initiating Google OAuth login
INFO:routers.auth:Stored frontend redirect URI in session: xxx
INFO:routers.auth:Using frontend redirect URI from session: xxx
INFO:routers.auth:Login successful for user: xxx
```

### **常见错误：**

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| `redirect_uri_mismatch` | Google Console 中没有配置这个URI | 在 Google Console 添加 |
| `mismatching_state` | Session cookie 丢失或CORS问题 | 检查 CORS 配置，确保 allow_credentials=True |
| `404 Not Found` | 重定向到错误的地址 | 检查 FRONTEND_URL 配置 |

---

现在你明白问题所在了吗？😊

