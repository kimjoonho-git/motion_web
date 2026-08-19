from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse


def register_project_routes(app: FastAPI, bridge, project_call) -> None:
    @app.get('/api/projects')
    async def motion_projects():
        return project_call(bridge.list_motion_projects)

    @app.post('/api/execution-context/apply')
    async def apply_execution_context():
        return project_call(bridge._reconcile_execution_context)

    @app.post('/api/projects')
    async def create_motion_project(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(bridge.create_motion_project, body)

    @app.get('/api/projects/{project_id}')
    async def motion_project(project_id: str):
        return project_call(bridge.load_motion_project, project_id)

    @app.patch('/api/projects/{project_id}')
    async def update_motion_project(project_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(bridge.update_motion_project, project_id, body)

    @app.post('/api/projects/{project_id}/select')
    async def select_motion_project(project_id: str):
        return project_call(bridge.select_motion_project, project_id)

    @app.delete('/api/projects/{project_id}')
    async def delete_motion_project(project_id: str):
        return project_call(bridge.delete_motion_project, project_id)

    @app.post('/api/projects/{project_id}/copy-file')
    async def copy_motion_project_file(project_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(bridge.copy_motion_project_file, project_id, body)

    @app.post('/api/projects/{project_id}/files')
    async def import_motion_project_file(project_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(bridge.import_motion_project_file, project_id, body)

    @app.get('/api/projects/{project_id}/tree-file')
    async def read_only_motion_project_file(project_id: str, relative_path: str):
        return project_call(
            bridge.load_read_only_project_file, project_id, relative_path
        )

    @app.get('/api/projects/{project_id}/files/{category}/{file_name}/download')
    async def download_motion_project_file(
        project_id: str, category: str, file_name: str
    ):
        path = project_call(
            bridge.download_motion_project_file, project_id, category, file_name
        )
        return FileResponse(str(path), filename=path.name)

    @app.get('/api/projects/{project_id}/files/{category}/{file_name}')
    async def motion_project_file(project_id: str, category: str, file_name: str):
        return project_call(
            bridge.load_motion_project_file, project_id, category, file_name
        )

    @app.put('/api/projects/{project_id}/files/{category}/{file_name}')
    async def save_motion_project_file(
        project_id: str, category: str, file_name: str, request: Request
    ):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(
            bridge.save_motion_project_file,
            project_id,
            category,
            file_name,
            body,
        )

    @app.post('/api/projects/{project_id}/files/{category}/{file_name}/rename')
    async def rename_motion_project_file(
        project_id: str, category: str, file_name: str, request: Request
    ):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return project_call(
            bridge.rename_motion_project_file,
            project_id,
            category,
            file_name,
            body,
        )

    @app.post('/api/projects/{project_id}/files/{category}/{file_name}/active')
    async def activate_motion_project_file(
        project_id: str, category: str, file_name: str
    ):
        return project_call(
            bridge.activate_motion_project_file,
            project_id,
            category,
            file_name,
        )

    @app.post('/api/projects/{project_id}/files/{category}/{file_name}/open-editor')
    async def open_motion_project_file_for_editing(
        project_id: str, category: str, file_name: str
    ):
        return project_call(
            bridge.open_motion_project_file_for_editing,
            project_id,
            category,
            file_name,
        )

    @app.delete('/api/projects/{project_id}/files/{category}/{file_name}')
    async def delete_motion_project_file(
        project_id: str, category: str, file_name: str
    ):
        return project_call(
            bridge.delete_motion_project_file, project_id, category, file_name
        )
