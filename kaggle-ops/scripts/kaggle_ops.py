import argparse
import builtins
import contextlib
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from pathlib import PurePosixPath

_original_open = builtins.open
CODE_LIMIT = 1048576
LOG_FILE_LIMIT = 16 * 1048576


def configure():
    """Use a portable writable directory, without persisting authentication."""
    directory = os.environ.get("KAGGLE_CONFIG_DIR")
    if not directory:
        directory = str(Path(tempfile.gettempdir()) / "kaggle-ops-config")
        os.environ["KAGGLE_CONFIG_DIR"] = directory
    Path(directory).expanduser().mkdir(parents=True, exist_ok=True)
    return directory


def utf8_open(file, mode="r", *args, **kwargs):
    if "b" not in mode and len(args) < 2 and "encoding" not in kwargs:
        kwargs["encoding"] = "utf-8"
    return _original_open(file, mode, *args, **kwargs)


def redact(text):
    for key in ("KAGGLE_API_TOKEN", "KAGGLE_KEY"):
        value = os.environ.get(key)
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(redact(json.dumps(value, ensure_ascii=False, indent=2)), encoding="utf-8")
    temporary.replace(path)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def append_monitor_event(output, job, event, monitor_started, status=None, error_type=None):
    """Best-effort local audit trail; never changes wait's API or return behavior."""
    record = {
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ref": job.get("ref"),
        "version": job.get("version"),
        "source_signature": job.get("source_signature"),
        "monitor_elapsed_seconds": round(max(0.0, time.monotonic() - monitor_started), 3),
        "event": event,
    }
    if status is not None:
        record["status"] = status
    if error_type is not None:
        record["error_type"] = error_type
    try:
        path = Path(output) / "monitor-events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(redact(json.dumps(record, ensure_ascii=False, separators=(",", ":"))) + "\n")
    except Exception:
        pass


def kernel_ref(value):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+", value):
        raise ValueError("ref must be owner/slug")
    return value


def validate_notification_target(value):
    """Validate an explicit ChatGPT automation source; this does not register it."""
    if not isinstance(value, dict) or set(value) != {"provider", "repository", "snapshot_ref", "snapshot_path"}:
        raise ValueError("notification config must contain provider, repository, snapshot_ref, and snapshot_path")
    if value.get("provider") != "chatgpt":
        raise ValueError("notification provider must be chatgpt")
    repository = kernel_ref(value["repository"])
    snapshot_ref = value["snapshot_ref"]
    ref_parts = snapshot_ref.split("/") if isinstance(snapshot_ref, str) else []
    if (not isinstance(snapshot_ref, str) or not snapshot_ref or snapshot_ref.startswith(("-", "/")) or snapshot_ref.endswith((".", "/")) or
            snapshot_ref == "@" or ".." in snapshot_ref or "@{" in snapshot_ref or "//" in snapshot_ref or
            any(not part or part.startswith(".") or part.endswith(".lock") for part in ref_parts) or
            any(ord(c) < 32 or c.isspace() or c in "~^:?*[\\\\`" for c in snapshot_ref)):
        raise ValueError("snapshot_ref must be a valid simple Git ref")
    snapshot_path = value["snapshot_path"]
    if (not isinstance(snapshot_path, str) or not snapshot_path or "\\" in snapshot_path or
            any(ord(c) < 32 or c.isspace() or c == "`" for c in snapshot_path)):
        raise ValueError("snapshot_path must be repository-relative")
    path = PurePosixPath(snapshot_path)
    if path.is_absolute() or path.as_posix() != snapshot_path or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("snapshot_path must be repository-relative and cannot traverse directories")
    return {"provider": "chatgpt", "repository": repository, "snapshot_ref": snapshot_ref,
            "snapshot_path": snapshot_path}


def validate_job(job):
    kernel_ref(job["ref"])
    if type(job.get("version")) is not int or job["version"] < 1:
        raise ValueError("version must be a positive integer")
    return job


def api():
    if not os.environ.get("KAGGLE_API_TOKEN"):
        raise ValueError("KAGGLE_API_TOKEN is not configured")
    configure()
    if sys.platform == "win32":
        builtins.open = utf8_open
    import requests
    original_send = requests.sessions.Session.send
    def bounded_send(session, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (10, 30)
        return original_send(session, request, **kwargs)
    requests.sessions.Session.send = bounded_send
    os.environ["KAGGLE_ENABLE_OAUTH"] = "false"
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink):
        import kaggle
    return kaggle.api


def read_retry(call):
    import requests
    for attempt in range(3):
        try:
            return call()
        except requests.RequestException as error:
            status = getattr(error.response, "status_code", None)
            if attempt == 2 or (status is not None and status != 429 and status < 500):
                raise
            retry_after = error.response.headers.get("Retry-After", "") if error.response is not None else ""
            if retry_after.isdecimal() and int(retry_after) > 60:
                raise
            time.sleep(max(2 ** attempt, int(retry_after) if retry_after.isdecimal() else 0))


def quota(client):
    def get():
        with client.build_kaggle_client() as sdk:
            response = sdk.kernels.kernels_api_client.get_accelerator_quota_statistics()
        result = {"refresh_at": response.quota_refresh_time.isoformat() if response.quota_refresh_time else None}
        for label in ("gpu", "tpu"):
            q = getattr(response, label + "_quota")
            if q is None:
                result[label] = None
                continue
            fields = {name: getattr(q, name).total_seconds() if getattr(q, name) is not None else None for name in ("time_used", "time_reserved", "total_time_allowed")}
            fields["remaining_seconds"] = max(0, fields["total_time_allowed"] - fields["time_used"] - fields["time_reserved"]) if all(v is not None for v in fields.values()) else None
            result[label] = fields
        return result
    return read_retry(get)


def status(client, ref, version=None):
    kernel_ref(ref)
    if version is not None:
        validate_job({"ref": ref, "version": version})
    def get():
        from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionStatusRequest
        request = ApiGetKernelSessionStatusRequest()
        request.user_name, request.kernel_slug = ref.split("/")
        if version is not None:
            request.version_label = "v" + str(version)
        with client.build_kaggle_client() as sdk:
            response = sdk.kernels.kernels_api_client.get_kernel_session_status(request)
        return {"ref": ref, "version": version, "status": response.status.name.lower(), "failure_message": redact(response.failure_message or ""),
                "kernel_session_id": None,
                "session_id_unavailable_reason": "pinned Kaggle SDK exposes no session-id lookup for ref/version; status response has only status and failure_message"}
    return read_retry(get)


def _validate_public_view_url(response):
    """Reject redirects and any response whose final URL left Kaggle HTTPS."""
    from urllib.parse import urlsplit
    url = urlsplit(getattr(response, "url", ""))
    if url.scheme != "https" or url.hostname != "www.kaggle.com" or url.username or url.password:
        raise ValueError("public session lookup response URL was not same-host HTTPS")


def _public_kernel_view_model(client, ref, version):
    """Resolve a public notebook run through Kaggle's anonymous frontend read route."""
    import requests
    owner, slug = kernel_ref(ref).split("/")
    validate_job({"ref": ref, "version": version})
    root_url = "https://www.kaggle.com/code"
    session = requests.Session()
    class AnonymousAuth:
        def __call__(self, request):
            return request
    # Keep environment proxy settings, while preventing requests' implicit netrc auth.
    session.auth = AnonymousAuth()
    try:
        page = session.get(root_url, timeout=(10, 30), allow_redirects=False)
        _validate_public_view_url(page)
        if page.status_code != 200:
            raise RuntimeError(f"public session lookup page HTTP {page.status_code}")
        xsrf = session.cookies.get("XSRF-TOKEN")
        if not xsrf:
            raise RuntimeError("public session lookup XSRF cookie is unavailable")
        headers = {"X-XSRF-TOKEN": xsrf, "Referer": root_url}
        build_version = session.cookies.get("build-hash")
        if build_version:
            headers["X-Kaggle-Build-Version"] = build_version
        endpoint = "https://www.kaggle.com/api/i/kernels.LegacyKernelsService/GetKernelViewModel"
        payload = {"authorUserName": owner, "kernelSlug": slug,
                   "kernelVersionId": 0, "versionNumber": version}
        response = session.post(endpoint, json=payload, headers=headers,
                                timeout=(10, 30), allow_redirects=False)
        _validate_public_view_url(response)
        if response.status_code != 200:
            raise RuntimeError(f"public session lookup API HTTP {response.status_code}")
        body = response.json()
    finally:
        session.close()

    if not isinstance(body, dict):
        raise ValueError("public session lookup returned an invalid response")
    kernel = body.get("kernel")
    run = body.get("kernelRun")
    author = body.get("author")
    kernel_author = kernel.get("author") if isinstance(kernel, dict) else None
    if not isinstance(kernel, dict) or not isinstance(run, dict) or not isinstance(author, dict) or not isinstance(kernel_author, dict):
        raise ValueError("public session lookup omitted required identity fields")
    kernel_id = kernel.get("id")
    session_id = run.get("id")
    current_version = body.get("currentVersionNumber")
    run_kernel_id = run.get("kernelId")
    run_version = run.get("kernelVersionNumber")
    if (type(kernel_id) is not int or kernel_id <= 0 or type(session_id) is not int or session_id <= 0 or
            kernel.get("slug") != slug or kernel.get("url") != f"/code/{owner}/{slug}" or
            author.get("userName") != owner or kernel_author.get("userName") != owner or
            type(current_version) is not int or current_version != version or
            type(run_version) is not int or run_version != version or
            type(run_kernel_id) is not int or run_kernel_id != kernel_id):
        raise ValueError("public session lookup identity or version did not match the requested notebook")
    # Confirm the frontend kernel id is the same kernel id returned by the authenticated SDK.
    remote = get_kernel(client, ref)
    sdk_kernel_id = getattr(remote.metadata, "id", None)
    if type(sdk_kernel_id) is not int or sdk_kernel_id <= 0 or sdk_kernel_id != kernel_id:
        raise ValueError("public session lookup kernel id did not match the SDK kernel metadata")
    return {"ref": ref, "version": version, "kernel_session_id": session_id,
            "kernel_id": kernel_id, "current_version_number": current_version,
            "kernel_run_kernel_id": run_kernel_id, "kernel_run_version_number": run_version,
            "author_user_name": owner, "kernel_url": kernel["url"],
            "source": "public_get_kernel_view_model"}


def resolve_session_identity(client, ref, version, is_private=True):
    """Resolve only an exact public frontend binding; private jobs remain unsupported."""
    kernel_ref(ref)
    validate_job({"ref": ref, "version": version})
    if is_private is False:
        try:
            return read_retry(lambda: _public_kernel_view_model(client, ref, version))
        except Exception as error:
            return {"ref": ref, "version": version, "kernel_session_id": None,
                    "session_id_unavailable_reason": f"public lookup failed: {type(error).__name__}"}
    if is_private is not True:
        raise ValueError("is_private must be a boolean")
    observed = status(client, ref, version)
    return {"ref": ref, "version": version,
            "kernel_session_id": observed.get("kernel_session_id"),
            "session_id_unavailable_reason": observed.get("session_id_unavailable_reason")}


def capture_session_identity(client, ref, version, is_private):
    """Capture an optional binding after accepted submission without undoing acceptance."""
    if is_private is True:
        return {"ref": ref, "version": version, "kernel_session_id": None,
                "session_id_unavailable_reason": "private notebook session ID lookup is unsupported"}
    try:
        return resolve_session_identity(client, ref, version, is_private=False)
    except Exception as error:
        return {"ref": ref, "version": version, "kernel_session_id": None,
                "session_id_unavailable_reason": f"session identity capture failed: {type(error).__name__}"}


def _cancel_cause(status_value, failure_message, attempts, ref=None, version=None, session_id=None):
    message = (failure_message or "").lower()
    if "max allowed execution duration" in message or "maximum allowed execution duration" in message:
        return "timeout", "Kaggle failure_message reports the maximum allowed execution duration"
    if status_value == "complete":
        return "completed", "the pinned version completed; cancellation was not confirmed"
    if status_value == "error":
        return "error", "the pinned version ended in error; cancellation was not confirmed"
    accepted = any(a.get("outcome") == "request_accepted" and
                   a.get("ref") == ref and a.get("version") == version and
                   a.get("kernel_session_id") == session_id and session_id is not None
                   for a in attempts)
    if status_value == "cancel_acknowledged" and accepted:
        return "user_requested", "a recorded API cancel request was accepted and this pinned version later acknowledged cancellation; attribution is not cryptographic"
    return "external_or_unknown", "status alone does not identify whether a user, UI action, or platform initiated cancellation"


@contextlib.contextmanager
def _cancel_job_lock(job_path, blocking=False):
    """Serialize cancellation attempts for one job across processes on POSIX and Windows."""
    lock_path = Path(job_path).with_name(Path(job_path).name + ".cancel.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
                msvcrt.locking(handle.fileno(), mode, 1)
            else:
                import fcntl
                flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                fcntl.flock(handle.fileno(), flags)
        except (OSError, BlockingIOError) as error:
            raise ValueError("another cancel command holds the job lock; inspect status after it exits") from error
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _reconcile_cancel_terminal(job_path, expected_ref, expected_version, current):
    """Persist terminal cancellation attribution from the latest serialized job state."""
    status_value = current.get("status")
    if status_value not in ("complete", "error", "cancel_acknowledged"):
        raise ValueError("cancel audit requires a terminal status")
    with _cancel_job_lock(job_path, blocking=True):
        job = validate_job(load(job_path))
        if job["ref"] != expected_ref or job["version"] != expected_version:
            raise ValueError("saved job ref/version changed while waiting; refusing to attach terminal status")
        if current.get("ref", expected_ref) != expected_ref or current.get("version", expected_version) != expected_version:
            raise ValueError("terminal status does not match the wait's pinned ref/version")
        attempts = job.get("cancel_attempts") or []
        if not attempts and status_value != "cancel_acknowledged":
            return None
        identity = job.get("session_identity") or {}
        session_id = identity.get("kernel_session_id")
        if session_id is not None and (identity.get("ref") != expected_ref or identity.get("version") != expected_version):
            raise ValueError("saved session binding does not match the wait's pinned ref/version")
        observed_session_id = current.get("kernel_session_id")
        if observed_session_id is not None and observed_session_id != session_id:
            raise ValueError("terminal status session identity conflicts with the saved binding")
        if identity.get("ref") != job["ref"] or identity.get("version") != job["version"]:
            session_id = None
        if type(session_id) is not int or session_id <= 0:
            session_id = None
        cause, evidence = _cancel_cause(status_value, current.get("failure_message", ""), attempts,
                                        expected_ref, expected_version, session_id)
        final = {"status": status_value, "failure_message": redact(current.get("failure_message", "") or ""),
                 "cause": cause, "cause_evidence": evidence, "observed_at": time.time()}
        job["cancel_final"] = final
        if status_value == "cancel_acknowledged":
            job["cancel_state"] = "complete"
        else:
            job["cancel_state"] = "terminal_without_cancellation"
        save(job_path, job)
        return final


def cancel(client, job_path, expected_ref, expected_version, reason):
    with _cancel_job_lock(job_path):
        return _cancel_locked(client, job_path, expected_ref, expected_version, reason)


def _cancel_locked(client, job_path, expected_ref, expected_version, reason):
    """Request cancellation only when this job has an SDK-resolved session ID binding."""
    job = load(job_path)
    validate_job(job)
    expected_ref = kernel_ref(expected_ref)
    if type(expected_version) is not int or expected_version < 1:
        raise ValueError("cancel version must be a positive integer")
    if job["ref"] != expected_ref or job["version"] != expected_version:
        raise ValueError("cancel ref/version does not match the saved job")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("cancel reason is required")
    identity = resolve_session_identity(client, expected_ref, expected_version, is_private=job.get("is_private", True))
    if identity.get("ref") != expected_ref or identity.get("version") != expected_version:
        raise ValueError("resolved session identity does not match the saved job")
    session_id = identity.get("kernel_session_id")
    saved_identity = job.get("session_identity") or {}
    saved_session_id = saved_identity.get("kernel_session_id")
    if saved_session_id is not None and (saved_identity.get("ref") != expected_ref or
                                         saved_identity.get("version") != expected_version or
                                         session_id != saved_session_id):
        raise ValueError("resolved session identity conflicts with the saved job binding")
    job["session_identity"] = identity
    attempts = job.setdefault("cancel_attempts", [])
    current = status(client, expected_ref, expected_version)
    if current.get("ref", expected_ref) != expected_ref or current.get("version", expected_version) != expected_version:
        raise ValueError("live status response does not match the pinned ref/version")
    live_session_id = current.get("kernel_session_id")
    if live_session_id is not None and (session_id is None or live_session_id != session_id):
        raise ValueError("live status session identity conflicts with the resolved binding")
    current_status = current["status"]
    current_message = current.get("failure_message", "")
    if current_status in ("complete", "error", "cancel_acknowledged"):
        cause, evidence = _cancel_cause(current_status, current_message, attempts, expected_ref, expected_version, session_id)
        job["cancel_final"] = {"status": current_status, "failure_message": redact(current_message), "cause": cause, "cause_evidence": evidence, "observed_at": time.time()}
        save(job_path, job)
        return {"request_outcome": "not_requested_terminal", **job["cancel_final"]}
    if current_status == "cancel_requested":
        cause, evidence = _cancel_cause(current_status, current_message, attempts, expected_ref, expected_version, session_id)
        job["cancel_final"] = {"status": current_status, "failure_message": redact(current_message), "cause": cause, "cause_evidence": "cancel_requested is pending and is not terminal; " + evidence, "observed_at": time.time()}
        save(job_path, job)
        return {"request_outcome": "not_requested_pending", **job["cancel_final"]}
    if attempts and attempts[-1].get("outcome") in ("request_unknown", "request_accepted"):
        raise ValueError("a prior cancel request is unresolved; observe status instead of resending")
    if not isinstance(session_id, int) or isinstance(session_id, bool) or session_id <= 0:
        attempt = {"outcome": "session_id_unavailable", "ref": expected_ref, "version": expected_version,
                   "reason": reason, "detail": identity.get("session_id_unavailable_reason"), "created_at": time.time()}
        attempts.append(attempt)
        job["cancel_state"] = "unavailable"
        save(job_path, job)
        return {"request_outcome": attempt["outcome"], "status": current_status, "session_id_unavailable_reason": attempt["detail"]}
    attempt = {"outcome": "request_unknown", "ref": expected_ref, "version": expected_version, "kernel_session_id": session_id,
               "reason": reason, "created_at": time.time()}
    attempts.append(attempt)
    job["cancel_state"] = "request_unknown"
    save(job_path, job)
    try:
        from kagglesdk.kernels.types.kernels_api_service import ApiCancelKernelSessionRequest
        request = ApiCancelKernelSessionRequest()
        request.kernel_session_id = session_id
        with client.build_kaggle_client() as sdk:
            response = sdk.kernels.kernels_api_client.cancel_kernel_session(request)
    except Exception as error:
        import requests
        if isinstance(error, requests.HTTPError) and getattr(error.response, "status_code", None) in (400, 401, 403, 404, 409, 422):
            attempt.update(outcome="request_rejected", error_type=type(error).__name__, http_status=error.response.status_code)
            job["cancel_state"] = "request_rejected"
            save(job_path, job)
            raise
        attempt.update(outcome="request_unknown", error_type=type(error).__name__)
        job["cancel_state"] = "request_unknown"
        save(job_path, job)
        raise
    error_message = redact(getattr(response, "error_message", "") or "")
    if error_message:
        attempt.update(outcome="request_rejected", error_message=error_message, responded_at=time.time())
        job["cancel_state"] = "request_rejected"
        save(job_path, job)
        return {"request_outcome": "request_rejected", "error_message": error_message}
    attempt.update(outcome="request_accepted", error_message="", responded_at=time.time())
    job["cancel_state"] = "request_accepted"
    save(job_path, job)
    try:
        final_status = status(client, expected_ref, expected_version)
    except Exception as error:
        attempt["status_observation"] = {"outcome": "unknown", "error_type": type(error).__name__, "observed_at": time.time()}
        job["cancel_state"] = "request_accepted_pending"
        save(job_path, job)
        raise
    if final_status.get("ref", expected_ref) != expected_ref or final_status.get("version", expected_version) != expected_version:
        attempt["status_observation"] = {"outcome": "mismatch", "observed_at": time.time()}
        job["cancel_state"] = "request_accepted_pending"
        save(job_path, job)
        raise ValueError("post-cancel status response does not match the pinned ref/version")
    final_session_id = final_status.get("kernel_session_id")
    if final_session_id is not None and final_session_id != session_id:
        attempt["status_observation"] = {"outcome": "session_mismatch", "observed_at": time.time()}
        job["cancel_state"] = "request_accepted_pending"
        save(job_path, job)
        raise ValueError("post-cancel status session identity conflicts with the resolved binding")
    cause, evidence = _cancel_cause(final_status["status"], final_status.get("failure_message", ""), attempts,
                                   expected_ref, expected_version, session_id)
    job["cancel_final"] = {"status": final_status["status"], "failure_message": redact(final_status.get("failure_message", "")),
                           "cause": cause, "cause_evidence": evidence, "observed_at": time.time()}
    if final_status["status"] == "cancel_acknowledged":
        job["cancel_state"] = "complete"
    elif final_status["status"] in ("complete", "error"):
        job["cancel_state"] = "terminal_without_cancellation"
    else:
        job["cancel_state"] = "request_accepted_pending"
    save(job_path, job)
    return {"request_outcome": "request_accepted", **job["cancel_final"]}


def get_kernel(client, ref):
    from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelRequest
    request = ApiGetKernelRequest()
    request.user_name, request.kernel_slug = kernel_ref(ref).split("/")
    def get():
        with client.build_kaggle_client() as sdk:
            return sdk.kernels.kernels_api_client.get_kernel(request)
    return read_retry(get)


def owned_kernel_absent(client, ref):
    """A 403 alone cannot distinguish a new notebook from a permission failure."""
    owner = getattr(client, "config_values", {}).get("username")
    if not owner:
        raise ValueError("authenticated Kaggle username is unavailable; check token authentication")
    if ref.split("/")[0] != owner:
        return False
    for page in range(1, 101):
        kernels = read_retry(lambda: client.kernels_list(mine=True, page_size=100, page=page))
        if any(kernel.ref == ref for kernel in kernels):
            return False
        if not kernels:
            return True
    raise ValueError("own notebook listing exceeded the preflight limit")


def latest_version(client, ref):
    import requests
    try:
        return get_kernel(client, ref).metadata.current_version_number
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            return 0
        if error.response is not None and error.response.status_code == 403 and owned_kernel_absent(client, ref):
            return 0
        raise


def source_signature(source, kernel_type):
    if kernel_type == "notebook":
        notebook = json.loads(source)
        cells = [(cell["cell_type"], "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"])
                 for cell in notebook["cells"]]
        source = json.dumps(cells, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def push_payload_bytes(code, kernel_type):
    """Match the source serialization used by pinned Kaggle 2.0.0."""
    text = code.read_text(encoding="utf-8")
    if kernel_type == "notebook":
        notebook = json.loads(text)
        for cell in notebook.get("cells", []):
            if cell.get("cell_type") == "code" and "outputs" in cell:
                cell["outputs"] = []
            if isinstance(cell.get("source"), list):
                cell["source"] = "".join(cell["source"])
        text = json.dumps(notebook)
    return len(text.encode("utf-8"))


def push_rejection(error):
    """Accept only explicit rejection of the actual SaveKernel POST."""
    import requests
    from urllib.parse import urlsplit
    response = getattr(error, "response", None)
    request = getattr(response, "request", None)
    status = getattr(response, "status_code", None)
    if not isinstance(error, requests.HTTPError) or status not in (400, 401, 403, 404, 413, 422) or request is None:
        return None
    url = urlsplit(request.url)
    path = url.path.lower()
    if url.scheme != "https" or request.method != "POST" or url.hostname not in ("api.kaggle.com", "www.kaggle.com"):
        return None
    if path not in ("/api/v1/kernels/push", "/v1/kernels/push"):
        return None
    return {"action": "SaveKernel", "http_status": status}


def verify_saved_version(client, job, version=None, ref=None, require_matching_ref=False):
    """Reconcile only a newer saved version containing exactly our code."""
    if not job.get("source_signature"):
        raise ValueError("job lacks a source signature; inspect the remote source before reconciling")
    ref = kernel_ref(ref or job["ref"])
    remote = get_kernel(client, ref)
    current = remote.metadata.current_version_number
    if current <= job.get("previous_version", 0) or (version is not None and current != version):
        raise ValueError("saved notebook version does not match the new submission")
    if require_matching_ref and getattr(remote.metadata, "ref", None) != ref:
        raise ValueError("saved notebook ref does not match the requested reconciliation ref")
    if remote.blob.kernel_type != job["kernel_type"]:
        raise ValueError("saved notebook type does not match")
    if source_signature(remote.blob.source, job["kernel_type"]) != job["source_signature"]:
        raise ValueError("saved notebook source does not match the submitted code")
    if remote.metadata.is_private is not job.get("is_private", True) or remote.metadata.enable_gpu != job["enable_gpu"]:
        raise ValueError("saved notebook privacy or GPU setting does not match")
    if getattr(remote.metadata, "enable_tpu", False) is not False:
        raise ValueError("saved notebook TPU setting does not match")
    if remote.metadata.enable_internet != job["enable_internet"]:
        raise ValueError("saved notebook Internet setting does not match")
    return current


def clean_upload(folder):
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError("upload folder does not exist")
    forbidden = {".git", ".env", ".kaggle", "kaggle.json", "access_token"}
    secrets = [value.encode() for key in ("KAGGLE_API_TOKEN", "KAGGLE_KEY") if (value := os.environ.get(key))]
    overlap = max((len(value) for value in secrets), default=1) - 1
    for path in folder.rglob("*"):
        if path.is_symlink() or path.name.lower() in forbidden or path.name.lower().startswith(".env."):
            raise ValueError("upload folder contains credentials, git data or symlinks")
        if secrets and path.is_file():
            with path.open("rb") as handle:
                tail = b""
                while chunk := handle.read(1048576):
                    content = tail + chunk
                    if any(secret in content for secret in secrets):
                        raise ValueError("upload file contains a configured credential")
                    tail = content[-overlap:] if overlap else b""
    return folder


def submit(client, folder, output, notification_target=None, allow_public=False):
    if type(allow_public) is not bool:
        raise ValueError("allow_public must be a boolean")
    notification = ({"target": validate_notification_target(notification_target), "state": "target_configured",
                     "delivery_status": "unknown"} if notification_target is not None else
                    {"target": {"provider": "chatgpt"}, "state": "not_configured", "delivery_status": "unknown"})
    folder = clean_upload(folder)
    metadata = load(folder / "kernel-metadata.json")
    ref = kernel_ref(metadata["id"])
    requested_private = metadata.get("is_private")
    if requested_private is not True and not (requested_private is False and allow_public):
        raise ValueError("private notebook is required unless --allow-public is explicitly set")
    if metadata.get("enable_tpu") is not False:
        raise ValueError("TPU must be disabled")
    code = (folder / metadata["code_file"]).resolve()
    if not code.is_relative_to(folder) or not code.is_file():
        raise ValueError("code_file must be inside the upload folder")
    title = metadata.get("title")
    if title is not None and (not isinstance(title, str) or len(title) < 5):
        raise ValueError("title must be at least five characters")
    payload = push_payload_bytes(code, metadata["kernel_type"])
    if payload >= CODE_LIMIT:
        raise ValueError(f"code payload is {payload} bytes (guard: under {CODE_LIMIT}); move large inputs to a dataset")
    params = load(folder / "training-params.json") if (folder / "training-params.json").exists() else {}
    seconds = params.get("timeout_seconds", 21600)
    if type(seconds) is not int or not 60 <= seconds <= 21600:
        raise ValueError("timeout_seconds must be 60..21600")
    budget = quota(client) if metadata.get("enable_gpu") else None
    if metadata.get("enable_gpu"):
        remaining = (budget.get("gpu") or {}).get("remaining_seconds")
        if remaining is None or remaining < seconds:
            raise ValueError("insufficient or unknown GPU quota")
    output = Path(output)
    job_path = output / "job.json"
    if job_path.exists():
        if load(job_path).get("state") != "not_submitted":
            raise ValueError("job.json already exists; resume instead of submitting again")
    previous_version = latest_version(client, ref)
    if previous_version and status(client, ref, previous_version)["status"] not in ("complete", "error", "cancel_acknowledged"):
        raise ValueError("an existing kernel version is active; resume it instead of submitting")
    job = {"ref": ref, "state": "submission_unknown", "previous_version": previous_version, "timeout_seconds": seconds, "quota_before": budget, "code_sha256": hashlib.sha256(code.read_bytes()).hexdigest(), "payload_bytes": payload, "created_at": time.time(),
           "kernel_type": metadata["kernel_type"], "source_signature": source_signature(code.read_text(encoding="utf-8"), metadata["kernel_type"]),
           "enable_gpu": bool(metadata.get("enable_gpu")), "enable_internet": metadata.get("enable_internet", True),
           "is_private": requested_private,
           "notification": notification}
    if job_path.exists():
        backup = output / f"job.not_submitted.{time.time_ns()}.json"
        job_path.replace(backup)
    output.mkdir(parents=True, exist_ok=True)
    with job_path.open("x", encoding="utf-8") as handle:
        json.dump(job, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        response = client.kernels_push(str(folder), timeout=str(seconds))
    except Exception as error:
        rejection = push_rejection(error)
        if rejection:
            job["push_rejection"] = rejection
            save(job_path, job)
            try:
                unchanged = latest_version(client, ref) == previous_version
            except Exception:
                unchanged = False
            if unchanged:
                job["state"] = "not_submitted"
                save(job_path, job)
        raise
    job["submission_response"] = {"ref": response.ref, "version": response.version_number,
                                  "error": redact(getattr(response, "error", ""))}
    save(job_path, job)
    returned_ref = kernel_ref(response.ref.removeprefix("/code/")) if response.ref else ref
    if returned_ref != ref:
        raise ValueError("submission response has an unexpected ref; inspect without resubmitting")
    version = response.version_number
    if getattr(response, "error", "") or not version:
        version = verify_saved_version(client, job)
        job["source_verified"] = True
    if version <= previous_version:
        raise ValueError("submission response lacks a newer version; inspect without resubmitting")
    job.update(version=version, state="submitted")
    job["session_identity"] = {"ref": ref, "version": version, "kernel_session_id": None,
                               "session_id_unavailable_reason": "session identity capture is pending"}
    save(job_path, job)
    job["session_identity"] = capture_session_identity(client, ref, version, requested_private)
    save(job_path, job)
    return job


def reconcile(client, job_path, version, expected_ref=None):
    job = load(job_path)
    if type(version) is not int or version < 1:
        raise ValueError("reconcile version must be a positive integer")
    if job.get("state") != "submission_unknown" or version <= job.get("previous_version", 0):
        raise ValueError("reconcile requires an unknown submission and a newer verified version")
    original_ref = kernel_ref(job["ref"])
    if expected_ref is not None:
        candidate_ref = kernel_ref(expected_ref)
        if type(job.get("previous_version")) is not int or job["previous_version"] != 0:
            raise ValueError("--ref reconciliation is limited to a first submission with previous_version 0")
        response = job.get("submission_response") or {}
        response_ref = response.get("ref")
        if isinstance(response_ref, str):
            response_ref = response_ref.removeprefix("/code/")
        if response_ref != candidate_ref:
            raise ValueError("--ref must exactly match the saved submission response ref")
        if response.get("version") != version or type(response.get("version")) is not int:
            raise ValueError("--ref version must exactly match the saved submission response")
        if response.get("error"):
            raise ValueError("submission response contains an error; --ref reconciliation is unsafe")
        if candidate_ref.split("/", 1)[0] != original_ref.split("/", 1)[0]:
            raise ValueError("--ref must keep the original notebook owner")
        config_values = getattr(client, "config_values", None)
        authenticated_owner = config_values.get("username") if isinstance(config_values, dict) else None
        if authenticated_owner and authenticated_owner != candidate_ref.split("/", 1)[0]:
            raise ValueError("authenticated Kaggle owner does not match the reconciliation ref")
        verify_saved_version(client, job, version, ref=candidate_ref, require_matching_ref=True)
        current = status(client, candidate_ref, version)
        if current.get("ref") != candidate_ref or current.get("version") != version:
            raise ValueError("status response does not match the requested reconciliation ref/version")
        job["requested_ref"] = original_ref
        job["reconciliation_ref_evidence"] = {"requested_ref": original_ref, "submission_response_ref": response_ref,
                                              "submission_response_version": response["version"],
                                              "verified_ref": candidate_ref, "verified_version": version,
                                              "verified_source_signature": job["source_signature"]}
        job["ref"] = candidate_ref
    else:
        verify_saved_version(client, job, version)
        current = status(client, original_ref, version)
    job.update(version=version, state="submitted", source_verified=True, reconciliation_status=current["status"])
    job["session_identity"] = {"ref": job["ref"], "version": version, "kernel_session_id": current.get("kernel_session_id"),
                               "session_id_unavailable_reason": current.get("session_id_unavailable_reason")}
    save(job_path, job)
    return job


def mark_not_accepted(client, job_path):
    job = load(job_path)
    if job.get("state") != "submission_unknown":
        raise ValueError("only an unknown submission can be marked as not accepted")
    rejection = job.get("push_rejection", {})
    if rejection.get("action") != "SaveKernel" or rejection.get("http_status") not in (400, 401, 403, 404, 413, 422):
        raise ValueError("no explicit push rejection recorded; unchanged version alone cannot prove non-acceptance")
    if latest_version(client, kernel_ref(job["ref"])) != job.get("previous_version", 0):
        raise ValueError("a newer version exists; reconcile it with --version instead")
    job.update(state="not_submitted", not_accepted_checked_at=time.time())
    save(job_path, job)
    return job


def log_slice(job, output, seconds=20):
    import requests
    validate_job(job)
    ref = kernel_ref(job["ref"])
    endpoint = f"https://api.kaggle.com/v1/kernels/logs/stream/{ref}"
    started = time.monotonic()
    text = bytearray()
    try:
        with requests.get(endpoint, headers={"Authorization": "Bearer " + os.environ["KAGGLE_API_TOKEN"], "Accept": "text/event-stream, */*"}, params={"versionLabel": "v" + str(job["version"])}, stream=True, timeout=(10, seconds)) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=1024):
                text.extend(chunk[:1048576 - len(text)])
                if len(text) >= 1048576 or time.monotonic() - started >= seconds or b"END_OF_LOG" in text:
                    break
            result = {"content_type": response.headers.get("content-type"), "bytes": len(text), "truncated": len(text) >= 1048576}
    finally:
        if text:
            Path(output).mkdir(parents=True, exist_ok=True)
            path = Path(output) / "session.log"
            remaining = LOG_FILE_LIMIT - (path.stat().st_size if path.exists() else 0)
            header = f"--- {datetime.now(timezone.utc).isoformat(timespec='seconds')} v{job['version']} ---\n"
            data = (header + redact(text.decode("utf-8", errors="replace")) + "\n").encode("utf-8")
            if remaining > 0:
                with path.open("ab") as handle:
                    handle.write(data[:remaining].decode("utf-8", errors="ignore").encode("utf-8"))
    return result


def outputs(client, job, output, download=True):
    import requests
    from urllib.parse import urlsplit
    from kagglesdk.kernels.types.kernels_api_service import ApiListKernelSessionOutputRequest
    validate_job(job)
    request = ApiListKernelSessionOutputRequest()
    request.user_name, request.kernel_slug = job["ref"].split("/")
    request.version_label = "v" + str(job["version"])
    request.page_size = 100
    root = (Path(output) / "artifacts").resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest = []
    tokens = set()
    total = 0
    for _ in range(100):
        def listing():
            with client.build_kaggle_client() as sdk:
                return sdk.kernels.kernels_api_client.list_kernel_session_output(request)
        response = read_retry(listing)
        if response.log:
            (Path(output) / "persisted.log").write_text(redact(response.log), encoding="utf-8")
        if not download:
            return []
        for item in response.files:
            target = (root / item.file_name.replace("\\", "/")).resolve()
            if not target.is_relative_to(root) or target == root:
                raise ValueError("output filename escapes artifact directory")
            url = urlsplit(item.url)
            if url.scheme != "https" or url.username or url.password:
                raise ValueError("output URL must be HTTPS without credentials")
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = target.with_name(target.name + ".part")
            checksum = hashlib.sha256()
            size = 0
            with requests.get(item.url, stream=True, timeout=(10, 60)) as content:
                content.raise_for_status()
                with partial.open("wb") as handle:
                    for chunk in content.iter_content(chunk_size=1048576):
                        total += len(chunk)
                        if total > 1000000000:
                            raise ValueError("output exceeds the 1GB recovery budget")
                        handle.write(chunk)
                        checksum.update(chunk)
                        size += len(chunk)
            partial.replace(target)
            manifest.append({"path": str(target.relative_to(root)), "bytes": size, "sha256": checksum.hexdigest()})
        if not response.next_page_token:
            return manifest
        if response.next_page_token in tokens:
            raise ValueError("output pagination did not advance")
        tokens.add(response.next_page_token)
        request.page_token = response.next_page_token
    raise ValueError("output pagination limit reached")


def wait(client, job_path, output, wait_seconds=21600, interval=30):
    if not 1 <= wait_seconds <= 21600:
        raise ValueError("wait_seconds must be 1..21600")
    job = validate_job(load(job_path))
    if job.get("state") not in ("submitted", "waiting", "complete") or not job.get("version"):
        raise ValueError("job submission needs reconciliation before waiting")
    result = {"ref": job["ref"], "version": job["version"], "ready_for_verification": False, "next_phase": "waiting_kaggle"}
    monitor_started = time.monotonic()
    deadline = monitor_started + wait_seconds
    append_monitor_event(output, job, "wait_started", monitor_started)
    while True:
        try:
            current = status(client, job["ref"], job["version"])
        except Exception as error:
            append_monitor_event(output, job, "status_retrieval_error", monitor_started, error_type=type(error).__name__)
            raise
        result.update(current)
        append_monitor_event(output, job, "status_observed", monitor_started, status=current.get("status"))
        try:
            result["log"] = read_retry(lambda: log_slice(job, output, seconds=min(20, max(1, int(deadline - time.monotonic())))))
        except Exception as error:
            result["log_error"] = type(error).__name__
            append_monitor_event(output, job, "log_retrieval_error", monitor_started, status=current.get("status"), error_type=type(error).__name__)
        if current["status"] == "complete":
            append_monitor_event(output, job, "terminal", monitor_started, status=current["status"])
            cancel_final = _reconcile_cancel_terminal(job_path, job["ref"], job["version"], current)
            if cancel_final is not None:
                result["cancel_final"] = cancel_final
            result["next_phase"] = "recovering_outputs"
            save(Path(output) / "continuation.json", result)
            try:
                result["files"] = outputs(client, job, Path(output))
            except Exception as error:
                result["output_error"] = type(error).__name__
                append_monitor_event(output, job, "output_recovery_error", monitor_started, status=current["status"], error_type=type(error).__name__)
                save(Path(output) / "continuation.json", result)
                raise
            result["ready_for_verification"] = True
            result["next_phase"] = "verification"
            save(Path(output) / "continuation.json", result)
            append_monitor_event(output, job, "verification_ready", monitor_started, status=current["status"])
            return result
        if current["status"] in ("error", "cancel_acknowledged"):
            append_monitor_event(output, job, "terminal", monitor_started, status=current["status"])
            result["next_phase"] = "failure_review"
            cancel_final = _reconcile_cancel_terminal(job_path, job["ref"], job["version"], current)
            if cancel_final is not None:
                result["cancel_final"] = cancel_final
            try:
                outputs(client, job, Path(output), download=False)
            except Exception as error:
                result["output_error"] = type(error).__name__
                append_monitor_event(output, job, "output_recovery_error", monitor_started, status=current["status"], error_type=type(error).__name__)
            save(Path(output) / "continuation.json", result)
            return result
        if time.monotonic() >= deadline:
            result["status"] = "waiting_timeout"
            append_monitor_event(output, job, "wait_deadline", monitor_started, status=current["status"])
            save(Path(output) / "continuation.json", result)
            return result
        save(Path(output) / "continuation.json", result)
        time.sleep(min(interval, max(0, deadline - time.monotonic())))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("doctor", "quota", "kernel-list", "kernel-status", "kernel-output", "submit", "reconcile", "wait", "cancel", "dataset-create", "dataset-update", "dataset-download"):
        sub = commands.add_parser(name)
        if name in ("kernel-status", "kernel-output", "dataset-download"):
            sub.add_argument("--ref", required=True, type=kernel_ref)
        if name in ("kernel-output", "submit", "wait", "dataset-download"):
            sub.add_argument("--output", required=True, type=Path)
        if name in ("submit", "dataset-create", "dataset-update"):
            sub.add_argument("--folder", required=True, type=Path)
        if name == "submit":
            sub.add_argument("--notification-config", help="JSON target for ChatGPT completion automation")
            sub.add_argument("--allow-public", action="store_true", help="allow submission of a public notebook")
        if name == "dataset-update":
            sub.add_argument("--message", required=True)
        if name in ("wait", "reconcile", "cancel"):
            sub.add_argument("--job", required=True, type=Path)
        if name in ("kernel-status", "reconcile", "cancel"):
            sub.add_argument("--version", type=int)
        if name == "reconcile":
            sub.add_argument("--ref", type=kernel_ref)
        if name == "cancel":
            sub.add_argument("--ref", required=True, type=kernel_ref)
            sub.add_argument("--reason", required=True)
        if name == "reconcile":
            sub.add_argument("--not-accepted", action="store_true")
        if name == "wait":
            sub.add_argument("--wait-seconds", type=int, default=21600)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=False, interpolate=False)
    try:
        configure()
        notification_target = None
        if args.command == "submit" and args.notification_config is not None:
            notification_target = validate_notification_target(json.loads(args.notification_config))
        if args.command == "doctor":
            result = {"kaggle_version": importlib.metadata.version("kaggle"), "token_present": bool(os.environ.get("KAGGLE_API_TOKEN")), "username_present": bool(os.environ.get("KAGGLE_USERNAME"))}
        else:
            client = api()
            if args.command == "quota":
                result = quota(client)
            elif args.command == "kernel-list":
                result = {"kernels": [{"ref": k.ref, "title": k.title} for k in read_retry(lambda: client.kernels_list(mine=True, page_size=20))], "note": "一覧は稼働中に限定されない"}
            elif args.command == "kernel-status":
                result = status(client, args.ref, args.version)
            elif args.command == "kernel-output":
                job = {"ref": args.ref, "version": latest_version(client, args.ref)}
                result = dict(job, files=outputs(client, job, args.output))
            elif args.command == "submit":
                result = submit(client, args.folder, args.output, notification_target, args.allow_public)
            elif args.command == "wait":
                result = wait(client, args.job, args.output, args.wait_seconds)
            elif args.command == "cancel":
                if args.version is None:
                    raise ValueError("cancel requires --version")
                result = cancel(client, args.job, args.ref, args.version, args.reason)
            elif args.command == "reconcile":
                if args.not_accepted == (args.version is not None):
                    raise ValueError("reconcile needs exactly one of --version or --not-accepted")
                if args.not_accepted and args.ref is not None:
                    raise ValueError("--ref cannot be combined with --not-accepted")
                result = mark_not_accepted(client, args.job) if args.not_accepted else reconcile(client, args.job, args.version, args.ref)
            elif args.command == "dataset-create":
                client.dataset_create_new(str(clean_upload(args.folder)), public=False, quiet=True, convert_to_csv=False, dir_mode="zip")
                result = {"created": True, "public": False}
            elif args.command == "dataset-update":
                client.dataset_create_version(str(clean_upload(args.folder)), args.message, quiet=True, convert_to_csv=False, delete_old_versions=False, dir_mode="zip")
                result = {"version_created": True}
            else:
                read_retry(lambda: client.dataset_download_files(args.ref, path=str(args.output), quiet=True, unzip=False))
                result = {"downloaded": True, "extracted": False}
        print(redact(json.dumps(result, ensure_ascii=False)))
        if result.get("request_outcome") in ("session_id_unavailable", "request_rejected"):
            return 2
        return 0 if result.get("ready_for_verification", True) else 3
    except (Exception, SystemExit) as error:
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(redact(json.dumps({"error": message, "http_status": getattr(getattr(error, "response", None), "status_code", None), "errno": getattr(error, "errno", None)}, ensure_ascii=False)), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
