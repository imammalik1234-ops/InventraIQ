"""Authenticated/CSRF-protected transport for deterministic read tools only."""
import json
from flask import Blueprint,Response,request
from werkzeug.exceptions import HTTPException
from assistant import tools,artifacts

bp=Blueprint('assistant_tools',__name__)


def init_app(app):
    app.register_blueprint(bp)
    from assistant.conversation_routes import bp as conversations_bp
    app.register_blueprint(conversations_bp)
    from assistant.proposal_routes import bp as proposals_bp
    app.register_blueprint(proposals_bp)
    # Authz inspects JSON before a view runs. Apply the body budget before that
    # inspection, including chunked bodies, rather than only after parsing.
    def input_budget():
        if request.endpoint=='assistant_tools.invoke_route' or (request.endpoint or '').startswith(('assistant_conversations.','assistant_proposals.')):
            request.max_content_length=tools.MAX_INPUT_BYTES
            if request.content_length is not None and request.content_length>tools.MAX_INPUT_BYTES:
                return tools._failure(None,'input_limit','Request exceeds the input budget.'),413
    app.before_request_funcs.setdefault(None,[]).insert(0,input_budget)


@bp.errorhandler(413)
def body_limit(error):return tools._failure(None,'input_limit','Request exceeds the input budget.'),413


@bp.get('/api/assistant/tools')
def catalog_route():
    if request.args:return {'error':'Unsupported parameters.'},400
    return dict(schema_version=tools.VERSION,tools=tools.catalog())


def _pairs(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError()
        value[key]=item
    return value


@bp.post('/api/assistant/tools/<name>')
def invoke_route(name):
    if request.args or not request.is_json:return tools._failure(None,'invalid_arguments','A JSON object without query parameters is required.'),400
    if request.content_length is not None and request.content_length>tools.MAX_INPUT_BYTES:
        return tools._failure(None,'input_limit','Request exceeds the input budget.'),413
    try:
        # The existing authorization middleware may already have cached JSON.
        raw=request.get_data(cache=True)
        if len(raw)>tools.MAX_INPUT_BYTES:return tools._failure(None,'input_limit','Request exceeds the input budget.'),413
        values=json.loads(raw,object_pairs_hook=_pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (ValueError,UnicodeError,RecursionError):
        return tools._failure(None,'invalid_arguments','Malformed JSON arguments.'),400
    result=tools.invoke(name,values)
    status=200 if result['status']!='error' else {'access_denied':403,'not_found':404,'version_conflict':409,'unavailable':503,'output_limit':413,'unsupported_tool':400}.get(result['error']['code'],400)
    return result,status


@bp.get('/assistant/artifacts/<artifact_id>')
def download(artifact_id):
    if request.args:return {'error':'Artifact unavailable.'},404
    try:item=artifacts.retrieve(artifact_id)
    except HTTPException:return {'error':'Artifact unavailable.'},404
    extension='csv' if item.media_type.startswith('text/csv') else 'json'
    return Response(item.content,content_type=item.media_type,headers={
        'Content-Disposition':f'attachment; filename="inventraiq-{artifact_id}.{extension}"',
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
        'Content-Security-Policy':"default-src 'none'; sandbox"})
