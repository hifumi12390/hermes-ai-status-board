"""Isolated API/UI verification. Never reads or modifies Hermes settings."""
import importlib.util
from pathlib import Path
import sys
import time
import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from ai_status_board.core import Board
from ai_status_board.adapters import ADAPTERS
from ai_status_board.transport import Transport
from test_board import bundle

spec=importlib.util.spec_from_file_location('preview_plugin_api',ROOT/'dashboard'/'plugin_api.py')
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
live='--live' in sys.argv
if live:
    board=Board(ROOT/'tests'/'preview-data'/'live.sqlite3')
else:
    fixtures={url:bundle(a.id)[key] for a in ADAPTERS for key,url in a.endpoints().items()}
    transport=Transport(fixtures,httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=fixtures[str(r.url)]))))
    board=Board(ROOT/'tests'/'preview-data'/'fixture.sqlite3',transport=transport)
    for a in ADAPTERS:board._poll(a)
api._board=board
board.start()
app=FastAPI()
app.include_router(api.router,prefix='/api/plugins/ai-status-board')
app.mount('/',StaticFiles(directory=ROOT/'tests'/'preview-dist',html=True))
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=18764,log_level='warning')
