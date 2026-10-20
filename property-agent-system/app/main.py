"""FastAPI 入口：REST API + 静态看板。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agents.coordinator import WorkOrderCoordinator
from .api.community import router as community_router
from .api.parking import router as parking_router
from .config import (APP_NAME, APP_VERSION, COMMUNITY_MONITOR_ENABLED,
                     COMMUNITY_SCAN_INTERVAL_SECONDS, REPORT_DIR, STATIC_DIR)
from .repository import Repository
from .schemas import AcceptRequest, CompleteRequest, MonitorRequest, ReassignRequest, TicketCreate
from .services import analytics
from .services.community import CommunityService
from .services.parking import ParkingService


def _parse_now(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail="时间格式应为 YYYY-MM-DD HH:MM:SS")


def get_repo(request: Request) -> Repository:
    return request.app.state.repo


def get_coord(request: Request) -> WorkOrderCoordinator:
    return request.app.state.coordinator


def create_app(db_path: Optional[str] = None, load_history: bool = True,
               start_monitor: bool = True) -> FastAPI:
    repo = Repository(db_path, load_history=load_history)
    coordinator = WorkOrderCoordinator(repo)
    community = CommunityService(repo)
    parking = ParkingService(repo)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 历史 CSV 可能在应用启动后才生成
        if load_history:
            try:
                repo.ensure_history()
            except Exception:
                pass
        monitor_task = None

        async def monitor_loop():
            while True:
                try:
                    await asyncio.to_thread(app.state.community.scan)
                except Exception:
                    pass
                await asyncio.sleep(COMMUNITY_SCAN_INTERVAL_SECONDS)

        if start_monitor and COMMUNITY_MONITOR_ENABLED:
            monitor_task = asyncio.create_task(monitor_loop())
        yield
        if monitor_task:
            monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await monitor_task

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)
    app.state.repo = repo
    app.state.coordinator = coordinator
    app.state.community = community
    app.state.parking = parking
    app.state.load_history = load_history
    app.state.started_at = datetime.now()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(community_router)
    app.include_router(parking_router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/reports", StaticFiles(directory=str(REPORT_DIR)), name="reports")

    @app.exception_handler(KeyError)
    async def key_error_handler(request: Request, exc: KeyError):
        detail = exc.args[0] if exc.args else "资源不存在"
        return JSONResponse(status_code=404, content={"detail": str(detail)})

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # ---------------- 基础 ----------------
    @app.get("/api/health")
    def health(request: Request):
        repo: Repository = request.app.state.repo
        return {
            "status": "ok",
            "version": APP_VERSION,
            "app": APP_NAME,
            "db": str(repo.db_path or "data/property.db"),
            "history_orders": repo.history_count(),
            "workers": len(repo.list_workers()),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    # ---------------- 看板 ----------------
    @app.get("/api/dashboard/summary")
    def dashboard_summary(request: Request, days: int = Query(30, ge=7, le=180)):
        return analytics.dashboard_summary(
            request.app.state.repo, days=days, include_history=request.app.state.load_history
        )

    # ---------------- 工单 ----------------
    @app.get("/api/tickets")
    def list_tickets(
        request: Request,
        status: str = "all",
        urgency: Optional[str] = None,
        q: Optional[str] = None,
        worker_id: Optional[str] = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        status_filter = None if status in ("all", "", None) else status
        items, total = request.app.state.repo.list_tickets(
            status=status_filter, urgency=urgency, q=q, worker_id=worker_id, limit=limit, offset=offset
        )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @app.get("/api/tickets/{ticket_id}")
    def ticket_detail(request: Request, ticket_id: str):
        trace = request.app.state.repo.get_trace(ticket_id)
        if not trace:
            raise HTTPException(status_code=404, detail="工单不存在")
        return trace

    @app.post("/api/tickets", status_code=201)
    def create_ticket(request: Request, body: TicketCreate):
        trace = request.app.state.coordinator.submit(
            description=body.description,
            reporter_name=body.reporter_name,
            reporter_phone=body.reporter_phone,
            building=body.building,
            unit=body.unit,
            room=body.room,
        )
        return trace

    @app.post("/api/tickets/{ticket_id}/accept")
    def accept_ticket(request: Request, ticket_id: str, body: Optional[AcceptRequest] = None):
        worker_id = body.worker_id if body else None
        return request.app.state.coordinator.accept(ticket_id, worker_id=worker_id)

    @app.post("/api/tickets/{ticket_id}/complete")
    def complete_ticket(request: Request, ticket_id: str, body: CompleteRequest):
        return request.app.state.coordinator.complete(
            ticket_id, result_note=body.result_note, photos_count=body.photos_count
        )

    @app.post("/api/tickets/{ticket_id}/reassign")
    def reassign_ticket(request: Request, ticket_id: str, body: ReassignRequest):
        trace = request.app.state.coordinator.reassign(ticket_id, body.worker_id, body.reason)
        return {"ticket": trace.get("ticket"), "trace": trace}

    # ---------------- 维修工 / 预警 ----------------
    @app.get("/api/workers")
    def list_workers(request: Request):
        items = request.app.state.repo.list_workers(active_only=False)
        return {"items": items, "total": len(items)}

    @app.get("/api/warnings")
    def list_warnings(request: Request, status: Optional[str] = None, limit: int = Query(100, ge=1, le=500)):
        items = request.app.state.repo.list_warnings(status=status, limit=limit)
        return {"items": items, "total": len(items)}

    @app.post("/api/monitor/scan")
    def monitor_scan(request: Request, body: Optional[MonitorRequest] = None):
        now = _parse_now(body.now if body else None)
        return request.app.state.coordinator.scan(now=now)

    # ---------------- 决策日志 ----------------
    @app.get("/api/logs")
    def list_logs(
        request: Request,
        agent: Optional[str] = None,
        ticket_id: Optional[str] = None,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        items, total = request.app.state.repo.list_agent_logs(
            ticket_id=ticket_id, agent=agent, limit=limit, offset=offset
        )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    # ---------------- 离线分析 ----------------
    @app.get("/api/analytics/agent-evaluation")
    def agent_evaluation():
        return analytics.evaluation_detail()

    @app.get("/api/analytics/type-stats")
    def type_stats(request: Request):
        summary = analytics.dashboard_summary(
            request.app.state.repo, include_history=request.app.state.load_history
        )
        return {"items": summary.get("type_stats", []), "total": len(summary.get("type_stats", []))}

    @app.get("/api/analytics/prior")
    def analysis_prior():
        return analytics.priors()

    # ---------------- 完整演示 ----------------
    @app.post("/api/demo/run")
    def demo_run(request: Request):
        return request.app.state.coordinator.run_demo()

    @app.get("/api/demo/latest")
    def demo_latest(request: Request):
        trace = request.app.state.coordinator.latest_demo()
        if not trace:
            raise HTTPException(status_code=404, detail="尚未运行演示")
        return trace

    # ---------------- 静态页面 ----------------
    @app.get("/")
    def index():
        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return {"app": APP_NAME, "message": "前端尚未构建，请访问 /docs 查看 API"}

    @app.get("/favicon.ico")
    def favicon():
        return JSONResponse(status_code=204, content={})

    return app


app = create_app()
