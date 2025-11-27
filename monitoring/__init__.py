"""Minimal monitoring stub - expand as needed"""
import logging

logger = logging.getLogger(__name__)

def start_metrics_server():
    logger.info("Metrics server disabled (stub)")

def set_pending_campaigns(count: int):
    pass

def track_message(msg_type: str):
    pass

def track_api_request(api: str, status: str):
    pass
