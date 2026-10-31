"""Jobs: long work started by a command (moves, scans) is followed here (jobs.py)."""
from flask import Blueprint, jsonify

from context import ctx

bp = Blueprint('jobs', __name__)


@bp.route('/api/jobs/<job_id>', methods=['GET'])
def job_status(job_id):
    """{id, kind, running, done, failed, total, phase, error, result, started_at, finished_at}."""
    job = ctx.jobs.get(job_id)
    if job is None:
        return jsonify({'error': 'Unknown job', 'code': 'job_unknown'}), 404
    return jsonify(job)
