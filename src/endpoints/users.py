"""Users endpoint module (demo / in-memory storage).

Provides basic CRUD-style routes for a ``User`` resource backed by a
simple Python dictionary.  Intended as a development placeholder until
a real persistence layer is wired up.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/users", tags=["Users"])

# --- In-memory storage for demo purposes ---
fake_db: dict[int, dict] = {
    1: {"id": 1, "name": "Alice", "email": "alice@example.com"},
    2: {"id": 2, "name": "Bob", "email": "bob@example.com"},
}
next_id = 3


class UserCreate(BaseModel):
    """Request body for creating a new user.

    Attributes:
        name (str): Full name of the user.
        email (str): Email address of the user.
    """

    name: str
    email: str


class UserResponse(BaseModel):
    """Response model representing a stored user.

    Attributes:
        id (int): Unique auto-incremented identifier.
        name (str): Full name of the user.
        email (str): Email address of the user.
    """

    id: int
    name: str
    email: str


# 1) GET all users
@router.get("/", response_model=list[UserResponse])
def get_users():
    """Return all users in the in-memory store.

    Returns:
        list[UserResponse]: A list of every user currently stored
            in ``fake_db``.
    """
    return list(fake_db.values())


# 2) GET a single user by ID
@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int):
    """Return a single user by their numeric ID.

    Args:
        user_id (int): The unique identifier of the user (passed as
            a URL path parameter).

    Returns:
        UserResponse: The matching user object.

    Raises:
        HTTPException (404): If no user with the given ID exists.
    """
    if user_id not in fake_db:
        raise HTTPException(status_code=404, detail="User not found")
    return fake_db[user_id]


# 3) POST create a new user
@router.post("/", response_model=UserResponse, status_code=201)
def create_user(user: UserCreate):
    """Create a new user and add them to the in-memory store.

    The new user is assigned the next available auto-incremented ID.

    Args:
        user (UserCreate): JSON body with:
            - ``name`` (str) – Full name of the user.
            - ``email`` (str) – Email address of the user.

    Returns:
        UserResponse: The newly created user (HTTP 201).
    """
    global next_id
    new_user = {"id": next_id, "name": user.name, "email": user.email}
    fake_db[next_id] = new_user
    next_id += 1
    return new_user
