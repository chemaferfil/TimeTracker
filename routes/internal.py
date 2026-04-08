from flask import Blueprint, request, jsonify, current_app
import os

from tasks.scheduler import auto_close_open_records


internal_bp = Blueprint("internal", __name__, url_prefix="/internal")


@internal_bp.route("/auto_close", methods=["POST"])
def internal_auto_close():
    """
    Secure internal endpoint to auto-close stale open records.
    Intended for GitHub Actions / external scheduler.

    Requires header: X-AUTO-CLOSE-TOKEN matching env AUTO_CLOSE_TOKEN.
    """
    expected = os.getenv("AUTO_CLOSE_TOKEN")
    provided = request.headers.get("X-AUTO-CLOSE-TOKEN")
    if not expected or provided != expected:
        current_app.logger.warning("Unauthorized internal auto_close attempt")
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    try:
        closed = auto_close_open_records(include_today=False)
        return jsonify({"ok": True, "closed": closed}), 200
    except Exception as e:
        current_app.logger.error(f"internal_auto_close error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

