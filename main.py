# File: main.py

import asyncio
import uuid
import random
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, WebSocket, HTTPException, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Any

# --- Basic Setup ---
app = FastAPI()

# --- CORS Configuration ---
# Allow requests from the frontend (adjust origin if your frontend is on a different port/domain)
origins = ["*"]  # For development, allow all. For production, restrict to your frontend's domain.

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- In-Memory "Database" ---
# This dictionary will store all our temporary inboxes.
# Structure:
# {
#   "email_id_1": {
#     "email_address": "random1@temp.com",
#     "messages": [{"sender": "...", "subject": "...", "body": "..."}],
#     "expires_at": datetime_object
#   },
#   ...
# }
inboxes: Dict[str, Dict[str, Any]] = {}

# This dictionary will manage active WebSocket connections.
# Structure: { "email_id": WebSocket_object }
active_connections: Dict[str, WebSocket] = {}

# --- Pydantic Models for Request Bodies ---
class EmailRequest(BaseModel):
    email_id: str

# --- Helper Functions & Background Tasks ---

async def cleanup_expired_inboxes():
    """
    A background task that runs periodically to remove expired inboxes.
    """
    while True:
        # Wait for 60 seconds before the next cleanup
        await asyncio.sleep(60)
        now = datetime.now(timezone.utc)
        
        # Find which inboxes have expired
        expired_ids = [
            email_id for email_id, data in inboxes.items() 
            if data["expires_at"] < now
        ]
        
        # Safely delete the expired inboxes and close any related connections
        for email_id in expired_ids:
            print(f"INFO: Deleting expired inbox for {inboxes[email_id]['email_address']}")
            if email_id in inboxes:
                del inboxes[email_id]
            if email_id in active_connections:
                # We can't guarantee a clean close, but we can remove it
                del active_connections[email_id]


async def simulate_email_reception():
    """
    A background task that simulates receiving new emails for active addresses.
    """
    while True:
        # Wait for a random interval (e.g., 10-25 seconds)
        await asyncio.sleep(random.randint(10, 25))

        if not inboxes:
            continue  # No active inboxes to send emails to

        # Pick a random active inbox
        target_email_id = random.choice(list(inboxes.keys()))
        inbox_data = inboxes[target_email_id]
        
        # Create a new fake email
        new_email = {
            "id": str(uuid.uuid4()),
            "sender": f"service-{random.randint(100,999)}@example.com",
            "subject": f"Important Notification #{random.randint(1000, 9999)}",
            "body": f"This is a simulated email body for {inbox_data['email_address']}. \n\nTimestamp: {datetime.now(timezone.utc).isoformat()}.\n\nLorem ipsum dolor sit amet, consectetur adipiscing elit. Sed non risus."
        }
        
        # Add the email to the in-memory inbox
        inbox_data["messages"].append(new_email)
        print(f"INFO: New email for {inbox_data['email_address']}")

        # If there's an active WebSocket connection, send the email in real-time
        if target_email_id in active_connections:
            connection = active_connections[target_email_id]
            try:
                await connection.send_json(new_email)
                print(f"INFO: Sent email via WebSocket to client for {inbox_data['email_address']}")
            except WebSocketDisconnect:
                # The client might have disconnected without a clean close
                del active_connections[target_email_id]


# --- FastAPI Startup Event ---
@app.on_event("startup")
async def startup_event():
    """
    On server startup, create the background tasks.
    """
    asyncio.create_task(cleanup_expired_inboxes())
    asyncio.create_task(simulate_email_reception())
    print("INFO:     Server startup complete. Background tasks running.")

# --- API Endpoints ---

@app.get("/api/new_email")
async def generate_new_email():
    """
    Generates a new, unique, temporary email address and a session ID.
    """
    # Generate unique identifiers
    email_id = str(uuid.uuid4())
    random_prefix = uuid.uuid4().hex[:8]
    email_address = f"{random_prefix}@temp-inbox.com"

    # Ensure no duplicates (highly unlikely, but good practice)
    while any(d['email_address'] == email_address for d in inboxes.values()):
        random_prefix = uuid.uuid4().hex[:8]
        email_address = f"{random_prefix}@temp-inbox.com"
        
    # Set expiration for one hour from now
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    
    # Store the new inbox
    inboxes[email_id] = {
        "email_address": email_address,
        "messages": [],
        "expires_at": expires_at
    }
    
    print(f"INFO: Created new email: {email_address} (ID: {email_id})")
    
    return {"email_id": email_id, "email_address": email_address}


@app.get("/api/check_email/{email_id}")
async def check_email_inbox(email_id: str):
    """
    Returns all emails for a given email_id. Fallback for WebSockets.
    """
    if email_id not in inboxes:
        raise HTTPException(status_code=404, detail="Email ID not found or has expired.")
    
    return {"inbox": inboxes[email_id]["messages"]}


@app.post("/api/delete_email")
async def delete_email(request: EmailRequest):
    """
    Deletes a temporary email account and its contents.
    """
    email_id = request.email_id
    if email_id not in inboxes:
        raise HTTPException(status_code=404, detail="Email ID not found or has already been deleted.")
        
    email_address = inboxes[email_id]['email_address']
    del inboxes[email_id]
    
    # If a websocket is open for this ID, close it
    if email_id in active_connections:
        connection = active_connections.pop(email_id)
        try:
            await connection.close()
        except RuntimeError:
            # Connection might already be closed
            pass
            
    print(f"INFO: Deleted email: {email_address} (ID: {email_id})")
    return {"message": f"Email address {email_address} and its inbox have been deleted."}


@app.post("/api/refresh_email")
async def refresh_email_timer(request: EmailRequest):
    """
    Resets the one-hour expiration timer for a given email_id.
    """
    email_id = request.email_id
    if email_id not in inboxes:
        raise HTTPException(status_code=404, detail="Email ID not found or has expired.")
        
    inboxes[email_id]["expires_at"] = datetime.now(timezone.utc) + timedelta(hours=1)
    email_address = inboxes[email_id]['email_address']
    
    print(f"INFO: Refreshed timer for: {email_address} (ID: {email_id})")
    return {"message": f"Timer for {email_address} has been reset to one hour."}


# --- WebSocket Endpoint ---

@app.websocket("/ws/inbox/{email_id}")
async def websocket_endpoint(websocket: WebSocket, email_id: str):
    """
    Handles the WebSocket connection for real-time email notifications.
    """
    if email_id not in inboxes:
        await websocket.close(code=1008) # Policy Violation
        return

    await websocket.accept()
    active_connections[email_id] = websocket
    print(f"INFO: WebSocket connected for email ID: {email_id}")

    try:
        # Keep the connection alive
        while True:
            # We don't need to receive anything, just wait for the connection to close.
            # The server will push messages.
            await websocket.receive_text()
    except WebSocketDisconnect:
        print(f"INFO: WebSocket disconnected for email ID: {email_id}")
    finally:
        # Clean up the connection from our dictionary
        if email_id in active_connections:
            del active_connections[email_id]

# To run this app: uvicorn main:app --reload
