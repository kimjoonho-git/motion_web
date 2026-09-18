from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse


def register_project_routes(app: FastAPI, bridge, project_call) -> None:
    @app.get('/api/projects')
    async def motion_projects():
        return await project_call(bridge._project.list_projects)

    @app.post('/api/projects')
    async def create_motion_project(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await project_call(bridge._project.create_project, body)

    @app.get('/api/projects/{project_id}')
    async def motion_project(project_id: str):
        return await project_call(bridge._project.load_project, project_id)

    @app.patch('/api/projects/{project_id}')
    async def update_motion_project(project_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await project_call(bridge._project.update_project, project_id, body)

    @app.post('/api/projects/{project_id}/select')
    async def select_motion_project(project_id: str):
        return await project_call(bridge._project.select_project, project_id)

    @app.delete('/api/projects/{project_id}')
    async def delete_motion_project(project_id: str):
        return await project_call(bridge._project.delete_project, project_id)

    @app.post('/api/projects/{project_id}/files')
    async def import_motion_project_file(project_id: str, request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await project_call(bridge._project.import_file, project_id, body)

    @app.get('/api/projects/{project_id}/tree-file')
    async def read_only_motion_project_file(project_id: str, relative_path: str):
        return await project_call(
            bridge._project.load_read_only_file, project_id, relative_path
        )

    @app.get('/api/projects/{project_id}/files/{category}/{file_name}/download')
    async def download_motion_project_file(
        project_id: str, category: str, file_name: str
    ):
        path = await project_call(
            bridge._project.download_file, project_id, category, file_name
        )
        return FileResponse(str(path), filename=path.name)

    @app.get('/api/projects/{project_id}/files/{category}/{file_name}')
    async def motion_project_file(project_id: str, category: str, file_name: str):
        return await project_call(
            bridge._project.load_file, project_id, category, file_name
        )

    @app.put('/api/projects/{project_id}/files/{category}/{file_name}')
    async def save_motion_project_file(
        project_id: str, category: str, file_name: str, request: Request
    ):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail='request body must be an object')
        return await project_call(
            bridge._project.save_file,
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
        return await project_call(
            bridge._project.rename_file,
            project_id,
            category,
            file_name,
            body,
        )

    @app.post('/api/projects/{project_id}/files/{category}/{file_name}/active')
    async def activate_motion_project_file(
        project_id: str, category: str, file_name: str
    ):
        return await project_call(
            bridge._project.activate_file,
            project_id,
            category,
            file_name,
        )

    @app.post('/api/projects/{project_id}/files/{category}/{file_name}/open-editor')
    async def open_motion_project_file_for_editing(
        project_id: str, category: str, file_name: str
    ):
        return await project_call(
            bridge._project.open_file_for_editing,
            project_id,
            category,
            file_name,
        )

    @app.delete('/api/projects/{project_id}/files/{category}/{file_name}')
    async def delete_motion_project_file(
        project_id: str, category: str, file_name: str
    ):
        return await project_call(
            bridge._project.delete_file, project_id, category, file_name
        )
