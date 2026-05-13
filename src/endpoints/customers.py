"""Customers REST API endpoints."""

from fastapi import APIRouter, HTTPException, Query

from database import get_db
from crud.customer import (
    get_customer_by_id,
    get_customer_by_whatsapp_number,
)

router = APIRouter(prefix="/customers", tags=["Customers"])

COLLECTION = "customers"


@router.get("/")
async def list_customers(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List all customers, newest first."""
    db = get_db()
    cursor = db[COLLECTION].find().skip(skip).limit(limit).sort("created_at", -1)
    customers = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        customers.append(doc)
    return customers


@router.get("/stats")
async def customer_stats():
    """Return total customer count and top bookers."""
    db = get_db()
    total = await db[COLLECTION].count_documents({})
    # Top 5 by bookings
    pipeline = [
        {"$sort": {"total_bookings": -1}},
        {"$limit": 5},
        {"$project": {"_id": 0, "customer_id": 1, "whatsapp_number": 1, "full_name": 1, "total_bookings": 1}},
    ]
    top = [doc async for doc in db[COLLECTION].aggregate(pipeline)]
    return {"total": total, "top_customers": top}


@router.get("/{customer_id}")
async def get_customer(customer_id: str):
    db = get_db()
    customer = await get_customer_by_id(db, customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer.model_dump(mode="json")
