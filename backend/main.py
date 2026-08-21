"""
HEAT Assessment API (Flask).

This is the backend the static assessment form talks to. It:
  1. Saves assessments into PlayerDev.hitting_assessments
  2. Lets the form list prior assessments (for previous-date confirm)
  3. Builds draft PDFs (local and/or GCS) via report_pipeline

Deployed on Google Cloud Run; MySQL credentials come from environment variables
(see db.get_db_connection).
"""

import os
import logging
import json
import re
from datetime import datetime
from io import BytesIO
from typing import Optional

from flask import Flask, request, jsonify, Response, send_file
from flask_cors import CORS
import mysql.connector

from db import get_db_connection

app = Flask(__name__)
# Trainer image uploads (multipart) — several files up to ~4 MB each
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

# Allow the GCS-hosted HTML form (and local file:// / other origins) to call /api/*.
CORS(app, resources={
    r"/api/*": {
        "origins": ["*"],
        "methods": ["POST", "GET", "DELETE", "PUT", "OPTIONS"],
        "allow_headers": ["Content-Type"]
    }
})

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Columns returned by GET endpoints (includes M1 comparison fields).
ASSESSMENT_SELECT_COLUMNS = """
    assessment_id,
    assessment_date,
    player_name,
    trainer_name,
    notes,
    video_analysis_url,
    used_blast,
    used_hittrax,
    used_vald,
    assessment_type,
    previous_assessment_id,
    report_gcs_uri,
    mechanics_phase_notes,
    mechanical_summary,
    training_focus,
    best_of_day_summary,
    created_at,
    updated_at
"""

MECHANICS_PHASE_SLOTS = (
    "load_phase",
    "load_position",
    "stride_phase",
    "launch_position",
    "impact",
)
MECHANICS_PHASE_STATUSES = ("strength", "monitor", "development")
SUMMARY_TEXT_MAX = 8000


def _parse_optional_int(value, field_name):
    """
    Parse an optional integer from JSON.

    Returns (ok, value_or_None, error_message).
    Empty / missing → (True, None, None).
    """
    if value is None or value == '':
        return True, None, None
    try:
        return True, int(value), None
    except (TypeError, ValueError):
        return False, None, f"{field_name} must be an integer"


def _parse_bool(value, default=True):
    """Parse JSON booleans while keeping omitted legacy fields enabled."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _normalize_video_url(value) -> Optional[str]:
    """Return a cleaned http(s) URL or None. Empty is allowed."""
    if value is None:
        return None
    url = str(value).strip()
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url[:1024]
    if url.startswith("youtu.be/") or url.startswith("www."):
        return ("https://" + url)[:1024]
    return None


def _normalize_optional_text(value, field_name, max_len=SUMMARY_TEXT_MAX):
    """Return (ok, cleaned_or_None, error). Empty → None."""
    if value is None:
        return True, None, None
    text = str(value).strip()
    if not text:
        return True, None, None
    return True, text[:max_len], None


def _normalize_mechanics_phase_notes(value):
    """
    Parse mechanicsPhaseNotes from JSON body into a cleaned dict or None.

    Each slot is {status, caption}. Legacy string values become caption-only.
    Returns (ok, value_or_None, error_message).
    """
    if value is None or value == "":
        return True, None, None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False, None, "mechanicsPhaseNotes must be a JSON object"
    if not isinstance(value, dict):
        return False, None, "mechanicsPhaseNotes must be an object"
    cleaned = {}
    for key, raw in value.items():
        slot = str(key).strip()
        if slot not in MECHANICS_PHASE_SLOTS:
            return False, None, f"Unknown mechanics phase slot: {slot}"
        status = ""
        caption = ""
        if isinstance(raw, dict):
            status = str(raw.get("status") or "").strip().lower()
            caption = str(
                raw.get("caption") or raw.get("notes") or raw.get("text") or ""
            ).strip()
        else:
            caption = str(raw or "").strip()
        if status and status not in MECHANICS_PHASE_STATUSES:
            return False, None, f"Invalid status for {slot}"
        entry = {}
        if status:
            entry["status"] = status
        if caption:
            entry["caption"] = caption[:SUMMARY_TEXT_MAX]
        if entry:
            cleaned[slot] = entry
    return True, (cleaned or None), None


def serialize_assessment_row(row):
    """
    Make a MySQL row JSON-safe.

    mysql.connector returns date/datetime objects; jsonify needs strings.
    Adds report_url (signed when possible) for browser-openable PDF links.
    """
    if not row:
        return row
    out = dict(row)
    if out.get('assessment_date'):
        out['assessment_date'] = out['assessment_date'].strftime('%Y-%m-%d')
    if out.get('created_at'):
        out['created_at'] = out['created_at'].strftime('%Y-%m-%d %H:%M:%S')
    if out.get('updated_at'):
        out['updated_at'] = out['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
    phase_notes = out.get("mechanics_phase_notes")
    if isinstance(phase_notes, str):
        try:
            phase_notes = json.loads(phase_notes)
        except (TypeError, ValueError, json.JSONDecodeError):
            phase_notes = None
    if phase_notes is not None and not isinstance(phase_notes, dict):
        phase_notes = None
    out["mechanics_phase_notes"] = phase_notes or None
    gcs_uri = out.get("report_gcs_uri")
    if gcs_uri:
        try:
            import gcs_upload

            out["report_url"] = gcs_upload.accessible_url(gcs_uri) or gcs_uri
        except Exception:
            out["report_url"] = gcs_uri
    else:
        out["report_url"] = None
    return out


def validate_assessment_data(data):
    """
    Validate POST body from the static form.

    Required: playerName, assessmentDate, assessmentType (initial | retest).
    previousAssessmentId is optional (trainer confirms which prior date to compare).
    baseline is never sent by the form — metrics resolve it as earliest initial.

    On success: data['_previousAssessmentId']
    """
    required_fields = ['playerName', 'assessmentDate', 'assessmentType']

    for field in required_fields:
        if field not in data or data[field] == '':
            return False, f"Missing required field: {field}"

    if len(data['playerName'].strip()) < 2:
        return False, "Player name must be at least 2 characters"

    try:
        datetime.strptime(data['assessmentDate'], '%Y-%m-%d')
    except ValueError:
        return False, "Invalid date format. Use YYYY-MM-DD"

    if 'trainerName' in data and data['trainerName']:
        if len(data['trainerName'].strip()) < 2:
            return False, "Trainer name must be at least 2 characters"

    assessment_type = str(data['assessmentType']).strip().lower()
    if assessment_type not in ('initial', 'retest'):
        return False, "assessmentType must be 'initial' or 'retest'"
    data['assessmentType'] = assessment_type

    ok, previous_id, err = _parse_optional_int(
        data.get('previousAssessmentId'), 'previousAssessmentId'
    )
    if not ok:
        return False, err

    # Initial assessments have no comparison peers.
    if assessment_type == 'initial':
        previous_id = None

    data['_previousAssessmentId'] = previous_id
    data['_usedBlast'] = _parse_bool(data.get('usedBlast'))
    data['_usedHittrax'] = _parse_bool(data.get('usedHittrax'))
    data['_usedVald'] = _parse_bool(data.get('usedVald'))

    raw_video = data.get('videoAnalysisUrl')
    if raw_video is not None and str(raw_video).strip():
        video_url = _normalize_video_url(raw_video)
        if not video_url:
            return False, "videoAnalysisUrl must be an http(s) link"
        data['_videoAnalysisUrl'] = video_url
    else:
        data['_videoAnalysisUrl'] = None

    notes_ok, phase_notes, notes_err = _normalize_mechanics_phase_notes(
        data.get("mechanicsPhaseNotes")
    )
    if not notes_ok:
        return False, notes_err
    data["_mechanicsPhaseNotes"] = phase_notes

    mech_ok, mech_summary, mech_err = _normalize_optional_text(
        data.get("mechanicalSummary"), "mechanicalSummary"
    )
    if not mech_ok:
        return False, mech_err
    data["_mechanicalSummary"] = mech_summary

    focus_ok, focus, focus_err = _normalize_optional_text(
        data.get("trainingFocus"), "trainingFocus"
    )
    if not focus_ok:
        return False, focus_err
    data["_trainingFocus"] = focus

    notes_text_ok, notes_text, notes_text_err = _normalize_optional_text(
        data.get("notes"), "notes"
    )
    if not notes_text_ok:
        return False, notes_text_err
    data["_notes"] = notes_text

    bod_ok, bod_summary, bod_err = _normalize_optional_text(
        data.get("bestOfDaySummary"), "bestOfDaySummary"
    )
    if not bod_ok:
        return False, bod_err
    data["_bestOfDaySummary"] = bod_summary
    return True, None


def fetch_assessment_by_id(cursor, assessment_id):
    """Load one hitting_assessments row (dict cursor) or None."""
    cursor.execute(
        f"SELECT {ASSESSMENT_SELECT_COLUMNS} FROM hitting_assessments WHERE assessment_id = %s",
        (assessment_id,),
    )
    return cursor.fetchone()


def validate_comparison_peers(cursor, player_name, previous_id):
    """
    DB check for retest links: previous ID must exist and match this player.
    """
    if previous_id is None:
        return True, None
    peer = fetch_assessment_by_id(cursor, previous_id)
    if not peer:
        return False, f"previous assessment {previous_id} not found"
    if peer['player_name'].strip().casefold() != player_name.strip().casefold():
        return False, f"previous assessment {previous_id} belongs to a different player"
    return True, None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.route('/health', methods=['GET'])
def health_check():
    """Cloud Run / load balancer probe — also verifies MySQL is reachable."""
    try:
        conn = get_db_connection()
        conn.close()
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as e:
        logger.error("Health check failed: %s", e)
        return jsonify({"status": "unhealthy", "error": str(e)}), 503


# ---------------------------------------------------------------------------
# Create assessment (form submit)
# ---------------------------------------------------------------------------

@app.route('/api/hitting-assessment', methods=['POST', 'OPTIONS'])
def submit_assessment():
    """
    Create a hitting_assessments row.

    OPTIONS: browser CORS preflight (empty 204).
    POST JSON example (retest — only previous is trainer-chosen):
      {
        "playerName": "Jason Peele",
        "assessmentDate": "2026-07-20",
        "assessmentType": "retest",
        "previousAssessmentId": 15,
        "trainerName": "Noah",
        "notes": "optional"
      }
    """
    # Browsers send OPTIONS before cross-origin POST; answer without hitting the DB.
    if request.method == 'OPTIONS':
        return '', 204

    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400

        is_valid, error_message = validate_assessment_data(data)
        if not is_valid:
            logger.warning("Validation failed: %s", error_message)
            return jsonify({"error": error_message}), 400

        player_name = data['playerName'].strip()
        previous_id = data['_previousAssessmentId']

        connection = get_db_connection()
        try:
            # dictionary=True → rows as dicts (needed by validate_comparison_peers)
            with connection.cursor(dictionary=True) as cursor:
                peers_ok, peers_err = validate_comparison_peers(
                    cursor, player_name, previous_id
                )
                if not peers_ok:
                    return jsonify({"error": peers_err}), 400

                # Baseline is not stored — metrics resolve earliest initial at read time.
                phase_notes = data.get("_mechanicsPhaseNotes")
                phase_notes_json = (
                    json.dumps(phase_notes) if phase_notes else None
                )
                sql = """
                    INSERT INTO hitting_assessments
                    (assessment_date, player_name, trainer_name, notes,
                     video_analysis_url,
                     used_blast, used_hittrax, used_vald,
                     assessment_type, previous_assessment_id,
                     mechanics_phase_notes, mechanical_summary,
                     training_focus, best_of_day_summary)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
                values = (
                    data['assessmentDate'],
                    player_name,
                    data.get('trainerName', '').strip() or None,
                    data.get("_notes"),
                    data['_videoAnalysisUrl'],
                    data['_usedBlast'],
                    data['_usedHittrax'],
                    data['_usedVald'],
                    data['assessmentType'],
                    previous_id,
                    phase_notes_json,
                    data.get("_mechanicalSummary"),
                    data.get("_trainingFocus"),
                    data.get("_bestOfDaySummary"),
                )
                cursor.execute(sql, values)
                connection.commit()
                # Auto-increment PK assigned by MySQL for this insert
                assessment_id = cursor.lastrowid
                logger.info(
                    "Assessment saved for player: %s, ID: %s, type: %s",
                    player_name,
                    assessment_id,
                    data['assessmentType'],
                )

                # M3: draft PDF on submit (local file:// or GCS if HEAT_GCS_BUCKET set)
                report_uri = None
                report_meta = None
                try:
                    from report_pipeline import generate_and_store_report

                    row = fetch_assessment_by_id(cursor, assessment_id)
                    report_meta = generate_and_store_report(connection, row)
                    if report_meta:
                        report_uri = report_meta.get("report_uri")
                except Exception as report_err:
                    logger.exception(
                        "Assessment %s saved but report failed: %s",
                        assessment_id,
                        report_err,
                    )

                return jsonify({
                    "success": True,
                    "message": "Hitting assessment submitted successfully",
                    "assessment_id": assessment_id,
                    "player": player_name,
                    "assessment_date": data['assessmentDate'],
                    "assessment_type": data['assessmentType'],
                    "previous_assessment_id": previous_id,
                    "report_uri": report_uri,
                    "report_url": report_uri,
                    "report_gcs_uri": (report_meta or {}).get("gcs_uri") or report_uri,
                    "report_version": (report_meta or {}).get("version_num"),
                }), 201
        finally:
            # Always close even if validation/insert fails after connect
            connection.close()

    except mysql.connector.Error as e:
        logger.error("Database error: %s", e)
        return jsonify({"error": "Database error occurred"}), 500
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        return jsonify({"error": "An unexpected error occurred"}), 500


# ---------------------------------------------------------------------------
# Read assessments (power form autocomplete / retest dropdowns)
# ---------------------------------------------------------------------------

@app.route("/api/hitting-assessment/data-peek", methods=["GET", "OPTIONS"])
def peek_hitting_assessment_data():
    """
    Counts of Blast / HitTrax / VALD rows for a player on one calendar day.

    Used by the form Session step so trainers can uncheck tools with no data.
    Query: playerName (or player) + date (YYYY-MM-DD).
    """
    if request.method == "OPTIONS":
        return "", 204
    player = (
        request.args.get("playerName")
        or request.args.get("player")
        or ""
    ).strip()
    date_str = (
        request.args.get("date")
        or request.args.get("assessmentDate")
        or ""
    ).strip()
    if len(player) < 2:
        return jsonify({"error": "playerName is required"}), 400
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400
    try:
        import report_metrics

        connection = get_db_connection()
        try:
            peek = report_metrics.peek_session_metrics(connection, player, date_str)
            return jsonify(peek), 200
        finally:
            connection.close()
    except Exception as e:
        logger.exception("data-peek failed for %s %s", player, date_str)
        return jsonify({"error": str(e)}), 500


@app.route("/api/hitting-assessment/player-bio", methods=["GET", "OPTIONS"])
def peek_player_directory_bio():
    """
    Height / weight / age from player_directory for the form peek.

    Query: playerName (or player) + optional date (YYYY-MM-DD) for age.
    """
    if request.method == "OPTIONS":
        return "", 204
    player = (
        request.args.get("playerName")
        or request.args.get("player")
        or ""
    ).strip()
    date_str = (
        request.args.get("date")
        or request.args.get("assessmentDate")
        or ""
    ).strip()
    if len(player) < 2:
        return jsonify({"error": "playerName is required"}), 400
    on_date = None
    if date_str:
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            on_date = date_str
        except ValueError:
            return jsonify({"error": "date must be YYYY-MM-DD"}), 400
    try:
        import report_metrics

        connection = get_db_connection()
        try:
            bio = report_metrics.lookup_player_directory_bio(
                connection, player, on_date
            )
            return jsonify({"player_name": player, **bio}), 200
        finally:
            connection.close()
    except Exception as e:
        logger.exception("player-bio peek failed for %s", player)
        return jsonify({"error": str(e)}), 500


@app.route("/api/hitting-assessment/roster", methods=["GET", "OPTIONS"])
def search_hitting_assessment_roster():
    """
    Typeahead roster: player_directory first, then prior assessment names.

    Query: q (or playerName) + optional limit (default 20, max 40).
    """
    if request.method == "OPTIONS":
        return "", 204
    query = (
        request.args.get("q")
        or request.args.get("playerName")
        or request.args.get("player")
        or ""
    ).strip()
    if len(query) < 1:
        return jsonify({"query": query, "players": []}), 200
    limit = request.args.get("limit", 20, type=int)
    try:
        import report_metrics

        connection = get_db_connection()
        try:
            players = report_metrics.search_player_roster(
                connection, query, limit=limit
            )
            return jsonify({"query": query, "players": players}), 200
        finally:
            connection.close()
    except Exception as e:
        logger.exception("roster search failed for %s", query)
        return jsonify({"error": str(e)}), 500


@app.route("/api/hitting-assessment/draft-summaries", methods=["POST", "OPTIONS"])
def draft_hitting_assessment_summaries():
    """Fill one Notes-step textarea from Anthropic using form + session context."""
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "No data provided"}), 400
    notes_ok, phase_notes, notes_err = _normalize_mechanics_phase_notes(
        data.get("mechanicsPhaseNotes") or data.get("mechanics_phase_notes")
    )
    if not notes_ok:
        return jsonify({"error": notes_err}), 400
    payload = dict(data)
    payload["mechanicsPhaseNotes"] = phase_notes
    try:
        import report_drafts

        connection = get_db_connection()
        try:
            result = report_drafts.draft_summary(connection, payload)
            return jsonify(result), 200
        finally:
            connection.close()
    except Exception as e:
        import report_drafts as _drafts

        if isinstance(e, _drafts.DraftError):
            return jsonify({"error": str(e)}), e.status
        logger.exception("draft-summaries failed")
        return jsonify({"error": str(e)}), 500


@app.route('/api/hitting-assessment/<int:assessment_id>', methods=['GET'])
def get_assessment(assessment_id):
    """Return one assessment by primary key."""
    try:
        connection = get_db_connection()
        try:
            with connection.cursor(dictionary=True) as cursor:
                result = fetch_assessment_by_id(cursor, assessment_id)
                if not result:
                    return jsonify({"error": "Assessment not found"}), 404
                return jsonify(serialize_assessment_row(result)), 200
        finally:
            connection.close()
    except Exception as e:
        logger.error("Error retrieving assessment: %s", e)
        return jsonify({"error": "An error occurred"}), 500


@app.route('/api/hitting-assessment/player/<player_name>', methods=['GET'])
def get_player_assessments(player_name):
    """
    All assessments for a player (newest first).

    The form calls this after name select to list prior dates (previous confirm).
    """
    try:
        connection = get_db_connection()
        try:
            with connection.cursor(dictionary=True) as cursor:
                sql = f"""
                    SELECT {ASSESSMENT_SELECT_COLUMNS}
                    FROM hitting_assessments
                    WHERE LOWER(player_name) = LOWER(%s)
                    ORDER BY assessment_date DESC, assessment_id DESC
                """
                cursor.execute(sql, (player_name,))
                results = [serialize_assessment_row(r) for r in cursor.fetchall()]
                # Prefer canonical casing from DB when we have matches
                canonical = results[0]["player_name"] if results else player_name
                return jsonify({
                    "player_name": canonical,
                    "total_assessments": len(results),
                    "assessments": results
                }), 200
        finally:
            connection.close()
    except Exception as e:
        logger.error("Error retrieving player assessments: %s", e)
        return jsonify({"error": "An error occurred"}), 500


@app.route('/api/hitting-assessment/recent', methods=['GET'])
def get_recent_assessments():
    """
    Recent assessments (default last 30 days).

    Used for form autocomplete of player names. Optional ?limit=N (default 50).
    """
    try:
        limit = request.args.get('limit', 50, type=int)
        connection = get_db_connection()
        try:
            with connection.cursor(dictionary=True) as cursor:
                sql = f"""
                    SELECT {ASSESSMENT_SELECT_COLUMNS}
                    FROM hitting_assessments
                    WHERE assessment_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                    ORDER BY assessment_date DESC, created_at DESC
                    LIMIT %s
                """
                cursor.execute(sql, (limit,))
                results = [serialize_assessment_row(r) for r in cursor.fetchall()]
                return jsonify({
                    "total": len(results),
                    "assessments": results
                }), 200
        finally:
            connection.close()
    except Exception as e:
        logger.error("Error retrieving recent assessments: %s", e)
        return jsonify({"error": "An error occurred"}), 500


# ---------------------------------------------------------------------------
# Metrics (M2) + report generation (M3)
# ---------------------------------------------------------------------------

@app.route('/api/hitting-assessment/<int:assessment_id>/metrics', methods=['GET'])
def get_hitting_assessment_metrics(assessment_id):
    """
    Return Blast + HitTrax aggregates for this assessment (calendar day only).

    Also includes previous (stored) and baseline (auto earliest initial) blocks.
    """
    try:
        import report_metrics

        connection = get_db_connection()
        try:
            with connection.cursor(dictionary=True) as cursor:
                row = fetch_assessment_by_id(cursor, assessment_id)
                if not row:
                    return jsonify({"error": "Assessment not found"}), 404
            bundle = report_metrics.build_report_bundle_json(connection, row)
            return jsonify({
                "assessment_id": assessment_id,
                "metrics": bundle,
            }), 200
        finally:
            connection.close()
    except Exception as e:
        logger.exception("Error building metrics for %s", assessment_id)
        return jsonify({"error": str(e)}), 500


@app.route('/api/hitting-assessment/<int:assessment_id>/report', methods=['POST'])
def regenerate_hitting_assessment_report(assessment_id):
    """
    Build draft PDF for this assessment (metrics → ReportLab → local / GCS).

    Optional JSON may update notes, videoAnalysisUrl, and
    usedBlast/usedHittrax/usedVald before generation. Omitted fields retain
    their stored values.
    Stores the resulting URI on hitting_assessments.report_gcs_uri and appends
    a row to assessment_report_versions (prior PDFs are kept).
    Without HEAT_GCS_BUCKET, returns a local file:// path.
    """
    try:
        from report_pipeline import generate_and_store_report

        connection = get_db_connection()
        try:
            with connection.cursor(dictionary=True) as cursor:
                row = fetch_assessment_by_id(cursor, assessment_id)
                if not row:
                    return jsonify({"error": "Assessment not found"}), 404

                data = request.get_json(silent=True) or {}
                updates = []
                values = []
                if "notes" in data:
                    ok, text, err = _normalize_optional_text(
                        data.get("notes"), "notes"
                    )
                    if not ok:
                        return jsonify({"error": err}), 400
                    updates.append("notes = %s")
                    values.append(text)
                if "videoAnalysisUrl" in data:
                    raw_video = data.get("videoAnalysisUrl")
                    if raw_video is not None and str(raw_video).strip():
                        video_url = _normalize_video_url(raw_video)
                        if not video_url:
                            return jsonify({
                                "error": "videoAnalysisUrl must be an http(s) link"
                            }), 400
                    else:
                        video_url = None
                    updates.append("video_analysis_url = %s")
                    values.append(video_url)
                for json_key, column in (
                    ("usedBlast", "used_blast"),
                    ("usedHittrax", "used_hittrax"),
                    ("usedVald", "used_vald"),
                ):
                    if json_key in data:
                        updates.append(f"{column} = %s")
                        values.append(_parse_bool(data[json_key]))
                if "mechanicsPhaseNotes" in data:
                    notes_ok, phase_notes, notes_err = _normalize_mechanics_phase_notes(
                        data.get("mechanicsPhaseNotes")
                    )
                    if not notes_ok:
                        return jsonify({"error": notes_err}), 400
                    updates.append("mechanics_phase_notes = %s")
                    values.append(
                        json.dumps(phase_notes) if phase_notes else None
                    )
                for json_key, column, field_name in (
                    ("mechanicalSummary", "mechanical_summary", "mechanicalSummary"),
                    ("trainingFocus", "training_focus", "trainingFocus"),
                    ("bestOfDaySummary", "best_of_day_summary", "bestOfDaySummary"),
                ):
                    if json_key in data:
                        ok, text, err = _normalize_optional_text(
                            data.get(json_key), field_name
                        )
                        if not ok:
                            return jsonify({"error": err}), 400
                        updates.append(f"{column} = %s")
                        values.append(text)
                if updates:
                    values.append(assessment_id)
                    cursor.execute(
                        f"""
                        UPDATE hitting_assessments
                        SET {", ".join(updates)}
                        WHERE assessment_id = %s
                        """,
                        tuple(values),
                    )
                    connection.commit()
                    row = fetch_assessment_by_id(cursor, assessment_id)

            meta = generate_and_store_report(connection, row)
            if not meta or not meta.get("report_uri"):
                return jsonify({"error": "Report generation returned no URI"}), 500

            return jsonify({
                "assessment_id": assessment_id,
                "status": "ok",
                "report_uri": meta.get("report_uri"),
                "report_url": meta.get("report_url"),
                "report_gcs_uri": meta.get("gcs_uri"),
                "report_version": meta.get("version_num"),
            }), 200
        finally:
            connection.close()
    except Exception as e:
        logger.exception("Error generating report for %s", assessment_id)
        return jsonify({"error": str(e)}), 500


@app.route(
    "/api/hitting-assessment/<int:assessment_id>/report-versions",
    methods=["GET", "OPTIONS"],
)
def list_hitting_assessment_report_versions(assessment_id):
    """List PDF versions for an assessment (newest first), with signed URLs."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        import report_versions

        connection = get_db_connection()
        try:
            with connection.cursor(dictionary=True) as cursor:
                row = fetch_assessment_by_id(cursor, assessment_id)
                if not row:
                    return jsonify({"error": "Assessment not found"}), 404
            versions = report_versions.list_versions(connection, assessment_id)
            return jsonify({
                "assessment_id": assessment_id,
                "versions": versions,
            }), 200
        finally:
            connection.close()
    except Exception as e:
        logger.exception("Error listing report versions for %s", assessment_id)
        return jsonify({"error": str(e)}), 500


def _download_filename(row: dict, version_num=None) -> str:
    player = (row.get("player_name") or "player").strip()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", player).strip("_").lower() or "player"
    adate = row.get("assessment_date")
    if hasattr(adate, "strftime"):
        date_part = adate.strftime("%Y-%m-%d")
    else:
        date_part = str(adate or "unknown")[:10]
    aid = row.get("assessment_id")
    if version_num is not None:
        return f"{slug}_{date_part}_{aid}_v{version_num}.pdf"
    return f"{slug}_{date_part}_{aid}.pdf"


@app.route(
    "/api/hitting-assessment/<int:assessment_id>/report/download",
    methods=["GET", "OPTIONS"],
)
def download_hitting_assessment_report(assessment_id):
    """
    Stream the PDF as an attachment so the browser downloads it.

    Optional ?version=N selects a historical version; otherwise latest
    hitting_assessments.report_gcs_uri is used.
    """
    if request.method == "OPTIONS":
        return "", 204
    try:
        import gcs_upload
        import report_versions

        version_raw = request.args.get("version")
        version_num = None
        if version_raw not in (None, ""):
            try:
                version_num = int(version_raw)
            except (TypeError, ValueError):
                return jsonify({"error": "version must be an integer"}), 400

        connection = get_db_connection()
        try:
            with connection.cursor(dictionary=True) as cursor:
                row = fetch_assessment_by_id(cursor, assessment_id)
                if not row:
                    return jsonify({"error": "Assessment not found"}), 404

            uri = None
            if version_num is not None:
                versions = report_versions.list_versions(connection, assessment_id)
                match = next(
                    (v for v in versions if int(v.get("version_num") or 0) == version_num),
                    None,
                )
                if not match:
                    return jsonify({"error": f"Version {version_num} not found"}), 404
                uri = match.get("gcs_uri")
            else:
                uri = row.get("report_gcs_uri")

            if not uri:
                return jsonify({"error": "No PDF available for this assessment"}), 404

            data = gcs_upload.download_bytes(uri)
            if data is None:
                return jsonify({"error": "Could not read PDF"}), 404

            filename = _download_filename(row, version_num)
            return Response(
                data,
                mimetype="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Length": str(len(data)),
                    "Cache-Control": "no-store",
                },
            )
        finally:
            connection.close()
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        logger.exception("Error downloading report for %s", assessment_id)
        return jsonify({"error": str(e)}), 500


def _regenerate_and_store(connection, row, assessment_id, generate_draft_report=None):
    """Rebuild the PDF and record its URI + version; never raises."""
    try:
        from report_pipeline import generate_and_store_report

        meta = generate_and_store_report(connection, row)
        if not meta:
            return None
        return meta.get("report_uri")
    except Exception as report_err:
        logger.exception(
            "Report regen failed for %s: %s", assessment_id, report_err
        )
        return None


@app.route(
    "/api/hitting-assessment/<int:assessment_id>/attachments/<int:attachment_id>/file",
    methods=["GET", "OPTIONS"],
)
def download_assessment_attachment_file(assessment_id, attachment_id):
    """Stream one attached image so the form can preview it."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        import attachments as attachments_mod

        connection = get_db_connection()
        try:
            with connection.cursor(dictionary=True) as cursor:
                row = fetch_assessment_by_id(cursor, assessment_id)
                if not row:
                    return jsonify({"error": "Assessment not found"}), 404
            att = next(
                (
                    a
                    for a in attachments_mod.list_attachments(connection, assessment_id)
                    if int(a["attachment_id"]) == attachment_id
                ),
                None,
            )
            if not att:
                return jsonify({"error": "Attachment not found"}), 404
            data = attachments_mod.load_image_bytes(att)
            if not data:
                return jsonify({"error": "Could not read image"}), 404
            mime = att.get("content_type") or "image/jpeg"
            return send_file(
                BytesIO(data),
                mimetype=mime,
                download_name=f"attachment_{attachment_id}",
                as_attachment=False,
                max_age=120,
            )
        finally:
            connection.close()
    except Exception as e:
        logger.exception(
            "Attachment file download failed for %s/%s",
            assessment_id,
            attachment_id,
        )
        return jsonify({"error": str(e)}), 500


@app.route(
    "/api/hitting-assessment/<int:assessment_id>/attachments/captions",
    methods=["PUT", "OPTIONS"],
)
def update_assessment_attachment_captions(assessment_id):
    """Body: { "captions": { "12": "updated caption", ... } }."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        import attachments as attachments_mod

        data = request.get_json(silent=True) or {}
        raw = data.get("captions")
        if not isinstance(raw, dict) or not raw:
            return jsonify({"error": "captions must be a non-empty object"}), 400
        captions = {}
        try:
            for key, value in raw.items():
                captions[int(key)] = "" if value is None else str(value)
        except (TypeError, ValueError):
            return jsonify({"error": "caption keys must be attachment ids"}), 400

        connection = get_db_connection()
        try:
            with connection.cursor(dictionary=True) as cursor:
                row = fetch_assessment_by_id(cursor, assessment_id)
                if not row:
                    return jsonify({"error": "Assessment not found"}), 404
            updated = attachments_mod.update_attachment_captions(
                connection, assessment_id, captions
            )
            return jsonify({
                "success": True,
                "assessment_id": assessment_id,
                "updated": updated,
            }), 200
        finally:
            connection.close()
    except Exception as e:
        logger.exception("Update captions failed for %s", assessment_id)
        return jsonify({"error": str(e)}), 500


@app.route(
    "/api/hitting-assessment/<int:assessment_id>/attachments/crops",
    methods=["PUT", "OPTIONS"],
)
def update_assessment_attachment_crops(assessment_id):
    """Body: { "crops": { "12": {"x":0.1,"y":0.2,"w":0.5,"h":0.6}, ... } }."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        import attachments as attachments_mod

        data = request.get_json(silent=True) or {}
        raw = data.get("crops")
        if not isinstance(raw, dict) or not raw:
            return jsonify({"error": "crops must be a non-empty object"}), 400
        crops = {}
        try:
            for key, value in raw.items():
                crops[int(key)] = value
        except (TypeError, ValueError):
            return jsonify({"error": "crop keys must be attachment ids"}), 400

        connection = get_db_connection()
        try:
            with connection.cursor(dictionary=True) as cursor:
                row = fetch_assessment_by_id(cursor, assessment_id)
                if not row:
                    return jsonify({"error": "Assessment not found"}), 404
            updated = attachments_mod.update_attachment_crops(
                connection, assessment_id, crops
            )
            return jsonify({
                "success": True,
                "assessment_id": assessment_id,
                "updated": updated,
            }), 200
        finally:
            connection.close()
    except Exception as e:
        logger.exception("Update crops failed for %s", assessment_id)
        return jsonify({"error": str(e)}), 500


@app.route(
    "/api/hitting-assessment/<int:assessment_id>/attachments/order",
    methods=["PUT", "OPTIONS"],
)
def reorder_assessment_attachments(assessment_id):
    """
    Set PDF order for already-attached images.

    Body: { "ids": [12, 9, 15] } — permutation of every attachment id
    on this assessment. Mechanics photos within a phase follow this
    relative order on the PDF card.
    """
    if request.method == "OPTIONS":
        return "", 204
    try:
        import attachments as attachments_mod

        data = request.get_json(silent=True) or {}
        raw_ids = data.get("ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return jsonify({"error": "ids must be a non-empty array"}), 400
        try:
            ordered_ids = [int(i) for i in raw_ids]
        except (TypeError, ValueError):
            return jsonify({"error": "ids must be integers"}), 400

        connection = get_db_connection()
        try:
            with connection.cursor(dictionary=True) as cursor:
                row = fetch_assessment_by_id(cursor, assessment_id)
                if not row:
                    return jsonify({"error": "Assessment not found"}), 404
            try:
                applied = attachments_mod.reorder_attachments(
                    connection, assessment_id, ordered_ids
                )
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
            return jsonify({
                "success": True,
                "assessment_id": assessment_id,
                "ids": applied,
            }), 200
        finally:
            connection.close()
    except Exception as e:
        logger.exception("Reorder attachments failed for %s", assessment_id)
        return jsonify({"error": str(e)}), 500


@app.route(
    "/api/hitting-assessment/<int:assessment_id>/attachments",
    methods=["POST", "GET", "DELETE", "OPTIONS"],
)
def assessment_attachments(assessment_id):
    """
    Trainer visual context images.

    GET  — list metadata for this assessment.
    POST multipart/form-data:
      files: image fields named file / file0 / images (repeatable)
      captions: caption0, caption1, … matching file order
      slots:    slot0, slot1, … optional
                (load_phase|load_position|stride_phase|launch_position|impact|
                 blast|vald|hittrax|other)
      replace:  "1" to remove existing images before saving these
      regenerate: "1" (default) to rebuild PDF after upload
    DELETE ?ids=3,4 (or ?all=1) — remove images, then rebuild the PDF
      unless regenerate=0.
    """
    if request.method == "OPTIONS":
        return "", 204

    try:
        import attachments as attachments_mod

        connection = get_db_connection()
        try:
            with connection.cursor(dictionary=True) as cursor:
                row = fetch_assessment_by_id(cursor, assessment_id)
                if not row:
                    return jsonify({"error": "Assessment not found"}), 404

            if request.method == "GET":
                rows = attachments_mod.list_attachments(connection, assessment_id)
                out = []
                root = request.url_root.rstrip("/")
                for r in rows:
                    item = dict(r)
                    if item.get("created_at") and hasattr(item["created_at"], "strftime"):
                        item["created_at"] = item["created_at"].strftime("%Y-%m-%d %H:%M:%S")
                    aid = item.get("attachment_id")
                    if aid is not None:
                        item["file_url"] = (
                            f"{root}/api/hitting-assessment/{assessment_id}"
                            f"/attachments/{aid}/file"
                        )
                    out.append(item)
                return jsonify({"assessment_id": assessment_id, "attachments": out}), 200

            if request.method == "DELETE":
                ids_param = (request.args.get("ids") or "").strip()
                delete_all = request.args.get("all") in ("1", "true", "True")
                if not ids_param and not delete_all:
                    return jsonify({"error": "Pass ids=1,2 or all=1"}), 400
                target_ids = None
                if not delete_all:
                    try:
                        target_ids = [
                            int(part) for part in ids_param.split(",") if part.strip()
                        ]
                    except ValueError:
                        return jsonify({"error": "ids must be integers"}), 400

                removed = attachments_mod.delete_attachments(
                    connection, assessment_id, target_ids
                )
                report_uri = None
                if request.args.get("regenerate", "1") not in ("0", "false", "False"):
                    report_uri = _regenerate_and_store(
                        connection, row, assessment_id
                    )
                return jsonify({
                    "success": True,
                    "assessment_id": assessment_id,
                    "deleted": removed,
                    "report_uri": report_uri,
                    "report_gcs_uri": report_uri,
                }), 200

            # Collect uploaded files (support several field names)
            uploaded = []
            for key in request.files:
                for storage in request.files.getlist(key):
                    if storage and storage.filename:
                        uploaded.append(storage)

            if not uploaded:
                return jsonify({"error": "No image files provided"}), 400
            if len(uploaded) > attachments_mod.MAX_FILES:
                return jsonify({
                    "error": f"At most {attachments_mod.MAX_FILES} images per request"
                }), 400

            replaced = []
            if request.form.get("replace") in ("1", "true", "True"):
                replaced = attachments_mod.delete_attachments(connection, assessment_id)

            existing = attachments_mod.list_attachments(connection, assessment_id)
            incoming_slots = []
            for i, storage in enumerate(uploaded):
                slot = (
                    request.form.get(f"slot{i}")
                    or request.form.get(f"slots[{i}]")
                    or request.form.get("slot")
                    or "other"
                )
                incoming_slots.append(str(slot).strip() or "other")
            existing_by_slot: dict[str, int] = {}
            for att in existing:
                s = str(att.get("slot") or "").strip()
                if s in attachments_mod.MECHANICS_PHASE_SLOTS:
                    existing_by_slot[s] = existing_by_slot.get(s, 0) + 1
            incoming_by_slot: dict[str, int] = {}
            for s in incoming_slots:
                if s in attachments_mod.MECHANICS_PHASE_SLOTS:
                    incoming_by_slot[s] = incoming_by_slot.get(s, 0) + 1
            cap = attachments_mod.MAX_PHOTOS_PER_MECHANICS_PHASE
            for s, n in incoming_by_slot.items():
                if existing_by_slot.get(s, 0) + n > cap:
                    return jsonify({
                        "error": (
                            f"At most {cap} photo per mechanics phase "
                            f"({s.replace('_', ' ')}). Remove extras first."
                        )
                    }), 400

            sort_base = len(existing)
            saved = []
            errors = []

            for i, storage in enumerate(uploaded):
                raw = storage.read()
                ct = storage.mimetype or "application/octet-stream"
                caption = (
                    request.form.get(f"caption{i}")
                    or request.form.get(f"captions[{i}]")
                    or request.form.get("caption")
                    or ""
                )
                slot = incoming_slots[i]
                crop_raw = (
                    request.form.get(f"crop{i}")
                    or request.form.get(f"crops[{i}]")
                    or request.form.get("crop")
                    or ""
                )
                try:
                    uri, local_path = attachments_mod.store_image_bytes(
                        assessment_id=assessment_id,
                        player_name=row["player_name"],
                        assessment_date=row.get("assessment_date"),
                        raw=raw,
                        content_type=ct,
                        slot=str(slot).strip() or "other",
                        original_name=storage.filename or "",
                    )
                    aid = attachments_mod.save_attachment_row(
                        connection,
                        assessment_id=assessment_id,
                        slot=str(slot).strip() or "other",
                        caption=caption,
                        gcs_uri=uri,
                        local_path=local_path,
                        content_type=ct,
                        sort_order=sort_base + i,
                        crop=crop_raw,
                    )
                    saved.append({
                        "attachment_id": aid,
                        "gcs_uri": uri,
                        "caption": caption or None,
                        "slot": slot,
                        "crop": attachments_mod.parse_crop(crop_raw),
                    })
                except Exception as e:
                    logger.exception("Attachment upload failed")
                    errors.append({"file": storage.filename, "error": str(e)})

            if not saved and errors:
                return jsonify({"error": "All uploads failed", "details": errors}), 400

            report_uri = None
            regenerate = request.form.get("regenerate", "1") not in ("0", "false", "False")
            if regenerate and saved:
                report_uri = _regenerate_and_store(
                    connection, row, assessment_id
                )

            return jsonify({
                "success": True,
                "assessment_id": assessment_id,
                "uploaded": saved,
                "replaced": replaced,
                "errors": errors,
                "report_uri": report_uri,
                "report_gcs_uri": report_uri,
            }), 201
        finally:
            connection.close()
    except Exception as e:
        logger.exception("Attachments endpoint failed for %s", assessment_id)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Error handlers + local run
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found"}), 404


@app.errorhandler(500)
def internal_error(error):
    logger.error("Internal server error: %s", error)
    return jsonify({"error": "Internal server error"}), 500


if __name__ == '__main__':
    # Local only — production uses gunicorn (see Dockerfile).
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
