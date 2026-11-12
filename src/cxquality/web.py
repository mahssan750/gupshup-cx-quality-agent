"""Local Flask dashboard and review UI."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for

from .db import Database
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

    @app.get("/")
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

    @app.get("/api/runs/<run_id>")
    def run_status(run_id: str):
        summary = database.dashboard(run_id)
        if not summary["run"]:
            return jsonify({"error": "run not found"}), 404
        return jsonify(summary)

    @app.get("/knowledge")
    def knowledge():
        return render_template("knowledge.html", items=database.list_knowledge())

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
