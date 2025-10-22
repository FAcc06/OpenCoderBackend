# 🚀 部署配置指南

## ❌ 当前问题

你的 `.env` 文件配置有误：

```bash
# ❌ 错误配置
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback  # 本地地址！
FRONTEND_URL=https://opencoderbackend.onrender.com  # 这是后端URL，不是前端！
```

---

## ✅ 正确的配置

### **本地开发环境 (.env)**

```bash
# MongoDB
MONGODB_URI=mongodb+srv://pengyouvt_db_user:b3omORBKNgypsiKt@cluster0.uewv0dc.mongodb.net/app_core?retryWrites=true&w=majority&appName=Cluster0

# Google OAuth
GOOGLE_CLIENT_ID=563398094094-k0ehp6asurcoa4p1n5ig75sis6k1st3c.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=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
GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback  ✅

# JWT
SECRET_KEY=UbNhSnMbZjBNJTJH_adnp62slmdcZIaBCRDTebC5ZtI
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080

# 前端URL
FRONTEND_URL=http://localhost:5173  ✅ (或者可以不设置，让前端传入redirect_uri)
```

---

### **生产环境 (Render 环境变量)**

在 Render Dashboard 中设置以下环境变量：

```bash
# MongoDB
MONGODB_URI=mongodb+srv://pengyouvt_db_user:b3omORBKNgypsiKt@cluster0.uewv0dc.mongodb.net/app_core?retryWrites=true&w=majority&appName=Cluster0

# Google OAuth
GOOGLE_CLIENT_ID=563398094094-k0ehp6asurcoa4p1n5ig75sis6k1st3c.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=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
GOOGLE_REDIRECT_URI=https://opencoderbackend.onrender.com/auth/google/callback  ✅

# JWT
SECRET_KEY=UbNhSnMbZjBNJTJH_adnp62slmdcZIaBCRDTebC5ZtI
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080

# 前端URL（重要！这是前端地址）
FRONTEND_URL=https://opencoderfrontend.onrender.com  ✅
```

---

## 🔑 关键区别

### **GOOGLE_REDIRECT_URI vs FRONTEND_URL**

| 变量 | 用途 | 值 |
|------|------|-----|
| `GOOGLE_REDIRECT_URI` | Google回调**后端** | `https://opencoderbackend.onrender.com/auth/google/callback` |
| `FRONTEND_URL` | OAuth完成后重定向**前端** | `https://opencoderfrontend.onrender.com` |

### **OAuth 流程：**

```
1. 用户 → 前端
   └─ 点击登录

2. 前端 → 后端 /auth/login
   └─ 请求OAuth登录

3. 后端 → Google
   └─ 携带 GOOGLE_REDIRECT_URI

4. Google → 后端 /auth/google/callback  ← 使用 GOOGLE_REDIRECT_URI
   └─ 验证用户身份

5. 后端 → 前端 /auth/callback?token=xxx  ← 使用 FRONTEND_URL
   └─ 返回JWT token
```

---

## 🌐 Google Cloud Console 配置

### **必须在 Google Console 中添加这两个回调地址：**

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)
2. 进入项目 → APIs & Services → Credentials
3. 点击你的 OAuth 2.0 Client ID
4. 在 **Authorized redirect URIs** 中添加：

```
✅ http://localhost:8000/auth/google/callback
✅ https://opencoderbackend.onrender.com/auth/google/callback
```

**⚠️ 如果没有添加生产环境的回调地址，Google会拒绝请求！**

---

## 🛠️ 修复步骤

### **1. 手动编辑本地 .env 文件**

```bash
# 打开 .env 文件，修改这一行：
FRONTEND_URL=https://opencoderfrontend.onrender.com
```

### **2. 在 Google Console 中添加回调地址**

添加：
```
https://opencoderbackend.onrender.com/auth/google/callback
```

### **3. 重启本地服务器**

```bash
python main.py
```

应该看到：
```
🔒 CORS Configuration:
   Environment: Development (Local)
   Allowed origins: ['http://localhost:5173', 'http://127.0.0.1:5173', 'http://localhost:8000']
```

### **4. 测试本地OAuth**

访问：`http://localhost:5173` → 点击登录

---

## 🚀 部署到 Render

### **1. 创建后端服务**

在 Render 中设置环境变量（见上面的"生产环境"部分）

### **2. 特别注意**

- ✅ `GOOGLE_REDIRECT_URI` 使用**后端域名**
- ✅ `FRONTEND_URL` 使用**前端域名**
- ✅ Google Console 中必须添加对应的回调地址

---

## 📋 检查清单

- [ ] 修改本地 `.env` 中的 `FRONTEND_URL` 为前端地址
- [ ] 在 Google Console 中添加生产环境回调地址
- [ ] Render 环境变量配置正确（`GOOGLE_REDIRECT_URI` 和 `FRONTEND_URL`）
- [ ] 重启本地服务器测试
- [ ] 部署到 Render 后测试

---

## 🐛 调试

如果还是有问题，检查服务器日志：

```bash
# 本地
python main.py

# 查看日志中的：
INFO:routers.auth:Initiating Google OAuth login
INFO:routers.auth:Using frontend redirect URI from session: xxx
```

确认重定向地址是否正确。

