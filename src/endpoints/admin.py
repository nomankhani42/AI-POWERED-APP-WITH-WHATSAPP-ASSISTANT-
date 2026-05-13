"""Admin authentication endpoints — register, login, profile."""

from fastapi import APIRouter, HTTPException

from database import get_db
from models.admin import AdminCreate, AdminLogin, AdminResponse
from crud.admin import create_admin, authenticate_admin, get_admin_by_id, list_admins

router = APIRouter(prefix="/admin", tags=["Admin Auth"])


@router.post("/register", response_model=AdminResponse, status_code=201)
async def register_admin(body: AdminCreate):
    """Create a new admin account."""
    db = get_db()
    try:
        admin = await create_admin(db, body)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return AdminResponse(**admin.model_dump())


@router.post("/login", response_model=dict)
async def login_admin(body: AdminLogin):
    """Authenticate an admin and return profile info.

    Returns admin info on success. (JWT to be added later.)
    """
    db = get_db()
    admin = await authenticate_admin(db, body.email, body.password)
    if not admin:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return {
        "status": "authenticated",
        "admin": AdminResponse(**admin.model_dump()).model_dump(),
    }


@router.get("/profile/{admin_id}", response_model=AdminResponse)
async def get_admin_profile(admin_id: str):
    """Get an admin's profile by ID."""
    db = get_db()
    admin = await get_admin_by_id(db, admin_id)
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found")
    return AdminResponse(**admin.model_dump())


@router.get("/list", response_model=list[AdminResponse])
async def list_all_admins():
    """List all admin accounts."""
    db = get_db()
    admins = await list_admins(db)
    return [AdminResponse(**a.model_dump()) for a in admins]
