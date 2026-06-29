from fastapi import APIRouter

api_router = APIRouter()

# Placeholder routers for major namespaces
auth_router = APIRouter(prefix="/auth", tags=["Auth"])
reports_router = APIRouter(prefix="/reports", tags=["Reports"])
reviews_router = APIRouter(prefix="/reviews", tags=["Reviews"])
comments_router = APIRouter(prefix="/comments", tags=["Comments"])
workflows_router = APIRouter(prefix="/workflows", tags=["Workflows"])
publish_router = APIRouter(prefix="/publish", tags=["Publishing"])
users_router = APIRouter(prefix="/users", tags=["Users"])

# Helper to create placeholder endpoint
def not_implemented():
    return {"message": "Not Implemented"}

# Add placeholder endpoints
@auth_router.post("/login")
def login(): return not_implemented()

@reports_router.get("/")
def get_reports(): return not_implemented()

@reviews_router.get("/")
def get_reviews(): return not_implemented()

@comments_router.get("/")
def get_comments(): return not_implemented()

@workflows_router.get("/")
def get_workflows(): return not_implemented()

@publish_router.post("/")
def publish_report(): return not_implemented()

@users_router.get("/me")
def get_me(): return not_implemented()

# Include sub-routers in main API router
api_router.include_router(auth_router)
api_router.include_router(reports_router)
api_router.include_router(reviews_router)
api_router.include_router(comments_router)
api_router.include_router(workflows_router)
api_router.include_router(publish_router)
api_router.include_router(users_router)
