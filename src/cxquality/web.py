"""Local Flask dashboard and review UI."""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)

from .db import Database
from .quality_review import COMPONENTS, explain_component, mistake_flags
from .run_config import configured_roster_path, infer_run_dates


def _number(
    value: str | None, label: str, *, minimum: float, maximum: float, integer: bool = False
) -> float | int:
    try:
        parsed = int(value or "") if integer else float(value or "")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{label} must be between {minimum:g} and {maximum:g}")
    return parsed


def _run_defaults() -> dict[str, object]:
    roster = configured_roster_path()
    return {
        "date_mode": "auto",
        "mode": "weekly",
        "sample_size": 200,
        "budget_usd": float(os.environ.get("CXQUALITY_BUDGET_USD", "4")),
        "stop_at_usd": float(os.environ.get("CXQUALITY_STOP_AT_USD", "3.8")),
        "roster_path": str(roster) if roster else "",
        "ai_workers": int(os.environ.get("CXQUALITY_AI_WORKERS", "6")),
        "decision_model": os.environ.get("CXQUALITY_DECISION_MODEL", "typesafe/jev-1.14"),
        "writer_model": os.environ.get("CXQUALITY_WRITER_MODEL", "openai/gpt-6-luna"),
        "no_ai": False,
        "prepare_only": False,
    }


def launch_run_worker(run_id: str, db_path: str | Path) -> int:
    """Start a detached local worker and return its process id."""

    project_root = Path(__file__).resolve().parents[2]
    log_dir = project_root / "local" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"run-{run_id}.log"
    command = [
        sys.executable,
        "-m",
        "cxquality.cli",
        "resume",
        run_id,
        "--db",
        str(Path(db_path).resolve()),
    ]
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    return int(process.pid)


def create_app(db_path: str | Path | None = None, *, testing: bool = False) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("CXQUALITY_SESSION_SECRET", "local-cxquality-session"),
        TESTING=testing,
        DATABASE=str(db_path or os.environ.get("CXQUALITY_DB", "local/cxquality.sqlite3")),
    )
    database = Database(app.config["DATABASE"])
    database.initialize()
    for completed_run in database.list_runs(limit=500):
        if completed_run["status"] == "completed":
            database.sync_knowledge_candidates(str(completed_run["id"]))
    app.extensions["cxquality_db"] = database

    @app.template_filter("percent")
    def percent(value: object) -> str:
        try:
            return f"{float(value) * 100:.1f}%"
        except (TypeError, ValueError):
            return "0.0%"

    @app.template_filter("money")
    def money(value: object) -> str:
        try:
            return f"${float(value):.4f}"
        except (TypeError, ValueError):
            return "$0.0000"

    ui_root = Path(__file__).resolve().parent / "static" / "app"

    @app.get("/")
    def app_shell():
        index = ui_root / "index.html"
        if index.is_file():
            return send_from_directory(ui_root, "index.html")
        return redirect(url_for("dashboard"))

    @app.get("/app-assets/<path:filename>")
    def app_assets(filename: str):
        return send_from_directory(ui_root, filename)

    @app.get("/classic")
    def dashboard():
        summary = database.dashboard(request.args.get("run_id"))
        return render_template("dashboard.html", summary=summary, runs=database.list_runs())

    @app.get("/runs")
    def runs():
        return render_template("runs.html", runs=database.list_runs(limit=200))

    @app.route("/runs/new", methods=["GET", "POST"])
    def new_run():
        defaults = _run_defaults()
        clone_id = request.args.get("clone")
        if clone_id and (clone := database.get_run(clone_id)):
            settings = dict(database.checkpoint(clone_id).get("settings") or {})
            defaults.update(
                {
                    "zip_folder": clone["zip_folder"],
                    "date_mode": "manual",
                    "from_date": clone["from_date"],
                    "to_date": clone["to_date"],
                    "mode": clone["mode"],
                    "sample_size": clone["sample_size"] or 200,
                    "budget_usd": clone["budget_usd"],
                    "stop_at_usd": clone["stop_at_usd"],
                    "roster_path": clone["roster_path"] or "",
                    **settings,
                }
            )
        if request.method == "POST":
            defaults.update(request.form.to_dict())
            defaults["no_ai"] = request.form.get("no_ai") == "1"
            defaults["prepare_only"] = request.form.get("prepare_only") == "1"
            try:
                zip_folder = Path(request.form["zip_folder"].strip()).expanduser().resolve()
                if not zip_folder.is_dir():
                    raise ValueError(f"ZIP folder was not found: {zip_folder}")
                zip_count = sum(1 for _ in zip_folder.glob("*.zip"))
                if zip_count == 0:
                    raise ValueError("The selected folder contains no ZIP files")
                active = database.active_run_for_folder(zip_folder)
                if active:
                    raise ValueError(
                        f"This folder already has an active or resumable run: {active['id'][:8]}"
                    )
                roster_raw = request.form.get(
                    "roster_path", str(defaults.get("roster_path") or "")
                ).strip()
                roster_path = Path(roster_raw).expanduser().resolve() if roster_raw else None
                if roster_path and not roster_path.is_file():
                    raise ValueError(
                        f"The configured roster workbook was not found: {roster_path}"
                    )
                if request.form.get("date_mode", "auto") == "manual":
                    from_date = request.form.get("from_date", "").strip()
                    to_date = request.form.get("to_date", "").strip()
                else:
                    from_date, to_date = infer_run_dates(zip_folder)
                mode = request.form.get("mode", str(defaults["mode"]))
                if mode not in {"weekly", "sample"}:
                    raise ValueError("Run mode must be full or sample")
                sample_size = (
                    int(
                        _number(
                            request.form.get("sample_size"),
                            "Sample size",
                            minimum=1,
                            maximum=zip_count,
                            integer=True,
                        )
                    )
                    if mode == "sample"
                    else None
                )
                budget = float(
                    _number(
                        request.form.get("budget_usd", str(defaults["budget_usd"])),
                        "Hard budget",
                        minimum=0.01,
                        maximum=1000,
                    )
                )
                stop_at = float(
                    _number(
                        request.form.get("stop_at_usd", str(defaults["stop_at_usd"])),
                        "Stop threshold",
                        minimum=0.01,
                        maximum=1000,
                    )
                )
                ai_workers = int(
                    _number(
                        request.form.get("ai_workers", str(defaults["ai_workers"])),
                        "AI workers",
                        minimum=1,
                        maximum=16,
                        integer=True,
                    )
                )
                decision_model = request.form.get(
                    "decision_model", str(defaults["decision_model"])
                ).strip()
                writer_model = request.form.get(
                    "writer_model", str(defaults["writer_model"])
                ).strip()
                if not decision_model or not writer_model or " " in decision_model + writer_model:
                    raise ValueError("Model IDs are required and cannot contain spaces")
                no_ai = request.form.get("no_ai") == "1"
                prepare_only = request.form.get("prepare_only") == "1"
                run_id = database.create_run(
                    from_date=from_date,
                    to_date=to_date,
                    zip_folder=str(zip_folder),
                    roster_path=str(roster_path) if roster_path else None,
                    budget_usd=budget,
                    stop_at_usd=stop_at,
                    sample_size=sample_size,
                    mode=mode,
                )
                database.save_checkpoint(
                    run_id,
                    {
                        "evaluation_version": "company-supervisor-v1",
                        "settings": {
                            "no_ai": no_ai,
                            "ai_workers": ai_workers,
                            "decision_model": decision_model,
                            "writer_model": writer_model,
                            "zip_count_at_creation": zip_count,
                            "prepared": prepare_only,
                        }
                    },
                )
            except (KeyError, ValueError) as exc:
                flash(str(exc), "error")
            else:
                if prepare_only:
                    flash("Run prepared. Review its controls, then click Start.", "success")
                elif not app.config["TESTING"]:
                    try:
                        worker_pid = launch_run_worker(run_id, database.path)
                    except OSError as exc:
                        database.update_run(run_id, status="failed", error=str(exc)[:1000])
                        flash(f"Run was created but could not start: {exc}", "error")
                    else:
                        flash(f"Run started in background (worker {worker_pid}).", "success")
                else:
                    flash("Run created.", "success")
                return redirect(url_for("run_detail", run_id=run_id))
        return render_template("run_new.html", defaults=defaults)

    @app.get("/runs/<run_id>")
    def run_detail(run_id: str):
        run = database.get_run(run_id)
        if not run:
            abort(404)
        checkpoint = database.checkpoint(run_id)
        return render_template(
            "run_detail.html",
            run=run,
            summary=database.dashboard(run_id),
            settings=dict(checkpoint.get("settings") or {}),
            rescore_count=len(database.rescore_session_ids(run_id)),
        )

    @app.post("/runs/<run_id>/stop")
    def stop_run(run_id: str):
        try:
            database.request_run_stop(run_id)
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        else:
            flash(
                "Stop requested. Already-started model requests will finish and be saved safely.",
                "success",
            )
        return redirect(url_for("run_detail", run_id=run_id))

    @app.post("/runs/<run_id>/resume")
    def resume_run(run_id: str):
        run = database.get_run(run_id)
        if not run:
            abort(404)
        if run["status"] == "running":
            flash("This run is already running.", "error")
            return redirect(url_for("run_detail", run_id=run_id))
        if run["status"] == "completed" and not database.rescore_session_ids(run_id):
            flash("This run is complete and has no conversations waiting for re-scoring.", "error")
            return redirect(url_for("run_detail", run_id=run_id))
        database.update_run(run_id, status="queued", error=None)
        resume_settings = dict(database.checkpoint(run_id).get("settings") or {})
        resume_settings["prepared"] = False
        database.save_checkpoint(run_id, {"settings": resume_settings})
        if not app.config["TESTING"]:
            try:
                worker_pid = launch_run_worker(run_id, database.path)
            except OSError as exc:
                database.update_run(run_id, status="failed", error=str(exc)[:1000])
                flash(f"Run could not start: {exc}", "error")
            else:
                flash(f"Run resumed in background (worker {worker_pid}).", "success")
        else:
            flash("Run queued for resume.", "success")
        return redirect(url_for("run_detail", run_id=run_id))

    @app.post("/runs/<run_id>/controls")
    def update_run_controls(run_id: str):
        run = database.get_run(run_id)
        if not run:
            abort(404)
        try:
            budget = float(
                _number(
                    request.form.get("budget_usd"),
                    "Hard budget",
                    minimum=max(0.01, float(run["cost_usd"])),
                    maximum=1000,
                )
            )
            stop_at = float(
                _number(
                    request.form.get("stop_at_usd"),
                    "Stop threshold",
                    minimum=max(0.01, float(run["cost_usd"])),
                    maximum=1000,
                )
            )
            workers = int(
                _number(
                    request.form.get("ai_workers"),
                    "AI workers",
                    minimum=1,
                    maximum=16,
                    integer=True,
                )
            )
            roster_raw = request.form.get("roster_path", "").strip()
            roster = Path(roster_raw).expanduser().resolve() if roster_raw else None
            if roster and not roster.is_file():
                raise ValueError(f"Roster workbook was not found: {roster}")
            settings = dict(database.checkpoint(run_id).get("settings") or {})
            settings.update(
                {
                    "no_ai": request.form.get("no_ai") == "1",
                    "ai_workers": workers,
                    "decision_model": request.form.get("decision_model", "").strip(),
                    "writer_model": request.form.get("writer_model", "").strip(),
                }
            )
            if not settings["decision_model"] or not settings["writer_model"]:
                raise ValueError("Both model IDs are required")
            if " " in settings["decision_model"] + settings["writer_model"]:
                raise ValueError("Model IDs cannot contain spaces")
            database.update_run_controls(
                run_id,
                budget_usd=budget,
                stop_at_usd=stop_at,
                roster_path=str(roster) if roster else None,
                settings=settings,
            )
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        else:
            flash("Run controls updated. They apply when the run starts or resumes.", "success")
        return redirect(url_for("run_detail", run_id=run_id))

    @app.get("/runs/<run_id>/log")
    def run_log(run_id: str):
        if not database.get_run(run_id):
            abort(404)
        log_path = Path(__file__).resolve().parents[2] / "local" / "logs" / f"run-{run_id}.log"
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines() if log_path.is_file() else []
        return render_template("run_log.html", run_id=run_id, lines=lines[-300:])

    @app.get("/control-center")
    def control_center():
        defaults = _run_defaults()
        health = {
            "database": str(database.path),
            "roster_ok": bool(
                defaults["roster_path"] and Path(str(defaults["roster_path"])).is_file()
            ),
            "api_key_ok": bool(os.environ.get("OPENROUTER_API_KEY", "").strip()),
            "pdftotext_ok": bool(shutil.which("pdftotext")),
            "node_ok": bool(shutil.which("node")),
        }
        return render_template(
            "control_center.html",
            health=health,
            defaults=defaults,
            counts=database.control_summary(),
        )

    def review_rows() -> list[dict[str, object]]:
        run_id = request.args.get("run_id") or None
        agent_id = request.args.get("agent_id") or None
        return database.scored_review_rows(run_id=run_id, agent_id=agent_id)

    def row_mistakes(
        row: dict[str, object], reviews: dict[tuple[str, str, str], dict[str, object]]
    ) -> list[dict[str, object]]:
        run_id, session_id = str(row["run_id"]), str(row["session_id"])
        flags = {str(flag["component"]): flag for flag in mistake_flags(row["scores"])}
        for component in COMPONENTS:
            review = reviews.get((run_id, session_id, component))
            if not review or component in flags:
                continue
            original = json.loads(str(review.get("original_scores_json") or "{}"))
            original_flag = next(
                (
                    flag
                    for flag in mistake_flags(original)
                    if str(flag["component"]) == component
                ),
                None,
            )
            if original_flag:
                flags[component] = original_flag
        return list(flags.values())

    def paginate(items: list[dict[str, object]], page_size: int = 50):
        page = max(1, request.args.get("page", default=1, type=int) or 1)
        total = len(items)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        return items[start : start + page_size], page, pages, total

    @app.get("/employee-mistakes")
    def employee_mistakes():
        rows = review_rows()
        reviews = database.manual_review_map("mistake")
        component_filter = request.args.get("component") or ""
        status_filter = request.args.get("review_status") or ""
        records: list[dict[str, object]] = []
        summary: dict[tuple[str, str], dict[str, object]] = {}
        for row in rows:
            key = (str(row["agent_name"]), str(row.get("branch") or ""))
            agent_summary = summary.setdefault(
                key,
                {
                    "agent_name": row["agent_name"],
                    "branch": row.get("branch") or "",
                    "staff_id": row.get("staff_id") or row.get("agent_id") or "",
                    "mistakes": 0,
                    "reviewed": 0,
                    "conversations": set(),
                    "categories": {},
                },
            )
            for flag in row_mistakes(row, reviews):
                component = str(flag["component"])
                review = reviews.get((str(row["run_id"]), str(row["session_id"]), component))
                review_status = str(review["decision"]) if review else "pending"
                agent_summary["mistakes"] = int(agent_summary["mistakes"]) + 1
                agent_summary["conversations"].add(str(row["session_id"]))
                categories = agent_summary["categories"]
                categories[component] = int(categories.get(component, 0)) + 1
                if review:
                    agent_summary["reviewed"] = int(agent_summary["reviewed"]) + 1
                if component_filter and component != component_filter:
                    continue
                if status_filter and review_status != status_filter:
                    continue
                records.append({**row, **flag, "review": review, "review_status": review_status})
        summaries = []
        for item in summary.values():
            item["conversation_count"] = len(item.pop("conversations"))
            summaries.append(item)
        summaries.sort(key=lambda item: int(item["mistakes"]), reverse=True)
        page_items, page, pages, total = paginate(records)
        reviewed = sum(1 for record in records if record["review_status"] != "pending")
        return render_template(
            "employee_mistakes.html",
            records=page_items,
            summaries=summaries,
            components=COMPONENTS,
            runs=database.list_runs(limit=200),
            agents=database.list_agents(),
            page=page,
            pages=pages,
            total=total,
            reviewed=reviewed,
        )

    @app.get("/employee-mistakes.csv")
    def employee_mistakes_csv():
        reviews = database.manual_review_map("mistake")
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "run_id",
                "session_id",
                "employee",
                "branch",
                "staff_id",
                "mistake",
                "ai_quality_score_0_100",
                "why",
                "review_status",
                "manager_reason",
            ]
        )
        for row in review_rows():
            for flag in row_mistakes(row, reviews):
                review = reviews.get(
                    (str(row["run_id"]), str(row["session_id"]), str(flag["component"]))
                )
                writer.writerow(
                    [
                        row["run_id"],
                        row["session_id"],
                        row["agent_name"],
                        row.get("branch") or "",
                        row.get("staff_id") or "",
                        flag["label"],
                        row["scores"].get("overall", ""),
                        flag["explanation"],
                        review["decision"] if review else "pending",
                        review["manager_reason"] if review else "",
                    ]
                )
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=employee-mistakes.csv"},
        )

    @app.get("/quality-review")
    def quality_review():
        rows = review_rows()
        status_filter = request.args.get("review_status") or ""
        search = (request.args.get("search") or "").strip().casefold()
        filtered = []
        for row in rows:
            review_status = str(row["review_decision"] or "pending")
            if status_filter and review_status != status_filter:
                continue
            if search and search not in (
                f"{row['session_id']} {row['agent_name']} {row.get('branch') or ''}".casefold()
            ):
                continue
            row["review_status"] = review_status
            row["explanations"] = {
                component: explain_component(component, float(row["scores"].get(component, 0)))
                for component in COMPONENTS
                if component in row["scores"]
            }
            filtered.append(row)
        page_items, page, pages, total = paginate(filtered)
        reviewed = sum(1 for row in filtered if row["review_status"] != "pending")
        corrected = sum(
            1 for row in filtered if row["review_status"] in {"corrected", "dismissed"}
        )
        return render_template(
            "quality_review.html",
            rows=page_items,
            components=COMPONENTS,
            runs=database.list_runs(limit=200),
            agents=database.list_agents(),
            page=page,
            pages=pages,
            total=total,
            reviewed=reviewed,
            corrected=corrected,
        )

    @app.get("/quality-review.csv")
    def quality_review_csv():
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "run_id",
                "session_id",
                "employee",
                "branch",
                "staff_id",
                "quality_score_0_100",
                "review_status",
                "manager_reason",
            ]
        )
        for row in review_rows():
            writer.writerow(
                [
                    row["run_id"],
                    row["session_id"],
                    row["agent_name"],
                    row.get("branch") or "",
                    row.get("staff_id") or "",
                    row["scores"].get("overall", ""),
                    row.get("review_decision") or "pending",
                    row.get("manager_reason") or "",
                ]
            )
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=quality-double-check.csv"},
        )

    @app.get("/quality-review/<run_id>/<session_id>")
    def quality_review_detail(run_id: str, session_id: str):
        item = database.scored_review_item(run_id, session_id)
        if not item:
            abort(404)
        full_messages = []
        transcript_error = None
        try:
            from .ingest import parse_zip

            transcript = parse_zip(item["source_zip"])
            full_messages = transcript.messages
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            transcript_error = f"Full transcript could not be opened: {type(exc).__name__}"
        mistake_reviews = database.manual_review_map("mistake")
        flags = []
        for flag in row_mistakes(item, mistake_reviews):
            flag["review"] = mistake_reviews.get((run_id, session_id, str(flag["component"])))
            flag["history"] = database.manual_review_history(
                int(flag["review"]["id"]) if flag["review"] else None
            )
            flags.append(flag)
        return render_template(
            "quality_review_detail.html",
            item=item,
            components=COMPONENTS,
            flags=flags,
            full_messages=full_messages,
            transcript_error=transcript_error,
            quality_history=database.manual_review_history(item.get("review_id")),
        )

    @app.post("/employee-mistakes/review")
    def review_employee_mistake():
        run_id = request.form.get("run_id", "")
        session_id = request.form.get("session_id", "")
        try:
            manager_verdict = request.form.get("manager_verdict", "").strip()
            corrected_raw = (
                "100" if manager_verdict == "compliant" else
                "0" if manager_verdict == "non_compliant" else
                request.form.get("corrected_overall", "").strip()
            )
            database.apply_manual_review(
                run_id=run_id,
                session_id=session_id,
                review_kind="mistake",
                component=request.form.get("component", ""),
                decision=request.form.get("decision", ""),
                corrected_overall=float(corrected_raw) if corrected_raw else None,
                manager_reason=request.form.get("manager_reason", ""),
                knowledge_rule=request.form.get("knowledge_rule", ""),
                reviewer=request.form.get("reviewer", "Quality Manager"),
            )
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        else:
            flash("Mistake decision saved, reports updated, and Knowledge rule learned.", "success")
        return redirect(url_for("quality_review_detail", run_id=run_id, session_id=session_id))

    @app.post("/quality-review/review")
    def review_quality_score():
        run_id = request.form.get("run_id", "")
        session_id = request.form.get("session_id", "")
        try:
            manager_verdict = request.form.get("manager_verdict", "").strip()
            corrected_raw = (
                "100" if manager_verdict == "compliant" else
                "0" if manager_verdict == "non_compliant" else
                request.form.get("corrected_overall", "").strip()
            )
            database.apply_manual_review(
                run_id=run_id,
                session_id=session_id,
                review_kind="quality",
                component="overall",
                decision=request.form.get("decision", ""),
                corrected_overall=float(corrected_raw) if corrected_raw else None,
                manager_reason=request.form.get("manager_reason", ""),
                knowledge_rule=request.form.get("knowledge_rule", ""),
                reviewer=request.form.get("reviewer", "Quality Manager"),
            )
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        else:
            flash("Quality decision saved, ranking updated, and Knowledge rule learned.", "success")
        return redirect(url_for("quality_review_detail", run_id=run_id, session_id=session_id))

    @app.get("/api/runs/<run_id>")
    def run_status(run_id: str):
        summary = database.dashboard(run_id)
        if not summary["run"]:
            return jsonify({"error": "run not found"}), 404
        return jsonify(summary)

    @app.get("/api/v1/overview")
    def api_overview():
        runs = database.list_runs(limit=30)
        latest = database.dashboard(runs[0]["id"]) if runs else database.dashboard()
        controls = database.control_summary()
        knowledge = database.list_knowledge(include_inactive=False)
        categories: dict[str, int] = {}
        for item in knowledge:
            category = str(item.get("category") or "general")
            categories[category] = categories.get(category, 0) + 1
        pending = database.list_knowledge_candidates("pending", limit=100)
        return jsonify(
            {
                "latest": latest,
                "runs": runs,
                "knowledge": {
                    "active": len(knowledge),
                    "categories": categories,
                    "pending_candidates": len(pending),
                    "last_updated": max(
                        (str(item.get("updated_at") or "") for item in knowledge), default=None
                    ),
                },
                "control": controls,
                "engine": "company-supervisor-v1",
            }
        )

    @app.post("/api/v1/runs")
    def api_create_run():
        payload = request.get_json(silent=True) or {}
        defaults = _run_defaults()
        try:
            zip_folder = Path(str(payload.get("zip_folder") or "").strip()).expanduser().resolve()
            if not zip_folder.is_dir():
                raise ValueError(f"ZIP folder was not found: {zip_folder}")
            zip_count = sum(1 for _ in zip_folder.glob("*.zip"))
            if zip_count == 0:
                raise ValueError("The selected folder contains no ZIP files")
            active = database.active_run_for_folder(zip_folder)
            if active:
                raise ValueError(
                    f"This folder already has an active or resumable run: {active['id'][:8]}"
                )
            from_date, to_date = infer_run_dates(zip_folder)
            roster_raw = str(defaults.get("roster_path") or "").strip()
            roster_path = Path(roster_raw).expanduser().resolve() if roster_raw else None
            if roster_path and not roster_path.is_file():
                roster_path = None
            run_id = database.create_run(
                from_date=from_date,
                to_date=to_date,
                zip_folder=str(zip_folder),
                roster_path=str(roster_path) if roster_path else None,
                budget_usd=float(defaults["budget_usd"]),
                stop_at_usd=float(defaults["stop_at_usd"]),
                sample_size=None,
                mode="weekly",
            )
            database.save_checkpoint(
                run_id,
                {
                    "evaluation_version": "company-supervisor-v1",
                    "settings": {
                        "no_ai": False,
                        "ai_workers": int(defaults["ai_workers"]),
                        "decision_model": str(defaults["decision_model"]),
                        "writer_model": str(defaults["writer_model"]),
                        "zip_count_at_creation": zip_count,
                        "prepared": False,
                    },
                },
            )
            worker_pid = None
            if not app.config["TESTING"]:
                worker_pid = launch_run_worker(run_id, database.path)
        except (OSError, ValueError) as exc:
            if "run_id" in locals() and database.get_run(run_id):
                database.update_run(run_id, status="failed", error=str(exc)[:1000])
            return jsonify({"error": str(exc)}), 409
        return jsonify(
            {
                "run_id": run_id,
                "worker_pid": worker_pid,
                "zip_count": zip_count,
                "status": "queued",
            }
        ), 201

    @app.get("/api/v1/runs/<run_id>")
    def api_run(run_id: str):
        summary = database.dashboard(run_id)
        if not summary["run"]:
            return jsonify({"error": "run not found"}), 404
        summary["sample"] = database.representative_cases(run_id, limit=8, reviewed=False)
        return jsonify(summary)

    @app.post("/api/v1/runs/<run_id>/stop")
    def api_stop_run(run_id: str):
        try:
            database.request_run_stop(run_id)
        except KeyError:
            return jsonify({"error": "run not found"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"run_id": run_id, "status": "blocked", "checkpoint_safe": True})

    @app.post("/api/v1/runs/<run_id>/resume")
    def api_resume_run(run_id: str):
        run = database.get_run(run_id)
        if not run:
            return jsonify({"error": "run not found"}), 404
        if run["status"] == "running":
            return jsonify({"error": "This run is already running"}), 409
        if run["status"] == "completed" and not database.rescore_session_ids(run_id):
            return jsonify({"error": "This run is complete"}), 409
        database.update_run(run_id, status="queued", error=None)
        settings = dict(database.checkpoint(run_id).get("settings") or {})
        settings["prepared"] = False
        database.save_checkpoint(run_id, {"settings": settings})
        worker_pid = None
        if not app.config["TESTING"]:
            try:
                worker_pid = launch_run_worker(run_id, database.path)
            except OSError as exc:
                database.update_run(run_id, status="failed", error=str(exc)[:1000])
                return jsonify({"error": str(exc)}), 500
        return jsonify({"run_id": run_id, "status": "queued", "worker_pid": worker_pid})

    @app.get("/api/v1/review-sample")
    def api_review_sample():
        run_id = request.args.get("run_id")
        if not run_id:
            runs = database.list_runs(1)
            run_id = str(runs[0]["id"]) if runs else ""
        if not run_id or not database.get_run(run_id):
            return jsonify({"run_id": None, "items": []})
        limit = max(1, min(40, request.args.get("limit", default=12, type=int) or 12))
        category = (request.args.get("category") or "all").strip()
        reviewed = category == "reviewed"
        selected_category = "all" if reviewed else category
        items = database.representative_cases(
            run_id,
            limit=100 if selected_category != "all" else limit,
            reviewed=reviewed,
        )
        if selected_category != "all":
            items = [item for item in items if item["category"] == category][:limit]
        return jsonify({"run_id": run_id, "items": items})

    @app.post("/api/v1/cases/<run_id>/<session_id>/review")
    def api_review_case(run_id: str, session_id: str):
        payload = request.get_json(silent=True) or {}
        verdict = str(payload.get("manager_verdict") or "").strip()
        if verdict not in {"compliant", "non_compliant"}:
            return jsonify({"error": "Choose compliant or non-compliant"}), 400
        item = database.scored_review_item(run_id, session_id)
        if not item:
            return jsonify({"error": "Scored conversation was not found"}), 404
        corrected = 100.0 if verdict == "compliant" else 0.0
        current = float(item["scores"].get("overall", 0))
        decision = "confirmed" if corrected == current else "corrected"
        try:
            review_id = database.apply_manual_review(
                run_id=run_id,
                session_id=session_id,
                review_kind="quality",
                component="overall",
                decision=decision,
                corrected_overall=corrected,
                manager_reason=str(payload.get("manager_reason") or ""),
                knowledge_rule=str(payload.get("knowledge_rule") or ""),
                reviewer=str(payload.get("reviewer") or "Quality Manager"),
            )
        except (KeyError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        review = database.manual_review_map("quality").get((run_id, session_id, "overall"))
        return jsonify(
            {
                "review_id": review_id,
                "learned": bool(review and review.get("knowledge_item_id")),
                "status": "reviewed",
            }
        )

    @app.get("/api/v1/knowledge")
    def api_knowledge():
        status = request.args.get("candidate_status") or None
        search = (request.args.get("search") or "").strip().casefold()
        items = database.list_knowledge(include_inactive=False)
        if search:
            items = [
                item
                for item in items
                if search in f"{item['title']} {item['content']} {item['category']}".casefold()
            ]
        return jsonify(
            {
                "items": items[:300],
                "candidates": database.list_knowledge_candidates(status, limit=200),
            }
        )

    @app.post("/api/v1/knowledge-candidates/analyze")
    def api_analyze_knowledge_candidates():
        payload = request.get_json(silent=True) or {}
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            runs = database.list_runs(1)
            run_id = str(runs[0]["id"]) if runs else ""
        run = database.get_run(run_id) if run_id else None
        if not run:
            return jsonify({"error": "run not found"}), 404
        try:
            from .ai import OpenRouterClient, OpenRouterConfig
            from .budget import BudgetGuard
            from .knowledge_agent import analyze_run_candidates

            settings = dict(database.checkpoint(run_id).get("settings") or {})
            client = OpenRouterClient(
                budget=BudgetGuard(
                    hard_limit_usd=float(run["budget_usd"]),
                    stop_at_usd=float(run["stop_at_usd"]),
                    initial_spend_usd=float(run["cost_usd"]),
                ),
                config=OpenRouterConfig(
                    decision_model=str(
                        settings.get("decision_model") or "typesafe/jev-1.14"
                    ),
                    writer_model=str(settings.get("writer_model") or "openai/gpt-6-luna"),
                ),
            )
            stats = analyze_run_candidates(database, run_id, client)
            database.save_checkpoint(run_id, {"knowledge_agent": stats})
        except (ValueError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"run_id": run_id, **stats})

    @app.post("/api/v1/knowledge-candidates/<int:candidate_id>/review")
    def api_review_knowledge_candidate(candidate_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            knowledge_id = database.review_knowledge_candidate(
                candidate_id,
                decision=str(payload.get("decision") or ""),
                rule_text=str(payload.get("rule_text") or ""),
                reviewer=str(payload.get("reviewer") or "Quality Manager"),
                resolution=str(payload.get("resolution") or "new"),
            )
        except KeyError:
            return jsonify({"error": "Knowledge draft was not found"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"knowledge_item_id": knowledge_id, "status": payload.get("decision")})

    @app.get("/knowledge")
    def knowledge():
        return render_template("knowledge.html", items=database.list_knowledge())

    @app.get("/supervisor-knowledge")
    def supervisor_knowledge():
        status = request.args.get("status") or "pending"
        category = request.args.get("category") or None
        import_id = request.args.get("import_id") or None
        page = max(1, request.args.get("page", default=1, type=int) or 1)
        page_size = 24
        cases = database.list_supervisor_cases(
            review_status=status or None,
            auto_category=category,
            import_id=import_id,
            limit=page_size + 1,
            offset=(page - 1) * page_size,
        )
        has_next = len(cases) > page_size
        return render_template(
            "supervisor_knowledge.html",
            cases=cases[:page_size],
            imports=database.list_supervisor_imports(),
            counts=database.supervisor_case_counts(),
            selected_status=status,
            selected_category=category or "",
            selected_import=import_id or "",
            page=page,
            has_next=has_next,
        )

    @app.post("/supervisor-knowledge/import")
    def import_supervisor_knowledge():
        from .supervisor_knowledge import import_whatsapp_supervisor_zip

        try:
            result = import_whatsapp_supervisor_zip(
                database,
                request.form.get("zip_path", ""),
                supervisor_sender=request.form.get("supervisor_sender") or None,
            )
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            flash(str(exc), "error")
        else:
            if result.get("duplicate"):
                flash("This WhatsApp export was already imported; no duplicate cases were created.", "success")
            else:
                flash(
                    f"Imported {result['case_count']} review items from "
                    f"{result['extracted_image_count']} available images. "
                    f"Detected supervisor: {result['supervisor_sender']}.",
                    "success",
                )
        return redirect(url_for("supervisor_knowledge"))

    @app.get("/supervisor-knowledge/media/<int:case_id>")
    def supervisor_case_media(case_id: int):
        item = database.get_supervisor_case(case_id)
        if not item or not item.get("image_path"):
            abort(404)
        path = Path(str(item["image_path"])).resolve()
        root = (database.path.parent / "supervisor-knowledge").resolve()
        try:
            path.relative_to(root)
        except ValueError:
            abort(403)
        if not path.is_file():
            abort(404)
        return send_file(path, conditional=True, max_age=3600)

    @app.post("/supervisor-knowledge/<int:case_id>/review")
    def review_supervisor_knowledge(case_id: int):
        try:
            database.review_supervisor_case(
                case_id,
                review_status=request.form.get("review_status", "pending"),
                final_category=request.form.get("final_category", "unclear"),
                caption_corrected=request.form.get("caption_corrected", ""),
                rule_text=request.form.get("rule_text", ""),
                review_notes=request.form.get("review_notes", ""),
                reviewer=request.form.get("reviewer", "Quality Manager"),
                promote_rule=bool(request.form.get("promote_rule")),
            )
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        else:
            flash("Supervisor case review saved.", "success")
        query = {
            "status": request.form.get("return_status", "pending"),
            "category": request.form.get("return_category", ""),
            "import_id": request.form.get("return_import", ""),
            "page": request.form.get("return_page", "1"),
        }
        return redirect(url_for("supervisor_knowledge", **query))

    @app.post("/knowledge")
    def add_knowledge():
        try:
            database.add_knowledge(
                request.form.get("title", ""),
                request.form.get("content", ""),
                category=request.form.get("category", "general"),
                source="ui",
            )
        except ValueError as exc:
            flash(str(exc), "error")
        else:
            flash("Knowledge item added.", "success")
        return redirect(url_for("knowledge"))

    @app.post("/knowledge/<int:item_id>/edit")
    def edit_knowledge(item_id: int):
        try:
            database.update_knowledge(
                item_id,
                title=request.form.get("title", "").strip(),
                content=request.form.get("content", "").strip(),
                category=request.form.get("category", "general").strip(),
                active=1 if request.form.get("active") else 0,
            )
        except ValueError as exc:
            flash(str(exc), "error")
        else:
            flash("Knowledge item updated.", "success")
        return redirect(url_for("knowledge"))

    @app.post("/knowledge/<int:item_id>/deactivate")
    def deactivate_knowledge(item_id: int):
        database.update_knowledge(item_id, active=0)
        flash("Knowledge item deactivated. Its history was preserved.", "success")
        return redirect(url_for("knowledge"))

    @app.get("/clarifications")
    def clarifications():
        status = request.args.get("status") or "pending"
        return render_template(
            "clarifications.html",
            groups=database.list_clarification_groups(status) if status == "pending" else [],
            items=database.list_clarifications(status) if status != "pending" else [],
            selected_status=status,
        )

    @app.post("/clarifications/groups/<theme>/answer")
    def answer_clarification_group(theme: str):
        try:
            _, count = database.answer_clarification_group(
                theme, request.form.get("answer", "")
            )
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        else:
            flash(
                f"One approved rule was saved to Knowledge and applied to {count} "
                "conversation(s). No scoring run was started.",
                "success",
            )
        return redirect(url_for("clarifications", status="pending"))

    @app.post("/clarifications/groups/<theme>/ignore")
    def ignore_clarification_group(theme: str):
        try:
            count = database.ignore_clarification_group(theme)
        except KeyError as exc:
            flash(str(exc), "error")
        else:
            flash(f"Ignored {count} conversation(s) in this pattern.", "success")
        return redirect(url_for("clarifications", status="pending"))

    @app.post("/clarifications/<int:item_id>/answer")
    def answer_clarification(item_id: int):
        try:
            knowledge_id = database.answer_clarification(
                item_id,
                request.form.get("answer", ""),
                add_to_knowledge=True,
            )
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        else:
            learned_count = (
                database.clarification_learning_count(knowledge_id) if knowledge_id else 1
            )
            flash(
                f"Answer learned and saved to Knowledge. {learned_count} matching "
                "conversation(s) were queued for re-scoring.",
                "success",
            )
        return redirect(url_for("clarifications"))

    @app.post("/clarifications/<int:item_id>/ignore")
    def ignore_clarification(item_id: int):
        database.ignore_clarification(item_id)
        flash("Question ignored. The conversation remains unscored.", "success")
        return redirect(url_for("clarifications"))

    @app.post("/clarifications/<int:item_id>/revise")
    def revise_clarification(item_id: int):
        try:
            count = database.revise_clarification_answer(
                item_id, request.form.get("answer", "")
            )
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        else:
            flash(
                f"Knowledge was updated for {count} linked conversation(s). "
                "No scoring run was started.",
                "success",
            )
        return redirect(url_for("clarifications", status="answered"))

    @app.get("/aliases")
    def aliases():
        return render_template(
            "aliases.html",
            aliases=database.list_aliases(request.args.get("status") or None),
            agents=database.list_agents(),
        )

    @app.post("/aliases/<int:alias_id>/accept")
    def accept_alias(alias_id: int):
        try:
            database.review_alias(alias_id, "accepted", request.form.get("agent_id") or None)
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        else:
            flash("Alias accepted and existing conversations were updated.", "success")
        return redirect(url_for("aliases"))

    @app.post("/aliases/<int:alias_id>/reject")
    def reject_alias(alias_id: int):
        try:
            database.review_alias(alias_id, "rejected")
        except (KeyError, ValueError) as exc:
            flash(str(exc), "error")
        else:
            flash("Alias rejected.", "success")
        return redirect(url_for("aliases"))

    @app.get("/results")
    def results():
        summary = database.dashboard(request.args.get("run_id"))
        return render_template("results.html", summary=summary, runs=database.list_runs())

    return app


def serve(db_path: str | Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The review UI is local-only and must bind to a loopback address")
    create_app(db_path).run(host=host, port=port, debug=False)
