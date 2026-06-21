"""
FastAPI 应用 —— 注册所有模拟端点。
"""

import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .scenarios import Scenario
from .handlers import (
    init_state,
    handle_login_page, handle_public_key, handle_login_submit,
    handle_identity_check, handle_login_failure_check,
    handle_clear_login_failure, handle_logout_prev,
    handle_xk_index, handle_display, handle_part_display,
    handle_check_cart, handle_check_conflict,
    handle_add_cart, handle_query_cart, handle_submit_cart,
    handle_query_selected, handle_drop_course,
)

log = logging.getLogger("mock_server")


def create_app(scenario: Scenario | None = None) -> FastAPI:
    """创建 FastAPI 应用（程序化启动用）"""
    if scenario is None:
        scenario = Scenario()
    init_state(scenario)

    app = FastAPI(title="微教务系统 Mock", version="1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])

    _register_routes(app, scenario)
    return app


def _register_routes(app: FastAPI, scenario: Scenario):
    """注册所有路由"""

    # ---- 登录 ----
    app.add_api_route(
        "/jwglxt/xtgl/login_slogin.html",
        handle_login_page, methods=["GET"])
    app.add_api_route(
        "/jwglxt/xtgl/login_getPublicKey.html",
        handle_public_key, methods=["GET"])
    app.add_api_route(
        "/jwglxt/xtgl/login_slogin.html",
        handle_login_submit, methods=["POST"])

    # ---- 登录前置检查 ----
    app.add_api_route(
        "/jwglxt/xtgl/yhgl_cxXxqrCheck.html",
        handle_identity_check, methods=["POST"])
    app.add_api_route(
        "/jwglxt/xtgl/login_cxDlxgxx.html",
        handle_login_failure_check, methods=["POST"])
    app.add_api_route(
        "/jwglxt/xtgl/login_cxUpdateDlsbcs.html",
        handle_clear_login_failure, methods=["POST"])
    app.add_api_route(
        "/jwglxt/xtgl/login_logoutAccount.html",
        handle_logout_prev, methods=["POST"])

    # ---- 选课页面 ----
    app.add_api_route(
        "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbIndex.html",
        handle_xk_index, methods=["GET"])
    app.add_api_route(
        "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbDisplay.html",
        handle_display, methods=["POST"])
    app.add_api_route(
        "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbPartDisplay.html",
        handle_part_display, methods=["POST"])

    # ---- 选课操作 ----
    app.add_api_route(
        "/jwglxt/xsxk/zzxkyzb_cxCheckZyZzxkYzbInCart.html",
        handle_check_cart, methods=["POST"])
    app.add_api_route(
        "/jwglxt/xsxk/zzxkyzb_cxCtKcZyZzxkYzb.html",
        handle_check_conflict, methods=["POST"])
    app.add_api_route(
        "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzbToCart.html",
        handle_add_cart, methods=["POST"])
    app.add_api_route(
        "/jwglxt/xsxk/zzxkyzb_cxWdgwcZzxkYzb.html",
        handle_query_cart, methods=["POST"])
    app.add_api_route(
        "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzbFromCart.html",
        handle_submit_cart, methods=["POST"])
    app.add_api_route(
        "/jwglxt/xsxk/zzxkyzbjk_xkBcZyZzxkYzb.html",
        handle_drop_course, methods=["POST"])
    app.add_api_route(
        "/jwglxt/xsxk/zzxkyzb_cxZzxkYzbChoosed.html",
        handle_query_selected, methods=["POST"])

    log.info(f"Mock server 就绪: {len(app.routes)} 条路由, "
             f"场景: window={'开' if scenario.window_open else '关'}")
