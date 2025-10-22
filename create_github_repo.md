# 使用命令行创建 GitHub 仓库

## 方案 1：GitHub CLI（推荐）

### 1. 安装 GitHub CLI

```bash
# Windows
winget install --id GitHub.cli

# 或下载：https://cli.github.com/
```

### 2. 登录 GitHub

```bash
gh auth login
# 按提示选择：
# - GitHub.com
# - HTTPS
# - Yes (authenticate Git)
# - Login with a web browser
```

### 3. 初始化本地仓库

```bash
cd C:\Users\amykm\Desktop\MongoDB
git init
git add .
git commit -m "Initial commit: FastAPI MongoDB annotation platform"
```

### 4. 创建 GitHub 仓库并推送（一条命令完成）

```bash
# 创建 public 仓库
gh repo create annotation-platform-backend --public --source=. --remote=origin --push

# 或创建 private 仓库
gh repo create annotation-platform-backend --private --source=. --remote=origin --push
```

✅ 完成！你的代码已经推送到 GitHub 了！

仓库地址：`https://github.com/你的用户名/annotation-platform-backend`

---

## 方案 2：传统方式（先创建仓库，再推送）

### 1. 创建仓库（使用 GitHub CLI）

```bash
# 登录
gh auth login

# 创建仓库（不推送）
gh repo create annotation-platform-backend --public

# 会输出仓库 URL，例如：
# https://github.com/yourusername/annotation-platform-backend.git
```

### 2. 推送代码

```bash
cd C:\Users\amykm\Desktop\MongoDB
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/你的用户名/annotation-platform-backend.git
git push -u origin main
```

---

## 方案 3：使用 PowerShell + GitHub API（无需安装 CLI）

### 1. 创建 Personal Access Token

1. 访问：https://github.com/settings/tokens
2. 点击 "Generate new token" → "Generate new token (classic)"
3. 勾选 `repo` 权限
4. 点击 "Generate token"
5. 复制 token（只显示一次）

### 2. 使用 API 创建仓库

```powershell
# 设置变量
$token = "你的_GitHub_Token"
$repoName = "annotation-platform-backend"
$description = "FastAPI MongoDB annotation platform backend"

# 创建仓库
$headers = @{
    "Authorization" = "token $token"
    "Accept" = "application/vnd.github.v3+json"
}

$body = @{
    "name" = $repoName
    "description" = $description
    "private" = $false
    "auto_init" = $false
} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "https://api.github.com/user/repos" -Method Post -Headers $headers -Body $body -ContentType "application/json"

Write-Host "仓库创建成功！"
Write-Host "仓库 URL: $($response.clone_url)"
```

### 3. 推送代码

```powershell
cd C:\Users\amykm\Desktop\MongoDB
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin $response.clone_url
git push -u origin main
```

---

## 推荐流程（最简单）

```bash
# 1. 安装 GitHub CLI
winget install --id GitHub.cli

# 2. 重启终端，然后登录
gh auth login

# 3. 进入项目目录
cd C:\Users\amykm\Desktop\MongoDB

# 4. 初始化并创建仓库（一条命令搞定）
git init
git add .
git commit -m "Initial commit: MongoDB annotation platform"
gh repo create annotation-platform-backend --public --source=. --remote=origin --push
```

✅ 完成！代码已推送到 GitHub

---

## 查看你的仓库

```bash
# 在浏览器中打开仓库
gh repo view --web

# 或直接访问
# https://github.com/你的用户名/annotation-platform-backend
```

---

## 下一步：部署到 Render

创建仓库后，按照 `QUICKSTART.md` 部署到 Render 即可！

