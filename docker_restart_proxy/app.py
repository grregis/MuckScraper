# docker_restart_proxy/app.py
#
# Minimal internal service that holds the Docker socket so the main admin
# app never has to. Exposes exactly three things: listing this compose
# project's containers (with start time and restart count), a bounded tail of
# one container's logs (read-only, used by the MCP server), and restarting
# one/all of them. Nothing else is routable here, regardless of what the
# socket itself would otherwise allow -- this is the whole point of not
# mounting the socket into the public-facing app container directly.

import os
import logging
import socket
import time

import docker
from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
client = docker.from_env()

SELF_SERVICE_NAME = "docker-restart-proxy"


def _detect_project_name():
    """The compose project this proxy belongs to.

    Compose names the project after the folder the repo was cloned into, so
    a fixed name would find no containers in a clone called anything else.
    Read it from this container's own labels (its hostname is its container
    id); COMPOSE_PROJECT_NAME in the environment overrides that.
    """
    explicit = os.environ.get("COMPOSE_PROJECT_NAME")
    if explicit:
        return explicit
    try:
        me = client.containers.get(socket.gethostname())
        name = me.labels.get("com.docker.compose.project")
        if name:
            return name
    except Exception:
        logger.warning("Could not read this container's compose project", exc_info=True)
    logger.warning("Assuming compose project 'muckscraper'")
    return "muckscraper"


PROJECT_NAME = _detect_project_name()
logger.info("Managing containers of compose project %r", PROJECT_NAME)

MAX_LOG_LINES = 5000
MAX_LOG_MINUTES = 7 * 24 * 60

# Preferred restart-all order: app is last because restarting it kills the
# in-flight request that triggered this call, so everything else should get
# a confirmed result first.
RESTART_ALL_ORDER = ["postgres", "scheduler", "app"]


def _project_containers():
    """This compose project's containers, excluding this proxy itself."""
    containers = client.containers.list(
        all=True,
        filters={"label": f"com.docker.compose.project={PROJECT_NAME}"},
    )
    result = []
    for c in containers:
        service = c.labels.get("com.docker.compose.service")
        if service == SELF_SERVICE_NAME:
            continue
        state = c.attrs.get("State", {})
        result.append({
            "name": c.name,
            "service": service,
            "status": c.status,
            "started_at": state.get("StartedAt"),
            "restart_count": c.attrs.get("RestartCount"),
            "health": (state.get("Health") or {}).get("Status"),
        })
    return result


def _find_container(identifier):
    """Look up a project container by service name or container name."""
    for c in client.containers.list(
        all=True, filters={"label": f"com.docker.compose.project={PROJECT_NAME}"}
    ):
        service = c.labels.get("com.docker.compose.service")
        if service == SELF_SERVICE_NAME:
            continue
        if identifier in (service, c.name):
            return c
    return None


@app.route("/containers", methods=["GET"])
def list_containers():
    return jsonify(_project_containers())


@app.route("/containers/<identifier>/logs", methods=["GET"])
def container_logs(identifier):
    """Bounded, read-only log tail: ?since_minutes=N (default 60) &tail=N
    (default 500). Both are capped so a caller can't pull unbounded output."""
    container = _find_container(identifier)
    if container is None:
        return jsonify({"error": f"Unknown container: {identifier}"}), 404
    try:
        minutes = min(max(int(request.args.get("since_minutes", 60)), 1), MAX_LOG_MINUTES)
        tail = min(max(int(request.args.get("tail", 500)), 1), MAX_LOG_LINES)
    except ValueError:
        return jsonify({"error": "since_minutes and tail must be integers"}), 400
    raw = container.logs(since=int(time.time()) - minutes * 60, tail=tail, timestamps=False)
    return jsonify({
        "name": container.name,
        "since_minutes": minutes,
        "lines": raw.decode("utf-8", errors="replace").splitlines(),
    })


@app.route("/containers/<identifier>/restart", methods=["POST"])
def restart_container(identifier):
    container = _find_container(identifier)
    if container is None:
        return jsonify({"error": f"Unknown container: {identifier}"}), 404
    logger.info("Restarting container %s (service=%s)", container.name, identifier)
    container.restart(timeout=30)
    return jsonify({"restarted": container.name})


@app.route("/restart-all", methods=["POST"])
def restart_all():
    containers = {c["service"]: c["name"] for c in _project_containers()}
    order = [s for s in RESTART_ALL_ORDER if s in containers]
    order += [s for s in containers if s not in order]

    results = []
    for service in order:
        container = _find_container(containers[service])
        try:
            container.restart(timeout=30)
            results.append({"service": service, "name": container.name, "restarted": True})
        except Exception as e:
            logger.exception("Failed to restart %s", service)
            results.append({"service": service, "name": container.name, "restarted": False, "error": str(e)})
    return jsonify({"results": results})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)
