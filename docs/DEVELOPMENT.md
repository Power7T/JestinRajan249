# Development Guide

Code structure, conventions, and development workflow for HostAI.

## Project Structure

```
hostai/
├── web/                          # FastAPI application
│   ├── app.py                    # Main application (7000+ lines, router definitions)
│   ├── models.py                 # SQLAlchemy database models
│   ├── schemas.py                # Pydantic request/response schemas
│   ├── database.py               # Database connection and session
│   ├── config.py                 # Configuration and environment variables
│   ├── rate_limiter.py           # Rate limiting utilities
│   ├── feature_flags.py          # Feature flag management
│   │
│   ├── integrations/             # Third-party integrations
│   │   ├── voice.py              # Twilio, OpenRouter, Deepgram, ElevenLabs
│   │   ├── email.py              # Email (IMAP, SMTP)
│   │   ├── whatsapp.py           # Meta Cloud API
│   │   ├── sms.py                # Twilio SMS
│   │   ├── stripe.py             # Stripe billing
│   │   └── storage.py            # Cloudflare R2
│   │
│   ├── services/                 # Business logic
│   │   ├── guest_contact_service.py
│   │   ├── reservation_service.py
│   │   ├── message_service.py
│   │   ├── voice_service.py
│   │   ├── billing_service.py
│   │   └── analytics_service.py
│   │
│   ├── middleware/               # Request/response middleware
│   │   ├── auth.py               # Authentication
│   │   ├── tenant.py             # Multi-tenant isolation
│   │   ├── cors.py               # CORS
│   │   └── error_handler.py      # Error handling
│   │
│   ├── calendar_worker.py        # Background iCal sync
│   ├── worker_manager.py         # Embedded worker lifecycle + leader locks
│   ├── alembic/                  # Database migrations
│   │   ├── env.py
│   │   ├── script.py.mako
│   │   └── versions/
│   │       ├── 001_initial.py
│   │       ├── 002_add_voice_calls.py
│   │       └── ...
│   │
│   ├── templates/                # Server-rendered Jinja pages
│   ├── static/                   # Static assets served by FastAPI
│   └── integrations/             # Channel / provider integrations
│
├── tests/                        # Unit + integration tests
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_page_health.py
│   └── ...
│
├── docs/                         # Documentation (this folder)
│   ├── README.md
│   ├── SETUP.md
│   ├── DEPLOYMENT.md
│   ├── API.md
│   ├── ADMIN.md
│   ├── SECURITY.md
│   ├── DEVELOPMENT.md (this file)
│   ├── FEATURES.md
│   ├── VOICE_AI.md
│   ├── PRICING.md
│   └── ...
│
├── docker-compose.dev.yml        # Local development
├── docker-compose.prod.yml       # Production
├── Dockerfile                    # Web service
├── entrypoint.sh                 # Web startup + migrations
├── requirements.txt              # Python dependencies
├── .env.example                  # Environment template
└── .gitignore
```

## Code Conventions

### Python Style

**Follow PEP 8 with these preferences:**

```python
# Line length: 100 characters
# Indentation: 4 spaces
# Imports: alphabetical within groups

# ✅ Good
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from web.models import Guest, Message
from web.schemas import GuestCreate, MessageResponse
from web.services import guest_service
from web.integrations.voice import VoiceAI

# Variables: snake_case
guest_name = "John Doe"
max_retries = 3

# Constants: UPPER_CASE
DEFAULT_TIMEOUT = 30
MAX_MESSAGE_LENGTH = 1000

# Classes: PascalCase
class GuestContactService:
    pass

# Functions: snake_case
def handle_incoming_call():
    pass
```

### Database Models

**Define in `web/models.py` only (centralized):**

```python
from sqlalchemy import Column, String, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from datetime import datetime
from enum import Enum as PyEnum

class Guest(Base):
    __tablename__ = "guests"

    id = Column(UUID, primary_key=True, default=uuid4)
    tenant_id = Column(UUID, ForeignKey("tenants.id"), nullable=False)
    name = Column(String(255), nullable=False)
    phone = Column(String(20), nullable=True)  # Encrypted
    email = Column(String(255), nullable=True)  # Encrypted
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    tenant = relationship("Tenant", back_populates="guests")
    messages = relationship("Message", back_populates="guest")

    # Indexes
    __table_args__ = (
        Index("idx_guest_tenant_phone", "tenant_id", "phone"),
    )
```

**Never duplicate models** — import from models.py.

### API Schemas

**Define request/response schemas in `web/schemas.py`:**

```python
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime

class GuestCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    phone: Optional[str] = Field(None, regex=r'^\+?1?\d{9,15}$')
    email: Optional[EmailStr] = None

class GuestResponse(BaseModel):
    id: UUID
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True  # Allow ORM model conversion
```

### API Routes

**Organize in `web/app.py` or separate router files:**

```python
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from web.models import Guest
from web.schemas import GuestCreate, GuestResponse
from web.middleware.auth import get_current_user
from web.database import get_db

router = APIRouter(prefix="/guests", tags=["guests"])

@router.get("/", response_model=List[GuestResponse])
async def list_guests(
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all guests for current tenant."""
    guests = db.query(Guest).filter(
        Guest.tenant_id == current_user.tenant_id
    ).limit(limit).offset(offset).all()
    return guests

@router.post("/", response_model=GuestResponse, status_code=201)
async def create_guest(
    guest_in: GuestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new guest."""
    # Validation
    if not guest_in.name.strip():
        raise HTTPException(400, "Guest name required")

    # Create and save
    guest = Guest(
        tenant_id=current_user.tenant_id,
        name=guest_in.name,
        phone=guest_in.phone,
        email=guest_in.email,
    )
    db.add(guest)
    db.commit()
    db.refresh(guest)
    return guest
```

### Error Handling

```python
from fastapi import HTTPException, status

# ✅ Correct error responses
if not guest:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Guest not found",
    )

if not current_user.is_admin:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Insufficient permissions",
    )

# ✅ Validation errors handled by Pydantic automatically
# ✅ Database errors caught by middleware and logged
```

### Type Hints

Always use type hints:

```python
# ✅ Good
def process_call(call_id: str, duration: int) -> dict:
    return {"success": True, "duration": duration}

async def send_message(
    recipient: str,
    body: str,
    channel: MessageChannel,
    user: User = Depends(get_current_user),
) -> MessageResponse:
    # Implementation
    pass

# ✅ Complex types
from typing import Optional, List, Dict, Tuple

def analyze_calls(
    calls: List[VoiceCall],
    filters: Optional[Dict[str, str]] = None,
) -> Tuple[int, float]:
    # Returns (call_count, average_duration)
    pass
```

### Comments

- **No comments for obvious code** — Code should be self-documenting
- **Comments for "why"** — Explain non-obvious decisions
- **Docstrings for functions** — Especially public APIs

```python
# ❌ Bad - Comments state the obvious
x = x + 1  # Increment x

# ✅ Good - Comments explain "why"
# Increment retry count before checking threshold
# (matches Twilio's retry policy of 3 attempts)
retry_count += 1

def get_guest_by_phone(phone: str, db: Session) -> Optional[Guest]:
    """
    Find guest by phone number.

    Uses fallback chain: current guests → past guests → external API.
    Phone numbers are stored encrypted, so we search by hash.

    Args:
        phone: Guest phone number in E.164 format (+14155551234)
        db: Database session

    Returns:
        Guest if found, None otherwise
    """
    pass
```

## Database Migrations

### Create Migration

```bash
# Auto-generate migration from model changes
alembic revision --autogenerate -m "Add phone_number to guests"

# Review generated file in alembic/versions/
# File will be named: 001_add_phone_number_to_guests.py
```

### Migration File Template

```python
"""Add phone_number to guests."""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '001_add_phone'
down_revision = '000_initial'

def upgrade():
    """Add column and create index."""
    op.add_column('guests', sa.Column('phone', sa.String(20), nullable=True))
    op.create_index('idx_guest_phone', 'guests', ['phone'])

def downgrade():
    """Rollback changes."""
    op.drop_index('idx_guest_phone')
    op.drop_column('guests', 'phone')
```

### Run Migrations

```bash
# Apply pending migrations
alembic upgrade head

# Test rollback
alembic downgrade -1
alembic upgrade +1
```

## Testing

### Unit Tests

```python
# web/tests/test_guest_service.py
import pytest
from web.services.guest_service import GuestService
from web.models import Guest

@pytest.fixture
def service(db_session):
    """Create service instance."""
    return GuestService(db_session)

def test_create_guest(service):
    """Test creating a guest."""
    guest = service.create(
        tenant_id="123",
        name="John Doe",
        phone="+14155551234",
    )
    assert guest.name == "John Doe"
    assert guest.phone == "+14155551234"

def test_create_guest_invalid_name(service):
    """Test that empty name raises error."""
    with pytest.raises(ValueError):
        service.create(
            tenant_id="123",
            name="",  # Invalid
            phone="+14155551234",
        )

def test_get_guest_not_found(service):
    """Test getting non-existent guest."""
    guest = service.get("123", "nonexistent_id")
    assert guest is None
```

### Integration Tests

```python
# web/tests/test_api_guests.py
from fastapi.testclient import TestClient
from web.app import app

client = TestClient(app)

def test_list_guests_requires_auth():
    """Test that endpoint requires authentication."""
    response = client.get("/api/guests")
    assert response.status_code == 401

def test_list_guests(authenticated_client):
    """Test listing guests."""
    response = authenticated_client.get("/api/guests")
    assert response.status_code == 200
    assert len(response.json()["data"]) >= 0

def test_create_guest(authenticated_client):
    """Test creating guest via API."""
    response = authenticated_client.post(
        "/api/guests",
        json={
            "name": "Jane Doe",
            "phone": "+14155555678",
        }
    )
    assert response.status_code == 201
    assert response.json()["name"] == "Jane Doe"
```

### Run Tests

```bash
# Run all tests
pytest web/tests/ -v

# Run tests for specific file
pytest web/tests/test_voice.py -v

# Run tests matching pattern
pytest web/tests/ -k "guest" -v

# Run with coverage report
pytest web/tests/ --cov=web --cov-report=html

# Run in parallel
pytest web/tests/ -n auto
```

## Logging

### Log Levels

```python
import logging

logger = logging.getLogger(__name__)

# DEBUG - Detailed information for debugging
logger.debug(f"Processing call {call_id}")

# INFO - General informational messages
logger.info(f"Guest {guest_name} created successfully")

# WARNING - Warning messages (potential issues)
logger.warning(f"High latency detected: {latency}ms")

# ERROR - Error messages (something went wrong)
logger.error(f"Failed to process call: {exception}")

# CRITICAL - Critical issues (system failure)
logger.critical("Database connection lost")
```

### Structured Logging

```python
# Include context for better debugging
logger.info(
    "Voice call completed",
    extra={
        "call_id": call.id,
        "tenant_id": tenant.id,
        "duration": call.duration_seconds,
        "sentiment": call.sentiment,
        "cost": call.cost,
    }
)
```

## Background Jobs

### Define Job

```python
# worker/tasks/email_tasks.py
from rq import get_current_job
import logging

logger = logging.getLogger(__name__)

def send_email_reminder(tenant_id: str, invoice_id: str):
    """Send email reminder to customer."""
    job = get_current_job()

    # Job context
    job.meta['status'] = 'processing'
    job.save_meta()

    try:
        # Your logic here
        logger.info(f"Sent email reminder for invoice {invoice_id}")
        job.meta['status'] = 'completed'
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        job.meta['status'] = 'failed'
        raise
    finally:
        job.save_meta()
```

### Queue Job

```python
# In API or service
from rq import Queue
from redis import Redis

redis_conn = Redis()
q = Queue(connection=redis_conn)

# Queue job for immediate execution
job = q.enqueue(
    "worker.tasks.email_tasks.send_email_reminder",
    tenant_id="123",
    invoice_id="456",
)

# Queue job for later execution
job = q.enqueue_at(
    datetime.now() + timedelta(hours=1),
    "worker.tasks.email_tasks.send_email_reminder",
    tenant_id="123",
    invoice_id="456",
)
```

## Performance Optimization

### Database Queries

```python
# ❌ Bad - N+1 queries
guests = db.query(Guest).all()
for guest in guests:
    print(guest.messages)  # Query for each guest

# ✅ Good - Join query
from sqlalchemy.orm import joinedload

guests = db.query(Guest).options(
    joinedload(Guest.messages)
).all()

# ✅ Good - Eager loading
guests = db.query(Guest).with_entities(
    Guest.id, Guest.name, func.count(Message.id).label('message_count')
).join(Message).group_by(Guest.id).all()
```

### Caching

```python
from redis import Redis

redis = Redis()

# Cache result for 1 hour
def get_guest_stats(guest_id: str) -> dict:
    cache_key = f"guest_stats:{guest_id}"

    # Try cache first
    cached = redis.get(cache_key)
    if cached:
        return json.loads(cached)

    # Compute if not cached
    stats = expensive_calculation(guest_id)

    # Store in cache
    redis.setex(
        cache_key,
        3600,  # 1 hour
        json.dumps(stats),
    )

    return stats
```

## Common Patterns

### Multi-Tenant Isolation

```python
# Every query should filter by tenant_id
def get_guest(guest_id: str, current_user: User, db: Session) -> Guest:
    """Get guest, ensuring tenant isolation."""
    guest = db.query(Guest).filter(
        Guest.id == guest_id,
        Guest.tenant_id == current_user.tenant_id,  # IMPORTANT
    ).first()

    if not guest:
        raise HTTPException(404, "Guest not found")

    return guest
```

### Pagination

```python
from fastapi import Query

@router.get("/guests")
def list_guests(
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List guests with pagination."""
    # Count total
    total = db.query(Guest).filter(
        Guest.tenant_id == current_user.tenant_id
    ).count()

    # Get page
    guests = db.query(Guest).filter(
        Guest.tenant_id == current_user.tenant_id
    ).limit(limit).offset(offset).all()

    return {
        "data": guests,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
```

### Soft Deletes

```python
# Instead of DELETE, use status field
class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID, primary_key=True)
    # ... other fields ...
    deleted_at = Column(DateTime, nullable=True)

# Query active records only
active_messages = db.query(Message).filter(
    Message.deleted_at == None
).all()

# Soft delete
message.deleted_at = datetime.utcnow()
db.commit()
```

## Development Workflow

### 1. Create Feature Branch

```bash
git checkout -b feature/add-guest-import
```

### 2. Make Changes

- Write code following conventions above
- Add tests for new functionality
- Update documentation if needed

### 3. Test Locally

```bash
# Run affected tests
pytest web/tests/test_guest*.py -v

# Format code
black web/

# Lint
pylint web/guest*

# Type check
mypy web/models.py web/services/guest_service.py
```

### 4. Create Migration (if needed)

```bash
alembic revision --autogenerate -m "Add import_source to guests"
```

### 5. Commit

```bash
git add web/ alembic/versions/
git commit -m "feat: Add guest import from CSV"
```

### 6. Push and Create PR

```bash
git push origin feature/add-guest-import
# Create pull request in GitHub
```

### 7. Code Review

- Address review comments
- Update tests if needed
- Re-push updated code

### 8. Merge

Merge after approval and tests pass.

## Debugging

### Debug API Request

```python
@app.middleware("http")
async def debug_middleware(request: Request, call_next):
    """Log all requests in debug mode."""
    if DEBUG:
        print(f"{request.method} {request.url.path}")
        print(f"Body: {await request.body()}")

    response = await call_next(request)

    if DEBUG:
        print(f"Status: {response.status_code}")

    return response
```

### Print Debugging

```python
# In FastAPI, use print() for temporary debugging
# It shows in Docker logs

print(f"DEBUG: guest_id={guest_id}, tenant_id={tenant_id}")
```

### Database Debugging

```bash
# Access database and run queries
docker compose -f docker-compose.dev.yml exec postgres psql -U hostai -d hostai_db

# Useful queries
SELECT * FROM guests LIMIT 10;
SELECT COUNT(*) FROM voice_calls WHERE created_at > NOW() - INTERVAL 1 day;
EXPLAIN ANALYZE SELECT * FROM guests WHERE tenant_id = '123';
```

## IDE Setup

### VS Code

**Extensions:**
- Python (Microsoft)
- Pylance (type checking)
- Pytest (test runner)
- SQLTools (database client)

**.vscode/settings.json:**
```json
{
    "python.linting.enabled": true,
    "python.linting.pylintEnabled": true,
    "python.formatting.provider": "black",
    "[python]": {
        "editor.formatOnSave": true,
        "editor.defaultFormatter": "ms-python.python"
    }
}
```

### PyCharm

- Built-in Python support
- Database client included
- Test runner integrated

**Settings:**
- Code style → Python → Line length: 100
- Tools → Python Integrated Tools → Test runner: pytest

## Performance Profiling

### Profile Function

```python
from cProfile import Profile
from pstats import Stats

def profile_function():
    """Profile expensive function."""
    pr = Profile()
    pr.enable()

    # Your code here
    result = expensive_operation()

    pr.disable()
    stats = Stats(pr)
    stats.sort_stats('cumulative')
    stats.print_stats(10)  # Top 10

    return result
```

### Profile API Endpoint

```python
import time

@router.get("/debug/profile")
async def profile_endpoint():
    """Test endpoint to profile."""
    start = time.time()

    # Simulate work
    guests = db.query(Guest).limit(1000).all()

    elapsed = time.time() - start
    return {
        "time_ms": elapsed * 1000,
        "count": len(guests),
    }
```

## Documentation

- Docstrings for all public functions
- README.md in each module
- API endpoints documented in API.md
- Complex logic explained in comments

## Support

- Read through existing code patterns
- Check git history: `git log --oneline web/services/`
- Ask in team discussions
- Refer to `/docs` for comprehensive guides

See **[SETUP.md](SETUP.md)** for local development setup.
See **[API.md](API.md)** for REST API documentation.
