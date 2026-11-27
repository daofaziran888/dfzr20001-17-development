"""业务路由汇总：所有模块共用一套资源路由，流水线与留样送检路由独立注册。"""
from __future__ import annotations

from app.routers import resource, pipeline_routes, sample_routes

ROUTERS = [resource, sample_routes, pipeline_routes]
