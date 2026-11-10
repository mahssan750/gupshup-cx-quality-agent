"""Local Flask dashboard and review UI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for

from .db import Database


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

    @app.route("/runs/new", methods=["GET", "POST"])
    def new_run():
        if request.method == "POST":
            try:
                sample_raw = request.form.get("sample_size", "").strip()
                budget = float(request.form.get("budget_usd", "4"))
                stop_at = float(request.form.get("stop_at_usd", "3.8"))
                zip_folder = Path(request.form["zip_folder"].strip())
                roster_raw = request.form.get("roster_path", "").strip()
                roster_path = Path(roster_raw) if roster_raw else None
                if not zip_folder.is_dir():
                    raise ValueError(f"ZIP folder was not found: {zip_folder}")
                if roster_path and not roster_path.is_file():
                    raise ValueError(f"Roster workbook was not found: {roster_path}")
                run_id = database.create_run(
                    from_date=request.form["from_date"],
                    to_date=request.form["to_date"],
                    zip_folder=str(zip_folder),
                    roster_path=str(roster_path) if roster_path else None,
                    budget_usd=budget,
                    stop_at_usd=stop_at,
                    sample_size=int(sample_raw) if sample_raw else None,
                )
            except (KeyError, ValueError) as exc:
                flash(str(exc), "error")
            else:
                if not app.config["TESTING"]:
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
        return render_template("run_new.html")

    @app.get("/runs/<run_id>")
    def run_detail(run_id: str):
        run = database.get_run(run_id)
        if not run:
            abort(404)
        return render_template("run_detail.html", run=run, summary=database.dashboard(run_id))

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
        status = request.args.get("status") or None
        return render_template(
            "clarifications.html",
            items=database.list_clarifications(status),
            selected_status=status,
        )

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
