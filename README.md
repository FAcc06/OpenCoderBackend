# MongoDB Annotation Platform - Backend API

FastAPI-based annotation platform backend with MongoDB Atlas.

## Features

- 🔐 Google OAuth Authentication
- 👥 Multi-role Support (Manager/Coder)
- 📝 Project Management
- 🏷️ Flexible Tag System
- ✅ Task Assignment & Annotation
- 📊 Progress Dashboard
- 🔄 Async MongoDB Operations

## Quick Start

### Prerequisites

- Python 3.11+
- MongoDB Atlas account
- Google OAuth credentials

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp env.example .env

# Edit .env with your credentials
# - MONGODB_URI
# - SECRET_KEY
# - GOOGLE_CLIENT_ID
# - GOOGLE_CLIENT_SECRET
```

### Run

```bash
python main.py
```

API will be available at `http://localhost:8000`

API Documentation: `http://localhost:8000/docs`

## Project Structure

```
.
├── main.py              # FastAPI application
├── database.py          # MongoDB connection
├── models.py            # Pydantic models
├── utils.py             # Utility functions
├── routers/             # API endpoints
│   ├── auth.py          # Authentication
│   ├── users.py         # User management
│   ├── projects.py      # Project management
│   ├── applications.py  # Application handling
│   ├── tasks.py         # Task management
│   ├── assignments.py   # Task assignments
│   ├── annotations.py   # Annotation creation
│   ├── tag_groups.py    # Tag group management
│   ├── board.py         # Dashboard/stats
│   ├── dashboard.py     # Analytics
│   └── public.py        # Public APIs
└── static/              # Static files
```

## Environment Variables

```bash
MONGODB_URI=mongodb+srv://...
SECRET_KEY=your-secret-key
ALGORITHM=HS256
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
FRONTEND_URL=http://localhost:
ENCRYPTION_KEY=your-encryption-key
```

## API Endpoints

See `API_DOCUMENTATION.md` for complete API reference.

### Authentication
- `GET /auth/login` - Initiate Google OAuth
- `GET /auth/google/callback` - OAuth callback
- `GET /auth/user` - Get current user

### Projects
- `POST /api/projects` - Create project
- `GET /api/projects` - List projects
- `GET /api/projects/{id}` - Get project details

### Tasks & Annotations
- `POST /api/projects/{id}/tasks/batch` - Upload tasks
- `POST /api/projects/{id}/assignments` - Assign tasks
- `GET /api/projects/{id}/my-next-task` - Get next task (Coder)
- `POST /api/projects/{id}/submit-and-next` - Submit & continue

### Applications
- `POST /api/projects/{id}/apply` - Apply to project
- `POST /api/projects/{id}/applications/{app_id}/approve` - Approve application

## Deployment

### Render

1. Push to GitHub
2. Connect to Render
3. Set environment variables
4. Deploy!

See `QUICKSTART.md` for details.

## License

MIT

