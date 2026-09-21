"""Scoped Hermes REST router. Imports only this plugin's backend package."""
import importlib.util
import os
from pathlib import Path
import sys
import threading
from contextlib import asynccontextmanager
from fastapi import APIRouter, HTTPException, Query

_root=Path(__file__).resolve().parent.parent
if 'ai_status_board' not in sys.modules:
    spec=importlib.util.spec_from_file_location('ai_status_board',_root/'ai_status_board'/'__init__.py',submodule_search_locations=[str(_root/'ai_status_board')])
    module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)
from ai_status_board.core import Board

_board=None
_lock=threading.Lock()

def get_board():
    global _board
    with _lock:
        if _board is None:
            home=Path(os.environ.get('HERMES_HOME') or Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'hermes')
            _board=Board(home/'plugin-data'/'ai-status-board'/'history.sqlite3')
            _board.start()
        return _board

@asynccontextmanager
async def lifespan(app):
    get_board()
    try: yield
    finally:
        global _board
        if _board: _board.close(); _board=None

router=APIRouter(lifespan=lifespan)

def validate_window(hours,step):
    if hours not in (6,24,168,720,2160,4320) or step not in (300,900,1800,3600,21600,86400) or hours*3600/step>600:
        raise HTTPException(422,'Choose a supported range and interval with at most 600 bars')

@router.get('/board')
def board(hours:int=24,step:int=1800):
    validate_window(hours,step)
    return get_board().snapshot(hours,step)

@router.post('/refresh')
def refresh(): return get_board().refresh(manual=True)

@router.get('/component')
def component(surface:str=Query(max_length=100),component:str=Query(max_length=256),hours:int=24,step:int=1800):
    validate_window(hours,step)
    b=get_board()
    if surface not in {a.id for a in b.adapters}: raise HTTPException(404,'Unknown surface')
    now=b.clock()
    result=b.history.timeline(surface,now-hours*3600,now,step,component)
    result['official_timeline']=b.history.official_timeline(surface,now-hours*3600,now,step,component)
    return result
